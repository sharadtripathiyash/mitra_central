"""Pass 3 — Global review pass. 1 LLM call.

Looks at the FULL tagging result (all files + their reasoning + the module
list) and proposes corrections for:
  - Files whose reasoning contradicts their tag
  - Filename families split across modules
  - Generic helper includes placed in module-specific buckets
  - Groups consistently mis-bucketed (the "9 xxinvappr* all in DOA" pattern)
"""
from __future__ import annotations

import sqlite3

import httpx

from ..llm_models import MODEL_PASS_3
from .config import utc_now
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


def build_review_prompt(
    rows: list[dict],
    modules: dict[str, str],
    customer_glossary: dict[str, dict],
) -> str:
    mod_lines = "\n".join(
        f"  - {tag}: {desc}" for tag, desc in sorted(modules.items())
    ) if modules else "  (none)"

    file_lines = []
    for r in rows:
        rs = (r.get("reasoning") or "").replace("\n", " ").strip()
        if len(rs) > 220:
            rs = rs[:220] + "…"
        file_lines.append(
            f"  - {r['file_path']:50s}  [{r['module_tag']}/{r['function_tag']}]  {rs}"
        )
    files_block = "\n".join(file_lines)

    glossary_block = format_customer_glossary(customer_glossary)

    return f"""You are reviewing a per-file tagging of {len(rows)} QAD source files
produced by an automated classifier. Spot mistakes; propose corrections.

CUSTOMER-DERIVED GLOSSARY (most authoritative source of module meaning):
{glossary_block}

CURRENT MODULES:
{mod_lines}

PER-FILE TAGGING
Format: <file_path>  [<module_tag>/<function_tag>]  <reasoning>

{files_block}

================================================================================
LOOK FOR THESE FAILURE PATTERNS
================================================================================

A. Files whose REASONING contradicts their TAG.
   e.g. reasoning says "implements invoice approval" but tagged DOA.

B. Filename FAMILIES split across different modules.
   e.g. xxspaapr.p in INVAPPR, xxsparej.p in MDM, xxspasbk.p in MDM — all
   should be SPA.

C. Files in /one/ module that are obviously the SAME CODE PATTERN as files
   in a /different/ module (split based on a filename suffix).

D. Generic helper includes (.i files like xxapprupd.i, xxnotifyupd.i,
   xxruleupd.i, xxdispmsg.p) tagged into module-specific buckets when
   they're cross-module utilities → propose SHARED.

E. (CRITICAL) Consistent groups WRONGLY bucketed. Several files sharing a
   distinct prefix all in one module that doesn't actually represent them.
   e.g. 9 files named xxinvappr* all tagged DOA → propose new INVAPPR
   module and move them all.

F. (NEW — CRITICAL) MODULE-MERGE candidates. Multiple modules in the
   CURRENT MODULES list that are clearly sub-families of one bigger module
   and should be merged into one. Look for:
     - Modules sharing a 3+ char prefix (DOA + DOAAPPR + DOARULE → DOA)
     - Modules with descriptions that describe sub-functions of a parent
       (e.g. "Sales Pricing Approval Rejection" + "Sales Pricing Approval
       Send-Back" + "Sales Pricing Approval" → all SPA)
   When you spot one, propose corrections that move ALL files from the
   smaller modules into the merged parent. The smaller modules will be
   auto-removed once empty.

================================================================================
RETURN ONLY VALID JSON
================================================================================

{{
  "corrections": [
    {{
      "file_path":      "<exact file_path from list above>",
      "from_module":    "<current module_tag — plain UPPERCASE, NO slashes>",
      "to_module":      "<corrected module_tag — plain UPPERCASE, NO slashes; can be EXISTING or BRAND-NEW>",
      "to_module_desc": "<1-sentence description; copy verbatim from CURRENT MODULES if to_module exists; write fresh if new>",
      "reason":         "<1-2 sentences justifying the correction.>"
    }}
  ]
}}

CRITICAL RULES:
  - ONLY include corrections where `to_module` is DIFFERENT from `from_module`.
    Do NOT include rows that confirm a correct tag. If a file is correctly
    tagged, omit it entirely.
  - to_module / from_module are PLAIN UPPERCASE TOKENS only. No slashes,
    no function names appended. "EINV" not "EINV/notification".
  - If no corrections are needed, return {{ "corrections": [] }}.
  - Up to 50 corrections in one response."""


async def review_tags(client: httpx.AsyncClient, conn: sqlite3.Connection) -> list[dict]:
    rows = fetch_all_tagged_with_module_desc(conn)
    if not rows:
        return []

    modules = get_existing_module_tags(conn)
    customer_glossary = get_customer_glossary(conn)

    system = (
        "You are an auditor of QAD customisation tagging. You spot individual "
        "mismatches, family splits, helper re-attribution, group splits where "
        "a consistent block of files belongs to its own new module, AND "
        "module-merge candidates where multiple over-fragmented sub-modules "
        "should be consolidated into one. module_tag values are plain "
        "UPPERCASE — never contain slashes. Only include corrections where "
        "to_module differs from from_module. Return ONLY valid JSON."
    )
    user = build_review_prompt(rows, modules, customer_glossary)
    result = await openai_call(
        client, system, user, max_tokens=6000, model=MODEL_PASS_3,
    )
    return result.get("corrections", []) or []


def apply_corrections(conn: sqlite3.Connection, corrections: list[dict]) -> tuple[int, int]:
    """Apply Pass 3 corrections to the DB.

    Returns (applied, skipped_noops) — applied = real changes;
                                       skipped_noops = rows where from == to.
    """
    applied = 0
    skipped_noops = 0

    for c in corrections:
        if not isinstance(c, dict):
            continue
        fp = str(c.get("file_path", "")).strip()
        to_mod = normalise_module_tag(c.get("to_module", ""))
        from_mod = normalise_module_tag(c.get("from_module", ""))
        if not fp or not to_mod or len(to_mod) > 10:
            continue
        # Belt-and-braces guard against the LLM still emitting no-op confirmations
        if to_mod == from_mod:
            skipped_noops += 1
            continue

        to_desc = str(c.get("to_module_desc", "")).strip()
        upsert_module(conn, to_mod, to_desc)
        if update_file_module(conn, fp, to_mod):
            applied += 1

    cleanup_orphan_modules(conn)
    conn.commit()
    return applied, skipped_noops
