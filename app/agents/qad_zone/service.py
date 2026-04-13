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

    await send_status(ws, "Analysing code and generating document structure...")

    system = """You are a QAD ERP technical writer producing corporate-quality documentation.
Always return valid JSON only — no markdown fences, no preamble, no extra text.
CRITICAL: Only include fields you can populate with real data from the code.
Omit any field, array, or sub-object entirely if the code does not provide enough information to fill it.
Never use placeholder text like [NAME], [VALUE], or example/template values — if you do not know it, leave the key out."""

    prompt = f"""Analyse the following QAD Progress 4GL custom program code and produce structured documentation data.

USER REQUEST: {question}

CODE:
{code}

Return ONLY valid JSON matching the structure below. Rules:
- Omit any key whose value you cannot determine from the actual code.
- For arrays: omit the key entirely rather than returning an empty array [].
- For strings: omit the key entirely rather than returning "", "N/A", or placeholder text.
- For objects with a "SHOW" key: set SHOW to false and omit data arrays if not applicable.
- Populate every field you DO include with real, specific data from the code.

{{
  "TITLE_PAGE": {{
    "SYSTEM_NAME": "Short code / abbreviation of the system (e.g. DOA, EINV)",
    "SYSTEM_FULL_NAME": "Full descriptive name of the system",
    "PLATFORM": "QAD ERP | Progress 4GL / OpenEdge",
    "MODULE": "e.g. Inventory Control / Purchasing / Finance",
    "VERSION": "e.g. 9.0 Custom Build — if determinable from code comments",
    "ORIGINAL_AUTHOR": "Name (Date) — from code comments if present",
    "LAST_MODIFIED_BY": "Name (ECO: CODE) — from code comments if present",
    "TOTAL_PROGRAMS": "Count of .p and .i files found",
    "DOCUMENT_DATE": "AUTO"
  }},
  "EXECUTIVE_SUMMARY": {{
    "PARA_1": "One paragraph describing what this system does, what business problem it solves, and what transaction types it handles.",
    "PARA_2": "One paragraph describing how this differs from standard QAD functionality and why a custom solution was needed.",
    "KEY_CAPABILITIES": [
      "Capability derived from code — business language"
    ],
    "COMPARISON_TABLE": {{
      "headers": ["Feature", "Standard QAD", "This Custom System"],
      "rows": [
        ["Feature name from code", "Standard QAD behavior", "Custom system behavior"]
      ]
    }}
  }},
  "ARCHITECTURE": {{
    "INTRO_PARA": "Brief paragraph describing the overall architecture and entry point.",
    "PROGRAM_HIERARCHY_TABLE": {{
      "headers": ["Program", "Type", "Role", "Called By", "Calls"],
      "rows": [
        ["program.p", "Maintenance / Report / Utility", "Description of role", "Caller or Menu", "Called programs"]
      ]
    }},
    "SHARED_VARIABLES_TABLE": {{
      "headers": ["Shared Variable", "Data Type", "Purpose"],
      "rows": [
        ["variable_name", "like xx_field or as integer", "What this variable carries"]
      ]
    }}
  }},
  "DATABASE_TABLES": [
    {{
      "TABLE_NAME": "xx_mstr",
      "TABLE_SUBTITLE": "Header / Master / Detail / Control / History",
      "TABLE_DESCRIPTION": "One sentence describing what this table stores.",
      "TABLE_FIELDS": {{
        "headers": ["Field", "Type / Format", "Description"],
        "rows": [
          ["field_name", "Character / Integer / Decimal / Logical / Date", "Field description"]
        ]
      }},
      "TABLE_UNIQUE_KEY": "domain + field1 + field2",
      "TABLE_NOTE": "Warning or important note if applicable — omit if none",
      "TABLE_INFO": "Informational note if applicable — omit if none"
    }}
  ],
  "PROGRAM_ANALYSIS": [
    {{
      "PROG_NAME": "program.p",
      "PROG_VERSION_INFO": "Created by: Name  Date: DD/MM/YY  Last Modified: Name (ECO) — from code comments",
      "PROG_PURPOSE": "Paragraph describing what this program does.",
      "PROG_CALLED_BY": "program_name.p or Menu",
      "PROG_CALLS": ["sub_program1.p", "sub_program2.p"],
      "PROG_INCLUDE_FILES": ["include1.i", "include2.i"],
      "PROG_SCREEN_LAYOUT": {{
        "FRAME_NAME": "Frame A / Frame B / Frame C",
        "headers": ["Field", "Label", "Editable When"],
        "rows": [
          ["field_name", "Screen Label", "Always / Create mode / condition"]
        ]
      }},
      "PROG_LOGIC_STEPS": [
        "Step 1: description",
        "Step 2: description"
      ],
      "PROG_VALIDATIONS": [
        "Validation rule derived from code"
      ],
      "PROG_TRIGGERS": [
        "ON WRITE OF table_name: description"
      ],
      "PROG_SPECIAL_TABLES": {{
        "SHOW": false,
        "headers": ["Mode", "Condition", "Formula"],
        "rows": []
      }},
      "PROG_EXTRA_SECTION": {{
        "SHOW": false,
        "TITLE": "Additional Section Title",
        "CONTENT_TYPE": "table",
        "PARA": "",
        "BULLETS": [],
        "TABLE": {{ "headers": [], "rows": [] }}
      }}
    }}
  ],
  "WORKFLOW": {{
    "INTRO_PARA": "Brief description of the business workflow lifecycle.",
    "PHASES_TABLE": {{
      "headers": ["Phase", "Action", "Program", "Key Validations", "Table Updates"],
      "rows": [
        ["Phase Name", "Action description", "program.p", "Validation checks", "Tables written"]
      ]
    }},
    "INTERNAL_CALL_FLOW": [
      "User navigates to root_program.p",
      "  root_program.p: enter key fields"
    ],
    "APPROVAL_WORKFLOW": {{
      "SHOW": false,
      "STEPS": [],
      "NOTE": ""
    }},
    "DELETE_RULES_TABLE": {{
      "headers": ["What", "When Allowed", "When Blocked"],
      "rows": [
        ["Table / Record", "Condition when delete is permitted", "Condition when blocked"]
      ]
    }}
  }},
  "SETUP_INSTRUCTIONS": {{
    "PREREQUISITES": [
      "Prerequisite from code analysis"
    ],
    "STEPS": [
      {{
        "STEP_NUMBER": "1",
        "STEP_TITLE": "Deploy Programs",
        "STEP_DESCRIPTION": "Brief description of this step.",
        "STEP_ITEMS": ["Step item derived from code"],
        "STEP_CODE": ["/* code line */"]
      }}
    ],
    "MENU_TABLE": {{
      "SHOW": false,
      "headers": ["Menu Option", "Program", "Description"],
      "rows": []
    }},
    "TEST_STEPS": [
      "Test step derived from code logic"
    ]
  }},
  "QAD_NATIVE_COMPARISON": {{
    "SHOW": false,
    "NATIVE_DESCRIPTION_PARA": "",
    "NATIVE_MODULES": [],
    "NATIVE_SETUP_STEPS": [],
    "DEPLOYMENT_DECISION_TABLE": {{ "headers": [], "rows": [] }}
  }},
  "ERROR_MESSAGES": {{
    "TABLE": {{
      "headers": ["Error Message / Code", "Triggering Condition", "Resolution"],
      "rows": [
        ["Error message text", "What causes this error", "How to fix it"]
      ]
    }}
  }},
  "CUSTOMIZATION_HISTORY": [
    {{
      "ECO_ID": "ECO001",
      "ECO_TITLE": "Brief title of this change",
      "ECO_AUTHOR": "Developer Name",
      "ECO_DATE": "Month Year",
      "ECO_CHANGES": [
        "Change description from code comments"
      ]
    }}
  ],
  "QUICK_REFERENCE": {{
    "TRANSACTION_TYPE_TABLE": {{
      "SHOW": false,
      "headers": ["Trans Type", "Code Value", "Transaction String", "Program Used", "Effect on Inventory"],
      "rows": []
    }},
    "AUTH_GROUP_TABLE": {{
      "SHOW": false,
      "headers": ["Action", "Group Field", "Where Stored"],
      "rows": []
    }},
    "INCLUDE_FILES_TABLE": {{
      "SHOW": false,
      "headers": ["Include File", "Purpose"],
      "rows": []
    }},
    "LOT_SERIAL_TABLE": {{
      "SHOW": false,
      "headers": ["Code", "Meaning", "Used In"],
      "rows": []
    }},
    "CUSTOM_TABLE_1": {{
      "SHOW": false,
      "TITLE": "",
      "headers": [],
      "rows": []
    }}
  }},
  "FLOWCHART": {{
    "SHOW": false,
    "LANES": [],
    "NODES": [],
    "ARROWS": []
  }}
}}

Fill every included field with specific data from the code. Omit keys you cannot fill with real data.
"""

    raw = await openai_chat(system, prompt, max_tokens=16000)

    try:
        parsed = parse_json_response(raw)
    except Exception:
        logger.warning("Failed to parse JSON from documentation LLM")
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