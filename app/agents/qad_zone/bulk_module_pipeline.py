"""Per-module documentation + migration blueprint generator (bulk-upload mode).

After the 8-pass tagging pipeline (``bulk_tagging.orchestrator``) has
segregated the customer's customisation into N modules, this module is
called ONCE PER MODULE to produce:

  • A System Documentation .docx  (same template as per-feature flow)
  • A Migration Blueprint .docx   (same template as per-feature flow)

Implementation strategy
-----------------------
The per-feature ``_handle_documentation`` in ``service.py`` is a single
async function that:
  1. Pass 1 — extract structured facts JSON from concatenated code
  2. Research — Qdrant ``qad_adaptive_features`` lookup (with web fallback)
  3. Pass 2 + Summary in parallel — generate Word doc JSON
  4. Pass 3 — Migration Blueprint JSON (Qdrant ``qad_adaptive_dev`` lookup)

For bulk-upload, we run the same logic but:
  • The code is the concatenation of one module's files (not a user upload).
  • We pass a ``label_hint`` (the module's customer-glossary meaning) into
    Pass 1 so the LLM doesn't have to re-derive the system name from scratch.
  • We do NOT send WS frames from in here — the caller (``bulk_service``)
    decides what progress to emit. We return a dict; that's it.

Reusing the per-feature prompts + helpers keeps the bulk-upload output
visually identical to module-wise output.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.core.llm import chat, parse_json_response
from app.agents.qad_zone.doc_generator import generate_document
from app.agents.qad_zone.blueprint_doc_generator import generate_blueprint_document
from app.agents.qad_zone.llm_models import (
    MODEL_DOC_FACTS,
    MODEL_DOC_GENERATE,
)
# We reuse the per-feature pipeline's internal helpers so prompts stay in
# ONE place. Re-importing avoids duplicating ~600 lines of prompt strings.
from app.agents.qad_zone.service import (
    _research_qad_adaptive,
    _generate_summary,
    _generate_blueprint,
)

logger = logging.getLogger(__name__)


# Cap the concatenated code we feed to per-module Pass 1.
#
# 280K chars ≈ 70K tokens. Plus our prompt template (~5K tokens) we're at
# 75K — well within Claude Opus 4.7's 200K context window. This handles
# merged modules (e.g. DOA absorbing APPR + SCPFIN after Pass 4.5) which
# can easily reach 14+ files of code without losing the tail to
# truncation.
_MAX_MODULE_CODE_CHARS = 280_000


def assemble_module_code(
    module_tag: str,
    module_desc: str,
    files: list[dict],
    input_dir,
) -> str:
    """Concatenate this module's files into a single code string for the LLM.

    Each file is preceded by a header so the LLM can attribute facts to the
    right file. Truncates the WHOLE block at ``_MAX_MODULE_CODE_CHARS`` —
    later files get dropped if the budget runs out (we prioritise the
    earlier files in module-listing order).
    """
    parts: list[str] = []
    parts.append(f"// MODULE: {module_tag} — {module_desc}\n")
    parts.append(f"// FILE COUNT: {len(files)}\n")

    total = sum(len(p) for p in parts)
    truncated = False

    for f in files:
        rel = f["file_path"]
        full = input_dir / rel
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            logger.warning("Bulk module-pipeline: failed to read %s: %s", full, exc)
            continue

        header = f"\n{'=' * 60}\n// MODULE FILE: {rel}\n{'=' * 60}\n"
        block = header + text
        if total + len(block) > _MAX_MODULE_CODE_CHARS:
            remaining = _MAX_MODULE_CODE_CHARS - total
            if remaining > 400:
                parts.append(block[:remaining] + "\n// ... TRUNCATED ...\n")
            truncated = True
            break
        parts.append(block)
        total += len(block)

    if truncated:
        logger.info("Module %s code concatenation truncated at ~%d chars",
                    module_tag, _MAX_MODULE_CODE_CHARS)

    return "".join(parts)


def _build_pass1_prompt(module_tag: str, module_desc: str, code: str) -> tuple[str, str]:
    """Build the Pass 1 (extract facts) system + user prompts.

    Pre-seeds the LLM with the module tag + customer-derived description so
    it doesn't have to re-discover the system name from filenames. This
    eliminates the "XX prefix leakage" failure mode and the
    "system_name = APPRN" hallucination we hit in standalone testing.
    """
    system = """You are a senior QAD ERP Progress 4GL code analyst.
Extract structured technical facts from the source code provided.
Return ONLY valid JSON — no markdown fences, no preamble, no extra text."""

    user = f"""Read the following QAD Progress 4GL source code carefully and extract every technical fact you can find.

MODULE CONTEXT (already derived by upstream tagging — use these as the system_name and system_full_name hints):
  module_tag:  {module_tag}
  module_desc: {module_desc or '(no description derived)'}

USER REQUEST: Generate full module documentation for the {module_tag} module.

SOURCE CODE (all files in module {module_tag}, concatenated):
{code}

Return ONLY valid JSON with this exact structure — populate every field you can find evidence for in the code:

{{
  "system_name": "{module_tag}",
  "system_full_name": "{(module_desc or module_tag)[:120]}",
  "platform": "QAD ERP | Progress 4GL / OpenEdge",
  "module": "Business module area inferred from the code (e.g. Inventory Control, Purchasing, Finance, Cross-module / Workflow)",
  "version": "Version string from comments if present",
  "original_author": "Author name and date from file header comments",
  "last_modified_by": "Last modifier name and ECO code from comments",
  "total_programs": "Total count of .p and .i files in the code",
  "business_purpose": "2-3 sentences: what business problem this MODULE solves",
  "why_custom": "2-3 sentences: why standard QAD is insufficient, what gap this fills",
  "capabilities": ["BUSINESS OUTCOMES this module delivers — NOT implementation steps. Stand back from the files and ask 'what does this module DO at a business level'. Typically 4-10 for a real custom module. Examples: 'Manage returnable / non-returnable delivery challans with multi-level approval', 'Notify approvers of pending requisitions'. NEVER list implementation steps like 'Format JSON', 'Read user mail'."],
  "standard_qad_comparison": [
    {{"feature": "feature name", "standard": "what standard QAD does", "custom": "what this module does differently"}}
  ],
  "architecture_overview": "2-3 sentences on overall architecture and entry point program(s) of this module",
  "programs": [
    {{
      "name": "exact_filename.p",
      "type": "Maintenance / Inquiry / Report / Batch / Include / Trigger",
      "role": "what this program does in one sentence",
      "called_by": "which program or menu calls this",
      "calls": ["list", "of", "programs", "this", "calls"],
      "include_files": ["include1.i", "include2.i"],
      "frame_name": "Frame name if UI program",
      "screen_fields": [
        {{"field": "table.field", "label": "screen label", "editable": "Always / condition"}}
      ],
      "logic_steps": ["Step 1: what happens", "Step 2: what happens"],
      "validations": ["validation rule 1", "validation rule 2"],
      "triggers": ["ON WRITE OF table: what it does"],
      "version_comment": "Created by / modified by line from file header"
    }}
  ],
  "shared_variables": [
    {{"name": "variable_name", "type": "data type", "purpose": "what it carries between programs"}}
  ],
  "database_tables": [
    {{
      "name": "table_name",
      "subtitle": "Master / Detail / Header / Control / History / Audit",
      "description": "one sentence on what this table stores",
      "fields": [
        {{"name": "field_name", "type": "Character/Integer/Decimal/Logical/Date", "desc": "field purpose"}}
      ],
      "unique_key": "domain + field1 + field2",
      "notes": "any important notes about this table"
    }}
  ],
  "workflow_phases": [
    {{"phase": "phase name", "action": "what happens", "program": "program.p", "validations": "checks done", "table_updates": "tables written"}}
  ],
  "call_flow": ["line 1 of indented call flow", "  line 2 indented under parent"],
  "approval_workflow": {{
    "exists": true,
    "steps": ["step 1", "step 2"],
    "note": "any special note about approvals"
  }},
  "delete_rules": [
    {{"what": "what can be deleted", "allowed_when": "condition", "blocked_when": "condition"}}
  ],
  "prerequisites": ["prerequisite 1", "prerequisite 2"],
  "setup_steps": [
    {{
      "number": "1",
      "title": "step title",
      "description": "what to do",
      "items": ["item 1", "item 2"],
      "code_lines": ["code line 1", "code line 2"]
    }}
  ],
  "menu_items": [
    {{"option": "menu label", "program": "program.p", "description": "what it does"}}
  ],
  "test_steps": ["test step 1", "test step 2"],
  "error_messages": [
    {{"message": "exact error text or msg number", "condition": "what triggers it", "resolution": "how to fix"}}
  ],
  "eco_history": [
    {{"id": "ECO001", "title": "change title", "author": "name", "date": "Month Year", "changes": ["change 1", "change 2"]}}
  ],
  "transaction_types": [
    {{"type": "type name", "code": "code value", "string": "transaction string", "program": "program.p", "effect": "inventory effect"}}
  ],
  "auth_groups": [
    {{"action": "action name", "field": "group field name", "stored_in": "table.field"}}
  ],
  "include_files": [
    {{"name": "{{include.i}}", "purpose": "what it provides"}}
  ],
  "flowchart_lanes": ["lane1_id:LANE LABEL:dark_blue", "lane2_id:LANE LABEL:light_blue"],
  "flowchart_nodes": [
    {{
      "id": "node_id",
      "type": "oval/box/diamond",
      "lane": "lane_id",
      "label": "Business action phrase. NEVER use a raw program filename as the only label.",
      "color": "dark_blue/light_blue/green/yellow/red"
    }}
  ],
  "flowchart_arrows": [
    {{"from": "node_id", "to": "node_id", "label": "YES/NO or brief condition", "color": "blue/green/red"}}
  ]
}}

Extract ONLY what you can find in the code. Omit keys with no evidence."""
    return system, user


def _build_pass2_prompt(facts_raw: str, web_research: str, facts: dict) -> tuple[str, str]:
    """Build the Pass 2 (Word doc JSON) prompts.

    Mirrors the per-feature flow's Pass 2 — same template, same critical
    instructions. The only delta vs. ``service._handle_documentation`` is
    that we don't need to inject anything different here; the inputs already
    carry the module context.
    """
    system = """You are a senior QAD ERP technical writer producing a comprehensive corporate Word document.
You are given pre-extracted facts from source code analysis. Transform these facts into rich, detailed documentation.
Return ONLY valid JSON — no markdown fences, no preamble, no extra text.
VERBOSITY RULES (strictly enforced):
- Every PARA / INTRO_PARA field must be AT LEAST 4-6 full sentences.
- Every PROG_PURPOSE must be AT LEAST 3-4 full sentences.
- Every logic step must be a complete sentence describing exactly what happens.
- Every table row must be fully populated — no empty cells.
- Boolean SHOW fields must be the JSON boolean true or false (not strings).
- FLOWCHART must always be generated using the program flow data available in the facts."""

    user = f"""Transform the extracted QAD code facts below into a complete, verbose documentation JSON for THIS MODULE.

EXTRACTED FACTS:
{facts_raw}

QAD ADAPTIVE KNOWLEDGE BASE EVIDENCE (cited chunks from official QAD documentation):
{web_research}

CRITICAL INSTRUCTIONS:
1. Every paragraph field: 4-6 full, detailed sentences.
2. Every program purpose: explain what the program does, what tables it reads/writes, what the user sees, and its role in the module.
3. Every logic step: write as a complete action sentence.
4. FLOWCHART: ALWAYS SHOW=true; synthesise from facts.programs, facts.workflow_phases, facts.call_flow. Lane per business role. Node per program or decision point. Labels are business action phrases — NEVER raw filenames.
5. QUICK_REFERENCE: SHOW=true for any table where facts have matching data.
6. APPROVAL_WORKFLOW: SHOW=true if facts.approval_workflow.exists is true.
7. All boolean SHOW values must be true or false (JSON booleans, not strings).
8. QAD_STANDARD_REPLACEMENT: always SHOW=true. Use ONLY the KB EVIDENCE above.

   Score by CAPABILITY PARITY, not IMPLEMENTATION PARITY:
   • The question is "can the BUSINESS OUTCOME be achieved with standard QAD?", NOT "does QAD reproduce every implementation detail?"
   • Thin wrapper around standard QAD programs = FULL replaceability — capability already lives in standard QAD.
   • "Partial" only when business RULES (calculations, validations, routing logic) need custom development.
   • "Not Available" only when no native QAD module covers the underlying business outcome.

   "Available Since" — only use a version if a chunk explicitly mentions one; otherwise default to "QAD Adaptive 2025".

   RECOMMENDATION:
   - "Full Replacement Possible" — every row Full, or rows Full/Partial with only formatting/UI differences.
   - "Partial Replacement" — at least one row has genuine business-rule gaps.
   - "Keep Custom — No Native Alternative" — at least one row is Not Available AND critical.

9. ROW-SHAPE RULE: REPLACEMENT_TABLE rows = CORE BUSINESS OUTCOMES, not facts.capabilities entries.
   Group facts.capabilities into 1-N outcomes; produce ONE row per outcome. NEVER produce a row for an implementation step.

Return ONLY valid JSON:

{{
  "TITLE_PAGE": {{
    "SYSTEM_NAME": "{facts.get('system_name', '')}",
    "SYSTEM_FULL_NAME": "{facts.get('system_full_name', '')}",
    "PLATFORM": "{facts.get('platform', 'QAD ERP | Progress 4GL / OpenEdge')}",
    "MODULE": "{facts.get('module', '')}",
    "VERSION": "include only if in facts",
    "ORIGINAL_AUTHOR": "include only if in facts",
    "LAST_MODIFIED_BY": "include only if in facts",
    "TOTAL_PROGRAMS": "{facts.get('total_programs', '')}",
    "DOCUMENT_DATE": "AUTO"
  }},
  "EXECUTIVE_SUMMARY": {{
    "PARA_1": "4-6 sentence paragraph from facts.business_purpose: what the module does, what business problem, transaction types, users, outcomes.",
    "PARA_2": "4-6 sentence paragraph from facts.why_custom: why standard QAD is insufficient, what gap, what custom logic adds, how it integrates with standard QAD.",
    "KEY_CAPABILITIES": [
      "Each capability as a complete descriptive sentence from facts.capabilities. List ONLY what the module actually does. Do NOT pad with generic outcomes ('improves efficiency')."
    ],
    "COMPARISON_TABLE": {{
      "headers": ["Feature", "Standard QAD", "This Custom Module"],
      "rows": [
        ["one row per key feature in facts.standard_qad_comparison"]
      ]
    }}
  }},
  "ARCHITECTURE": {{
    "INTRO_PARA": "4-6 sentence paragraph from facts.architecture_overview.",
    "PROGRAM_HIERARCHY_TABLE": {{
      "headers": ["Program", "Type", "Role", "Called By", "Calls"],
      "rows": [["one row per program"]]
    }},
    "SHARED_VARIABLES_TABLE": {{
      "headers": ["Shared Variable", "Data Type", "Purpose"],
      "rows": [["one row per shared variable"]]
    }}
  }},
  "DATABASE_TABLES": [
    {{
      "TABLE_NAME": "exact table name",
      "TABLE_SUBTITLE": "Master / Detail / Header / Control / History / Audit",
      "TABLE_DESCRIPTION": "2-3 sentence description",
      "TABLE_FIELDS": {{
        "headers": ["Field", "Type / Format", "Description"],
        "rows": [["one row per field"]]
      }},
      "TABLE_UNIQUE_KEY": "from facts",
      "TABLE_NOTE": "from facts.notes — omit if empty"
    }}
  ],
  "PROGRAM_ANALYSIS": [
    {{
      "PROG_NAME": "exact filename",
      "PROG_VERSION_INFO": "from facts — omit key if absent",
      "PROG_PURPOSE": "3-4 sentence paragraph",
      "PROG_CALLED_BY": "from facts — omit if absent",
      "PROG_CALLS": ["from facts"],
      "PROG_INCLUDE_FILES": ["from facts"],
      "PROG_SCREEN_LAYOUT": {{
        "FRAME_NAME": "omit whole block if no UI",
        "headers": ["Field", "Label", "Editable When"],
        "rows": [["one row per screen field"]]
      }},
      "PROG_LOGIC_STEPS": ["full sentences"],
      "PROG_VALIDATIONS": ["full sentences"],
      "PROG_TRIGGERS": ["full sentences"],
      "PROG_SPECIAL_TABLES": {{"SHOW": false, "headers": [], "rows": []}},
      "PROG_EXTRA_SECTION": {{"SHOW": false, "TITLE": "", "CONTENT_TYPE": "para", "PARA": "", "BULLETS": [], "TABLE": {{"headers": [], "rows": []}}}}
    }}
  ],
  "WORKFLOW": {{
    "INTRO_PARA": "4-6 sentence paragraph describing the complete business workflow.",
    "PHASES_TABLE": {{
      "headers": ["Phase", "Action", "Program", "Key Validations", "Table Updates"],
      "rows": [["one row per phase"]]
    }},
    "INTERNAL_CALL_FLOW": ["lines from facts.call_flow with exact indentation"],
    "APPROVAL_WORKFLOW": {{
      "SHOW": true,
      "STEPS": ["each step as complete sentence"],
      "NOTE": "from facts — omit if absent"
    }},
    "DELETE_RULES_TABLE": {{
      "headers": ["What", "When Allowed", "When Blocked"],
      "rows": [["one row per rule"]]
    }}
  }},
  "SETUP_INSTRUCTIONS": {{
    "PREREQUISITES": ["each as complete sentence"],
    "STEPS": [
      {{"STEP_NUMBER": "1", "STEP_TITLE": "Deploy Programs", "STEP_DESCRIPTION": "2-3 sentences", "STEP_ITEMS": ["item 1"], "STEP_CODE": []}}
    ],
    "MENU_TABLE": {{
      "SHOW": true,
      "headers": ["Menu Option", "Program", "Description"],
      "rows": [["one row per menu item"]]
    }},
    "TEST_STEPS": ["each as complete sentence"]
  }},
  "ERROR_MESSAGES": {{
    "TABLE": {{
      "headers": ["Error Message / Code", "Triggering Condition", "Resolution"],
      "rows": [["one row per error"]]
    }}
  }},
  "CUSTOMIZATION_HISTORY": [
    {{"ECO_ID": "from facts", "ECO_TITLE": "from facts", "ECO_AUTHOR": "from facts", "ECO_DATE": "from facts", "ECO_CHANGES": ["each as complete sentence"]}}
  ],
  "QUICK_REFERENCE": {{
    "TRANSACTION_TYPE_TABLE": {{"SHOW": true, "headers": ["Trans Type", "Code Value", "Transaction String", "Program Used", "Effect on Inventory"], "rows": [["one per transaction type"]]}},
    "AUTH_GROUP_TABLE": {{"SHOW": true, "headers": ["Action", "Group Field", "Where Stored"], "rows": [["one per auth group"]]}},
    "INCLUDE_FILES_TABLE": {{"SHOW": true, "headers": ["Include File", "Purpose"], "rows": [["one per include file"]]}},
    "LOT_SERIAL_TABLE": {{"SHOW": false, "headers": [], "rows": []}},
    "CUSTOM_TABLE_1": {{"SHOW": false, "TITLE": "", "headers": [], "rows": []}}
  }},
  "FLOWCHART": {{
    "SHOW": true,
    "LANES": [
      {{"LANE_ID": "user", "LANE_LABEL": "USER\\nINPUT", "LANE_COLOR": "light_blue"}},
      {{"LANE_ID": "auth", "LANE_LABEL": "AUTHORIZATION\\nCHECK", "LANE_COLOR": "dark_blue"}},
      {{"LANE_ID": "processing", "LANE_LABEL": "PROCESSING\\nLOGIC", "LANE_COLOR": "light_blue"}},
      {{"LANE_ID": "database", "LANE_LABEL": "DATABASE\\nOPERATIONS", "LANE_COLOR": "green"}}
    ],
    "NODES": [
      {{"ID": "start", "TYPE": "oval", "LANE": "user", "LABEL": "START — User Initiates Process", "COLOR": "dark_blue"}}
    ],
    "ARROWS": [
      {{"FROM": "node1", "TO": "node2", "LABEL": "", "COLOR": "blue"}}
    ]
  }},
  "QAD_STANDARD_REPLACEMENT": {{
    "SHOW": true,
    "INTRO_PARA": "3-5 sentence paragraph grounded in the KB EVIDENCE.",
    "REPLACEMENT_TABLE": {{
      "headers": ["Business Capability", "Custom Implementation (Current)", "Standard QAD Native Module / Feature", "Available Since (QAD Version)", "Replacement Feasibility"],
      "rows": [["ONE row per CORE BUSINESS OUTCOME — grouped from facts.capabilities. Each row 5 columns fully populated."]]
    }},
    "RECOMMENDATION": "Full Replacement Possible | Partial Replacement | Keep Custom — No Native Alternative",
    "RECOMMENDATION_DETAIL": "3-5 sentence paragraph.",
    "GAPS_IF_REPLACED": ["each gap as complete sentence — derived strictly from KB EVIDENCE"],
    "VERSION_AVAILABILITY_NOTE": "omit key if no version cited in KB"
  }}
}}

OUTPUT REQUIREMENT: The JSON must be at least 10,000 characters long. Every array must have real entries."""
    return system, user


async def generate_doc_and_blueprint_for_module(
    module_tag: str,
    module_desc: str,
    files: list[dict],
    input_dir,
) -> dict[str, Any]:
    """Generate the System Documentation + Migration Blueprint for one module.

    Inputs::

        module_tag   "RTDC"
        module_desc  "Returnable / Non-Returnable Delivery Challan workflow"
        files        [{file_path, function_tag, description, reasoning, sha256}, ...]
        input_dir    Path to <job>/input/

    Returns::

        {
          "module_tag":     "RTDC",
          "title":          "Returnable / Non-Returnable Delivery Challan workflow",
          "doc_url":        "/static/downloads/<slug>_<hex>.docx" | None,
          "blueprint_url":  "/static/downloads/<slug>_<hex>_blueprint.docx" | None,
          "summary_data":   <executive summary dict> | None,
          "errors":         ["if anything failed, descriptive strings"],
        }

    Never raises — partial failures are captured in ``errors`` so the caller
    can keep generating docs for the remaining modules.
    """
    errors: list[str] = []
    doc_url: str | None = None
    blueprint_url: str | None = None
    summary_data: dict | None = None
    title = module_desc or module_tag

    # ── Step 1: assemble code ───────────────────────────────────────────────
    code = assemble_module_code(module_tag, module_desc, files, input_dir)
    if not code.strip():
        errors.append("No readable code in module")
        return {
            "module_tag":    module_tag,
            "title":         title,
            "doc_url":       None,
            "blueprint_url": None,
            "summary_data":  None,
            "errors":        errors,
        }

    # ── Step 2: Pass 1 — extract facts ──────────────────────────────────────
    # Claude Opus 4.7 with extended thinking — Opus's strongest category is
    # deep code reading + structured extraction. The 12K thinking budget lets
    # the model reason about the code before committing to a 25-field JSON.
    # JSON output reliability comes from Opus's instruction-following + our
    # explicit "Return ONLY valid JSON" prompt + parse_json_response's
    # tolerant fence stripping.
    pass1_system, pass1_user = _build_pass1_prompt(module_tag, module_desc, code)
    try:
        raw1 = await chat(
            MODEL_DOC_FACTS,
            pass1_system, pass1_user,
            max_tokens=8000, temperature=0.2,
        )
        facts = parse_json_response(raw1)
    except Exception as exc:
        logger.exception("Module %s: Pass 1 failed: %s", module_tag, exc)
        errors.append(f"Pass 1 (facts extraction) failed: {exc}")
        return {
            "module_tag":    module_tag,
            "title":         title,
            "doc_url":       None,
            "blueprint_url": None,
            "summary_data":  None,
            "errors":        errors,
        }

    # ── Step 3: Research — Qdrant features KB ───────────────────────────────
    try:
        web_research = await _research_qad_adaptive(facts)
    except Exception as exc:
        logger.warning("Module %s: KB research failed: %s", module_tag, exc)
        web_research = "(KB research unavailable)"
        errors.append(f"KB research failed: {exc}")

    # ── Step 4: Pass 2 (Word doc JSON) + Summary in parallel ────────────────
    pass2_system, pass2_user = _build_pass2_prompt(raw1, web_research, facts)
    pass2_task = asyncio.create_task(
        chat(
            MODEL_DOC_GENERATE,
            pass2_system, pass2_user,
            max_tokens=16000, temperature=0.2,
        )
    )
    summary_task = asyncio.create_task(_generate_summary(raw1, web_research))

    raw2, summary_result = await asyncio.gather(
        pass2_task, summary_task, return_exceptions=True,
    )

    if isinstance(summary_result, dict):
        summary_data = summary_result
    elif isinstance(summary_result, Exception):
        logger.warning("Module %s: Summary failed: %s", module_tag, summary_result)
        errors.append(f"Summary generation failed: {summary_result}")

    if isinstance(raw2, Exception):
        logger.exception("Module %s: Pass 2 failed: %s", module_tag, raw2)
        errors.append(f"Pass 2 (doc JSON) failed: {raw2}")
        return {
            "module_tag":    module_tag,
            "title":         title,
            "doc_url":       None,
            "blueprint_url": None,
            "summary_data":  summary_data,
            "errors":        errors,
        }

    # ── Step 5: parse Pass 2 + render System Documentation .docx ────────────
    try:
        parsed_doc = parse_json_response(raw2)
    except Exception as exc:
        logger.warning("Module %s: Pass 2 JSON unparseable: %s", module_tag, exc)
        errors.append(f"Pass 2 JSON parse failed: {exc}")
        parsed_doc = None

    if parsed_doc:
        # Pull title from the Pass 2 TITLE_PAGE if richer than our module_desc
        tp = parsed_doc.get("TITLE_PAGE") or {}
        title = tp.get("SYSTEM_FULL_NAME") or tp.get("SYSTEM_NAME") or title
        try:
            doc_url = generate_document(
                title=title,
                sections=[{"heading": "structured_data", "metadata": parsed_doc}],
            )
        except Exception as exc:
            logger.exception("Module %s: doc render failed: %s", module_tag, exc)
            errors.append(f"Word render failed: {exc}")

    # ── Step 6: Pass 3 — Migration Blueprint ────────────────────────────────
    if parsed_doc:
        try:
            blueprint_data = await _generate_blueprint(facts, web_research, parsed_doc)
        except Exception as exc:
            logger.exception("Module %s: blueprint generation failed: %s", module_tag, exc)
            blueprint_data = None
            errors.append(f"Blueprint generation failed: {exc}")

        if blueprint_data:
            bp_tp = blueprint_data.setdefault("TITLE_PAGE", {})
            bp_tp.setdefault("SYSTEM_NAME",      tp.get("SYSTEM_NAME") or module_tag)
            bp_tp.setdefault("SYSTEM_FULL_NAME", title)
            bp_tp.setdefault("TARGET_PLATFORM",  "QAD Adaptive 2025")

            try:
                blueprint_url = generate_blueprint_document(blueprint_data, system_full=title)
            except Exception as exc:
                logger.exception("Module %s: blueprint render failed: %s", module_tag, exc)
                errors.append(f"Blueprint render failed: {exc}")

    return {
        "module_tag":    module_tag,
        "title":         title,
        "doc_url":       doc_url,
        "blueprint_url": blueprint_url,
        "summary_data":  summary_data,
        "errors":        errors,
    }
