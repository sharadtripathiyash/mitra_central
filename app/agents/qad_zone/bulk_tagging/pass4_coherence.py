"""Pass 4 — Module Coherence Verification.

After Passes 1-3 have classified everything, sample each module's content and
ask the LLM: "Do these N files genuinely belong to ONE business module?
Any outliers that don't fit?"

This catches the residual cases where Pass 2 dumped an unrelated file into
a module and Pass 3 didn't spot it (because the file looked superficially OK
in isolation but is clearly wrong when read alongside its sibling files).

Concurrency: PASS_4_CONCURRENCY parallel LLM calls (one per module).
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import httpx

from . import config
from .config import (
    OPENAI_MODEL_PRO,
    PASS_4_CONCURRENCY,
)
from .db import (
    cleanup_orphan_modules,
    fetch_all_tagged_with_module_desc,
    get_customer_glossary,
    get_existing_module_tags,
    update_file_module,
    upsert_module,
)
from .llm import normalise_module_tag, openai_call
from .pass_a_glossary import format_for_prompt as format_customer_glossary


# Per-file sample size (chars) during coherence verification — small.
COHERENCE_SAMPLE_CHARS = 700


def build_coherence_prompt(
    module_tag: str,
    module_desc: str,
    file_rows: list[dict],
    other_module_tags: list[str],
    customer_glossary: dict[str, dict],
) -> str:
    samples: list[str] = []
    # Sample at most ~8 files — bigger modules don't need every file inspected
    for r in file_rows[:8]:
        full = config.INPUT_DIR / r["file_path"]
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
            snippet = text[:COHERENCE_SAMPLE_CHARS]
        except Exception:
            snippet = "(could not read file)"
        samples.append(
            f"--- FILE: {r['file_path']}  [function: {r['function_tag']}] ---\n"
            f"description: {r['description']}\n"
            f"reasoning:   {r['reasoning']}\n"
            f"--- code sample ---\n{snippet}"
        )

    rest_count = len(file_rows) - len(samples)
    rest_note = ""
    if rest_count > 0:
        rest_note = (
            f"\n(plus {rest_count} more file(s) in this module not shown — "
            "their file paths and per-file descriptions are visible below.)"
        )

    all_files_list = "\n".join(
        f"  - {r['file_path']}  ({r['function_tag']}) — {r['description']}"
        for r in file_rows
    )

    other_tags_list = ", ".join(other_module_tags) if other_module_tags else "(none)"

    return f"""You are verifying the coherence of a single module in this customer's
QAD customisation tagging.

================================================================================
MODULE UNDER REVIEW
================================================================================

  Tag:         {module_tag}
  Description: {module_desc}
  File count:  {len(file_rows)}

================================================================================
CUSTOMER GLOSSARY (authoritative module meanings)
================================================================================

{format_customer_glossary(customer_glossary)}

================================================================================
ALL FILES CURRENTLY IN THIS MODULE
================================================================================

{all_files_list}

================================================================================
CODE SAMPLES FROM REPRESENTATIVE FILES
================================================================================

{chr(10).join(samples)}
{rest_note}

================================================================================
OTHER MODULES THAT EXIST (potential destinations for outliers)
================================================================================
{other_tags_list}

================================================================================
YOUR TASK
================================================================================

Read the file list and code samples. Decide:

  1. Do all these files genuinely belong to ONE coherent business module
     matching the description above?

  2. If yes, return {{ "verdict": "coherent", "outliers": [] }}.

  3. If you see outliers (files that clearly don't fit this module — for
     example, a generic utility, or code about a completely different
     business area), list them with WHERE they should go instead.

Be conservative — only flag files as outliers when their reasoning /
sample code obviously doesn't match the rest of the module. Borderline
cases stay.

Return ONLY valid JSON:

{{
  "verdict":  "coherent" | "has_outliers",
  "outliers": [
    {{
      "file_path":   "<exact path from list above>",
      "to_module":   "<existing module tag from OTHER MODULES list, OR a new tag if genuinely needed>",
      "reason":      "<1 sentence explaining why this file doesn't fit and where it does>"
    }}
  ]
}}"""


async def verify_one_module(
    client: httpx.AsyncClient,
    module_tag: str,
    module_desc: str,
    file_rows: list[dict],
    other_module_tags: list[str],
    customer_glossary: dict[str, dict],
    sem: asyncio.Semaphore,
) -> dict:
    # Modules with 1-2 files: skip coherence check (no real "coherence" to check)
    if len(file_rows) < 3:
        return {"module_tag": module_tag, "verdict": "skipped", "outliers": []}

    system = (
        "You verify the coherence of QAD module tagging by reading code "
        "samples from files assigned to a single module. You flag outliers "
        "conservatively — only files that clearly don't belong. Return ONLY "
        "valid JSON."
    )
    user = build_coherence_prompt(
        module_tag, module_desc, file_rows, other_module_tags, customer_glossary
    )
    async with sem:
        result = await openai_call(
            client, system, user, max_tokens=2000, model=OPENAI_MODEL_PRO,
        )
    return {
        "module_tag": module_tag,
        "verdict":    str(result.get("verdict", "")).strip().lower(),
        "outliers":   result.get("outliers", []) or [],
    }


async def verify_all_modules(
    client: httpx.AsyncClient,
    conn: sqlite3.Connection,
) -> list[dict]:
    """Returns per-module verification results. Caller applies outlier moves."""
    rows = fetch_all_tagged_with_module_desc(conn)
    if not rows:
        return []

    modules = get_existing_module_tags(conn)
    customer_glossary = get_customer_glossary(conn)
    by_mod: dict[str, list[dict]] = {}
    for r in rows:
        by_mod.setdefault(r["module_tag"], []).append(r)

    sem = asyncio.Semaphore(PASS_4_CONCURRENCY)
    all_tags = sorted(modules.keys())
    tasks = [
        verify_one_module(
            client,
            tag,
            modules.get(tag, ""),
            file_rows,
            [t for t in all_tags if t != tag],
            customer_glossary,
            sem,
        )
        for tag, file_rows in by_mod.items()
    ]

    results: list[dict] = []
    completed = 0
    total = len(tasks)
    for coro in asyncio.as_completed(tasks):
        try:
            r = await coro
        except Exception as exc:
            print(f"  COHERENCE FAIL: {exc}")
            continue
        results.append(r)
        completed += 1
        n_out = len(r["outliers"])
        mark = "✓" if r["verdict"] == "coherent" or r["verdict"] == "skipped" else "!"
        suffix = "" if not n_out else f" — {n_out} outlier(s)"
        print(f"  [{completed:>3}/{total}] {mark} {r['module_tag']:<10} verdict={r['verdict']}{suffix}")

    return results


def apply_outlier_moves(
    conn: sqlite3.Connection,
    coherence_results: list[dict],
) -> int:
    """Apply Pass 4 outlier moves to the DB. Returns count applied."""
    applied = 0
    for res in coherence_results:
        for outlier in res.get("outliers", []):
            if not isinstance(outlier, dict):
                continue
            fp = str(outlier.get("file_path", "")).strip()
            to_mod = normalise_module_tag(outlier.get("to_module", ""))
            if not fp or not to_mod or len(to_mod) > 10:
                continue
            # If target module doesn't exist, create stub
            upsert_module(conn, to_mod, "")
            if update_file_module(conn, fp, to_mod):
                applied += 1
    cleanup_orphan_modules(conn)
    conn.commit()
    return applied
