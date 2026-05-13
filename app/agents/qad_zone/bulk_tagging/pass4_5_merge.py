"""Pass 4.5 — Final cross-module merge sweep. 1 LLM call.

After Passes 1-4 have finished, the module list can still be over-fragmented:
  - Sub-domains kept as separate modules (e.g. SO + SOS + SORP)
  - Function-named modules that should fold into a real domain
    (e.g. CRJSON → EINV, BMC → BOM)
  - Two modules with near-identical descriptions

Pass 4.5 takes ALL current modules + file listings + customer glossary and
asks gpt-4o "which pairs are the same business domain and should merge?".

Cheap (1 call), high yield — this is where over-fragmentation gets fixed.

Unlike Pass 3 which proposes per-FILE corrections, Pass 4.5 proposes whole-
module merges: every file in `from_module` moves to `to_module`, and the
emptied module is auto-removed by cleanup_orphan_modules.
"""
from __future__ import annotations

import sqlite3

import httpx

from .config import OPENAI_MODEL_PRO
from .db import (
    cleanup_orphan_modules,
    fetch_all_tagged_with_module_desc,
    get_customer_glossary,
    get_existing_module_tags,
)
from .llm import normalise_module_tag, openai_call
from .pass_a_glossary import format_for_prompt as format_customer_glossary


# How many sample file paths to show per module in the prompt (keep small).
SAMPLE_FILES_PER_MODULE = 8


def build_merge_prompt(
    by_mod: dict[str, list[dict]],
    modules: dict[str, str],
    customer_glossary: dict[str, dict],
) -> str:
    blocks: list[str] = []
    for tag in sorted(by_mod.keys()):
        desc = modules.get(tag, "") or "(no description)"
        files = by_mod[tag]
        sample = [f["file_path"] for f in files[:SAMPLE_FILES_PER_MODULE]]
        extra = (
            f"\n      ... and {len(files) - SAMPLE_FILES_PER_MODULE} more"
            if len(files) > SAMPLE_FILES_PER_MODULE
            else ""
        )
        blocks.append(
            f"━━ {tag} ({len(files)} file{'s' if len(files) != 1 else ''}) — {desc}\n"
            f"      files: {', '.join(sample)}{extra}"
        )
    modules_block = "\n\n".join(blocks)

    glossary_block = format_customer_glossary(customer_glossary)

    return f"""You are doing a FINAL consolidation pass on a QAD customisation tagging.
{len(by_mod)} modules currently exist. Some are over-fragmented and should
be merged. Your job: find module pairs that are clearly the same business
domain and propose merges.

{glossary_block}

================================================================================
CURRENT MODULES ({len(by_mod)} total)
================================================================================

{modules_block}

================================================================================
LOOK FOR THESE MERGE PATTERNS
================================================================================

1. SAME DOMAIN, DIFFERENT VERBS — sub-functions split into their own modules.
   Example: SO (sales order) + SOS (sales order shipping) + SORP (sales order
   reporting) → all should be SO. Look at the customer glossary first to
   decide what the "real" parent domain is.

2. FUNCTION-NAMED MODULE WITH NO REAL DOMAIN — a tag derived from a filename
   pattern (CRJSON = "Create JSON", APPRN = "Approval Notification", etc.)
   that's actually a function within another module, not a domain of its
   own. Fold it into the domain whose data it serves.
     - CRJSON files that build e-invoice JSON → fold into EINV
     - JSON creation for E-Way Bill → fold into EWB
     - Generic approval-notification helpers → fold into SHARED

3. TWO MODULES WITH NEAR-IDENTICAL DESCRIPTIONS — pure duplicates.

4. TINY MODULE (1-2 files) WHOSE FILES OBVIOUSLY BELONG SOMEWHERE ELSE —
   when there's a much larger module with the same business focus, merge in.

================================================================================
DO NOT MERGE
================================================================================

  - Different business domains that happen to share a substring.
      EINV (E-Invoice) ≠ INV (regular invoice) — KEEP SEPARATE.
      AP (Accounts Payable) ≠ APPR (Approval) — KEEP SEPARATE.
  - Modules covering genuinely different functional areas, even if both
    happen to involve "approval" or "notification" etc.
  - SHARED is a legitimate cross-cutting bucket — do NOT merge it into a
    specific module.
  - UNCLASSIFIED can be merged INTO real modules if its files clearly fit,
    but never merge real modules INTO UNCLASSIFIED.

================================================================================
RETURN ONLY VALID JSON
================================================================================

{{
  "merges": [
    {{
      "from_module": "<smaller module being absorbed — exact tag from CURRENT MODULES list>",
      "to_module":   "<larger / more authoritative module — exact tag from CURRENT MODULES list>",
      "reason":      "<1-2 sentences. Cite the customer glossary or file evidence.>"
    }}
  ]
}}

CRITICAL RULES:
  - Both from_module and to_module MUST be exact tags from the CURRENT MODULES
    list above. Do NOT invent new tags.
  - from_module != to_module.
  - Be conservative — only propose merges you are confident about. If unsure,
    leave them separate. Aim for the codebase ending up with 15-25 modules
    total; if it's already in that range, return {{"merges": []}}.
  - Do not chain merges (don't have A→B and B→C in the same response). If
    you spot a chain, collapse it: emit A→C and B→C separately.
  - Up to 20 merges in one response."""


async def propose_merges(
    client: httpx.AsyncClient,
    conn: sqlite3.Connection,
) -> list[dict]:
    """Ask the LLM which modules should be merged. Returns the merge list."""
    rows = fetch_all_tagged_with_module_desc(conn)
    if not rows:
        return []

    modules = get_existing_module_tags(conn)
    customer_glossary = get_customer_glossary(conn)

    by_mod: dict[str, list[dict]] = {}
    for r in rows:
        by_mod.setdefault(r["module_tag"], []).append(r)

    # If we're already at or below the target band, skip the call entirely
    if len(by_mod) <= 20:
        print(f"  Already at {len(by_mod)} modules (≤ 20) — skipping merge sweep.")
        return []

    system = (
        "You are doing the final consolidation pass on QAD customisation "
        "tagging. You look at ALL current modules and propose merges only "
        "where two modules clearly cover the same business domain. You never "
        "merge across distinct domains (EINV ≠ INV, AP ≠ APPR). Both "
        "from_module and to_module must be exact tags from the CURRENT "
        "MODULES list. Return ONLY valid JSON."
    )
    user = build_merge_prompt(by_mod, modules, customer_glossary)
    result = await openai_call(
        client, system, user, max_tokens=3000, model=OPENAI_MODEL_PRO,
    )
    return result.get("merges", []) or []


def apply_merges(
    conn: sqlite3.Connection,
    merges: list[dict],
) -> tuple[int, int]:
    """Apply Pass 4.5 module merges.

    For each {from_module, to_module}: re-tag every file currently in
    from_module → to_module, then cleanup_orphan_modules removes the now-empty
    from_module.

    Returns (modules_merged, files_moved).
    """
    if not merges:
        return 0, 0

    existing = set(get_existing_module_tags(conn).keys())

    # Collapse any accidental chains (A→B, B→C) → both go to C.
    edges: dict[str, str] = {}
    for m in merges:
        if not isinstance(m, dict):
            continue
        src = normalise_module_tag(m.get("from_module", ""))
        dst = normalise_module_tag(m.get("to_module", ""))
        if not src or not dst or src == dst:
            continue
        if src not in existing or dst not in existing:
            print(f"     skip (unknown tag): {src} → {dst}")
            continue
        if src == "SHARED":
            print(f"     skip (SHARED is protected): {src} → {dst}")
            continue
        edges[src] = dst

    def resolve(tag: str, seen: set[str]) -> str:
        if tag in seen or tag not in edges:
            return tag
        seen.add(tag)
        return resolve(edges[tag], seen)

    modules_merged = 0
    files_moved = 0
    for src, _ in list(edges.items()):
        final_dst = resolve(src, set())
        if final_dst == src:
            continue
        # Move all files from src → final_dst
        cur = conn.execute(
            "UPDATE tagged_files SET module_tag = ? WHERE module_tag = ?",
            (final_dst, src),
        )
        moved = cur.rowcount
        files_moved += moved
        if moved > 0:
            modules_merged += 1
            print(f"     {src:<10} → {final_dst:<10}  ({moved} file(s))")

    cleanup_orphan_modules(conn)
    conn.commit()
    return modules_merged, files_moved


async def run_pass4_5(
    client: httpx.AsyncClient,
    conn: sqlite3.Connection,
) -> tuple[int, int]:
    """Top-level entry point. Returns (modules_merged, files_moved)."""
    try:
        merges = await propose_merges(client, conn)
    except Exception as exc:
        print(f"  Pass 4.5 LLM call failed: {exc}. Keeping module list as-is.")
        return 0, 0

    if not merges:
        print("  No merges proposed.")
        return 0, 0

    print(f"  {len(merges)} merge(s) proposed:")
    for m in merges:
        src = normalise_module_tag(m.get("from_module", ""))
        dst = normalise_module_tag(m.get("to_module", ""))
        rsn = (m.get("reason", "") or "").replace("\n", " ")[:120]
        print(f"    {src:<10} → {dst:<10}  {rsn}")

    return apply_merges(conn, merges)
