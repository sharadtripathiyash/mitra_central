"""QAD-Zone — 3-mode agent for custom QAD code management.

Modes:
1. query        — RAG Q&A over custom programs (full module code stuffing) with chat history
2. documentation — Generate corporate Word docs + executive Summary JSON in parallel
3. modernisation — Takes current_version + target_version directly from WS payload,
                   runs web research + LLM analysis, generates Word migration plan.

File upload support:
- Query mode: uploaded code preferred, falls back to on-disk modules if no upload.
- Documentation mode: REQUIRES uploaded files (no disk fallback). Demo modules
  RTDC/DOA/MRN are intercepted on the frontend and never reach this handler.
- ZIP archives are automatically extracted; all supported text files inside are included.

Documentation pipeline (3 logical phases — only 2 sequential LLM passes):
    Pass 1 (extract facts JSON)
            │
            ▼
    QAD Adaptive research:
      Primary  → vector-search Qdrant `qad_adaptive_features` (online help,
                 RN, Warehousing UG, Business Events UG). Multiple parallel
                 queries — broad + per-capability — dedup, rank by score.
      Fallback → OpenAI live web search ONCE if KB best score < 0.5.
            │
            ▼
    asyncio.gather:
      • Pass 2  → Word doc JSON (uses facts + research)
      • Summary → Executive summary JSON (uses facts + research)  ← runs in PARALLEL with Pass 2
            │
            ▼
    Send `summary` frame + `doc` frame
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import zipfile
from pathlib import Path

from fastapi import WebSocket

from app.core.config import settings
from app.core.llm import groq_chat, openai_stream, openai_chat, openai_search, parse_json_response
from app.core.session import append_turn, load_history, set_context, get_context
from app.core.ws import send_done, send_error, send_frame, send_status, send_token
from app.vector.qdrant import search_chunks
from app.agents.qad_zone.programs import list_modules, load_module_code, load_all_code_summary
from app.agents.qad_zone.doc_generator import generate_document
from app.agents.qad_zone.blueprint_doc_generator import generate_blueprint_document
from app.agents.qad_zone.modernisation import analyse_modernisation

logger = logging.getLogger(__name__)

# ── Web search helper (DuckDuckGo — same approach as modernisation module) ────
try:
    from duckduckgo_search import DDGS
    _ddg_available = True
except ImportError:
    _ddg_available = False
    logger.warning("duckduckgo-search not installed; QAD replacement web search disabled")


# Per-search hard timeout (seconds). Web research is best-effort enrichment;
# if DDG is slow, throttling, or silent we MUST not block the doc pipeline.
_WEB_SEARCH_TIMEOUT_SECS = 10


def _web_search(query: str, max_results: int = 5) -> str:
    """Search the web using DuckDuckGo. Returns formatted results string.

    Hard-bounded by `_WEB_SEARCH_TIMEOUT_SECS` so a slow/silent DDG response
    never wedges the calling thread. Errors are swallowed and reported as
    text — callers continue with whatever (possibly empty) result they get.
    """
    if not _ddg_available:
        return "Web search not available (install duckduckgo-search)."
    try:
        results = []
        # `timeout` here is forwarded to the underlying httpx client used by
        # duckduckgo-search; older versions ignored it but newer (>= 6.x) honour it.
        with DDGS(timeout=_WEB_SEARCH_TIMEOUT_SECS) as ddgs:
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

# Hard timeout for the OpenAI web-search FALLBACK call (seconds). The primary
# research path is now the local Qdrant KB; the web search only runs when the
# KB has insufficient coverage for this customisation's module/capabilities.
_RESEARCH_TIMEOUT_SECS = 45

# KB retrieval tuning
_KB_MIN_SCORE        = 0.5   # If best chunk's cosine score < this, fall back to web.
_KB_TOP_K_PER_QUERY  = 6     # Vector-search results per individual query.
_KB_FINAL_TOP_K      = 18    # Cap on total chunks fed to downstream prompts (after dedup).


async def _kb_research_qad_adaptive(facts: dict) -> list[dict]:
    """Vector-search the features collection in parallel — one broad query
    plus one focused query per extracted capability — then dedup across
    queries and return the top ``_KB_FINAL_TOP_K`` chunks by score.

    Multi-query (rather than one big query) gives every capability a fair
    shot at retrieving its own best evidence. Dedup by (source_path,
    section) prevents the same chunk dominating the result set just
    because several capabilities matched it.

    Returns chunks sorted by score descending. Empty list = nothing to use.
    """
    sys_full     = facts.get("system_full_name") or facts.get("system_name") or "QAD custom module"
    module       = facts.get("module") or ""
    capabilities = facts.get("capabilities") or []

    # Broad query covers the system as a whole; focused queries hit per-capability evidence.
    queries = [
        f"What does standard QAD Adaptive ERP natively provide for "
        f"{sys_full}{(' in the ' + module) if module else ''}?"
    ]
    for cap in capabilities[:8]:
        queries.append(f"Does QAD Adaptive natively support: {cap}")

    tasks = [
        search_chunks(q, collection=settings.qdrant_collection_features, top_k=_KB_TOP_K_PER_QUERY)
        for q in queries
    ]
    per_query = await asyncio.gather(*tasks, return_exceptions=True)

    # Dedup by (source_path, section) — keep best score per key.
    best: dict[str, dict] = {}
    for results in per_query:
        if isinstance(results, Exception):
            logger.warning("KB query failed: %s", results)
            continue
        for r in results:
            meta = r.get("metadata") or {}
            key = f"{meta.get('source_path','')}#{meta.get('section','')}"
            if key not in best or r.get("score", 0) > best[key].get("score", 0):
                best[key] = r

    return sorted(best.values(), key=lambda c: -c.get("score", 0))[:_KB_FINAL_TOP_K]


def _format_kb_chunks(chunks: list[dict]) -> str:
    """Format chunks into the citation-style text shape Pass 2 / Summary
    prompts already know how to consume. Mirrors OpenAI search output so
    the downstream prompts stay unchanged.
    """
    if not chunks:
        return "[No relevant content found in QAD Adaptive knowledge base.]"
    parts = []
    for i, c in enumerate(chunks, 1):
        meta       = c.get("metadata") or {}
        source     = meta.get("source_doc", "qad_kb")
        breadcrumb = meta.get("breadcrumb") or meta.get("title", "")
        version    = meta.get("version", "")
        score      = float(c.get("score", 0.0))
        text       = (c.get("text") or "").strip()
        loc = f"{source}"
        if version:
            loc += f" [{version}]"
        if breadcrumb:
            loc += f" — {breadcrumb}"
        parts.append(f"[{i}] Source: {loc}  (relevance={score:.3f})\n{text}")
    return "\n\n".join(parts)


async def _research_qad_adaptive(facts: dict) -> str:
    """Find evidence about what standard QAD Adaptive natively offers vs
    this customisation's capabilities.

    Strategy:
      1. Vector-search the local ``qad_adaptive_features`` collection
         (built from QAD's online help, Release Notes 2025, Warehousing UG,
         Business Events UG). Multiple parallel queries — one broad + one
         per capability — then dedup and rank by score.
      2. If the best chunk scores >= ``_KB_MIN_SCORE`` → use KB results.
         **No internet call.** Citations come from your authoritative QAD
         documentation.
      3. Otherwise (KB had insufficient coverage for this module/system)
         → fall back ONCE to OpenAI live web search. Hard-bounded by
         ``_RESEARCH_TIMEOUT_SECS`` so a slow/silent search never wedges
         the pipeline.

    Same return shape as before — a single string consumed by Pass 2 and
    ``_generate_summary``. Demo modules (MRN/DOA/RTDC) never reach this
    function — they're intercepted on the frontend.
    """
    # ── KB first ───────────────────────────────────────────────────────
    logger.info("QAD Adaptive research: querying features KB (collection=%s)",
                settings.qdrant_collection_features)
    try:
        chunks = await _kb_research_qad_adaptive(facts)
    except Exception as exc:
        logger.warning("KB research raised — will fall back to web: %s", exc)
        chunks = []

    best_score = chunks[0].get("score", 0.0) if chunks else 0.0
    logger.info("KB research: %d chunks retrieved, best score=%.3f",
                len(chunks), best_score)

    if chunks and best_score >= _KB_MIN_SCORE:
        logger.info("Using KB results — no internet call.")
        return _format_kb_chunks(chunks)

    # ── Fallback: OpenAI live web search (one-shot, time-bounded) ──────
    logger.warning(
        "KB had insufficient coverage (best score=%.3f < %.3f) — "
        "falling back to web search.",
        best_score, _KB_MIN_SCORE,
    )

    sys_full     = facts.get("system_full_name") or facts.get("system_name") or "QAD custom module"
    module_area  = facts.get("module") or ""
    capabilities = facts.get("capabilities") or []
    cap_lines = "\n".join(f"  - {c}" for c in capabilities[:10]) if capabilities else "  (none extracted)"

    query = (
        "Research what STANDARD QAD Adaptive ERP natively offers today that could replace or "
        "partially replace the following custom Progress 4GL system. Answer with citations to "
        "real QAD pages (qad.com, community.qad.com, learning.qad.com, partner blogs).\n\n"
        f"System name: {sys_full}\n"
        f"QAD module area: {module_area or '(unspecified)'}\n"
        f"Custom capabilities to compare against:\n{cap_lines}\n\n"
        "For EACH capability above answer:\n"
        "1. Does QAD Adaptive ERP have a native module/feature that covers it? (Full / Partial / None)\n"
        "2. The QAD module name (e.g. 'QAD Procurement', 'QAD Requisition Management', 'QAD Workflow').\n"
        "3. The QAD version it was introduced in or last enhanced (e.g. 'QAD 2019 SE', 'QAD Cloud EE 2022').\n"
        "4. A concise note on functional gaps that would remain if migrated to standard QAD.\n\n"
        "End with a list of the 4-6 most useful source URLs you found."
    )

    try:
        text = await asyncio.wait_for(
            openai_search(query, max_tokens=2000),
            timeout=_RESEARCH_TIMEOUT_SECS,
        )
        logger.info("Web fallback complete: %d chars", len(text))
        return text
    except asyncio.TimeoutError:
        logger.warning("Web fallback timed out (>%ds)", _RESEARCH_TIMEOUT_SECS)
        return f"[KB had no useful matches and web research timed out after {_RESEARCH_TIMEOUT_SECS}s.]"
    except Exception as exc:
        logger.warning("Web fallback failed: %s", exc)
        return f"[KB had no useful matches and web research failed: {exc}]"


async def _generate_summary(raw1: str, web_research: str) -> dict | None:
    """Generate the executive Summary JSON for the front-end Summary tab.

    Returns the parsed dict on success, or None on failure (caller will
    send a {"error": "unavailable"} placeholder so the doc still ships).
    """
    summary_system = (
        "You are a senior QAD ERP modernisation analyst. "
        "Read the extracted facts about a custom QAD Progress 4GL system and the cited QAD Adaptive ERP "
        "knowledge-base evidence, then produce a single executive-summary JSON for business stakeholders. "
        "Return ONLY valid JSON — no markdown fences, no preamble, no extra text."
    )

    summary_prompt = f"""Given the extracted code facts and the QAD Adaptive knowledge-base evidence below,
produce an executive summary JSON for a business audience.

EXTRACTED FACTS:
{raw1}

QAD ADAPTIVE KNOWLEDGE BASE EVIDENCE (cited chunks from official QAD documentation):
{web_research}

Return ONLY valid JSON with this exact structure (all keys required):

{{
  "systemName":      "Business short code (3-5 letters), e.g. RTDC, DOA, MRN. Strip any 'XX'/'YY' filename prefix.",
  "systemFullName":  "Full descriptive name in plain English, e.g. 'Returnable / Non-Returnable Delivery Challan'",
  "executiveSummary": "5-7 sentence paragraph for business stakeholders: what the system does, what business problem it solves, key capabilities, integration points with QAD, audit/workflow features. Do NOT describe code structure, file names, or programming patterns.",
  "tags": ["2-4 functional area tags, e.g. Sales, Inventory, Customer Service, Finance, Manufacturing, Workflow, Cross-module"],
  "keyCapabilities": [
    "ONLY the capabilities the system ACTUALLY HAS — list as many as truly exist, no more. Typically 2-6 for thin wrappers, 6-12 for full custom modules. Each item is one complete business capability statement based on facts.capabilities. DO NOT pad with generic benefits like 'improves efficiency', 'enhances compliance', 'facilitates communication' — those are outcomes, not capabilities. If the system only does 3 things, list 3."
  ],
  "replaceability": "REPLACE_WITH_INTEGER_0_TO_100_COMPUTED_PER_SCORING_GUIDANCE_BELOW",
  "confidence":     "REPLACE_WITH_INTEGER_0_TO_100_COMPUTED_PER_SCORING_GUIDANCE_BELOW",
  "businessImpact":  "REPLACE_WITH_High_OR_Medium_OR_Low",
  "migrationEffort": "REPLACE_WITH_High_OR_Medium_OR_Low",
  "sources": [
    {{"label": "Citation in 'source_doc — breadcrumb' shape from the KB EVIDENCE block (e.g. 'online_help_2025 — Purchasing > Requisition > Requisition Approvals Overview'). If the chunk also carries a URL, include it; otherwise set url to null.", "url": null}}
  ]
}}

SCORING GUIDANCE:

⚠ CRITICAL — START HERE:
The four scoring fields (replaceability / confidence / businessImpact / migrationEffort)
contain PLACEHOLDER STRINGS in the JSON template above. You MUST replace those placeholder
strings with computed values per the rules below. Outputting the literal placeholder
text (e.g. "REPLACE_WITH_...") is a FAILURE — those exist only to remind you to compute.

⚠ FRAME THE SCORING AT SYSTEM LEVEL, NOT FILE LEVEL:
Step back from the specific files (a script, an email recipient, a JSON format).
Ask "what is the OVERALL business system this customisation delivers?" — e.g.
"a delivery-challan management system with multi-level approvals", "a custom approval
workflow engine", "an inter-site requisition system". Score based on whether QAD Adaptive
covers THAT SYSTEM, not on how the individual files happen to be implemented.

- replaceability (integer 0-100): based on the KB EVIDENCE, what percentage of this custom system's BUSINESS OUTCOMES are achievable with standard QAD Adaptive today? Score by CAPABILITY parity, NOT IMPLEMENTATION parity:
    100 = every business outcome is natively achievable in standard QAD; the custom code is essentially a thin wrapper / format / report layer that can be retired.
    80-95 = all major outcomes covered with only minor formatting, delivery, or UI differences.
    50-70 = roughly half covered; meaningful business-rule gaps remain (calculations, routing logic, validations) that require custom development.
    20-40 = only a small portion of outcomes have native equivalents; mostly unique business logic.
    0-15 = no meaningful native coverage.
  IMPORTANT: a custom email-notification wrapper that calls standard QAD's requisition/approval programs and just changes the format (JSON vs HTML) or transport (mailx vs SMTP) is HIGH replaceability (90%+) — NOT Partial. The underlying capability already lives in QAD; only the wrapper goes away.

  ALSO IMPORTANT: replaceability is scored on the UNDERLYING SYSTEM, not on the count of bullets in keyCapabilities. A simple wrapper described in 8 bullets is still 85-95% replaceable; do not average the score down because the bullet list happens to be long. Ignore generic outcome statements ("enhances compliance", "improves efficiency", "facilitates communication") when scoring — they are not capabilities, they are downstream benefits that any ERP achieves.

- confidence (integer 0-100): higher when KB chunks score well, multiple chunks support each capability, and the facts are rich. Lower when chunks are sparse, scores are weak, or facts are thin.

- businessImpact: "High" = critical to daily business operations / regulatory or financial flow; "Medium" = important but contained to a single department; "Low" = nice-to-have or used infrequently.

- migrationEffort:
    "Low" = mostly configuration in standard QAD; thin custom layers (formatting, delivery, simple integrations) can be retired with minimal redevelopment. Use this when replaceability >= 80.
    "Medium" = some custom logic must be re-implemented (e.g. as Business Events handlers, report customisations, or workflow extensions). Use this when replaceability is 50-79.
    "High" = significant rework — custom data models to migrate, complex integrations to rebuild, proprietary business rules without QAD equivalent. Use this when replaceability < 50.
  Examples: a JSON-email wrapper around standard QAD requisitions = Low effort. A custom approval engine with proprietary routing tables = High effort. A custom report layout = Low effort.

OUTPUT REQUIREMENTS (strict):
- 'sources' MUST come from the KB EVIDENCE block above. Pick the 3-5 chunks most relevant to the system's headline capabilities. Use the 'source_doc — breadcrumb' label format and url: null. NEVER invent URLs and never reference web pages that don't appear in the evidence.
- 'tags' = functional business areas, never technical layers (no 'Backend', 'Database', etc.).
- 'replaceability' and 'confidence' MUST be JSON integers (not strings) — replace the placeholder text in the template with a computed integer.
- 'businessImpact' and 'migrationEffort' MUST be exactly one of "High", "Medium", "Low" — replace the placeholder text with one of those three strings.
- DO NOT output the placeholder tokens ("REPLACE_WITH_...") verbatim. Compute the values from SCORING GUIDANCE."""

    try:
        raw = await openai_chat(summary_system, summary_prompt, max_tokens=2000, model="gpt-4o", temperature=0.2)
        parsed = parse_json_response(raw)
        # Coerce numeric fields defensively. If the model echoed the placeholder
        # (REPLACE_WITH_...) string, log it loudly and fall to None so the
        # frontend can show "—" rather than a misleading number.
        for k in ("replaceability", "confidence"):
            v = parsed.get(k)
            if isinstance(v, str) and "REPLACE_WITH" in v.upper():
                logger.warning("Summary returned placeholder for %s; clearing to None", k)
                parsed[k] = None
                continue
            try:
                parsed[k] = max(0, min(100, int(v)))
            except (TypeError, ValueError):
                logger.warning("Summary %s could not be coerced to int (got %r); clearing", k, v)
                parsed[k] = None
        # Normalise impact/effort labels — placeholder echo also clears
        for k in ("businessImpact", "migrationEffort"):
            v = str(parsed.get(k, "")).strip()
            if "REPLACE_WITH" in v.upper():
                logger.warning("Summary returned placeholder for %s; clearing", k)
                parsed[k] = None
                continue
            v_cap = v.capitalize()
            parsed[k] = v_cap if v_cap in ("High", "Medium", "Low") else None
        # Ensure list shapes
        if not isinstance(parsed.get("tags"), list):
            parsed["tags"] = []
        if not isinstance(parsed.get("keyCapabilities"), list):
            parsed["keyCapabilities"] = []
        if not isinstance(parsed.get("sources"), list):
            parsed["sources"] = []
        return parsed
    except Exception as exc:
        logger.exception("Summary generation failed: %s", exc)
        return None


# ── Migration Blueprint (Pass 3) ─────────────────────────────────────────────
#
# After Pass 2 produces the System Documentation, Pass 3 produces a separate
# Migration Blueprint Word doc — the "how to actually build the migration"
# companion to the "what does standard QAD already cover" main doc.
#
# Pulls evidence from the dev collection (qad_adaptive_dev — Implementation
# Guide, Security Admin Guide, Confluence developer pages) so the blueprint
# can reference real configuration steps, TypeScript extension patterns,
# Business Component definitions, Business Event subscriptions, and REST APIs.
#
# Demo modules (MRN/DOA/RTDC) never reach this — they're intercepted on the
# frontend and served pre-built blueprint files from /demo-blueprint/.

_KB_DEV_TOP_K_PER_QUERY = 5
_KB_DEV_FINAL_TOP_K     = 25     # bigger budget than features — blueprint needs more context

_BLUEPRINT_TIMEOUT_SECS = 120    # the Pass 3 LLM call can be heavy; give it room


async def _kb_research_qad_dev(facts: dict, gaps: list, capabilities: list) -> list[dict]:
    """Vector-search the dev collection for migration / extension / API content.

    Multiple parallel queries:
      • One general extension-framework query
      • One per gap (how to bridge this gap in QAD Adaptive)
      • One per capability (how to migrate this capability)
    Dedup by (source_path, section), return top ``_KB_DEV_FINAL_TOP_K`` chunks.
    """
    sys_full = facts.get("system_full_name") or facts.get("system_name") or "QAD custom module"

    queries = [
        "QAD Enterprise Platform extension framework — TypeScript, Java, Business Components, Business Events",
        f"Migrating to standard QAD Adaptive — implementation steps for {sys_full}",
        "QAD Business Events subscription handler implementation",
        "QAD Business Component definition: properties, validations, lifecycle",
        "QAD Adaptive REST API authentication, endpoints, payload examples",
    ]
    for cap in (capabilities or [])[:5]:
        if isinstance(cap, str) and cap.strip():
            queries.append(f"How to implement on standard QAD Adaptive: {cap}")
    for gap in (gaps or [])[:5]:
        if isinstance(gap, str) and gap.strip():
            queries.append(f"Implementation guide to fill this gap: {gap}")

    tasks = [
        search_chunks(q, collection=settings.qdrant_collection_dev,
                      top_k=_KB_DEV_TOP_K_PER_QUERY)
        for q in queries
    ]
    per_query = await asyncio.gather(*tasks, return_exceptions=True)

    best: dict[str, dict] = {}
    for results in per_query:
        if isinstance(results, Exception):
            logger.warning("Dev-KB query failed: %s", results)
            continue
        for r in results:
            meta = r.get("metadata") or {}
            key = f"{meta.get('source_path','')}#{meta.get('section','')}"
            if key not in best or r.get("score", 0) > best[key].get("score", 0):
                best[key] = r

    return sorted(best.values(), key=lambda c: -c.get("score", 0))[:_KB_DEV_FINAL_TOP_K]


async def _generate_blueprint(facts: dict, features_research: str,
                              pass2_result: dict) -> dict | None:
    """Pass 3 — produce a structured Migration Blueprint JSON for the dev-doc.

    Inputs:
      • facts          (Pass 1 output) — what the custom system is + does
      • features_research              — replaceability evidence (already retrieved)
      • pass2_result   (Pass 2 output) — main doc JSON; we pull GAPS_IF_REPLACED
                                          and the capability list from this

    Returns the parsed JSON dict, or None on failure (caller handles graceful
    degradation — system doc + Summary still ship even if blueprint fails).
    """
    qsr           = (pass2_result.get("QAD_STANDARD_REPLACEMENT") or {}) if isinstance(pass2_result, dict) else {}
    gaps          = qsr.get("GAPS_IF_REPLACED") or []
    rep_recommend = qsr.get("RECOMMENDATION") or ""
    capabilities  = facts.get("capabilities") or []

    # ── Pull dev-KB evidence ──────────────────────────────────────────────────
    try:
        dev_chunks = await _kb_research_qad_dev(facts, gaps, capabilities)
    except Exception as exc:
        logger.warning("Dev-KB research failed: %s", exc)
        dev_chunks = []
    dev_evidence = _format_kb_chunks(dev_chunks)
    logger.info("Blueprint dev-KB: %d chunks, best score=%.3f",
                len(dev_chunks),
                dev_chunks[0].get("score", 0.0) if dev_chunks else 0.0)

    # ── Build prompt ──────────────────────────────────────────────────────────
    blueprint_system = (
        "You are a senior QAD ERP modernisation architect. "
        "Given a customer's custom Progress 4GL system, the replaceability analysis "
        "(already done) and authoritative QAD Adaptive 2025 implementation evidence, "
        "produce a DETAILED Migration Blueprint as a structured JSON document. "
        "The blueprint must contain CONCRETE implementation details: actual TypeScript "
        "extension code (not pseudocode), specific Business Component definitions, "
        "Business Event subscriptions with event names and handler logic, REST API "
        "endpoints with example payloads, step-by-step configuration actions, and "
        "citations to the dev-KB evidence. "
        "Return ONLY valid JSON — no markdown fences, no preamble, no extra text."
    )

    facts_summary = json.dumps(facts, indent=2, default=str)[:4000]
    capabilities_list = "\n".join(f"  - {c}" for c in capabilities[:10]) if capabilities else "  (none extracted)"
    gaps_list = "\n".join(f"  - {g}" for g in gaps[:8]) if gaps else "  (none — recommendation: " + rep_recommend + ")"

    blueprint_prompt = f"""Build a detailed Migration Blueprint for migrating this custom QAD Progress 4GL system to standard QAD Adaptive 2025.

CUSTOM SYSTEM FACTS (extracted from source code):
{facts_summary}

CUSTOM CAPABILITIES TO MIGRATE:
{capabilities_list}

GAPS NOT NATIVELY COVERED BY STANDARD QAD (must be re-implemented as extensions / events / integrations):
{gaps_list}

OVERALL REPLACEABILITY RECOMMENDATION (from Pass 2):
{rep_recommend or "(not specified)"}

QAD ADAPTIVE PLATFORM IMPLEMENTATION EVIDENCE
(from QAD Implementation Guide, Security Admin Guide, and Developer Confluence — use these chunks to ground the migration approach):

{dev_evidence}

Return ONLY valid JSON with this exact structure:

{{
  "TITLE_PAGE": {{
    "SYSTEM_NAME":      "{facts.get('system_name', 'CUSTOM')}",
    "SYSTEM_FULL_NAME": "{facts.get('system_full_name', 'Custom Module')}",
    "TARGET_PLATFORM":  "QAD Adaptive 2025",
    "DOCUMENT_TYPE":    "Migration Blueprint — Implementation Plan"
  }},
  "EXECUTIVE_SUMMARY": {{
    "OVERVIEW":          "5-7 sentence paragraph describing the migration: what's being migrated, the high-level approach (mostly configuration / extension-heavy / hybrid), key QAD Adaptive components used, and the expected outcome.",
    "BUSINESS_VALUE":    "3-5 sentence paragraph: why this migration matters — TCO, supportability, cloud-readiness, removing dependency on Progress 4GL talent, ability to roll forward with QAD upgrades, etc.",
    "ESTIMATED_EFFORT":  "Total estimate, e.g. '15-25 person-days'",
    "KEY_DEPENDENCIES":  ["concrete dep 1", "concrete dep 2"]
  }},
  "MIGRATION_STRATEGY": {{
    "INTRO_PARA": "4-6 sentence paragraph describing the overall approach: phased / big-bang, extension framework vs configuration, integration touchpoints, sequencing rationale.",
    "PHASES": [
      {{
        "PHASE_NUMBER": "1",
        "PHASE_NAME":   "Foundation Setup",
        "DURATION":     "1-2 weeks",
        "ACTIVITIES":   ["activity 1", "activity 2"],
        "OUTCOME":      "what's complete at end of phase"
      }}
    ]
  }},
  "CAPABILITY_MIGRATIONS": [
    {{
      "CAPABILITY":          "Name (one of facts.capabilities, paraphrased for clarity if needed)",
      "CURRENT_BEHAVIOUR":   "1-2 sentences: what the custom code does today",
      "TARGET_APPROACH":     "Configuration | TypeScript Extension | Business Event | Hybrid",
      "QAD_MODULE":          "Standard QAD module/feature, e.g. 'QAD Requisition Management'. Cite source_doc + breadcrumb if from KB.",
      "MIGRATION_DETAIL":    "4-6 sentences: how this capability is migrated, what config + custom layers are involved, how the user experience changes (or stays the same).",
      "CONFIGURATION_STEPS": [
        {{
          "STEP_NUMBER": "1",
          "TITLE":       "step title",
          "DESCRIPTION": "2-3 sentences",
          "ACTIONS":     ["action 1", "action 2"],
          "REFERENCE":   "Citation from dev KB if applicable, otherwise omit"
        }}
      ],
      "BUSINESS_COMPONENT": {{
        "SHOW":        false,
        "NAME":        "BC name e.g. RequisitionApprovalTrigger",
        "PURPOSE":     "what this BC does",
        "PROPERTIES":  [
          {{"NAME": "prop1", "TYPE": "string | integer | boolean | date", "REQUIRED": true, "DESCRIPTION": "what it carries"}}
        ],
        "VALIDATIONS": ["validation rule 1"],
        "NOTES":       "1-2 sentences"
      }},
      "TYPESCRIPT_EXTENSION": {{
        "SHOW":    false,
        "PURPOSE": "1-2 sentences why a TS extension is needed",
        "FILES": [
          {{
            "FILENAME": "approval-trigger.ts",
            "PURPOSE":  "what this file does",
            "CODE":     "ACTUAL TypeScript code — full file with imports, decorators, class definition, methods. Use the QAD extensibility imports cited in the dev KB. Concrete and runnable, not pseudocode. ~30-80 lines.",
            "NOTES":    ["note 1", "note 2"]
          }}
        ]
      }},
      "BUSINESS_EVENT_SUBSCRIPTION": {{
        "SHOW":            false,
        "EVENT_NAME":      "official event name from KB if cited (e.g. 'RequisitionStatusChanged'); otherwise mark as TBC",
        "TRIGGER":         "when fired",
        "HANDLER_LOGIC":   "1-2 sentences describing what the handler does",
        "PAYLOAD_FIELDS":  ["field1", "field2"]
      }},
      "API_INTEGRATION": {{
        "SHOW":            false,
        "ENDPOINT":        "HTTP method + path",
        "PURPOSE":         "1-2 sentences",
        "EXAMPLE_PAYLOAD": "JSON payload example as a string",
        "AUTH":            "auth method e.g. OAuth 2.0"
      }},
      "DATA_MIGRATION_NOTES": {{
        "SHOW":             false,
        "TABLES_AFFECTED":  ["xxmrh_hist", "..."],
        "MIGRATION_APPROACH": "How to migrate this data"
      }},
      "EFFORT_ESTIMATE": "X-Y days",
      "DEPENDENCIES":    ["dep 1"],
      "RISKS":           [
        {{"RISK": "risk description", "MITIGATION": "how to mitigate"}}
      ]
    }}
  ],
  "DATA_MIGRATION": {{
    "SHOW":       true,
    "INTRO_PARA": "How custom-system data is migrated to standard QAD tables / structures.",
    "TABLES_TABLE": {{
      "headers": ["Source Table", "Target Module / Table", "Migration Approach", "Notes"],
      "rows":    [["xxmrh_hist", "Standard QAD audit trail", "Extract via SQL → import via REST API", "..."]]
    }}
  }},
  "TESTING_PLAN": {{
    "INTRO_PARA": "How to validate the migration before go-live.",
    "TEST_CASES": [
      {{"SCENARIO": "End-to-end requisition approval", "STEPS": ["step 1", "step 2"], "EXPECTED": "expected outcome"}}
    ]
  }},
  "GO_LIVE_CHECKLIST": [
    "Item 1 — concrete cutover task",
    "Item 2 — verification step"
  ],
  "REFERENCES": [
    {{"label": "section title", "section": "section title", "source_doc": "confluence_QEP250 | implementation_guide_2025 | security_admin_guide_2025"}}
  ]
}}

CRITICAL REQUIREMENTS:
1. Generate a CAPABILITY_MIGRATIONS entry for each capability listed above. Pure-config capabilities have empty TYPESCRIPT_EXTENSION (SHOW: false) and longer CONFIGURATION_STEPS. Capabilities needing custom code have TYPESCRIPT_EXTENSION populated with real, runnable code.

2. TYPESCRIPT_EXTENSION code MUST be syntactically valid TypeScript. Use ACTUAL imports cited in the dev KB (e.g. ``import {{ ... }} from '@qad/...'``). Include decorators, class structure, method bodies. NEVER write `// TODO` or pseudocode.

3. BUSINESS_COMPONENT, BUSINESS_EVENT_SUBSCRIPTION, API_INTEGRATION sections MUST cite specifics from the dev KB. If the KB doesn't cover a specific event name or endpoint, set the value to "TBC — verify with QAD partner; see dev KB section X" and continue.

4. Use evidence from the QAD ADAPTIVE PLATFORM IMPLEMENTATION EVIDENCE block. REFERENCES at the end MUST list the actual source_doc + section pairs you used (no fabrications).

5. Effort estimates: configuration only = 1-3 days/capability; TS extension required = 5-10 days/capability; complex integration = 10-20 days/capability. Sum into EXECUTIVE_SUMMARY.ESTIMATED_EFFORT.

6. NEVER invent QAD module names, event names, or API endpoints not present in the dev KB evidence above. If something isn't covered, mark explicitly "TBC — not in our KB; consult QAD partner."

7. The blueprint output must be at least 6,000 characters of detailed JSON — this is a deep implementation document, not a summary."""

    try:
        raw = await asyncio.wait_for(
            openai_chat(blueprint_system, blueprint_prompt,
                        max_tokens=16000, model="gpt-4o", temperature=0.2),
            timeout=_BLUEPRINT_TIMEOUT_SECS,
        )
    except asyncio.TimeoutError:
        logger.warning("Blueprint Pass 3 timed out (>%ds)", _BLUEPRINT_TIMEOUT_SECS)
        return None
    except Exception as exc:
        logger.exception("Blueprint Pass 3 LLM call failed: %s", exc)
        return None

    try:
        parsed = parse_json_response(raw)
        logger.info("Blueprint parsed top-level keys: %s", list(parsed.keys()))
        return parsed
    except Exception as exc:
        logger.warning("Blueprint Pass 3 returned unparseable JSON: %s", exc)
        logger.debug("Blueprint raw (first 1000): %s", raw[:1000] if isinstance(raw, str) else "")
        return None


async def _handle_documentation(ws: WebSocket, question: str, session_id: str,
                                uploaded_files: list[dict] | None = None) -> None:
    """Generate structured corporate Word doc + executive Summary JSON in parallel.

    Uploaded code is REQUIRED — there is no on-disk fallback for documentation mode.
    Demo modules (RTDC / DOA / MRN) are intercepted by the React frontend and never
    reach this handler.
    """
    # ── Upload guard ─────────────────────────────────────────────────────────
    if not uploaded_files:
        await send_error(ws, "Please upload .p / .i / .xml / .zip files to generate documentation.")
        return

    uploaded_code = _extract_uploaded_code(uploaded_files or [])
    if not uploaded_code:
        await send_error(ws, "Could not read any supported code from the uploaded files. Supported types: .p .i .xml .cls .w .df .txt (or .zip containing them).")
        return

    code = uploaded_code
    module = "uploaded"

    # ── PASS 1: Extract structured facts from code ────────────────────────────
    await send_status(ws, "Extracting code facts…")

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
  "capabilities": ["BUSINESS OUTCOMES this whole system delivers — NOT implementation steps. Stand back from the files and ask 'what does this system DO at a business level'. For a thin wrapper around standard QAD programs, this is typically 1-2 outcomes. For a full custom module, this is typically 4-8 outcomes. EXAMPLES OF GOOD CAPABILITIES: 'Notify approvers of pending requisitions with details and approver list', 'Manage returnable / non-returnable delivery challans with multi-level approval'. EXAMPLES OF BAD CAPABILITIES (these are implementation steps, NEVER list these): 'Format requisition data as JSON', 'Build approver list from rqalttd.i temp-table', 'Send email via OS-COMMAND mailx', 'Read user mail address from usr_mstr'. Implementation details are NOT capabilities."],
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
    raw1 = await openai_chat(pass1_system, pass1_prompt, max_tokens=8000, model="gpt-4o")
    logger.info("PASS1 raw response length: %d chars", len(raw1))

    try:
        facts = parse_json_response(raw1)
        logger.info("PASS1 extracted keys: %s", list(facts.keys()))
    except Exception:
        logger.warning("PASS1 failed to parse — falling back to single-pass")
        facts = {}

    # ── RESEARCH: QAD Adaptive coverage from KB (web fallback) ──────────────
    await send_status(ws, "Researching QAD Adaptive ERP coverage…")
    web_replacement_research = await _research_qad_adaptive(facts)
    logger.info("Adaptive-research complete: %d chars (KB-first; web only if KB sparse)",
                len(web_replacement_research))

    # ── PARALLEL: Pass 2 (Word doc) + Summary (executive JSON) ───────────────
    await send_status(ws, "Building documentation & executive summary in parallel…")

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

QAD ADAPTIVE KNOWLEDGE BASE EVIDENCE (cited chunks from official QAD documentation —
online help, Release Notes 2025, Warehousing UG, Business Events UG):
{web_replacement_research}

CRITICAL INSTRUCTIONS:
1. Every paragraph field: write 4-6 full, detailed sentences — never a single short sentence.
2. Every program purpose: explain what the program does, what tables it reads/writes, what the user sees, and its role in the overall system.
3. Every logic step: write as a complete action sentence (e.g. "The program validates that the site code entered exists in the site master table and displays an error if not found.").
4. FLOWCHART: ALWAYS set SHOW to true and generate a complete flowchart by synthesizing facts.programs, facts.workflow_phases, and facts.call_flow. Create one LANE per business role or phase (e.g. User, Authorization, Processing, Database). Create one NODE per program or decision point. Create ARROWS following the call flow. CRITICAL LABEL RULE: every node LABEL must be a plain-English business action phrase describing WHAT IS HAPPENING — e.g. "Enter Requisition Header", "Validate Authorization Group", "Post Transaction to Inventory", "Display Error — Site Not Found". NEVER use a raw program filename as the only label. If you want to reference the program add it in parentheses after the phrase: "Enter Requisition Details (xxmr.p)". A business user who has never seen the source code must fully understand every label. Use dark_blue for main entry program nodes, yellow for decision diamonds, green for save/post/success nodes, red for error/denied nodes.
5. QUICK_REFERENCE: set SHOW to true for any table where facts contain matching data (transaction_types → TRANSACTION_TYPE_TABLE, auth_groups → AUTH_GROUP_TABLE, include_files → INCLUDE_FILES_TABLE).
6. APPROVAL_WORKFLOW: set SHOW to true if facts.approval_workflow.exists is true.
7. All boolean SHOW values must be true or false (JSON booleans, not strings).
8. QAD_STANDARD_REPLACEMENT: always set SHOW to true. Use ONLY the KB EVIDENCE above to populate this accurately. For each major business capability, find the closest standard QAD native module/feature in the cited chunks.

   CRITICAL — score by CAPABILITY PARITY, not IMPLEMENTATION PARITY:
   • The question is "can the BUSINESS OUTCOME be achieved with standard QAD?", NOT "does QAD reproduce every implementation detail of this code?"
   • If the custom code is a thin wrapper around standard QAD programs/tables (e.g. it CALLS rqrqmt.p, READS rqm_mstr, and just adds JSON formatting / email-via-mailx / a custom report layout on top), that's FULL replaceability — the underlying capability already lives in standard QAD; only the wrapper goes away.
   • Differences in data format (JSON vs HTML), file output, transport (mailx vs SMTP API), or UI layout do NOT downgrade feasibility. They are reconfiguration, not loss of capability.
   • Mark "Partial" only when standard QAD covers MOST of the business outcome but specific BUSINESS RULES (calculations, validations, routing logic) would still need custom development.
   • Mark "Not Available" only when there is no native QAD module for the underlying business outcome at all (rare; should be cited explicitly from the chunks).

   "Available Since" column — MUST be grounded:
   - If a chunk explicitly mentions a version (e.g. "since QAD 2019 SE", "introduced in Adaptive 2024 EE", "new in QAD Adaptive 2025"), use that exact phrase.
   - Otherwise default to "QAD Adaptive 2025" (the version covered by our KB). NEVER invent older version codes without explicit citation.

   RECOMMENDATION (overall) rules:
   - "Full Replacement Possible" — every row is Full, OR rows are Full/Partial with only formatting/delivery/UI differences (no business-rule gaps).
   - "Partial Replacement" — at least one row has genuine business-rule gaps that need redevelopment as Business Events / customisation framework / custom logic.
   - "Keep Custom — No Native Alternative" — at least one row is "Not Available" AND that capability is critical.

   Name real QAD modules from the chunks (e.g. "QAD Requisition Management", "QAD Procurement", "QAD Business Events", "QAD Action Centers"). Do not name modules absent from the evidence.

9. ROW-SHAPE RULE (critical — prevents fragmenting one capability into many "Partial" rows):
   The REPLACEMENT_TABLE rows are CORE BUSINESS OUTCOMES, not facts.capabilities entries.
   • First, group facts.capabilities into the 1-N actual business outcomes the system delivers end-to-end. For a thin wrapper around standard QAD programs (e.g. CALLS rqrqmt.p, READS rqm_mstr, then formats output and sends an email), the wrapper delivers ONE outcome ("notify approver of pending requisition"), no matter how many internal steps facts.capabilities lists. ONE row.
   • For a full custom module (15+ files, multiple business processes), there will be 4-8 outcomes — one row per genuine business outcome.
   • NEVER produce a row for an internal implementation step (formatting, list-retrieval, data-extraction, transport, UI layout). Those are HOW the outcome is delivered, not the outcome itself.
   • A common failure mode is to take 3 facts.capabilities items like "Generate notification content / Retrieve approver list / Build email" and produce 3 rows all marked "Partial". This is wrong. Those are 3 internal steps of ONE outcome — produce ONE row, score Full (since QAD natively delivers the same outcome).
   • After grouping: if the resulting count of rows is the same as len(facts.capabilities), pause and re-check whether you've actually consolidated into outcomes or just copied the implementation-step list. The latter produces noisy "all Partial" tables.

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
      "Each capability as a complete descriptive sentence — from facts.capabilities. List ONLY what the system actually does (typically 2-6 for thin wrappers, 6-12 for full custom modules). Do NOT pad the list with generic outcome statements ('improves efficiency', 'enhances compliance', 'facilitates communication'). If the system has 3 real capabilities, list 3."
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
    "INTRO_PARA": "3-5 sentence paragraph: based on the KB EVIDENCE chunks above and the extracted facts, explain whether standard QAD Adaptive ERP provides native functionality that could replace or partially replace this customization. Mention the business capability being compared, what standard QAD offers (citing the source_doc/breadcrumb where useful), and the overall conclusion (full replacement possible / partial replacement / keep custom).",
    "REPLACEMENT_TABLE": {{
      "headers": ["Business Capability", "Custom Implementation (Current)", "Standard QAD Native Module / Feature", "Available Since (QAD Version)", "Replacement Feasibility"],
      "rows": [
        ["ONE row per CORE BUSINESS OUTCOME — NOT one row per facts.capabilities entry. Step 1: read facts.capabilities and group them into 1-N core business outcomes. Step 2: produce ONE row per outcome. CRITICAL: if the system is a thin wrapper that delivers ONE business outcome via several internal steps (e.g. 'extract data + build approver list + format + send email' = ONE outcome 'notify approver of pending requisition'), produce ONE row, not three or four. Implementation steps (formatting, list-building, transport, data extraction) are NEVER separate rows. Each row's columns: describe what the custom code does end-to-end | the closest standard QAD module/feature cited in the KB EVIDENCE | version per chunk (default 'QAD Adaptive 2025' if not cited; NEVER invent older versions) | Feasibility: Full / Partial / Not Available — see the per-row scoring rules in the CRITICAL INSTRUCTIONS above."]
      ]
    }},
    "RECOMMENDATION": "Full Replacement Possible | Partial Replacement | Keep Custom — No Native Alternative",
    "RECOMMENDATION_DETAIL": "3-5 sentence paragraph explaining the recommendation: which capabilities can switch to standard, which require custom logic to remain, any data migration considerations, and the suggested approach. Be specific about QAD module names from the KB EVIDENCE — do not invent module names not present in the chunks.",
    "GAPS_IF_REPLACED": [
      "Each gap as a complete sentence: what this custom system does that standard QAD cannot do even after migration — derived strictly from the KB EVIDENCE (i.e. capabilities not covered by any cited chunk)"
    ],
    "VERSION_AVAILABILITY_NOTE": "1-2 sentence note on which QAD version(s) first introduced the relevant standard functionality, ONLY if a version is explicitly cited in the KB EVIDENCE chunks above. If no version is cited, omit this key entirely. Never fabricate version codes."
  }}
}}

OUTPUT REQUIREMENT: The JSON must be at least 15,000 characters long. Every array must have real entries. Every paragraph must be 4+ sentences."""

    logger.info("PASS2 prompt length: %d chars", len(pass2_prompt))

    # Run Pass 2 (Word doc) and Summary (executive JSON) concurrently. The summary
    # call is small (~3-5s) so it virtually never blocks Pass 2 (~10-20s); we just
    # wait for the slower of the two.
    pass2_task   = asyncio.create_task(
        openai_chat(pass2_system, pass2_prompt, max_tokens=16000, model="gpt-4o")
    )
    summary_task = asyncio.create_task(
        _generate_summary(raw1, web_replacement_research)
    )
    raw, summary_data = await asyncio.gather(pass2_task, summary_task, return_exceptions=True)

    # ── Send `summary` frame (graceful degradation if it failed) ─────────────
    if isinstance(summary_data, dict) and summary_data:
        await send_frame(ws, "summary", summary_data)
        logger.info("SUMMARY sent: keys=%s", list(summary_data.keys()))
    else:
        if isinstance(summary_data, Exception):
            logger.exception("Summary task raised: %s", summary_data)
        await send_frame(ws, "summary", {"error": "unavailable"})
        logger.warning("SUMMARY unavailable — sending placeholder")

    # ── Process Pass 2 result (Word doc) ────────────────────────────────────
    if isinstance(raw, Exception):
        logger.exception("PASS2 task raised: %s", raw)
        await send_error(ws, f"Documentation generation failed: {raw}")
        return

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

    # ── PASS 3: Migration Blueprint ──────────────────────────────────────────
    #
    # Run after the system doc has shipped so the user sees the first download
    # immediately; the blueprint follows ~30-90s later as a `blueprint` frame.
    # If Pass 3 fails (timeout / parse error / KB empty) we silently skip — the
    # main doc + Summary tab already shipped successfully.

    await send_status(ws, "Generating Migration Blueprint…")
    blueprint_data: dict | None = None
    blueprint_url:  str  | None = None
    try:
        blueprint_data = await _generate_blueprint(facts, web_replacement_research, parsed)
    except Exception:
        logger.exception("Pass 3 blueprint generation raised; continuing without blueprint.")
        blueprint_data = None

    if blueprint_data:
        # Default the title page to the system info from the main doc if Pass 3
        # didn't override it.
        bp_tp = blueprint_data.setdefault("TITLE_PAGE", {})
        bp_tp.setdefault("SYSTEM_NAME",      tp.get("SYSTEM_NAME", module_label))
        bp_tp.setdefault("SYSTEM_FULL_NAME", title)
        bp_tp.setdefault("TARGET_PLATFORM",  "QAD Adaptive 2025")

        try:
            blueprint_url = generate_blueprint_document(blueprint_data, system_full=title)
        except Exception:
            logger.exception("Blueprint Word render failed; continuing without blueprint.")
            blueprint_url = None

    if blueprint_url:
        await send_frame(ws, "blueprint", {
            "url":   blueprint_url,
            "title": f"{title} — Migration Blueprint",
        })
        logger.info("Blueprint shipped: %s", blueprint_url)
    else:
        logger.warning("Blueprint not shipped (data=%s, url=%s)",
                       bool(blueprint_data), bool(blueprint_url))

    append_turn(session_id, AGENT_KEY, {
        "q": question, "a": summary, "mode": "documentation",
        "doc_url": doc_url, "blueprint_url": blueprint_url,
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
    """Main WebSocket handler for QAD-Zone (4 modes)."""
    # Lazy import to avoid a circular at module load — bulk_module_pipeline
    # imports from this very module (service.py).
    from app.agents.qad_zone.bulk_service import handle_bulk_upload

    try:
        while True:
            data = await ws.receive_json()
            mode = (data.get("mode") or "query").strip().lower()

            try:
                if mode == "modernisation":
                    current_version = (data.get("current_version") or "").strip()
                    target_version = (data.get("target_version") or "").strip()
                    await _handle_modernisation(ws, session_id, current_version, target_version)

                elif mode == "bulk-upload":
                    # New: accept a single .zip of the customer's customisation,
                    # run the 8-pass tagging pipeline, then generate one
                    # documentation + migration blueprint per detected module.
                    uploaded_files = data.get("uploaded_files") or []
                    await handle_bulk_upload(ws, session_id, uploaded_files)

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