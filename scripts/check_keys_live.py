#!/usr/bin/env python3
"""Live check of provider keys in .env (Plan Step 0.2, Tests line).

What it does
  1. Finds .env at the repo root (or --env-file) and checks it is git-ignored.
  2. Checks that every required key exists and is not an empty or placeholder value.
  3. Makes ONE tiny read-only call per provider (no tokens spent, no quota used).
  4. Prints a clear message per provider. Key values are NEVER printed.

Uses only the Python standard library, so it runs before `uv sync`.

Run:   python3 scripts/check_keys_live.py
       python3 scripts/check_keys_live.py --only groq,gemini
Exit:  0 = all checked providers OK, 1 = at least one problem.
"""
from __future__ import annotations

import argparse
import base64
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV = REPO_ROOT / ".env"
TIMEOUT_SECONDS = 15
DEFAULT_LANGFUSE_HOST = "https://cloud.langfuse.com"

# Endpoints are module constants so tests can point them at a stub server.
GROQ_URL = "https://api.groq.com/openai/v1/models"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1"
LANGFUSE_PATH = "/api/public/projects"

PLACEHOLDER_MARKERS = ("your_", "your-", "changeme", "replace_me", "xxxx", "<", "todo")


@dataclass(frozen=True)
class Result:
    name: str
    ok: bool
    message: str


class NetworkProblem(Exception):
    """Could not reach the provider (DNS, timeout, proxy, offline)."""


# ----------------------------------------------------------------- .env parsing
def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()  # strip() also removes a Windows \r
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if value[:1] in ("'", '"') and value[-1:] == value[:1] and len(value) >= 2:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].strip()
        values[key.strip()] = value
    return values


def is_placeholder(value: str) -> bool:
    low = value.lower()
    return any(marker in low for marker in PLACEHOLDER_MARKERS)


# ------------------------------------------------------------------ HTTP helper
def http_get(url: str, headers: dict[str, str]) -> int:
    """Return the HTTP status. Never includes headers or keys in errors."""
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return int(response.status)
    except urllib.error.HTTPError as err:
        return int(err.code)
    except (socket.timeout, TimeoutError):
        raise NetworkProblem(f"timed out after {TIMEOUT_SECONDS}s")
    except urllib.error.URLError as err:
        raise NetworkProblem(f"cannot reach the server ({type(err.reason).__name__})")


def judge(status: int, rejected: tuple[int, ...], hint: str) -> tuple[bool, str]:
    if status == 200:
        return True, "key accepted"
    if status in rejected:
        return False, f"key REJECTED by the provider (HTTP {status}). {hint}"
    if status == 429:
        return True, "key accepted, but the provider is rate-limiting right now (HTTP 429)"
    return False, f"unexpected answer from the provider (HTTP {status})"


# -------------------------------------------------------------------- providers
def probe_groq(env: dict[str, str]) -> tuple[bool, str]:
    status = http_get(GROQ_URL, {
        "Authorization": f"Bearer {env['GROQ_API_KEY']}",
        "Accept": "application/json",
        "User-Agent": "support-copilot-key-check",
    })
    return judge(status, (401, 403),
                 "Create a new key at console.groq.com and update GROQ_API_KEY.")


def probe_gemini(env: dict[str, str]) -> tuple[bool, str]:
    status = http_get(GEMINI_URL, {
        "x-goog-api-key": env["GEMINI_API_KEY"],
        "Accept": "application/json",
        "User-Agent": "support-copilot-key-check",
    })
    return judge(status, (400, 401, 403),
                 "Create a new key in Google AI Studio and update GEMINI_API_KEY.")


def probe_langfuse(env: dict[str, str]) -> tuple[bool, str]:
    host = (env.get("LANGFUSE_HOST") or DEFAULT_LANGFUSE_HOST).rstrip("/")
    if not host.startswith(("http://", "https://")):
        return False, "LANGFUSE_HOST must start with https:// (EU: https://cloud.langfuse.com)"
    pair = f"{env['LANGFUSE_PUBLIC_KEY']}:{env['LANGFUSE_SECRET_KEY']}".encode()
    status = http_get(host + LANGFUSE_PATH, {
        "Authorization": "Basic " + base64.b64encode(pair).decode(),
        "Accept": "application/json",
        "User-Agent": "support-copilot-key-check",
    })
    return judge(status, (401, 403),
                 "Check the public/secret pair and that LANGFUSE_HOST matches your region "
                 "(EU https://cloud.langfuse.com, US https://us.cloud.langfuse.com).")


@dataclass(frozen=True)
class Provider:
    name: str
    required: tuple[str, ...]
    probe: Callable[[dict[str, str]], tuple[bool, str]]


PROVIDERS: tuple[Provider, ...] = (
    Provider("groq", ("GROQ_API_KEY",), probe_groq),
    Provider("gemini", ("GEMINI_API_KEY",), probe_gemini),
    Provider("langfuse", ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"), probe_langfuse),
)


# ----------------------------------------------------------------------- checks
def check_env_file_hygiene(path: Path) -> Result:
    if not path.is_file():
        return Result(".env file", False,
                      f"not found at {path}. Run: cp .env.example .env  then fill in your keys.")
    try:
        completed = subprocess.run(["git", "check-ignore", "-q", str(path)],
                                   cwd=REPO_ROOT, capture_output=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return Result(".env file", True, "found (git not available, ignore check skipped)")
    if completed.returncode == 0:
        return Result(".env file", True, "found and ignored by git")
    if completed.returncode == 1:
        return Result(".env file", False,
                      "found, but git does NOT ignore it. Add a line `.env` to .gitignore "
                      "before you put real keys in it.")
    return Result(".env file", True, "found (not inside a git repo, ignore check skipped)")


def check_provider(provider: Provider, env: dict[str, str]) -> Result:
    missing = [k for k in provider.required if not env.get(k)]
    if missing:
        return Result(provider.name, False,
                      f"MISSING in .env: {', '.join(missing)}. Add it, or skip this "
                      f"provider with --only.")
    placeholders = [k for k in provider.required if is_placeholder(env[k])]
    if placeholders:
        return Result(provider.name, False,
                      f"{', '.join(placeholders)} still holds a placeholder value. Paste the real key.")
    try:
        ok, message = provider.probe(env)
    except NetworkProblem as err:
        return Result(provider.name, False,
                      f"network problem: {err}. Check your internet, VPN or proxy, then retry.")
    return Result(provider.name, ok, message)


def run_checks(env_path: Path = DEFAULT_ENV, only: tuple[str, ...] = ()) -> list[Result]:
    hygiene = check_env_file_hygiene(env_path)
    results = [hygiene]
    if not env_path.is_file():
        return results
    env = parse_env(env_path)
    selected = [p for p in PROVIDERS if not only or p.name in only]
    results.extend(check_provider(p, env) for p in selected)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live check of provider keys in .env")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--only", default="", help="comma list: groq,gemini,langfuse")
    args = parser.parse_args(argv)

    only = tuple(s.strip() for s in args.only.split(",") if s.strip())
    unknown = [s for s in only if s not in {p.name for p in PROVIDERS}]
    if unknown:
        print(f"[FAIL] unknown provider(s): {', '.join(unknown)}")
        return 1

    results = run_checks(args.env_file, only)
    for r in results:
        print(f"[{'OK' if r.ok else 'FAIL'}] {r.name}: {r.message}")
    failed = [r for r in results if not r.ok]
    print()
    print("All keys work." if not failed else f"{len(failed)} problem(s). Fix the [FAIL] lines and rerun.")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())