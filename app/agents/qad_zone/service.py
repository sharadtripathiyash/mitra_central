"""QAD-Zone — 3-mode agent for custom QAD code management.

Modes:
1. query        — RAG Q&A over custom programs (full module code stuffing) with chat history
2. documentation — Generate corporate Word docs using the structured template
3. modernisation — Takes current_version + target_version directly from WS payload,
                   runs web research + LLM analysis, generates Word migration plan.

File upload support:
- Users can upload .p, .i, .xml files or .zip archives directly in Query and Docs modes.
- Uploaded code is used as the primary code context (instead of disk modules).
- ZIP archives are automatically extracted; all supported text files inside are included.
"""
from __future__ import annotations

import base64
import io
import logging
import zipfile
from pathlib import Path

from fastapi import WebSocket

from app.core.llm import groq_chat, openai_stream, openai_chat, parse_json_response
from app.core.session import append_turn, load_history, set_context, get_context
from app.core.ws import send_done, send_error, send_frame, send_status, send_token
from app.agents.qad_zone.programs import list_modules, load_module_code, load_all_code_summary
from app.agents.qad_zone.doc_generator import generate_document
from app.agents.qad_zone.modernisation import analyse_modernisation

logger = logging.getLogger(__name__)

AGENT_KEY = "qadzone"

# Supported text-based extensions for uploaded files
_UPLOAD_EXTENSIONS = {".p", ".i", ".xml", ".cls", ".w", ".df", ".txt"}
_MAX_UPLOAD_CHARS = 120_000


def _extract_uploaded_code(files: list[dict]) -> str | None:
    """Decode and extract code from uploaded files payload.

    Each entry in `files` is:
        {"name": "filename.ext", "data": "<base64-encoded content>"}

    Returns concatenated code string, or None if no files provided.
    Handles ZIP archives by extracting all supported text files inside.
    """
    if not files:
        return None

    parts: list[str] = []
    total = 0

    def _add(filename: str, content: str) -> bool:
        nonlocal total
        header = f"\n{'='*60}\n// UPLOADED FILE: {filename}\n{'='*60}\n"
        chunk = header + content
        if total + len(chunk) > _MAX_UPLOAD_CHARS:
            remaining = _MAX_UPLOAD_CHARS - total
            if remaining > 200:
                parts.append(chunk[:remaining] + "\n// ... TRUNCATED ...")
            return False
        parts.append(chunk)
        total += len(chunk)
        return True

    for file_entry in files:
        filename: str = file_entry.get("name", "unknown")
        b64_data: str = file_entry.get("data", "")
        if not b64_data:
            continue

        try:
            raw_bytes = base64.b64decode(b64_data)
        except Exception as exc:
            logger.warning("Failed to decode uploaded file %s: %s", filename, exc)
            continue

        ext = Path(filename).suffix.lower()

        if ext == ".zip":
            # Extract all supported text files from the ZIP
            try:
                with zipfile.ZipFile(io.BytesIO(raw_bytes), "r") as zf:
                    for entry in zf.infolist():
                        if entry.is_dir():
                            continue
                        inner_ext = Path(entry.filename).suffix.lower()
                        if inner_ext not in _UPLOAD_EXTENSIONS:
                            continue
                        try:
                            inner_bytes = zf.read(entry.filename)
                            content = inner_bytes.decode("utf-8", errors="replace")
                            inner_name = f"{filename}/{Path(entry.filename).name}"
                            if not _add(inner_name, content):
                                return "\n".join(parts)
                        except Exception as exc:
                            logger.warning("Failed to read %s from zip %s: %s",
                                           entry.filename, filename, exc)
            except Exception as exc:
                logger.warning("Failed to open uploaded ZIP %s: %s", filename, exc)

        elif ext in _UPLOAD_EXTENSIONS:
            try:
                content = raw_bytes.decode("utf-8", errors="replace")
                if not _add(filename, content):
                    return "\n".join(parts)
            except Exception as exc:
                logger.warning("Failed to decode text file %s: %s", filename, exc)

    return "\n".join(parts) if parts else None


async def _detect_module(question: str, available_modules: list[str]) -> str | None:
    """Use Groq (free/fast) to decide which module folder is relevant."""
    if not available_modules:
        return None
    prompt = (
        f"Available QAD custom code modules: {available_modules}\n\n"
        f"User question: {question}\n\n"
        "Which module is most relevant? Return ONLY the module name as a single word. "
        "If none match, return 'none'."
    )
    raw = await groq_chat(
        "You classify QAD questions to code modules. Return only the module name.",
        prompt, temperature=0, max_tokens=20,
    )
    result = raw.strip().lower().strip('"\'')
    return result if result in available_modules else None


# ── Mode 1: Query ─────────────────────────────────────────────────────────────

async def _handle_query(ws: WebSocket, question: str, session_id: str,
                        uploaded_files: list[dict] | None = None) -> None:
    """Q&A over custom programs — Groq routes to module, GPT-4o streams answer.

    If uploaded_files are provided they are used as the code context;
    otherwise the on-disk module store is used as before.
    """
    uploaded_code = _extract_uploaded_code(uploaded_files or [])

    if uploaded_code:
        await send_status(ws, "Using uploaded code as context...")
        code = uploaded_code
        module = "uploaded"
    else:
        modules = list_modules()
        module = await _detect_module(question, modules)
        await send_status(ws, f"Loading code from module: {module or 'all'}...")
        code = load_module_code(module) if module else load_all_code_summary()

    history = load_history(session_id, AGENT_KEY)
    chat_history = []
    for h in history[-6:]:
        if h.get("mode", "query") != "query":
            continue
        if h.get("q"):
            chat_history.append({"role": "user", "content": h["q"]})
        if h.get("a"):
            chat_history.append({"role": "assistant", "content": h["a"]})

    system = f"""You are a QAD ERP expert who answers questions about custom Progress 4GL code.

CUSTOM CODE:
{code}

RULES:
- Answer based on the provided code. Reference specific program files and logic.
- If the code doesn't contain the answer, say so clearly.
- For code modifications, show specific changes with before/after examples.
- Suggest 2-3 follow-up questions starting with ">>>" on separate lines at the end.
"""

    await send_status(ws, "Analysing code...")
    full_answer: list[str] = []
    async for token in openai_stream(system, question, history=chat_history):
        full_answer.append(token)
        await send_token(ws, token)

    answer_text = "".join(full_answer)
    followups = []
    for line in answer_text.split("\n"):
        if line.strip().startswith(">>>"):
            followups.append(line.strip()[3:].strip())
    if followups:
        await send_frame(ws, "followup", followups)

    append_turn(session_id, AGENT_KEY, {"q": question, "a": answer_text, "mode": "query"})


# ── Mode 2: Documentation ─────────────────────────────────────────────────────

async def _handle_documentation(ws: WebSocket, question: str, session_id: str,
                                uploaded_files: list[dict] | None = None) -> None:
    """Generate structured corporate Word doc from custom code using the template.

    If uploaded_files are provided they are used as the code context;
    otherwise the on-disk module store is used as before.
    """
    uploaded_code = _extract_uploaded_code(uploaded_files or [])

    if uploaded_code:
        await send_status(ws, "Using uploaded code for documentation...")
        code = uploaded_code
        module = "uploaded"
    else:
        modules = list_modules()
        module = await _detect_module(question, modules)
        await send_status(ws, f"Loading code from module: {module or 'all'}...")
        code = load_module_code(module) if module else load_all_code_summary()

    # ── PASS 1: Extract structured facts from code ────────────────────────────
    await send_status(ws, "Pass 1/2 — Reading and extracting code facts...")

    pass1_system = """You are a senior QAD ERP Progress 4GL code analyst.
Extract structured technical facts from the source code provided.
Return ONLY valid JSON — no markdown fences, no preamble, no extra text."""

    pass1_prompt = f"""Read the following QAD Progress 4GL source code carefully and extract every technical fact you can find.

USER REQUEST: {question}

SOURCE CODE:
{code}

Return ONLY valid JSON with this exact structure — populate every field you can find evidence for in the code:

{{
  "system_name": "Short code/abbreviation (e.g. MRN, DOA) from file names or comments",
  "system_full_name": "Full descriptive system name from comments or screen titles",
  "platform": "QAD ERP | Progress 4GL / OpenEdge",
  "module": "Business module (e.g. Inventory Control, Purchasing, Finance)",
  "version": "Version string from comments if present",
  "original_author": "Author name and date from file header comments",
  "last_modified_by": "Last modifier name and ECO code from comments",
  "total_programs": "Total count of .p and .i files in the code",
  "business_purpose": "2-3 sentences: what business problem this system solves",
  "why_custom": "2-3 sentences: why standard QAD is insufficient, what gap this fills",
  "capabilities": ["capability 1", "capability 2", "capability 3"],
  "standard_qad_comparison": [
    {{"feature": "feature name", "standard": "what standard QAD does", "custom": "what this system does differently"}}
  ],
  "architecture_overview": "2-3 sentences on overall architecture and entry point program",
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
    {{"id": "node_id", "type": "oval/box/diamond", "lane": "lane_id", "label": "display label", "color": "dark_blue/light_blue/green/yellow/red"}}
  ],
  "flowchart_arrows": [
    {{"from": "node_id", "to": "node_id", "label": "YES/NO or empty", "color": "blue/green/red"}}
  ]
}}

Extract ONLY what you can find in the code. Omit keys with no evidence."""

    logger.info("PASS1 prompt length: %d chars | code length: %d chars", len(pass1_prompt), len(code))
    raw1 = await openai_chat(pass1_system, pass1_prompt, max_tokens=8000)
    logger.info("PASS1 raw response length: %d chars", len(raw1))

    try:
        facts = parse_json_response(raw1)
        logger.info("PASS1 extracted keys: %s", list(facts.keys()))
    except Exception:
        logger.warning("PASS1 failed to parse — falling back to single-pass")
        facts = {}

    # ── PASS 2: Generate full documentation JSON from extracted facts ─────────
    await send_status(ws, "Pass 2/2 — Building full documentation structure...")

    pass2_system = """You are a QAD ERP technical writer producing corporate-quality documentation.
You are given pre-extracted facts from code analysis. Use ONLY these facts to populate the documentation.
Return ONLY valid JSON — no markdown fences, no preamble, no extra text.
Be verbose and detailed — write full sentences and paragraphs, not short phrases."""

    pass2_prompt = f"""Using the extracted code facts below, produce the complete documentation JSON.

EXTRACTED FACTS:
{raw1}

Rules:
- Use ONLY information present in the facts above.
- Omit any key whose value is not supported by the facts.
- Write full descriptive paragraphs for PARA fields (3-5 sentences minimum each).
- Write complete detailed steps for logic, not just keywords.
- For FLOWCHART: set SHOW to true and populate LANES/NODES/ARROWS if flowchart_lanes and flowchart_nodes exist in facts.
- For QUICK_REFERENCE tables: set SHOW to true only if the facts contain relevant data.

Return ONLY valid JSON with this exact structure:

{{
  "TITLE_PAGE": {{
    "SYSTEM_NAME": "from facts.system_name",
    "SYSTEM_FULL_NAME": "from facts.system_full_name",
    "PLATFORM": "from facts.platform",
    "MODULE": "from facts.module",
    "VERSION": "from facts.version — omit if not in facts",
    "ORIGINAL_AUTHOR": "from facts.original_author — omit if not in facts",
    "LAST_MODIFIED_BY": "from facts.last_modified_by — omit if not in facts",
    "TOTAL_PROGRAMS": "from facts.total_programs",
    "DOCUMENT_DATE": "AUTO"
  }},
  "EXECUTIVE_SUMMARY": {{
    "PARA_1": "Detailed paragraph from facts.business_purpose — expand to 4-5 sentences",
    "PARA_2": "Detailed paragraph from facts.why_custom — expand to 4-5 sentences",
    "KEY_CAPABILITIES": ["from facts.capabilities — each as a full sentence"],
    "COMPARISON_TABLE": {{
      "headers": ["Feature", "Standard QAD", "This Custom System"],
      "rows": [["feature", "standard behavior", "custom behavior"]]
    }}
  }},
  "ARCHITECTURE": {{
    "INTRO_PARA": "Detailed paragraph from facts.architecture_overview — expand to 4-5 sentences",
    "PROGRAM_HIERARCHY_TABLE": {{
      "headers": ["Program", "Type", "Role", "Called By", "Calls"],
      "rows": [["from facts.programs — one row per program"]]
    }},
    "SHARED_VARIABLES_TABLE": {{
      "headers": ["Shared Variable", "Data Type", "Purpose"],
      "rows": [["from facts.shared_variables"]]
    }}
  }},
  "DATABASE_TABLES": [
    {{
      "TABLE_NAME": "from facts.database_tables[n].name",
      "TABLE_SUBTITLE": "from facts.database_tables[n].subtitle",
      "TABLE_DESCRIPTION": "from facts.database_tables[n].description",
      "TABLE_FIELDS": {{
        "headers": ["Field", "Type / Format", "Description"],
        "rows": [["field", "type", "desc"]]
      }},
      "TABLE_UNIQUE_KEY": "from facts.database_tables[n].unique_key",
      "TABLE_NOTE": "from facts.database_tables[n].notes — omit if empty"
    }}
  ],
  "PROGRAM_ANALYSIS": [
    {{
      "PROG_NAME": "from facts.programs[n].name",
      "PROG_VERSION_INFO": "from facts.programs[n].version_comment — omit if not in facts",
      "PROG_PURPOSE": "Detailed paragraph from facts.programs[n].role — expand to 3-4 sentences",
      "PROG_CALLED_BY": "from facts.programs[n].called_by — omit if not in facts",
      "PROG_CALLS": ["from facts.programs[n].calls"],
      "PROG_INCLUDE_FILES": ["from facts.programs[n].include_files"],
      "PROG_SCREEN_LAYOUT": {{
        "FRAME_NAME": "from facts.programs[n].frame_name",
        "headers": ["Field", "Label", "Editable When"],
        "rows": [["field", "label", "condition"]]
      }},
      "PROG_LOGIC_STEPS": ["from facts.programs[n].logic_steps — detailed steps"],
      "PROG_VALIDATIONS": ["from facts.programs[n].validations"],
      "PROG_TRIGGERS": ["from facts.programs[n].triggers"],
      "PROG_SPECIAL_TABLES": {{"SHOW": false, "headers": [], "rows": []}},
      "PROG_EXTRA_SECTION": {{"SHOW": false, "TITLE": "", "CONTENT_TYPE": "para", "PARA": "", "BULLETS": [], "TABLE": {{"headers": [], "rows": []}}}}
    }}
  ],
  "WORKFLOW": {{
    "INTRO_PARA": "Detailed paragraph describing the full business workflow lifecycle — 4-5 sentences",
    "PHASES_TABLE": {{
      "headers": ["Phase", "Action", "Program", "Key Validations", "Table Updates"],
      "rows": [["from facts.workflow_phases"]]
    }},
    "INTERNAL_CALL_FLOW": ["from facts.call_flow — preserve indentation"],
    "APPROVAL_WORKFLOW": {{
      "SHOW": "true if facts.approval_workflow.exists else false",
      "STEPS": ["from facts.approval_workflow.steps"],
      "NOTE": "from facts.approval_workflow.note"
    }},
    "DELETE_RULES_TABLE": {{
      "headers": ["What", "When Allowed", "When Blocked"],
      "rows": [["from facts.delete_rules"]]
    }}
  }},
  "SETUP_INSTRUCTIONS": {{
    "PREREQUISITES": ["from facts.prerequisites"],
    "STEPS": [
      {{
        "STEP_NUMBER": "from facts.setup_steps[n].number",
        "STEP_TITLE": "from facts.setup_steps[n].title",
        "STEP_DESCRIPTION": "from facts.setup_steps[n].description",
        "STEP_ITEMS": ["from facts.setup_steps[n].items"],
        "STEP_CODE": ["from facts.setup_steps[n].code_lines"]
      }}
    ],
    "MENU_TABLE": {{
      "SHOW": "true if facts.menu_items exists and non-empty",
      "headers": ["Menu Option", "Program", "Description"],
      "rows": [["from facts.menu_items"]]
    }},
    "TEST_STEPS": ["from facts.test_steps"]
  }},
  "ERROR_MESSAGES": {{
    "TABLE": {{
      "headers": ["Error Message / Code", "Triggering Condition", "Resolution"],
      "rows": [["from facts.error_messages"]]
    }}
  }},
  "CUSTOMIZATION_HISTORY": [
    {{
      "ECO_ID": "from facts.eco_history[n].id",
      "ECO_TITLE": "from facts.eco_history[n].title",
      "ECO_AUTHOR": "from facts.eco_history[n].author",
      "ECO_DATE": "from facts.eco_history[n].date",
      "ECO_CHANGES": ["from facts.eco_history[n].changes"]
    }}
  ],
  "QUICK_REFERENCE": {{
    "TRANSACTION_TYPE_TABLE": {{
      "SHOW": "true if facts.transaction_types exists and non-empty",
      "headers": ["Trans Type", "Code Value", "Transaction String", "Program Used", "Effect on Inventory"],
      "rows": [["from facts.transaction_types"]]
    }},
    "AUTH_GROUP_TABLE": {{
      "SHOW": "true if facts.auth_groups exists and non-empty",
      "headers": ["Action", "Group Field", "Where Stored"],
      "rows": [["from facts.auth_groups"]]
    }},
    "INCLUDE_FILES_TABLE": {{
      "SHOW": "true if facts.include_files exists and non-empty",
      "headers": ["Include File", "Purpose"],
      "rows": [["from facts.include_files"]]
    }},
    "LOT_SERIAL_TABLE": {{"SHOW": false, "headers": [], "rows": []}},
    "CUSTOM_TABLE_1": {{"SHOW": false, "TITLE": "", "headers": [], "rows": []}}
  }},
  "FLOWCHART": {{
    "SHOW": "true if facts.flowchart_nodes is non-empty",
    "LANES": [
      {{
        "LANE_ID": "from facts.flowchart_lanes — split id:label:color",
        "LANE_LABEL": "label part",
        "LANE_COLOR": "color part"
      }}
    ],
    "NODES": [
      {{
        "ID": "from facts.flowchart_nodes[n].id",
        "TYPE": "from facts.flowchart_nodes[n].type",
        "LANE": "from facts.flowchart_nodes[n].lane",
        "LABEL": "from facts.flowchart_nodes[n].label",
        "COLOR": "from facts.flowchart_nodes[n].color"
      }}
    ],
    "ARROWS": [
      {{
        "FROM": "from facts.flowchart_arrows[n].from",
        "TO": "from facts.flowchart_arrows[n].to",
        "LABEL": "from facts.flowchart_arrows[n].label",
        "COLOR": "from facts.flowchart_arrows[n].color"
      }}
    ]
  }}
}}

Be thorough and verbose. Write complete sentences. Use all available facts."""

    logger.info("PASS2 prompt length: %d chars", len(pass2_prompt))
    raw = await openai_chat(pass2_system, pass2_prompt, max_tokens=16000)
    logger.info("PASS2 raw response length: %d chars", len(raw))

    try:
        parsed = parse_json_response(raw)
        logger.info("PASS2 parsed top-level keys: %s", list(parsed.keys()))
    except Exception:
        logger.warning("Failed to parse JSON from documentation LLM (pass 2)")
        logger.warning("PASS2 raw response (first 2000): %s", raw[:2000])
        # Fallback: use title-based approach with raw content as a section
        title = question.replace("document", "").replace("documentation", "").strip().title() or "QAD Custom Module Documentation"
        doc_url = generate_document(
            title=title,
            sections=[{"heading": "Module Documentation", "content": raw, "level": 1}],
        )
        summary = f"Documentation generated for the requested module."
        await send_token(ws, summary)
        await send_frame(ws, "doc", {"url": doc_url, "title": title})
        append_turn(session_id, AGENT_KEY, {"q": question, "a": summary, "mode": "documentation", "doc_url": doc_url})
        return

    # Extract title from new template structure
    tp = parsed.get("TITLE_PAGE") or {}
    title = tp.get("SYSTEM_FULL_NAME") or tp.get("SYSTEM_NAME") or "QAD Custom Module Documentation"
    module_label = tp.get("SYSTEM_NAME") or (module.upper() if module else "module")

    doc_url = generate_document(
        title=title,
        sections=[{"heading": "structured_data", "metadata": parsed}],
    )

    caps = (parsed.get("EXECUTIVE_SUMMARY") or {}).get("KEY_CAPABILITIES") or []
    summary = f"**{title}**\n\nDocumentation generated for **{module_label}** covering:\n"
    for cap in caps:
        summary += f"- {cap}\n"

    for chunk in [summary[i:i + 30] for i in range(0, len(summary), 30)]:
        await send_token(ws, chunk)

    await send_frame(ws, "doc", {"url": doc_url, "title": title})

    append_turn(session_id, AGENT_KEY, {
        "q": question, "a": summary, "mode": "documentation", "doc_url": doc_url,
    })


# ── Mode 3: Modernisation ─────────────────────────────────────────────────────

async def _handle_modernisation(
    ws: WebSocket,
    session_id: str,
    current_version: str,
    target_version: str,
) -> None:
    """One-shot migration analysis — versions come directly from WS payload."""
    current_version = (current_version or "").strip()
    target_version = (target_version or "").strip()

    if not current_version or not target_version:
        await send_error(ws, "Both current_version and target_version are required.")
        return

    set_context(session_id, AGENT_KEY, {
        "current_version": current_version,
        "target_version": target_version,
        "mode": "modernisation",
    })

    await send_status(ws, f"Starting migration analysis: {current_version} → {target_version}...")
    await send_status(ws, "Loading all custom module code...")
    await send_status(ws, "Searching web for version differences and upgrade guides...")

    try:
        result = await analyse_modernisation(current_version, target_version)
    except Exception as exc:
        logger.exception("Modernisation analysis failed")
        await send_error(ws, f"Analysis failed: {exc}")
        return

    summary = result.get("summary", "Migration analysis complete.")
    for chunk in [summary[i:i + 30] for i in range(0, len(summary), 30)]:
        await send_token(ws, chunk)

    await send_frame(ws, "doc", {
        "url": result["doc_url"],
        "title": f"Migration Plan: {current_version} → {target_version}",
    })

    append_turn(session_id, AGENT_KEY, {
        "q": f"Migration: {current_version} → {target_version}",
        "a": summary,
        "mode": "modernisation",
    })


# ── Main WebSocket Handler ────────────────────────────────────────────────────

async def handle_qadzone_ws(ws: WebSocket, session_id: str, user: dict) -> None:
    """Main WebSocket handler for QAD-Zone (3 modes)."""
    try:
        while True:
            data = await ws.receive_json()
            mode = (data.get("mode") or "query").strip().lower()

            try:
                if mode == "modernisation":
                    current_version = (data.get("current_version") or "").strip()
                    target_version = (data.get("target_version") or "").strip()
                    await _handle_modernisation(ws, session_id, current_version, target_version)

                elif mode == "documentation":
                    question = (data.get("question") or "").strip()
                    uploaded_files = data.get("uploaded_files") or []
                    if not question and not uploaded_files:
                        await send_error(ws, "Question or uploaded files are required for documentation mode.")
                    else:
                        if not question:
                            question = "Generate documentation for the uploaded code"
                        await _handle_documentation(ws, question, session_id, uploaded_files)

                else:
                    question = (data.get("question") or "").strip()
                    uploaded_files = data.get("uploaded_files") or []
                    if not question:
                        await send_error(ws, "Question is required.")
                    else:
                        await _handle_query(ws, question, session_id, uploaded_files)

            except Exception as exc:
                logger.exception("QAD-Zone handler error (mode=%s)", mode)
                await send_error(ws, f"Error: {exc}")

            await send_done(ws)

    except Exception as exc:
        logger.exception("QAD-Zone WS error: %s", exc)
        try:
            await send_error(ws, str(exc))
            await send_done(ws)
        except Exception:
            pass