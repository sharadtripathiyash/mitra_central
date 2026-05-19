"""Pass 2 — Per-file LLM tagging. N calls (one per file).

Each call:
  - Sends the FULL code of one file.
  - Tells the model which module Pass 1 suggested for this file (strong hint).
  - Asks the model to confirm/override the module, plus produce function_tag,
    description, and reasoning.
  - Writes the result into tagged_files via db helpers.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import httpx

from . import config
from .config import (
    FUNCTION_TAGS,
    INTER_CALL_DELAY,
    MAX_CODE_CHARS,
)
from .db import (
    file_sha256,
    get_already_tagged_shas,
    get_customer_glossary,
    get_existing_module_tags,
    upsert_module,
    upsert_tagged_file,
)
from ..llm_models import MODEL_PASS_2_TAGGING
from .llm import normalise_module_tag, openai_call
from .pass_a_glossary import format_for_prompt as format_customer_glossary
from .standard_glossary import format_for_prompt as format_standard_glossary


def build_per_file_prompt(
    rel_path: str,
    code: str,
    suggested_module: str,
    existing_modules: dict[str, str],
    customer_glossary: dict[str, dict],
) -> str:
    fn_enum = ", ".join(FUNCTION_TAGS)

    tag_lines = "\n".join(
        f"  - {tag}: {desc}" for tag, desc in sorted(existing_modules.items())
    )
    if not tag_lines:
        tag_lines = "  (none yet)"

    allowed_tags = sorted(existing_modules.keys())
    allowed_csv = ", ".join(allowed_tags) if allowed_tags else "(none yet)"

    suggestion_block = (
        f"  Pass 1 (taxonomy refinement) suggested this file belongs to module "
        f"`{suggested_module}` based on filename, prefix, glossary, and "
        f"include-graph analysis. Confirm or override after reading the code."
        if suggested_module
        else "  No prior suggestion — decide based on the code."
    )

    glossary_block = format_customer_glossary(customer_glossary)
    standard_block = format_standard_glossary()

    return f"""Classify this QAD Progress 4GL source file.

  module_tag    — the BUSINESS SYSTEM the file belongs to
  function_tag  — what THIS file does within its module

================================================================================
PRIOR SUGGESTION (from Pass 1)
================================================================================
{suggestion_block}

================================================================================
CUSTOMER-DERIVED GLOSSARY (Pass A — derived from this customer's code)
================================================================================
{glossary_block}

================================================================================
STANDARD QAD GLOSSARY (universal)
================================================================================
{standard_block}

================================================================================
FULL CODE
================================================================================

FILE PATH: {rel_path}

SOURCE CODE:
{code[:MAX_CODE_CHARS]}

================================================================================
EXISTING MODULES IN THIS CODEBASE (with descriptions)
================================================================================
{tag_lines}

================================================================================
ALLOWED module_tag VALUES — YOU MUST PICK FROM THIS LIST
================================================================================
{allowed_csv}

  The taxonomy was set by Pass 1 (gpt-4o, global view). Pass 2 (per-file) is
  meant to CONFIRM or ADJUST the placement of files into THAT taxonomy — not
  to invent new modules from filename patterns.

  Inventing a new module from a single file is the #1 cause of module sprawl
  (CRJSON, OPPMT, BMC etc. — function names masquerading as domains). DON'T.

  ESCAPE HATCH — only when ALL of these are true:
    • The Pass 1 suggestion is UNCLASSIFIED (or empty).
    • NO existing module in the ALLOWED list genuinely fits this file.
    • The file clearly represents a real business domain not yet captured.
  Then set "module_tag" to "NEW:<TAG>" (e.g. "NEW:RTDC") and justify it in
  `reasoning`. The system will treat NEW: as your one allowed invention.

================================================================================
DECISION RULES
================================================================================

1. Read the FULL code above.
2. Does the code clearly support the Pass 1 suggested module? If yes → use it.
3. If you disagree with Pass 1's suggestion, override it WITH AN EXISTING tag
   from the ALLOWED list — but explain why in `reasoning`. (Pass 1 only saw
   filenames; you see code.)
4. NEVER use a function name (approval, notification, report) as a module_tag.
5. module_tag is plain UPPERCASE letters/digits only — NO slashes, NO spaces.
6. module_tag MUST be either an exact value from the ALLOWED list, OR start
   with "NEW:" (only as per the escape hatch above).

================================================================================
RETURN ONLY VALID JSON:
================================================================================

{{
  "reasoning":    "<2-3 sentences. What THIS code does, then whether you agreed with the Pass 1 suggestion or overrode it (and why). If you used NEW:, justify why no existing module fits.>",
  "module_tag":   "<one of the ALLOWED tags above, OR 'NEW:<TAG>' as per escape hatch>",
  "module_desc":  "<1-sentence description of the BUSINESS SYSTEM. Copy verbatim from EXISTING MODULES list if reusing>",
  "function_tag": "<exactly one of: {fn_enum}>",
  "description":  "<1 plain-English sentence describing what THIS specific file does>"
}}"""


async def tag_one_file(
    client: httpx.AsyncClient,
    rel_path: str,
    code: str,
    suggested_module: str,
    existing_modules: dict[str, str],
    customer_glossary: dict[str, dict],
) -> dict:
    system = (
        "You classify QAD Progress 4GL files into business-domain modules. "
        "Pass 1 has already proposed a module for this file based on filename, "
        "prefix, glossary, and include-graph analysis. You read the FULL code "
        "and either confirm or override. Use the customer-derived glossary as "
        "the most authoritative source for what each module means. module_tag "
        "is plain UPPERCASE letters/digits, never contains slashes. Output a "
        "JSON object with a `reasoning` field first. Return ONLY valid JSON."
    )
    user = build_per_file_prompt(
        rel_path, code, suggested_module, existing_modules, customer_glossary
    )
    return await openai_call(client, system, user,
                             max_tokens=600, model=MODEL_PASS_2_TAGGING)


async def run_pass2(
    client: httpx.AsyncClient,
    conn: sqlite3.Connection,
    candidates: list[Path],
    file_to_module: dict[str, str],
) -> None:
    """Per-file tagging loop.

    candidates       — Paths to scan (already resolved against INPUT_DIR).
    file_to_module   — Pass 1's suggested module per relative file path.

    The customer glossary is loaded once at start and passed into every call.
    """
    existing_shas = get_already_tagged_shas(conn)
    customer_glossary = get_customer_glossary(conn)

    to_process: list[tuple[Path, str]] = []
    skipped = 0
    for p in candidates:
        try:
            sha = file_sha256(p)
        except Exception:
            continue
        if sha in existing_shas:
            skipped += 1
            continue
        to_process.append((p, sha))

    print(f"  Files to tag: {len(to_process)} ({skipped} already tagged, skipped)")
    if not to_process:
        return

    for idx, (path, sha) in enumerate(to_process, 1):
        rel_path = str(path.relative_to(config.INPUT_DIR)).replace("\\", "/")
        try:
            code = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            print(f"[{idx:>3}/{len(to_process)}] READ-FAIL {rel_path}: {exc}")
            continue

        suggested = file_to_module.get(rel_path, "")
        existing_modules = get_existing_module_tags(conn)

        try:
            res = await tag_one_file(
                client, rel_path, code, suggested,
                existing_modules, customer_glossary,
            )
        except Exception as exc:
            print(f"[{idx:>3}/{len(to_process)}] LLM-FAIL {rel_path}: {exc}")
            await asyncio.sleep(5)
            continue

        raw_mod  = str(res.get("module_tag", "")).strip()
        mod_desc = str(res.get("module_desc", "")).strip()
        fn       = str(res.get("function_tag", "")).strip().lower()
        desc     = str(res.get("description", "")).strip()
        reason   = str(res.get("reasoning", "")).strip()

        if fn not in FUNCTION_TAGS:
            fn = "other"

        # ── Whitelist enforcement + NEW: escape hatch ──────────────────────
        # The LLM is told: pick from existing_modules OR prefix with "NEW:".
        # Anything else (a free-form invented tag) is rejected and falls back
        # to either the Pass 1 suggestion or UNCLASSIFIED.
        is_new = raw_mod.upper().startswith("NEW:")
        candidate = normalise_module_tag(raw_mod[4:] if is_new else raw_mod)
        allowed = set(existing_modules.keys())
        suggested_norm = normalise_module_tag(suggested) if suggested else ""

        if not candidate or len(candidate) > 10:
            print(f"[{idx:>3}/{len(to_process)}] BAD-TAG {rel_path} "
                  f"(module_tag={raw_mod!r})")
            continue

        if is_new:
            # LLM consciously chose to invent — honour it (escape hatch)
            mod = candidate
            marker = "★"   # new module spawned
        elif candidate in allowed:
            mod = candidate
            marker = "✓" if suggested_norm and mod == suggested_norm else (
                     "↻" if suggested_norm else "+")
        else:
            # Free-form invention without NEW: prefix → reject, fall back
            fallback = suggested_norm if suggested_norm in allowed else "UNCLASSIFIED"
            print(f"[{idx:>3}/{len(to_process)}] ⨯ BLOCKED-INVENT {rel_path} "
                  f"(tried {candidate!r}; not in whitelist) → {fallback}")
            mod = fallback
            marker = "⨯"
            # The LLM's mod_desc described the invented tag — don't overwrite
            # the real fallback module's description with it. Blank it so the
            # upsert is a no-op for the desc column.
            mod_desc = ""
            reason = f"[Pass 2 invent-block: tried {candidate}] " + reason

        upsert_module(conn, mod, mod_desc)
        upsert_tagged_file(conn, sha, rel_path, mod, fn, desc, reason)
        conn.commit()

        print(f"[{idx:>3}/{len(to_process)}] {marker} {rel_path:50s} → {mod:<8s} / {fn}")

        if idx < len(to_process):
            await asyncio.sleep(INTER_CALL_DELAY)
