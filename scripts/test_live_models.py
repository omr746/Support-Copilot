#!/usr/bin/env python3
"""Zero-dependency live tester for Groq models (Standard Library only).

Tests modern models: openai/gpt-oss-20b, openai/gpt-oss-120b, qwen/qwen3.8-27b, allam-2-7b, etc.

Usage:
  python scripts/test_live_models.py
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"

CANDIDATES = [
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "allam-2-7b",
]


def load_groq_api_key(env_path: Path) -> str:
    if not env_path.exists():
        print(f"[ERROR] .env file not found at {env_path}")
        sys.exit(1)
    for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" in line:
            k, _, v = line.partition("=")
            if k.strip() == "GROQ_API_KEY":
                val = v.strip().strip("'\"")
                if " #" in val:
                    val = val.split(" #", 1)[0].strip()
                return val
    print("[ERROR] GROQ_API_KEY not found in .env")
    sys.exit(1)


def fetch_available_models(api_key: str) -> list[str]:
    url = "https://api.groq.com/openai/v1/models"
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


def test_chat_completion(api_key: str, model_id: str, prompt: str) -> dict[str, str | float | int]:
    url = "https://api.groq.com/openai/v1/chat/completions"
    payload = json.dumps(
        {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": 200,
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
                (msg.get("reasoning_content") or msg.get("reasoning") or "")
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
            "error": msg[:80],
        }
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        return {
            "ok": False,
            "status": 0,
            "latency": round(elapsed, 3),
            "error": str(exc)[:80],
        }


def main() -> None:
    print("=" * 80)
    print("        Groq Live Models Benchmark & Discovery (Support Copilot)       ")
    print("=" * 80)

    api_key = load_groq_api_key(ENV_PATH)
    masked_key = f"{api_key[:6]}...{api_key[-4:]}" if len(api_key) > 10 else "***"
    print(f"[AUTH] Key loaded: {masked_key}")

    print("\n--> 1. Fetching available models list from Groq API...")
    try:
        all_models = fetch_available_models(api_key)
        chat_models = [
            m
            for m in all_models
            if not any(
                ex in m.lower() for ex in ("guard", "whisper", "orpheus", "embed", "safeguard")
            )
        ]
        print(f"[OK] Total models: {len(all_models)} | Chat models: {len(chat_models)}")
        print("\nActive Chat Models on your Groq key:")
        for m in chat_models:
            print(f"  * {m}")
    except Exception as exc:
        print(f"[ERROR] Could not fetch models list: {exc}")
        all_models = []
        chat_models = []

    # Target models to test
    targets = list(CANDIDATES)
    for m in chat_models:
        if any(k in m.lower() for k in ("oss", "qwen", "allam")) and m not in targets:
            targets.append(m)

    prompt = "مرحبا، ما دورك في دعم العملاء لشبكة الإنترنت؟ (أجب باختصار)"
    print(f"\n--> 2. Testing Models with prompt: '{prompt}'")
    print("=" * 80)
    header = (
        f"{'Model':<24} | {'Status':<10} | {'Latency':<8} | {'Tokens':<10} | {'Response Preview'}"
    )
    print(header)
    print("-" * 80)

    for mid in targets:
        res = test_chat_completion(api_key, mid, prompt)
        if res["ok"]:
            status_str = "200 OK"
            tokens_str = f"{res['prompt_tokens']}/{res['completion_tokens']}"
            display_text = res["content"] or (
                f"[Thinking]: {res['reasoning']}" if res["reasoning"] else ""
            )
            preview = str(display_text)[:40] + ("..." if len(str(display_text)) > 40 else "")
            print(
                f"{mid:<24} | {status_str:<10} | {res['latency']:<6}s  | {tokens_str:<10} | {preview}"
            )
        else:
            status_str = f"ERR {res['status']}"
            print(
                f"{mid:<24} | {status_str:<10} | {res['latency']:<6}s  | {'-/-':<10} | {res.get('error')}"
            )

    print("=" * 80)
    print("[DONE] Live verification completed.")


if __name__ == "__main__":
    main()
