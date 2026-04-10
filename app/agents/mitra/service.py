"""Mitra — text-to-SQL agent on live QAD Progress DB via ODBC.

Full pipeline (all in Python, no n8n):
1. Identify relevant tables (Groq — fast & free)
2. Look up schemas for those tables
3. Check business rules for pre-built SQL
4. Generate SQL via OpenAI (silent — JSON response, not prose)
5. Parse JSON, extract SQL + explanation + followups
6. Execute SQL via ODBC
7. Stream only the explanation text to frontend, then send table + followups
"""
from __future__ import annotations

import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from app.core.config import settings
from app.core.llm import groq_chat, openai_chat, parse_json_response
from app.core.session import append_turn, get_user_settings, load_history, set_user_settings
from app.core.ws import send_done, send_error, send_frame, send_status, send_token
from app.db.odbc import run_select
from app.agents.mitra.table_catalog import TABLE_CATALOG
from app.agents.mitra.table_schemas import get_schemas_for_tables
from app.agents.mitra.rules import find_matching_rule

logger = logging.getLogger(__name__)

AGENT_KEY = "mitra"

# Output format kept as a separate string so .format() never sees its curly braces
_OUTPUT_FORMAT = """{
  "query": "SELECT ...",
  "explanation": {
    "basis": "What the user asked and the logic/criteria used to filter results.",
    "benefit": "How this data helps the user make decisions."
  },
  "followup_questions": ["question 1", "question 2"]
}"""

_RULE_OUTPUT_FORMAT_TEMPLATE = """{
  "query": "SQL_PLACEHOLDER",
  "explanation": {
    "basis": "What the user asked and the logic/criteria used.",
    "benefit": "How this data helps the user."
  },
  "followup_questions": ["FOLLOWUP_PLACEHOLDER", "What other analysis would be helpful?"]
}"""

SQL_SYSTEM_PROMPT = """You are an expert QAD ERP SQL assistant. Generate a query based on the user's question using ONLY the provided table schemas.

SCHEMAS:
{schemas}

PROGRESS OPENEDGE SQL - MANDATORY RULES
You are targeting a Progress OpenEdge database via ODBC (DataDirect driver).
You MUST use ONLY the functions listed below. Any other date/time syntax will cause a runtime error.

DATE AND TIME - use these EXACT functions, no alternatives:
  CURDATE()            returns today's date as DATE
  CURDATE() - 15       date 15 days ago  (integer = days for DATE arithmetic)
  CURDATE() - 30       date 30 days ago
  CURDATE() - 90       date 90 days ago
  date_col - CURDATE() returns integer number of days difference
  ADD_MONTHS(date, n)  add or subtract whole months, e.g. ADD_MONTHS(CURDATE(), -3)
  YEAR(date)           extract year as integer
  MONTH(date)          extract month as integer
  DAY(date)            extract day as integer

NEVER USE - these all cause errors on this database:
  TODAY, CURRENT_DATE, CURRENT_TIMESTAMP, GETDATE(), SYSDATE, NOW(), NOW
  DATEADD, DATE_SUB, DATEDIFF, TIMESTAMPADD, TIMESTAMPDIFF, INTERVAL
  LIMIT, OFFSET, ROWNUM, FETCH FIRST
  ISNULL, NVL, CONCAT()

PAGINATION - Progress uses TOP not LIMIT:
  SELECT TOP {limit} col1, col2 FROM ...
  TOP goes immediately after SELECT, before column names.

SCHEMA PREFIX - always prefix every table name with PUB.:
  PUB.pt_mstr, PUB.pd_hist, PUB.oe_head

DOMAIN FILTER - always add:
  WHERE some_table.domain_field = '{domain}'

STRING CONCAT - use || not CONCAT():
  col1 || ' ' || col2

NULL HANDLING - use COALESCE not ISNULL or NVL:
  COALESCE(col, 0)

ALIASES - always use double-quoted aliases:
  pt_desc1 AS "Item Description"

{business_hint}

CONVERSATION CONTEXT (previous exchanges):
{history_text}

OUTPUT FORMAT - respond with ONLY valid JSON, no markdown fences, no extra text:
{output_format}
"""

SQL_WITH_RULE_PROMPT = """You are an expert QAD ERP SQL assistant. A pre-built query is provided. Your job is ONLY to explain it and suggest follow-ups.

PRE-BUILT QUERY:
{sql}

RULES:
- Use the EXACT query above in your response. Do NOT modify it.
- Explain what the query does and how it helps the user.

OUTPUT FORMAT - respond with ONLY valid JSON, no markdown fences:
{output_format}
"""


async def _identify_tables(question: str) -> list[str]:
    """Use Groq (free/fast) to identify which QAD tables are relevant."""
    prompt = f"{TABLE_CATALOG}\n\nUser question: {question}\n\nJSON array of relevant tables:"
    raw = await groq_chat(
        "You are a QAD ERP expert. Return ONLY a JSON array of table names.",
        prompt,
        temperature=0,
        max_tokens=100,
    )
    try:
        cleaned = raw.strip()
        if not cleaned.startswith("["):
            start = cleaned.find("[")
            end = cleaned.rfind("]") + 1
            cleaned = cleaned[start:end]
        tables = json.loads(cleaned)
        return [t.strip().lower() for t in tables if isinstance(t, str)]
    except Exception:
        logger.warning("Failed to parse table list: %s", raw)
        return ["pt_mstr", "in_mstr"]


def _build_history_text(history: list[dict]) -> str:
    lines = []
    for h in history[-6:]:
        lines.append(f"User: {h.get('q', '')}")
        if h.get("sql"):
            lines.append(f"SQL: {h['sql']}")
        if h.get("row_count") is not None:
            lines.append(f"Result: {h['row_count']} rows")
    return "\n".join(lines) if lines else "No previous conversation."


def _stream_chunks(text: str, chunk_size: int = 30):
    """Split text into chunks for token streaming."""
    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]


async def handle_mitra_ws(ws: WebSocket, session_id: str, user: dict) -> None:
    """Main WebSocket handler for Mitra text-to-SQL agent."""
    try:
        while True:
            data = await ws.receive_json()
            question = (data.get("question") or "").strip()
            if not question:
                await send_error(ws, "Question is required")
                continue

            user_settings = get_user_settings(session_id)
            row_limit = user_settings.get("row_limit", settings.default_row_limit)

            # Domain: WS payload > session setting > env config
            domain_from_payload = (data.get("domain") or "").strip()
            if domain_from_payload:
                # Persist the new domain choice for the rest of the session
                set_user_settings(session_id, {"qad_domain": domain_from_payload})
                active_domain = domain_from_payload
            else:
                active_domain = user_settings.get("qad_domain") or settings.qad_domain

            roles = user.get("roles", [])

            # Step 1: Identify tables
            await send_status(ws, "Identifying relevant tables...")
            tables = await _identify_tables(question)

            # Step 2: Get schemas
            schemas_text = get_schemas_for_tables(tables)

            # Step 3: Check business rules
            rule = find_matching_rule(question, roles)
            history = load_history(session_id, AGENT_KEY)
            history_text = _build_history_text(history)

            sql_to_execute = None
            explanation_basis = ""
            explanation_benefit = ""
            followups = []

            # ── Path A: pre-built rule SQL ────────────────────────────────
            if rule and rule.get("sql"):
                sql_to_execute = rule["sql"].format(
                    domain=active_domain,
                    limit=row_limit,
                )
                rule_followup = rule.get("followup", "")

                await send_status(ws, "Using optimised business rule query...")

                rule_output = _RULE_OUTPUT_FORMAT_TEMPLATE.replace(
                    "SQL_PLACEHOLDER", sql_to_execute.replace('"', '\\"')
                ).replace("FOLLOWUP_PLACEHOLDER", rule_followup)

                explain_prompt = SQL_WITH_RULE_PROMPT.format(
                    sql=sql_to_execute,
                    output_format=rule_output,
                )

                try:
                    raw_text = await openai_chat(
                        "You are a QAD ERP expert. Return only valid JSON.", explain_prompt
                    )
                    parsed = parse_json_response(raw_text)
                    exp = parsed.get("explanation", {})
                    explanation_basis = exp.get("basis", "")
                    explanation_benefit = exp.get("benefit", "")
                    followups = parsed.get("followup_questions", [])
                    if rule_followup and rule_followup not in followups:
                        followups.insert(0, rule_followup)
                except Exception as exc:
                    logger.exception("LLM explain failed for rule query")
                    explanation_basis = f"Pre-built query for: {question}"

            # ── Path B: LLM generates SQL ────────────────────────────────
            else:
                business_hint = ""
                if rule and rule.get("logic"):
                    business_hint = f"BUSINESS LOGIC HINT: {rule['logic']}"

                await send_status(ws, "Generating SQL query...")

                system = SQL_SYSTEM_PROMPT.format(
                    schemas=schemas_text,
                    domain=active_domain,
                    limit=row_limit,
                    business_hint=business_hint,
                    history_text=history_text,
                    output_format=_OUTPUT_FORMAT,
                )

                try:
                    # Silent call — LLM returns JSON, not prose; never stream raw JSON
                    raw_text = await openai_chat(
                        system, question,
                        max_tokens=1500,
                        temperature=0.1,
                    )
                except Exception as exc:
                    logger.exception("OpenAI call failed")
                    await send_error(ws, f"LLM error: {exc}")
                    await send_done(ws)
                    continue

                try:
                    parsed = parse_json_response(raw_text)
                    sql_to_execute = parsed.get("query")
                    exp = parsed.get("explanation", {})
                    explanation_basis = exp.get("basis", "")
                    explanation_benefit = exp.get("benefit", "")
                    followups = parsed.get("followup_questions", [])
                    if rule and rule.get("followup"):
                        followups.insert(0, rule["followup"])
                except Exception:
                    logger.warning("Could not parse LLM JSON response, extracting SQL")
                    for line in raw_text.split("\n"):
                        stripped = line.strip()
                        if stripped.upper().startswith(("SELECT", "WITH")):
                            sql_to_execute = stripped
                            break

            # ── Step 4: Stream explanation as clean prose ─────────────────
            # Build a readable explanation card — never expose raw JSON
            if explanation_basis or explanation_benefit:
                explanation_md = ""
                if explanation_basis:
                    explanation_md += f"{explanation_basis}\n\n"
                if explanation_benefit:
                    explanation_md += f"{explanation_benefit}"
                for chunk in _stream_chunks(explanation_md.strip()):
                    await send_token(ws, chunk)

            # ── Step 5: Execute SQL ───────────────────────────────────────
            result = {"columns": [], "rows": [], "row_count": 0}
            if sql_to_execute:
                await send_frame(ws, "sql", sql_to_execute)
                await send_status(ws, "Executing query on QAD...")
                try:
                    result = await run_select(sql_to_execute, limit=row_limit)
                    await send_frame(ws, "table", {
                        "columns": result["columns"],
                        "rows": result["rows"],
                        "row_count": result["row_count"],
                    })
                except Exception as exc:
                    logger.exception("ODBC query failed")
                    await send_error(ws, f"SQL execution failed: {exc}")
            else:
                await send_error(ws, "Could not generate a valid SQL query. Please rephrase your question.")

            # ── Step 6: Follow-ups ────────────────────────────────────────
            if followups:
                await send_frame(ws, "followup", followups[:3])

            # ── Step 7: Save history ──────────────────────────────────────
            append_turn(session_id, AGENT_KEY, {
                "q": question,
                "sql": sql_to_execute,
                "row_count": result.get("row_count", 0),
                "role": "user",
            })

            await send_done(ws)

    except WebSocketDisconnect:
        # Client closed connection — normal, not an error
        pass
    except Exception as exc:
        logger.exception("Mitra WS error: %s", exc)
        try:
            await send_error(ws, str(exc))
            await send_done(ws)
        except Exception:
            pass