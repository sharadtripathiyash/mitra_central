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

# ── Web search helper (DuckDuckGo — same approach as modernisation module) ────
try:
    from duckduckgo_search import DDGS
    _ddg_available = True
except ImportError:
    _ddg_available = False
    logger.warning("duckduckgo-search not installed; QAD replacement web search disabled")


def _web_search(query: str, max_results: int = 5) -> str:
    """Search the web using DuckDuckGo. Returns formatted results string."""
    if not _ddg_available:
        return "Web search not available (install duckduckgo-search)."
    try:
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append(
                    f"• {r.get('title', '')}\n  {r.get('body', '')}\n  Source: {r.get('href', '')}"
                )
        return "\n\n".join(results) if results else "No results found."
    except Exception as exc:
        logger.warning("DuckDuckGo search failed: %s", exc)
        return f"Web search failed: {exc}"

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
  "system_name": "Business-meaningful short code (3-5 letters) derived from the system's FUNCTION — strip any company-specific prefix like 'XX' or 'YY'. Example: programs named 'xxmr*.p' handling requisitions → 'MR'. Programs named 'xxdoa*.p' for document approval → 'DOA'. Derive from what the system DOES, never from the raw file prefix alone.",
  "system_full_name": "Full descriptive business name in plain English from comments, screen titles, or menu labels (e.g. 'Material Requisition Maintenance', 'Document Approval Workflow'). Must clearly state what the system is for.",
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
    {{
      "id": "node_id",
      "type": "oval/box/diamond",
      "lane": "lane_id",
      "label": "Business action phrase describing WHAT IS HAPPENING at this step — e.g. 'Enter Requisition Header', 'Validate Authorization Group', 'Post Material Transaction'. NEVER use a raw program filename as the label. If the program name is useful context, add it in parentheses: 'Enter Requisition Details (xxmr.p)'.",
      "color": "dark_blue/light_blue/green/yellow/red"
    }}
  ],
  "flowchart_arrows": [
    {{"from": "node_id", "to": "node_id", "label": "YES/NO or brief condition (e.g. 'Approved', 'Stock Available') or empty", "color": "blue/green/red"}}
  ]
}}

Extract ONLY what you can find in the code. Omit keys with no evidence."""

    logger.info("PASS1 prompt length: %d chars | code length: %d chars", len(pass1_prompt), len(code))
    raw1 = await openai_chat(pass1_system, pass1_prompt, max_tokens=8000, model="gpt-5")
    logger.info("PASS1 raw response length: %d chars", len(raw1))

    try:
        facts = parse_json_response(raw1)
        logger.info("PASS1 extracted keys: %s", list(facts.keys()))
    except Exception:
        logger.warning("PASS1 failed to parse — falling back to single-pass")
        facts = {}

    # ── WEB RESEARCH: Search for standard QAD native replacements ────────────
    await send_status(ws, "Searching QAD knowledge base for standard native alternatives...")

    _sys_full   = facts.get("system_full_name") or facts.get("system_name") or "QAD custom module"
    _module_area = facts.get("module") or ""
    _biz_purpose = facts.get("business_purpose") or ""

    _searches = [
        f"QAD ERP standard native module \"{_sys_full}\" built-in functionality which version",
        f"QAD ERP {_module_area} standard feature replace custom program native alternative",
        f"QAD ERP {_sys_full} {_module_area} standard functionality introduced version release",
        f"QAD Enterprise Applications {_module_area} {_sys_full} out-of-box feature",
    ]
    _web_parts = []
    for _q in _searches:
        logger.info("QAD replacement search: %s", _q)
        _web_parts.append(f"QUERY: {_q}\n{_web_search(_q, max_results=4)}")
    web_replacement_research = "\n\n---\n\n".join(_web_parts)
    logger.info("Web research complete: %d chars", len(web_replacement_research))

    # ── PASS 2: Generate full documentation JSON from extracted facts ─────────
    await send_status(ws, "Pass 2/2 — Building full documentation structure...")

    pass2_system = """You are a senior QAD ERP technical writer producing a comprehensive corporate Word document.
You are given pre-extracted facts from source code analysis. Transform these facts into rich, detailed documentation.
Return ONLY valid JSON — no markdown fences, no preamble, no extra text.
VERBOSITY RULES (strictly enforced):
- Every PARA / INTRO_PARA field must be AT LEAST 4-6 full sentences.
- Every PROG_PURPOSE must be AT LEAST 3-4 full sentences describing what the program does, why it exists, and how it fits the system.
- Every logic step must be a complete sentence describing exactly what happens.
- Every table row must be fully populated — no empty cells.
- Boolean SHOW fields must be the JSON boolean true or false (not strings).
- FLOWCHART must always be generated using the program flow data available in the facts."""

    pass2_prompt = f"""Transform the extracted QAD code facts below into a complete, verbose documentation JSON.

EXTRACTED FACTS:
{raw1}

WEB RESEARCH — STANDARD QAD NATIVE REPLACEMENT ANALYSIS:
{web_replacement_research}

CRITICAL INSTRUCTIONS:
1. Every paragraph field: write 4-6 full, detailed sentences — never a single short sentence.
2. Every program purpose: explain what the program does, what tables it reads/writes, what the user sees, and its role in the overall system.
3. Every logic step: write as a complete action sentence (e.g. "The program validates that the site code entered exists in the site master table and displays an error if not found.").
4. FLOWCHART: ALWAYS set SHOW to true and generate a complete flowchart by synthesizing facts.programs, facts.workflow_phases, and facts.call_flow. Create one LANE per business role or phase (e.g. User, Authorization, Processing, Database). Create one NODE per program or decision point. Create ARROWS following the call flow. CRITICAL LABEL RULE: every node LABEL must be a plain-English business action phrase describing WHAT IS HAPPENING — e.g. "Enter Requisition Header", "Validate Authorization Group", "Post Transaction to Inventory", "Display Error — Site Not Found". NEVER use a raw program filename as the only label. If you want to reference the program add it in parentheses after the phrase: "Enter Requisition Details (xxmr.p)". A business user who has never seen the source code must fully understand every label. Use dark_blue for main entry program nodes, yellow for decision diamonds, green for save/post/success nodes, red for error/denied nodes.
5. QUICK_REFERENCE: set SHOW to true for any table where facts contain matching data (transaction_types → TRANSACTION_TYPE_TABLE, auth_groups → AUTH_GROUP_TABLE, include_files → INCLUDE_FILES_TABLE).
6. APPROVAL_WORKFLOW: set SHOW to true if facts.approval_workflow.exists is true.
7. All boolean SHOW values must be true or false (JSON booleans, not strings).
8. QAD_STANDARD_REPLACEMENT: always set SHOW to true. Use the WEB RESEARCH section above to populate this accurately. For each major business capability of this system, find the closest standard QAD native module/feature from the research and state which QAD version introduced it. If the research shows no native alternative exists, still include the row with "Not Available" in the feasibility column and explain why in RECOMMENDATION_DETAIL. Be specific — name actual QAD modules (e.g. "QAD Requisition Management", "QAD Procurement", "QAD Financials AP").

Return ONLY valid JSON:

{{
  "TITLE_PAGE": {{
    "SYSTEM_NAME": "Business short code from facts.system_name — must NOT start with 'XX' or 'YY'. If facts.system_name starts with XX/YY strip those letters. If it still looks like a raw filename code, derive a 2-5 letter acronym from the system_full_name instead (e.g. 'Requisition Maintenance' → 'RM', 'Document Approval' → 'DOA'). Current value from facts: {facts.get('system_name', '')}",
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
    "PARA_1": "4-6 sentence paragraph: what this system does, what business problem it solves, what transaction types it handles, who uses it, and what the key outcomes are. Derived from facts.business_purpose.",
    "PARA_2": "4-6 sentence paragraph: why standard QAD is insufficient, what gap this fills, what the custom logic adds, and how it integrates with standard QAD. Derived from facts.why_custom.",
    "KEY_CAPABILITIES": [
      "Each capability as a complete descriptive sentence — from facts.capabilities"
    ],
    "COMPARISON_TABLE": {{
      "headers": ["Feature", "Standard QAD", "This Custom System"],
      "rows": [
        ["one row per key differentiating feature found in facts.standard_qad_comparison"]
      ]
    }}
  }},
  "ARCHITECTURE": {{
    "INTRO_PARA": "4-6 sentence paragraph: how the system is structured, what the entry point program is, how programs call each other, what shared variables are used, and how the system is deployed. From facts.architecture_overview.",
    "PROGRAM_HIERARCHY_TABLE": {{
      "headers": ["Program", "Type", "Role", "Called By", "Calls"],
      "rows": [
        ["one row per program in facts.programs — use exact program names, types, roles, callers, callees"]
      ]
    }},
    "SHARED_VARIABLES_TABLE": {{
      "headers": ["Shared Variable", "Data Type", "Purpose"],
      "rows": [
        ["one row per variable in facts.shared_variables"]
      ]
    }}
  }},
  "DATABASE_TABLES": [
    {{
      "TABLE_NAME": "exact table name from facts.database_tables[n].name",
      "TABLE_SUBTITLE": "from facts.database_tables[n].subtitle",
      "TABLE_DESCRIPTION": "from facts.database_tables[n].description — expand to 2-3 sentences",
      "TABLE_FIELDS": {{
        "headers": ["Field", "Type / Format", "Description"],
        "rows": [
          ["one row per field in facts.database_tables[n].fields — field name, type, full description"]
        ]
      }},
      "TABLE_UNIQUE_KEY": "from facts.database_tables[n].unique_key",
      "TABLE_NOTE": "from facts.database_tables[n].notes — include only if non-empty"
    }}
  ],
  "PROGRAM_ANALYSIS": [
    {{
      "PROG_NAME": "exact filename from facts.programs[n].name",
      "PROG_VERSION_INFO": "from facts.programs[n].version_comment — omit key if absent",
      "PROG_PURPOSE": "3-4 sentence paragraph: what this program does, what tables it reads and writes, what the user interface looks like (if applicable), and its role in the overall system.",
      "PROG_CALLED_BY": "from facts.programs[n].called_by — omit key if absent",
      "PROG_CALLS": ["every program this calls from facts.programs[n].calls"],
      "PROG_INCLUDE_FILES": ["every include file from facts.programs[n].include_files"],
      "PROG_SCREEN_LAYOUT": {{
        "FRAME_NAME": "from facts.programs[n].frame_name — omit whole block if no UI",
        "headers": ["Field", "Label", "Editable When"],
        "rows": [["one row per screen field from facts.programs[n].screen_fields"]]
      }},
      "PROG_LOGIC_STEPS": [
        "Each step as a full sentence describing exactly what the program does at that point — from facts.programs[n].logic_steps"
      ],
      "PROG_VALIDATIONS": [
        "Each validation as a complete sentence — from facts.programs[n].validations"
      ],
      "PROG_TRIGGERS": [
        "Each trigger as a complete sentence — from facts.programs[n].triggers"
      ],
      "PROG_SPECIAL_TABLES": {{"SHOW": false, "headers": [], "rows": []}},
      "PROG_EXTRA_SECTION": {{"SHOW": false, "TITLE": "", "CONTENT_TYPE": "para", "PARA": "", "BULLETS": [], "TABLE": {{"headers": [], "rows": []}}}}
    }}
  ],
  "WORKFLOW": {{
    "INTRO_PARA": "4-6 sentence paragraph describing the complete business workflow from start to finish: who initiates it, what phases it goes through, what approvals are required, what transactions are posted, and what the end state is.",
    "PHASES_TABLE": {{
      "headers": ["Phase", "Action", "Program", "Key Validations", "Table Updates"],
      "rows": [
        ["one row per phase from facts.workflow_phases — all 5 columns fully populated"]
      ]
    }},
    "INTERNAL_CALL_FLOW": [
      "lines from facts.call_flow — preserve exact indentation with spaces to show hierarchy"
    ],
    "APPROVAL_WORKFLOW": {{
      "SHOW": true,
      "STEPS": ["each approval step as a complete sentence from facts.approval_workflow.steps"],
      "NOTE": "from facts.approval_workflow.note — omit key if absent"
    }},
    "DELETE_RULES_TABLE": {{
      "headers": ["What", "When Allowed", "When Blocked"],
      "rows": [["one row per rule from facts.delete_rules"]]
    }}
  }},
  "SETUP_INSTRUCTIONS": {{
    "PREREQUISITES": ["each prerequisite as a complete sentence from facts.prerequisites"],
    "STEPS": [
      {{
        "STEP_NUMBER": "1",
        "STEP_TITLE": "Deploy Programs",
        "STEP_DESCRIPTION": "2-3 sentence description of what this step involves.",
        "STEP_ITEMS": ["step item 1", "step item 2"],
        "STEP_CODE": ["code or command line if applicable"]
      }},
      {{
        "STEP_NUMBER": "2",
        "STEP_TITLE": "Initialise Control Records",
        "STEP_DESCRIPTION": "2-3 sentence description.",
        "STEP_ITEMS": ["step item"],
        "STEP_CODE": ["CREATE table. ASSIGN field = value."]
      }}
    ],
    "MENU_TABLE": {{
      "SHOW": true,
      "headers": ["Menu Option", "Program", "Description"],
      "rows": [["one row per menu item from facts.menu_items — if absent, generate likely menu paths from program names"]]
    }},
    "TEST_STEPS": ["each test step as a complete sentence from facts.test_steps — if absent, generate reasonable UAT steps based on the system's purpose"]
  }},
  "ERROR_MESSAGES": {{
    "TABLE": {{
      "headers": ["Error Message / Code", "Triggering Condition", "Resolution"],
      "rows": [["one row per error from facts.error_messages — all 3 columns populated"]]
    }}
  }},
  "CUSTOMIZATION_HISTORY": [
    {{
      "ECO_ID": "from facts.eco_history[n].id",
      "ECO_TITLE": "from facts.eco_history[n].title",
      "ECO_AUTHOR": "from facts.eco_history[n].author",
      "ECO_DATE": "from facts.eco_history[n].date",
      "ECO_CHANGES": ["each change as a complete sentence"]
    }}
  ],
  "QUICK_REFERENCE": {{
    "TRANSACTION_TYPE_TABLE": {{
      "SHOW": true,
      "headers": ["Trans Type", "Code Value", "Transaction String", "Program Used", "Effect on Inventory"],
      "rows": [["one row per transaction type from facts.transaction_types"]]
    }},
    "AUTH_GROUP_TABLE": {{
      "SHOW": true,
      "headers": ["Action", "Group Field", "Where Stored"],
      "rows": [["one row per auth group from facts.auth_groups — if absent infer from programs that do authorization checks"]]
    }},
    "INCLUDE_FILES_TABLE": {{
      "SHOW": true,
      "headers": ["Include File", "Purpose"],
      "rows": [["one row per include file from facts.include_files"]]
    }},
    "LOT_SERIAL_TABLE": {{"SHOW": false, "headers": [], "rows": []}},
    "CUSTOM_TABLE_1": {{"SHOW": false, "TITLE": "", "headers": [], "rows": []}}
  }},
  "FLOWCHART": {{
    "SHOW": true,
    "LANES": [
      {{
        "LANE_ID": "user",
        "LANE_LABEL": "USER\\nINPUT",
        "LANE_COLOR": "light_blue"
      }},
      {{
        "LANE_ID": "auth",
        "LANE_LABEL": "AUTHORIZATION\\nCHECK",
        "LANE_COLOR": "dark_blue"
      }},
      {{
        "LANE_ID": "processing",
        "LANE_LABEL": "PROCESSING\\nLOGIC",
        "LANE_COLOR": "light_blue"
      }},
      {{
        "LANE_ID": "database",
        "LANE_LABEL": "DATABASE\\nOPERATIONS",
        "LANE_COLOR": "green"
      }}
    ],
    "NODES": [
      {{
        "ID": "start",
        "TYPE": "oval",
        "LANE": "user",
        "LABEL": "START — User Initiates Process",
        "COLOR": "dark_blue"
      }},
      "INSTRUCTIONS (replace these with real nodes — one per major step or decision):",
      "• TYPE: oval = START/END, box = process step, diamond = decision/branch",
      "• LANE: assign to the lane matching the business role (user / auth / processing / database)",
      "• LABEL RULE — CRITICAL: Label must be a plain-English business action phrase describing WHAT IS HAPPENING at this step. Examples: 'Enter Requisition Header', 'Validate Authorization Group', 'Check Stock Availability', 'Post Material Transaction to Inventory', 'Display Error — Insufficient Stock'. NEVER use a raw program filename alone. If program context helps, add it in parentheses: 'Enter Requisition Details (xxmr.p)'. A business user who has never seen the code must understand every label.",
      "• COLOR: dark_blue for main entry programs, light_blue for sub-programs, yellow for decision diamonds, green for success/save/post operations, red for error/denied outcomes"
    ],
    "ARROWS": [
      {{
        "FROM": "node1",
        "TO": "node2",
        "LABEL": "YES or brief condition e.g. 'Approved' / 'Stock OK' / empty string",
        "COLOR": "green"
      }},
      "INSTRUCTIONS (replace with real arrows following facts.call_flow and facts.workflow_phases):",
      "• LABEL: use YES/NO for decisions, a brief condition phrase ('Approved', 'Invalid Site'), or leave empty for simple sequential flow.",
      "• COLOR: green for success/approved paths, red for error/rejected paths, blue for normal flow"
    ]
  }},
  "QAD_STANDARD_REPLACEMENT": {{
    "SHOW": true,
    "INTRO_PARA": "3-5 sentence paragraph: based on web research and facts, explain whether standard QAD ERP provides native functionality that could replace or partially replace this customization. Mention the business capability being compared, what standard QAD offers, and the overall conclusion (full replacement possible / partial replacement / keep custom).",
    "REPLACEMENT_TABLE": {{
      "headers": ["Business Capability", "Custom Implementation (Current)", "Standard QAD Native Module / Feature", "Available Since (QAD Version)", "Replacement Feasibility"],
      "rows": [
        ["One row per major business capability of this system. For each: describe what the custom code does | describe the closest standard QAD module/feature found in web research | the QAD version it was introduced (e.g. 'QAD 2019 SE', 'QAD Cloud EE 2022') | Feasibility: Full / Partial / Not Available"]
      ]
    }},
    "RECOMMENDATION": "Full Replacement Possible | Partial Replacement | Keep Custom — No Native Alternative",
    "RECOMMENDATION_DETAIL": "3-5 sentence paragraph explaining the recommendation: which capabilities can switch to standard, which require custom logic to remain, any data migration considerations, and the suggested approach. Be specific about QAD module names and versions from the web research.",
    "GAPS_IF_REPLACED": [
      "Each gap as a complete sentence: what this custom system does that standard QAD cannot do even after migration — from web research findings"
    ],
    "VERSION_AVAILABILITY_NOTE": "1-2 sentence note on which QAD version(s) first introduced the relevant standard functionality. Reference specific release names/years from the web research (e.g. 'This functionality was introduced in QAD Cloud EE 2021.1 as part of the Procurement module enhancements.'). Omit key if no version data found in research."
  }}
}}

OUTPUT REQUIREMENT: The JSON must be at least 15,000 characters long. Every array must have real entries. Every paragraph must be 4+ sentences."""

    logger.info("PASS2 prompt length: %d chars", len(pass2_prompt))
    raw = await openai_chat(pass2_system, pass2_prompt, max_tokens=16000, model="gpt-5")
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