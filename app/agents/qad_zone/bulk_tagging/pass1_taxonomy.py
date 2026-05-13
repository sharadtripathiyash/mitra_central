"""Pass 1 — Taxonomy + Assignment. Split into THREE sub-passes for reliability:

  Pass 1a — TAXONOMY: propose the module list only. No file assignments.
            Small output → never truncated. Uses customer glossary + standard
            glossary + Pass 0/0.5 families + Pass 0.7 include components.

  Pass 1b — ASSIGNMENT: given the locked-down taxonomy and full file list,
            assign every input file to exactly one module.

  Pass 1c — COVERAGE CHECK (pure Python): assert every file ended up in
            exactly one module. Any missing files are forced into an
            UNCLASSIFIED bucket so Pass 2 can sort them out.

Both LLM calls use gpt-4o (not mini) — global reasoning matters here.
"""
from __future__ import annotations

import httpx

from .config import OPENAI_MODEL_PRO
from .llm import normalise_module_tag, openai_call
from .pass_a_glossary import format_for_prompt as format_customer_glossary
from .standard_glossary import format_for_prompt as format_standard_glossary


# ─────────────────────────────────────────────────────────────────────────────
# PASS 1A — Propose taxonomy
# ─────────────────────────────────────────────────────────────────────────────

def build_taxonomy_prompt(
    families: dict[str, list[str]],
    singletons: list[str],
    merge_log: dict[str, list[str]],
    components: list[list[str]],
    customer_glossary: dict[str, dict],
) -> str:
    fam_lines = []
    for tag, files in families.items():
        merged_from = merge_log.get(tag, [])
        merge_note = f"  [merged from sub-prefixes: {', '.join(merged_from)}]" if merged_from else ""
        sample = ", ".join(files[:4])
        more = "" if len(files) <= 4 else f"  (+ {len(files) - 4} more)"
        fam_lines.append(f"  - {tag} ({len(files)} files): {sample}{more}{merge_note}")
    fam_block = "\n".join(fam_lines) if fam_lines else "  (none)"

    sing_block = "\n".join(f"  - {s}" for s in singletons[:50])
    if len(singletons) > 50:
        sing_block += f"\n  ... and {len(singletons) - 50} more singletons"
    if not singletons:
        sing_block = "  (none)"

    comp_lines = []
    for comp in components[:15]:
        sample = ", ".join(comp[:5])
        more = "" if len(comp) <= 5 else f"  (+ {len(comp) - 5} more)"
        comp_lines.append(f"  - [{len(comp)} files mutually reference each other] {sample}{more}")
    comp_block = "\n".join(comp_lines) if comp_lines else "  (none detected)"
    if len(components) > 15:
        comp_block += f"\n  ... and {len(components) - 15} more components"

    return f"""You are designing the module taxonomy for this customer's QAD customisation.

You have THREE strong signals to work with:

================================================================================
SIGNAL 1 — CUSTOMER-DERIVED GLOSSARY (most authoritative)
================================================================================

Pass A read the customer's actual code and derived what each prefix means
FOR THIS CUSTOMER. Trust this:

{format_customer_glossary(customer_glossary)}

================================================================================
SIGNAL 2 — STANDARD QAD GLOSSARY (universal, applies to all customers)
================================================================================

{format_standard_glossary()}

================================================================================
SIGNAL 3 — PREFIX FAMILIES (from Pass 0 + 0.5 deterministic grouping)
================================================================================

{fam_block}

UNGROUPED SINGLETONS (need to be placed by you):
{sing_block}

================================================================================
SIGNAL 4 — INCLUDE / RUN CROSS-REFERENCE GRAPH (Pass 0.7)
================================================================================

Files in the same "connected component" actually reference each other in
source. Same component = almost certainly same module:

{comp_block}

================================================================================
YOUR TASK — PROPOSE THE TAXONOMY ONLY
================================================================================

Propose 8-25 modules total (target: 15-20 for typical 250-file codebases).
For each module:
  - module_tag:   UPPERCASE 3-8 chars business code. Re-use prefix family
                  tags from Signal 3 when sensible.
  - module_desc:  1-sentence description GROUNDED in the glossaries (Signals
                  1 + 2). If the customer glossary has high-confidence
                  meaning for the prefix, COPY that meaning into the
                  description. NEVER invent business names not supported by
                  evidence.

CRITICAL RULES:
  1. AGGRESSIVELY MERGE sub-families that are clearly one business system.
     Example: DOA, DOAAPPR, DOARULE → ONE module DOA (Pass 0.5 may have
     already merged these; you finish the job). Target is <25 modules for
     a 250-file codebase.
  2. NEVER use action words (APPRN, NOTIF, REPORT) as module_tag.
  3. Include a SHARED module if there are obvious cross-module helpers
     (especially `.i` includes with generic-looking names).
  4. ONLY propose a module here if it will have files. Don't propose empty
     modules.

DO NOT assign files yet. That's Pass 1b's job. Just propose the taxonomy.

Return ONLY valid JSON, this exact shape:

{{
  "modules": [
    {{
      "module_tag":  "<UPPERCASE 3-8 chars>",
      "module_desc": "<1-sentence business description, grounded in glossary>"
    }}
  ]
}}"""


async def pass1a_propose_taxonomy(
    client: httpx.AsyncClient,
    families: dict[str, list[str]],
    singletons: list[str],
    merge_log: dict[str, list[str]],
    components: list[list[str]],
    customer_glossary: dict[str, dict],
) -> list[dict]:
    """Returns [{module_tag, module_desc}, ...] — taxonomy only, no assignments."""
    system = (
        "You design module taxonomies for QAD customisations. You ground every "
        "module description in the provided customer glossary; you never "
        "invent business meanings the glossary doesn't support. You aggressively "
        "merge sub-families. Output ONLY valid JSON."
    )
    user = build_taxonomy_prompt(families, singletons, merge_log, components, customer_glossary)
    result = await openai_call(client, system, user, max_tokens=3000, model=OPENAI_MODEL_PRO)

    cleaned: list[dict] = []
    for m in result.get("modules", []):
        if not isinstance(m, dict):
            continue
        tag  = normalise_module_tag(m.get("module_tag", ""))
        desc = str(m.get("module_desc", "")).strip()
        if not tag or len(tag) > 10:
            continue
        cleaned.append({"module_tag": tag, "module_desc": desc})
    return cleaned


# ─────────────────────────────────────────────────────────────────────────────
# PASS 1B — Assign files
# ─────────────────────────────────────────────────────────────────────────────

def build_assignment_prompt(
    rel_paths: list[str],
    taxonomy: list[dict],
    families: dict[str, list[str]],
    customer_glossary: dict[str, dict],
) -> str:
    tax_block = "\n".join(
        f"  - {m['module_tag']:<10} — {m['module_desc']}"
        for m in taxonomy
    )

    fam_block = "\n".join(
        f"  - {tag} ({len(files)} files)"
        for tag, files in families.items()
    ) if families else "  (none)"

    file_lines = "\n".join(f"  - {fp}" for fp in rel_paths)

    return f"""Assign every file to exactly one module from the LOCKED-DOWN taxonomy below.

================================================================================
LOCKED TAXONOMY (do NOT invent new modules):
================================================================================
{tax_block}

================================================================================
CUSTOMER GLOSSARY (use to match files to modules):
================================================================================

{format_customer_glossary(customer_glossary)}

================================================================================
PREFIX FAMILIES FROM PASS 0 (strong hint for assignment):
================================================================================
{fam_block}

================================================================================
ALL FILES TO ASSIGN ({len(rel_paths)} total):
================================================================================
{file_lines}

================================================================================
RULES
================================================================================

  1. Every file MUST be assigned to exactly ONE module from the LOCKED
     TAXONOMY above. You may NOT invent new modules at this stage.
  2. Use prefix family hints + customer glossary to match files to modules.
  3. For `.i` include files with generic names that don't fit any specific
     module, assign to SHARED (if SHARED is in the taxonomy).
  4. CRITICAL: the total file count in your output MUST equal {len(rel_paths)}.
     Every file in the "ALL FILES TO ASSIGN" list above MUST appear in
     exactly one module's `files` array. Do NOT drop files.

Return ONLY valid JSON:

{{
  "assignments": [
    {{
      "module_tag": "<must be one of the LOCKED TAXONOMY tags>",
      "files":      ["file_path_1", "file_path_2", "..."]
    }}
  ]
}}"""


async def pass1b_assign_files(
    client: httpx.AsyncClient,
    rel_paths: list[str],
    taxonomy: list[dict],
    families: dict[str, list[str]],
    customer_glossary: dict[str, dict],
) -> dict[str, str]:
    """Returns {file_path: module_tag}. Will not contain modules outside taxonomy."""
    system = (
        "You assign QAD source files to modules from a fixed, locked taxonomy. "
        "You may NOT invent new modules. Every input file must appear in "
        "exactly one module's `files` array. Output ONLY valid JSON."
    )
    user = build_assignment_prompt(rel_paths, taxonomy, families, customer_glossary)
    # Big output — assignments for hundreds of files. Use max_tokens generously.
    result = await openai_call(client, system, user, max_tokens=10000, model=OPENAI_MODEL_PRO)

    valid_tags = {m["module_tag"] for m in taxonomy}
    file_to_module: dict[str, str] = {}
    for entry in result.get("assignments", []):
        if not isinstance(entry, dict):
            continue
        tag = normalise_module_tag(entry.get("module_tag", ""))
        if tag not in valid_tags:
            continue
        for fp in entry.get("files", []):
            file_to_module[str(fp)] = tag
    return file_to_module


# ─────────────────────────────────────────────────────────────────────────────
# PASS 1C — Coverage check (pure Python)
# ─────────────────────────────────────────────────────────────────────────────

def pass1c_coverage_check(
    rel_paths: list[str],
    file_to_module: dict[str, str],
) -> list[str]:
    """Return list of files NOT assigned by Pass 1b. Caller decides what to do
    (typically: shove them into an UNCLASSIFIED bucket and let Pass 2 sort)."""
    return [fp for fp in rel_paths if fp not in file_to_module]
