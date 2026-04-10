"""Visual Intelligence — KPI & chart generation from aggregation SQL.

Key difference from Mitra: the LLM is prompted to generate ONLY aggregation
queries (COUNT, SUM, AVG, TOP N, GROUP BY). Never raw SELECT *.
The response includes a chart spec rendered by Chart.js on the frontend.

Important: tokens are NOT streamed to the frontend — the raw JSON response
must be parsed before anything is shown. Status messages are sent instead.
"""
from __future__ import annotations

import logging
import json

from fastapi import WebSocket

from app.core.config import settings
from app.core.llm import groq_chat, openai_chat, parse_json_response
from app.core.session import append_turn, get_user_settings, load_history
from app.core.ws import send_done, send_error, send_frame, send_status, send_token
from app.db.odbc import run_select
from app.agents.mitra.table_catalog import TABLE_CATALOG
from app.agents.mitra.table_schemas import get_schemas_for_tables

logger = logging.getLogger(__name__)

AGENT_KEY = "visual"

# Output format kept as a separate string so .format() never sees its curly braces
_OUTPUT_FORMAT = """{
  "query": "SELECT ...",
  "explanation": {
    "basis": "What the user asked and logic used.",
    "benefit": "How this helps the user."
  },
  "chart": {
    "type": "bar",
    "title": "Chart Title",
    "x": "column_alias_for_x_axis",
    "y": ["column_alias_for_values"]
  },
  "followup_questions": ["question 1", "question 2"]
}

For KPI type use this chart format instead:
{
  "type": "kpi",
  "title": "Summary",
  "kpis": [
    {"label": "Total Orders", "column": "order_count", "format": "number"},
    {"label": "Total Value",  "column": "total_value",  "format": "currency"}
  ]
}
"""

VISUAL_SYSTEM_PROMPT = """You are a QAD ERP analytics and KPI assistant. You generate AGGREGATION queries for dashboards and charts.

SCHEMAS:
{schemas}

PROGRESS OPENEDGE SQL - MANDATORY RULES
You are targeting a Progress OpenEdge database via ODBC (DataDirect driver).
You MUST use ONLY the functions and syntax listed below. Anything else causes a runtime error.

DATE AND TIME - use these EXACT functions only:
  CURDATE()              returns today's date as DATE
  CURDATE() - 30         date 30 days ago  (integer = days for DATE arithmetic)
  CURDATE() - 90         date 90 days ago
  CURDATE() - 365        date 365 days ago
  date_col - CURDATE()   integer days difference
  ADD_MONTHS(date, -n)   subtract n whole months, e.g. ADD_MONTHS(CURDATE(), -3)
  ADD_MONTHS(date, n)    add n whole months
  YEAR(date)             extract year as integer
  MONTH(date)            extract month as integer
  DAY(date)              extract day as integer

FOR MONTHLY GROUPING - use YEAR() and MONTH() together:
  SELECT YEAR(date_col) AS "Year", MONTH(date_col) AS "Month", COUNT(*) AS "Count"
  GROUP BY YEAR(date_col), MONTH(date_col)
  ORDER BY YEAR(date_col), MONTH(date_col)

FOR THIS YEAR filter:
  WHERE YEAR(date_col) = YEAR(CURDATE())

NEVER USE - these all cause errors:
  DATE_TRUNC, TRUNC, EXTRACT, TO_DATE (for current date)
  TODAY, CURRENT_DATE, CURRENT_TIMESTAMP, GETDATE(), SYSDATE, NOW(), NOW
  DATEADD, DATE_SUB, DATEDIFF, TIMESTAMPADD, TIMESTAMPDIFF, INTERVAL
  LIMIT, OFFSET, ROWNUM, FETCH FIRST
  ISNULL, NVL, CONCAT()

PAGINATION - Progress uses TOP not LIMIT:
  SELECT TOP 50 ...   (TOP goes immediately after SELECT)

SCHEMA PREFIX - always prefix every table name with PUB.:
  PUB.po_mstr, PUB.oe_head, PUB.pt_mstr

DOMAIN FILTER - always add this WHERE condition:
  table.domain_field = '{domain}'

AGGREGATION RULES:
1. ONLY generate aggregation queries: COUNT, SUM, AVG, MIN, MAX, GROUP BY.
2. NEVER return SELECT * or raw row dumps. Always aggregate.
3. Keep result small (max 50 rows) - suitable for charts.
4. Always use double-quoted aliases on every output column.
5. The column aliases in the SQL MUST exactly match the "x", "y", and "column" values in the chart spec.

CHART TYPES:
  kpi      - single summary numbers (totals, averages, counts) — use when user wants "total", "how many", "overall"
  bar      - comparisons across categories (suppliers, items, customers)
  line     - trends over time (monthly, weekly, daily)
  pie      - proportions / share of whole
  doughnut - proportions with hollow center

EXAMPLE - monthly PO count this year:
  SQL:
    SELECT TOP 50
      YEAR(po_ord_date) AS "Year",
      MONTH(po_ord_date) AS "Month",
      COUNT(*) AS "PO Count"
    FROM PUB.po_mstr
    WHERE po_domain = '{domain}'
      AND YEAR(po_ord_date) = YEAR(CURDATE())
    GROUP BY YEAR(po_ord_date), MONTH(po_ord_date)
    ORDER BY YEAR(po_ord_date), MONTH(po_ord_date)

  chart spec:
    "type": "line", "x": "Month", "y": ["PO Count"]

CRITICAL: The "x" value must be EXACTLY the alias used in SELECT (e.g. "Month" not "month").
          The "y" values must be EXACTLY the aliases used in SELECT (e.g. "PO Count" not "po_count").

OUTPUT FORMAT - respond with ONLY valid JSON, no markdown fences, no extra text:
{output_format}
"""


async def _identify_tables(question: str) -> list[str]:
    raw = await groq_chat(
        "You are a QAD ERP expert. Return ONLY a JSON array of table names.",
        f"{TABLE_CATALOG}\n\nUser question: {question}\n\nJSON array:",
        temperature=0, max_tokens=100,
    )
    try:
        cleaned = raw.strip()
        if not cleaned.startswith("["):
            start = cleaned.find("[")
            end = cleaned.rfind("]") + 1
            cleaned = cleaned[start:end]
        return [t.strip().lower() for t in json.loads(cleaned)]
    except Exception:
        return ["pt_mstr", "in_mstr"]


async def handle_visual_ws(ws: WebSocket, session_id: str, user: dict) -> None:
    try:
        while True:
            data = await ws.receive_json()
            question = (data.get("question") or "").strip()
            if not question:
                await send_error(ws, "Question is required")
                continue

            user_settings = get_user_settings(session_id)
            row_limit = min(user_settings.get("row_limit", 50), 100)

            # Step 1: Identify tables
            await send_status(ws, "Identifying relevant tables...")
            tables = await _identify_tables(question)
            schemas_text = get_schemas_for_tables(tables)

            # Step 2: Build prompt
            system = VISUAL_SYSTEM_PROMPT.format(
                schemas=schemas_text,
                domain=settings.qad_domain,
                output_format=_OUTPUT_FORMAT,
            )

            # Step 3: Call LLM — NO token streaming, collect silently
            # Raw JSON must be parsed before showing anything; streaming it
            # would dump the JSON directly into the chat bubble.
            await send_status(ws, "Generating analytics query...")
            try:
                raw_text = await openai_chat(system, question, max_tokens=1024)
            except Exception as exc:
                logger.exception("OpenAI call failed")
                await send_error(ws, f"LLM error: {exc}")
                await send_done(ws)
                continue

            # Step 4: Parse LLM response
            sql_to_execute = None
            chart_spec = None
            followups = []
            explanation_text = ""

            try:
                parsed = parse_json_response(raw_text)
                sql_to_execute = parsed.get("query")
                chart_spec = parsed.get("chart")
                followups = parsed.get("followup_questions", [])
                exp = parsed.get("explanation", {})
                explanation_text = exp.get("basis", "") + "\n\n" + exp.get("benefit", "")
            except Exception:
                logger.warning("Could not parse Visual LLM response: %s", raw_text[:200])
                await send_error(ws, "Could not parse analytics response. Please rephrase.")
                await send_done(ws)
                continue

            if not sql_to_execute:
                await send_error(ws, "Could not generate analytics query. Please rephrase.")
                await send_done(ws)
                continue

            # Step 5: Execute SQL
            await send_frame(ws, "sql", sql_to_execute)
            await send_status(ws, "Executing analytics query...")
            try:
                result = await run_select(sql_to_execute, limit=row_limit)
            except Exception as exc:
                logger.exception("ODBC query failed")
                await send_error(ws, f"SQL execution failed: {exc}")
                await send_done(ws)
                continue

            # Step 6: Stream explanation text (clean, not raw JSON)
            if explanation_text.strip():
                for chunk in [explanation_text[i:i+30] for i in range(0, len(explanation_text), 30)]:
                    await send_token(ws, chunk)

            # Step 7: Send chart spec + table data as SEPARATE frames
            # Frontend renderChart() expects:
            #   currentChart = the spec object {type, title, x, y} or {type, kpis}
            #   currentTable = {columns, rows, row_count}
            # So we send "chart" frame with just the spec, and "table" frame with the data.
            await send_frame(ws, "table", {
                "columns": result["columns"],
                "rows": result["rows"],
                "row_count": result["row_count"],
            })

            if chart_spec:
                await send_frame(ws, "chart", chart_spec)  # just the spec, not wrapped

            # Step 8: Follow-ups
            if followups:
                await send_frame(ws, "followup", followups[:3])

            append_turn(session_id, AGENT_KEY, {
                "q": question, "sql": sql_to_execute, "role": "user",
            })
            await send_done(ws)

    except Exception as exc:
        logger.exception("Visual WS error: %s", exc)
        try:
            await send_error(ws, str(exc))
            await send_done(ws)
        except Exception:
            pass