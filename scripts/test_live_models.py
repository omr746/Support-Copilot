#!/usr/bin/env python3
"""Zero-dependency live tester for Groq and Gemini models (Standard Library only).

Benchmarks active chat completion models:
- Groq: openai/gpt-oss-20b, openai/gpt-oss-120b, qwen/qwen3.8-27b, allam-2-7b
- Gemini: gemini-3.8-flash, gemini-3.6-flash, gemini-3-flash, gemini-3.1-flash-lite, etc.

Usage:
  python scripts/test_live_models.py
  python scripts/test_live_models.py --provider gemini
  python scripts/test_live_models.py --provider groq
  python scripts/test_live_models.py --prompt "Ping"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"

GROQ_CANDIDATES = [
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "allam-2-7b",
]

GEMINI_CANDIDATES = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]


def load_env_var(name: str, env_path: Path) -> str:
    """Load an environment variable from os.environ or .env file."""
    if val := os.environ.get(name):
        return val.strip()
    if not env_path.exists():
        return ""
    for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" in line:
            k, _, v = line.partition("=")
            if k.strip() == name:
                val = v.strip().strip("'\"")
                if " #" in val:
                    val = val.split(" #", 1)[0].strip()
                return val
    return ""


def mask_key(key: str) -> str:
    """Mask key preserving prefix and suffix."""
    if not key:
        return "<not set>"
    if len(key) <= 8:
        return "***"
    return f"{key[:6]}...{key[-4:]}"


def fetch_groq_models(api_key: str) -> list[str]:
    """Query Groq /models endpoint."""
    url = f"{GROQ_BASE_URL}/models"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "support-copilot-model-tester",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        models = [m["id"] for m in data.get("data", [])]
        return sorted(models)


def test_chat_completion(
    base_url: str,
    api_key: str,
    model_id: str,
    prompt: str,
    max_tokens: int = 150,
) -> dict[str, Any]:
    """Send a chat completion request to an OpenAI-compatible endpoint."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = json.dumps(
        {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": max_tokens,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "support-copilot-model-tester",
        },
        method="POST",
    )

    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            elapsed = time.perf_counter() - t0
            raw = json.loads(resp.read().decode("utf-8"))
            usage = raw.get("usage", {})
            choice = raw.get("choices", [{}])[0]
            msg = choice.get("message", {})
            content = (msg.get("content") or "").replace("\n", " ").strip()
            reasoning = (
                (msg.get("reasoning_content") or msg.get("reasoning") or msg.get("thought") or "")
                .replace("\n", " ")
                .strip()
            )
            finish_reason = choice.get("finish_reason", "")
            return {
                "ok": True,
                "status": resp.status,
                "latency": round(elapsed, 3),
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
                "content": content,
                "reasoning": reasoning,
                "finish_reason": finish_reason,
            }
    except urllib.error.HTTPError as err:
        elapsed = time.perf_counter() - t0
        err_body = err.read().decode("utf-8", errors="replace")
        try:
            err_json = json.loads(err_body)
            msg = err_json.get("error", {}).get("message", err_body)
        except Exception:
            msg = err_body
        return {
            "ok": False,
            "status": err.code,
            "latency": round(elapsed, 3),
            "error": str(msg)[:80],
        }
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        return {
            "ok": False,
            "status": 0,
            "latency": round(elapsed, 3),
            "error": str(exc)[:80],
        }


def run_benchmark_table(
    provider_name: str,
    base_url: str,
    api_key: str,
    models: list[str],
    prompt: str,
) -> None:
    """Run benchmark against a list of models for a given provider and display formatted table."""
    print("=" * 80)
    print(f"[{provider_name.upper()}] Live Models Benchmark ({base_url})")
    print(f"[AUTH] Key: {mask_key(api_key)}")
    print(f"[PROMPT] '{prompt}'")
    print("=" * 80)
    header = (
        f"{'Model':<25} | {'Status':<10} | {'Latency':<8} | {'Tokens':<10} | {'Response Preview'}"
    )
    print(header)
    print("-" * 80)

    for mid in models:
        res = test_chat_completion(base_url, api_key, mid, prompt)
        if res["ok"]:
            status_str = "200 OK"
            tokens_str = f"{res['prompt_tokens']}/{res['completion_tokens']}"
            display_text = res["content"] or (
                f"[Thinking]: {res['reasoning']}" if res["reasoning"] else ""
            )
            preview = str(display_text)[:38] + ("..." if len(str(display_text)) > 38 else "")
            print(
                f"{mid:<25} | {status_str:<10} | {res['latency']:<6}s  | {tokens_str:<10} | {preview}"
            )
        else:
            status_str = f"ERR {res['status']}"
            print(
                f"{mid:<25} | {status_str:<10} | {res['latency']:<6}s  | {'-/-':<10} | {res.get('error')}"
            )
    print("=" * 80 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Zero-dependency live tester for Groq and Gemini models."
    )
    parser.add_argument(
        "--provider",
        choices=["groq", "gemini", "all"],
        default="all",
        help="Provider to benchmark (default: all)",
    )
    parser.add_argument(
        "--prompt",
        default="مرحبا، ما دورك في دعم العملاء لشبكة الإنترنت؟ (أجب باختصار)",
        help="Prompt to test models with",
    )
    args = parser.parse_args()

    groq_key = load_env_var("GROQ_API_KEY", ENV_PATH)
    gemini_key = load_env_var("GEMINI_API_KEY", ENV_PATH)

    run_groq = args.provider in ("groq", "all")
    run_gemini = args.provider in ("gemini", "all")

    if not groq_key and run_groq and args.provider == "groq":
        print("[ERROR] GROQ_API_KEY not found in .env or environment")
        sys.exit(1)
    if not gemini_key and run_gemini and args.provider == "gemini":
        print("[ERROR] GEMINI_API_KEY not found in .env or environment")
        sys.exit(1)

    print("=" * 80)
    print("       Support Copilot — Multi-Provider Live Models Benchmark       ")
    print("=" * 80 + "\n")

    if run_groq:
        if not groq_key:
            print("[SKIP] Groq: GROQ_API_KEY not set.\n")
        else:
            # Discover models on Groq
            print("--> Discovering active Groq models via /models...")
            try:
                all_models = fetch_groq_models(groq_key)
                chat_models = [
                    m
                    for m in all_models
                    if not any(
                        ex in m.lower()
                        for ex in ("guard", "whisper", "orpheus", "embed", "safeguard")
                    )
                ]
                print(
                    f"[OK] Total Groq models: {len(all_models)} | Chat models: {len(chat_models)}"
                )
            except Exception as exc:
                print(f"[WARN] Could not fetch Groq models list: {exc}")
                chat_models = []

            groq_targets = list(GROQ_CANDIDATES)
            for m in chat_models:
                if any(k in m.lower() for k in ("oss", "qwen", "allam")) and m not in groq_targets:
                    groq_targets.append(m)

            run_benchmark_table("groq", GROQ_BASE_URL, groq_key, groq_targets, args.prompt)

    if run_gemini:
        if not gemini_key:
            print("[SKIP] Gemini: GEMINI_API_KEY not set.\n")
        else:
            print("--> Testing Gemini models via OpenAI-compatible endpoint...")
            run_benchmark_table(
                "gemini", GEMINI_BASE_URL, gemini_key, GEMINI_CANDIDATES, args.prompt
            )

    print("[DONE] Live verification completed.")


if __name__ == "__main__":
    main()
