"""Pass A — Customer Glossary Derivation.

For each prefix family detected by Pass 0 / 0.5, sample the family's code and
ask the LLM "what does this prefix mean for THIS customer?". The result is
stored in the `customer_glossary` table and injected into every downstream
prompt.

This is the change that lets RTDC mean "Returnable Delivery Challan" for one
customer and something else entirely for another — the meaning comes from
the customer's actual code, not from the LLM's general knowledge.

Concurrency: PASS_A_CONCURRENCY parallel LLM calls. Each call is small
(~3-4K tokens), so a 50-family codebase finishes in ~30-60 seconds.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import httpx

from . import config
from .config import (
    OPENAI_MODEL_MINI,
    PASS_A_CONCURRENCY,
)
from .db import upsert_glossary_entry
from .llm import openai_call
from .standard_glossary import lookup as standard_lookup


# How much code we sample per file in the family (chars). Keep small —
# we just need program headers and a sense of what the code does.
SAMPLE_CHARS_PER_FILE = 2000
# How many files per family we sample (largest N).
MAX_SAMPLE_FILES = 3


def build_sample_block(prefix: str, files: list[str]) -> str:
    """Pick up to MAX_SAMPLE_FILES files from the family and return a
    formatted code-sample block for the prompt."""
    # Order by size descending so we pick the meatier files first
    sized: list[tuple[int, str]] = []
    for rel in files:
        full = config.INPUT_DIR / rel
        try:
            sized.append((full.stat().st_size, rel))
        except Exception:
            sized.append((0, rel))
    sized.sort(reverse=True)

    parts: list[str] = []
    for _, rel in sized[:MAX_SAMPLE_FILES]:
        full = config.INPUT_DIR / rel
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        snippet = text[:SAMPLE_CHARS_PER_FILE]
        parts.append(f"--- FILE: {rel} ({len(text):,} chars total) ---\n{snippet}")

    return "\n\n".join(parts)


def build_glossary_prompt(prefix: str, files: list[str], sample_block: str) -> str:
    file_list = "\n".join(f"  - {f}" for f in files[:15])
    if len(files) > 15:
        file_list += f"\n  ... and {len(files) - 15} more"

    return f"""You are deriving the customer-specific meaning of a module prefix used in
this customer's QAD customisation codebase.

PREFIX: {prefix}

FILES IN THIS FAMILY ({len(files)} total):
{file_list}

SAMPLE SOURCE CODE FROM UP TO {MAX_SAMPLE_FILES} REPRESENTATIVE FILES:
================================================================================
{sample_block}
================================================================================

Based ONLY on what you can see in the code above (program headers, comments,
table names, variable names, business logic), derive what this prefix
"{prefix}" represents for THIS customer.

Different customers use different prefixes for different things — DO NOT
guess based on the prefix's general English meaning. Ground every claim in
what the code actually shows.

If the sample code clearly explains the meaning (e.g. a header comment says
"program: xxrtdcmt.p — Returnable Delivery Challan Maintenance"), confidence
is "high".

If you can only infer the general area from variable names and structure
without explicit confirmation, confidence is "medium".

If the code doesn't reveal what the prefix means (just an opaque utility),
confidence is "low" — and write meaning as "{prefix} module — specific
business meaning not derivable from sampled code".

Return ONLY valid JSON with this exact shape:

{{
  "prefix":     "{prefix}",
  "meaning":    "<1-sentence meaning of the prefix for this customer; e.g. 'Returnable / Non-Returnable Delivery Challan workflow' — derived from the sampled code>",
  "confidence": "<high | medium | low>",
  "evidence":   "<1-2 sentences quoting or citing what in the sampled code revealed this meaning. Mention specific filename(s) and header comments / variable / table names that supported the conclusion. If confidence is low, explain what's missing.>"
}}"""


async def derive_one(
    client: httpx.AsyncClient,
    prefix: str,
    files: list[str],
    sem: asyncio.Semaphore,
) -> dict:
    """One LLM call for one prefix family."""
    # Short-circuit if prefix is a known QAD-standard term
    std = standard_lookup(prefix)
    if std:
        return {
            "prefix":     prefix,
            "meaning":    std,
            "confidence": "high",
            "evidence":   "Universal QAD standard acronym — pre-defined in standard_glossary.py",
        }

    sample_block = build_sample_block(prefix, files)
    if not sample_block:
        return {
            "prefix":     prefix,
            "meaning":    f"{prefix} module — sampled files unreadable",
            "confidence": "low",
            "evidence":   "Could not read any sample file for this prefix",
        }

    system = (
        "You derive customer-specific QAD module prefix meanings by reading "
        "sample code. You NEVER guess based on the prefix's English-language "
        "meaning — every claim must be supported by what's actually in the "
        "sample code. Return ONLY valid JSON."
    )
    user = build_glossary_prompt(prefix, files, sample_block)

    async with sem:
        result = await openai_call(client, system, user,
                                   max_tokens=600, model=OPENAI_MODEL_MINI)

    return {
        "prefix":     prefix,
        "meaning":    str(result.get("meaning", "")).strip(),
        "confidence": str(result.get("confidence", "low")).strip().lower(),
        "evidence":   str(result.get("evidence", "")).strip(),
    }


async def derive_customer_glossary(
    client: httpx.AsyncClient,
    conn: sqlite3.Connection,
    families: dict[str, list[str]],
) -> dict[str, dict]:
    """Pass A — derive meaning for every prefix family. Stores in DB.

    Returns the assembled customer glossary {PREFIX: {meaning, confidence, evidence}}.
    Parallelised at PASS_A_CONCURRENCY.
    """
    if not families:
        return {}

    sem = asyncio.Semaphore(PASS_A_CONCURRENCY)
    tasks = [
        derive_one(client, prefix, files, sem)
        for prefix, files in families.items()
    ]

    # Process in batches of CONCURRENCY × 2 so we can stream-print progress
    results: list[dict] = []
    completed = 0
    total = len(tasks)
    for coro in asyncio.as_completed(tasks):
        try:
            r = await coro
        except Exception as exc:
            print(f"  GLOSSARY FAIL: {exc}")
            continue
        results.append(r)
        completed += 1
        conf_emoji = {"high": "✓", "medium": "~", "low": "?"}.get(r["confidence"], "?")
        meaning_short = r["meaning"][:80]
        print(f"  [{completed:>3}/{total}] {conf_emoji} {r['prefix']:<10} = {meaning_short}")

    # Persist
    glossary: dict[str, dict] = {}
    for r in results:
        upsert_glossary_entry(
            conn, r["prefix"], r["meaning"], r["confidence"], r["evidence"]
        )
        glossary[r["prefix"]] = {
            "meaning":    r["meaning"],
            "confidence": r["confidence"],
            "evidence":   r["evidence"],
        }
    conn.commit()
    return glossary


def format_for_prompt(glossary: dict[str, dict]) -> str:
    """Render the customer glossary as a text block for inclusion in prompts."""
    if not glossary:
        return "CUSTOMER-DERIVED GLOSSARY: (none yet)"
    lines = [
        "CUSTOMER-DERIVED MODULE GLOSSARY "
        "(meanings derived from THIS customer's actual code by Pass A):"
    ]
    for prefix, info in sorted(glossary.items()):
        conf = info.get("confidence", "?")
        meaning = info.get("meaning", "")
        marker = {"high": "✓", "medium": "~", "low": "?"}.get(conf, "?")
        lines.append(f"  {marker} {prefix:<10} = {meaning}")
    return "\n".join(lines)
