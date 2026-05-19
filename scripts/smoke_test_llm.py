"""LLM smoke test — verify OpenAI + Anthropic credentials and JSON parsing.

Run BEFORE re-running the 30-minute bulk-upload pipeline. Catches:

  - Missing / wrong OPENAI_API_KEY or ANTHROPIC_API_KEY in .env
  - Model identifier mismatches (e.g. OpenAI deprecates "gpt-5.4-mini")
  - JSON parsing issues from either provider
  - Quota / billing problems (429 returns clear errors)

Usage:

    python scripts/smoke_test_llm.py

Exit code 0 = both providers OK, ready to run the full pipeline.
Exit code 1 = at least one provider failed; check the printed error.
"""
from __future__ import annotations

import asyncio
import sys

# Add the project root to sys.path so we can import app.* when running this
# script standalone (without `python -m`).
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.core.llm import chat, parse_json_response


# Small JSON-output test prompt. Both models should produce identical structure.
_SYSTEM = (
    "You are a JSON-only responder. Return ONLY valid JSON, no prose, "
    "no markdown fences."
)
_USER = (
    'Return JSON: {"greeting": "hello", "model_self_id": "<a short string '
    'identifying you>", "two_plus_two": <integer result of 2+2>}. '
    "Nothing else."
)


async def _try_call(label: str, model_spec: str) -> bool:
    """Run one provider call and report pass/fail. Returns True on success."""
    print(f"\n── {label} ── {model_spec}")
    try:
        raw = await chat(
            model_spec,
            _SYSTEM,
            _USER,
            max_tokens=200,
            temperature=0.0,
        )
    except Exception as exc:
        print(f"  ✗ FAIL — network/auth error: {type(exc).__name__}: {exc}")
        return False

    if not raw or not raw.strip():
        print("  ✗ FAIL — empty response")
        return False

    print(f"  raw response ({len(raw)} chars):\n    {raw.strip()[:300]}")

    try:
        parsed = parse_json_response(raw)
    except Exception as exc:
        print(f"  ✗ FAIL — JSON parse failed: {exc}")
        return False

    if "two_plus_two" not in parsed:
        print(f"  ✗ FAIL — response missing expected key: {parsed}")
        return False

    val = parsed.get("two_plus_two")
    if val not in (4, "4"):
        print(f"  ⚠ WARN — two_plus_two is {val!r}, expected 4 (model still works, "
              "but arithmetic is off)")

    print(f"  ✓ OK   — parsed: model_self_id={parsed.get('model_self_id')!r}, "
          f"two_plus_two={val!r}")
    return True


async def main() -> int:
    print("=" * 70)
    print("LLM Smoke Test — verifying OpenAI + Anthropic credentials")
    print("=" * 70)

    # Quick credential sanity check before making network calls
    if not settings.openai_api_key:
        print("✗ OPENAI_API_KEY is not set in .env. Aborting.")
        return 1
    if not settings.anthropic_api_key:
        print("✗ ANTHROPIC_API_KEY is not set in .env. Aborting.")
        return 1

    print(f"\nOPENAI_API_KEY:    {settings.openai_api_key[:8]}…"
          f"{settings.openai_api_key[-4:]}")
    print(f"ANTHROPIC_API_KEY: {settings.anthropic_api_key[:8]}…"
          f"{settings.anthropic_api_key[-4:]}")

    # ── OpenAI test ─────────────────────────────────────────────────────────
    # Use GPT-5.4 Mini — cheapest viable, exercises the same JSON mode path
    # the bulk-tagging Pass 2 uses.
    ok_openai = await _try_call("OpenAI (GPT-5.4 Mini)", "openai:gpt-5.4-mini")

    # ── Anthropic test ──────────────────────────────────────────────────────
    # Use Claude Haiku 4.5 — cheapest Anthropic model. Exercises the same
    # anthropic_chat path that Opus calls use (without extended thinking
    # which is expensive). If Haiku JSON parsing works, Opus will too.
    ok_anthropic = await _try_call("Anthropic (Claude Haiku 4.5)", "anthropic:claude-haiku-4-5")

    print("\n" + "=" * 70)
    if ok_openai and ok_anthropic:
        print("✓ BOTH PROVIDERS OK — safe to run the full bulk-upload pipeline.")
        print("=" * 70)
        return 0

    print("✗ AT LEAST ONE PROVIDER FAILED — fix the error above before running")
    print("  the full pipeline. (Check .env, API key validity, quota, model name.)")
    print("=" * 70)
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
