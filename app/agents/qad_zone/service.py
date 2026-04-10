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
Always return valid JSON only — no markdown fences, no preamble, no extra text."""

    prompt = f"""Analyse the following QAD custom program code and produce structured documentation data.

USER REQUEST: {question}

CODE:
{code}

Return ONLY valid JSON with this exact structure. Populate every field from the actual code.
If a field cannot be determined from the code, use a descriptive placeholder (never leave empty strings for required narrative fields).

{{
  "module_name": "Short module name/code (e.g. DOA, E-INVOICE)",
  "module_title": "Full descriptive title of the module",
  "module_code": "Module code identifier",
  "qad_version": "QAD version this runs on if determinable from code, else [QAD EE VERSION]",
  "company_name": "[COMPANY NAME]",
  "primary_purpose": "One-sentence purpose derived from code analysis",
  "business_domain": "e.g. Financial Controls / Procurement / Compliance / Operations",
  "owning_department": "[DEPARTMENT]",
  "dev_status": "Production",
  "criticality": "HIGH / MEDIUM / LOW — with brief justification based on the code",
  "total_files": "Count of .p, .i, .df, .xml files found",
  "key_capabilities": [
    "Capability 1 — derived from code, in plain business language",
    "Capability 2",
    "Capability 3",
    "Capability 4"
  ],
  "scope_in": "What processes and features this module covers, based on code analysis.",
  "scope_out": "Adjacent processes handled by standard QAD or other customisations.",
  "background": "2-3 paragraphs: why this module was built, the business problem, regulatory or operational driver, and how it fits the ERP landscape.",
  "process_flow": "Numbered steps describing the business process from triggering event to final outcome. Use business language not code language.",
  "user_roles": [
    {{"role": "Role name e.g. Requestor", "responsibilities": "What they do in this module", "domain": "[DOMAIN]", "notes": "Restrictions or conditions"}}
  ],
  "architecture": "1-2 paragraphs: how this customisation is structured — standalone 4GL app, layered extension, persistent procedure library, or batch process. How deployed.",
  "integrations": [
    {{"system": "QAD module or external system", "code": "[CODE]", "type": "Shared Table / Trigger / API / File", "data": "Tables or fields", "direction": "Read / Write / Both"}}
  ],
  "custom_tables": [
    {{"name": "Table name", "df_file": "source.df", "purpose": "What this table stores"}}
  ],
  "key_fields": [
    {{"field": "TABLE.FIELD", "label": "Screen label", "type": "CHAR/INT/DEC/DATE", "key": "PK/FK/—", "desc": "Business meaning"}}
  ],
  "standard_tables": [
    {{"table": "QAD table e.g. po_hdr", "owner": "QAD module", "access": "Read / Write / Both", "purpose": "Why accessed and which fields"}}
  ],
  "source_files": [
    {{"name": "filename.p", "type": ".p", "lines": 450, "purpose": "One-line description of what this file does"}}
  ],
  "key_programs": [
    {{
      "name": "program_filename.p",
      "type": "Maintenance / Inquiry / Report / Batch / Trigger / Persistent Procedure",
      "called_by": "Caller programs or Direct menu launch",
      "calls": "Programs or internal procedures this invokes",
      "tables_write": "Tables where this program writes data",
      "tables_read": "Tables read by this program",
      "includes": ".i files included",
      "logic_flow": "Step-by-step walkthrough of the program's main logic. Reference key variable names and block labels.",
      "code_snippet": "5-15 lines of the most significant logic block from the actual code",
      "error_handling": "ON ERROR blocks, validation failures, user-facing error messages. Note any FOR EACH loops on large tables, NO-LOCK vs SHARE-LOCK strategies."
    }}
  ],
  "business_rules": [
    {{"name": "Rule name", "description": "Plain-English rule — e.g. A record cannot be approved by the same user who created it.", "enforced_in": "Program where enforced", "consequence": "Error shown / Blocked / Audit entry"}}
  ],
  "config_params": [
    {{"param": "Parameter name", "stored_in": "TABLE.FIELD or Config Program", "default": "Default value", "desc": "What changes when this is set"}}
  ],
  "audit_trail": "What is logged, which table stores it, when it is written, and what data is captured. If no audit trail exists, state this clearly.",
  "security_objects": [
    {{"object": "program.p or MENU ITEM", "type": "Program / Menu / Report", "role": "Required role or token", "notes": "Conditional access or restrictions"}}
  ],
  "test_cases": [
    {{"id": "TC-001", "scenario": "Test scenario description", "steps": "Step-by-step actions", "expected": "Expected outcome", "status": "Pending"}}
  ],
  "known_issues": [
    {{"id": "ISS-001", "severity": "High / Med / Low", "description": "Limitation or defect identified during code review", "workaround": "Workaround if any", "status": "Open"}}
  ],
  "glossary_terms": [
    {{"term": "Module-specific term", "definition": "Definition extracted from variable names, screen labels, or code comments"}}
  ],
  "files_scanned": "Count and list of files analysed",
  "lines_analysed": "Total estimated line count across all source files"
}}

Populate every array field with real data extracted from the code. For key_programs, include one entry per significant program (entry points, main logic files). Do not include empty arrays.
"""

    raw = await openai_chat(system, prompt, max_tokens=4096)

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

    # The new doc_generator accepts both flat data dict (new) and sections list (old).
    # Pass the structured parsed dict as sections for compatibility.
    title = parsed.get("module_title") or parsed.get("module_name") or "QAD Custom Module Documentation"
    module_label = parsed.get("module_name", module.upper() if module else "module")

    doc_url = generate_document(
        title=title,
        sections=[{"heading": "structured_data", "metadata": parsed}],
    )

    summary = f"**{title}**\n\nDocumentation generated for **{module_label}** covering:\n"
    for cap in (parsed.get("key_capabilities") or []):
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