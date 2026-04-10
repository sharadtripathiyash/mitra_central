"""QAD modernisation analysis module.

Flow:
1. Receive current_version + target_version directly (no chat parsing)
2. Load inventory of all custom programs across all modules
3. Load full code of each module for deep analysis
4. Search the web for target version features and upgrade guides
5. LLM analyses and returns structured JSON matching the migration plan template
6. Generate a corporate Word doc using migration_doc_generator
"""
from __future__ import annotations

import logging

from app.core.llm import openai_chat
from app.agents.qad_zone.programs import load_all_code_summary, list_modules, load_module_code
from app.agents.qad_zone.migration_doc_generator import generate_migration_document

logger = logging.getLogger(__name__)

try:
    from duckduckgo_search import DDGS
    _ddg_available = True
except ImportError:
    _ddg_available = False
    logger.warning("duckduckgo-search not installed; web search disabled for modernisation")


def _web_search(query: str, max_results: int = 6) -> str:
    if not _ddg_available:
        return "Web search not available (install duckduckgo-search)."
    try:
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append(f"• {r.get('title', '')}\n  {r.get('body', '')}\n  Source: {r.get('href', '')}")
        return "\n\n".join(results) if results else "No results found."
    except Exception as exc:
        logger.warning("DuckDuckGo search failed: %s", exc)
        return f"Web search failed: {exc}"


def _load_all_module_code(max_chars_per_module: int = 40_000) -> str:
    modules = list_modules()
    if not modules:
        return "No custom program modules found."
    parts = []
    for mod in modules:
        code = load_module_code(mod, max_chars=max_chars_per_module)
        parts.append(f"\n{'#'*70}\n# MODULE: {mod.upper()}\n{'#'*70}\n{code}")
    return "\n".join(parts)


async def analyse_modernisation(
    current_version: str,
    target_version: str,
) -> dict:
    """Run full modernisation analysis. Returns dict with summary, data, doc_url."""

    programs_summary = load_all_code_summary()
    all_code = _load_all_module_code()

    searches = [
        f"QAD {target_version} new features changelog what's new",
        f"QAD {current_version} to {target_version} upgrade migration guide",
        f"QAD {target_version} customisation standard functionality replaced deprecated",
        f"QAD ERP {target_version} Progress 4GL OpenEdge compatibility breaking changes",
    ]

    web_parts = []
    for q in searches:
        logger.info("Searching: %s", q)
        result = _web_search(q, max_results=5)
        web_parts.append(f"SEARCH: {q}\n{result}")

    web_context = "\n\n".join(web_parts)

    prompt = f"""You are a senior QAD ERP modernisation consultant.
Analyse the custom QAD programs below and produce a comprehensive, structured migration plan.

CURRENT VERSION: {current_version}
TARGET VERSION: {target_version}

━━━ CUSTOM PROGRAMS INVENTORY ━━━
{programs_summary}

━━━ FULL CUSTOM CODE ━━━
{all_code}

━━━ WEB RESEARCH — TARGET VERSION & MIGRATION ━━━
{web_context}

━━━ INSTRUCTIONS ━━━
Return ONLY a valid JSON object (no markdown fences, no preamble) with exactly this structure.
If you cannot populate a field from the available information, use a descriptive placeholder string, never leave it empty.
Do NOT include empty arrays — if you have no items, omit the key entirely.

{{
  "executive_summary": "2-3 paragraph executive summary for CFO/CIO audience covering migration purpose, viability, and top recommendation.",
  "strategic_recommendation": "1 paragraph strategic recommendation covering what the organisation should do next and immediate actions.",
  "scope": "Scope statement: what is in scope and what is explicitly out of scope.",
  "summary_metrics": {{
    "total_modules": "[N] modules",
    "carry_forward": "[N] modules",
    "adapt": "[N] modules",
    "replace": "[N] modules",
    "decommission": "[N] modules",
    "total_effort": "[LOW/MEDIUM/HIGH] — approx. [X]–[Y] person-days",
    "approach": "[BIG BANG / PHASED / PARALLEL RUN]",
    "duration": "[X] months",
    "risk_rating": "[LOW / MEDIUM / HIGH / CRITICAL]"
  }},
  "source_system": {{
    "product_name": "{current_version}",
    "architecture": "Describe the architecture of {current_version}",
    "database": "Progress OpenEdge version used",
    "ui_framework": "UI framework description",
    "code_size": "[N] source files / approx. [X,000] lines of code",
    "business_areas": "Key business areas customised",
    "support_status": "Current support status of {current_version}"
  }},
  "target_system": {{
    "product_name": "{target_version}",
    "architecture": "Describe the architecture of {target_version}",
    "database": "Target database version",
    "ui_framework": "Target UI framework",
    "custom_code_approach": "How customisations work in {target_version} (e.g. QCF, REST APIs)",
    "key_capabilities": "Key new capabilities in {target_version} relevant to this customer",
    "deployment": "Deployment model (On-Premise / Cloud SaaS / Hybrid)"
  }},
  "architectural_changes": "3-5 paragraphs on the most impactful architectural changes between {current_version} and {target_version}.",
  "deprecated_features": [
    {{"item": "Deprecated API or feature name", "version": "Version deprecated in", "replacement": "Recommended replacement"}}
  ],
  "modules": [
    {{
      "name": "Module name (e.g., DOA, E-Invoice)",
      "business_area": "e.g. Finance / Procurement / Manufacturing",
      "type": "e.g. Workflow / Report / Interface / Approval / Integration",
      "files": 3,
      "files_list": "file1.p, file2.w (comma separated list of actual filenames)",
      "loc": "~1,200",
      "complexity": "Low | Medium | High | Very High",
      "api_deps": "QAD API dependencies found in code, or None identified",
      "integrations": "External integrations found in code, or None",
      "owner": "Unknown",
      "last_modified": "Unknown — no source control metadata",
      "action": "Carry Forward | Adapt | Replace with Standard | Decommission",
      "rationale": "2-3 sentences explaining why this action is recommended based on the actual code.",
      "standard_feature": "Name of standard QAD feature replacing this, or N/A",
      "effort": "Low (1–5 days) | Medium (5–15 days) | High (15–40 days) | Very High (40+ days)",
      "effort_breakdown": {{
        "analysis": "1",
        "development": "5",
        "testing": "2",
        "deployment": "0.5"
      }},
      "priority": "P1 – Business Critical | P2 – Important | P3 – Desirable | P4 – Optional",
      "dependencies": "Other modules this depends on, or None",
      "risk_if_not_migrated": "Business impact if this module is left unaddressed.",
      "business_purpose": "1-3 paragraphs describing what this module does from a business perspective. No code language.",
      "technical_arch": "1-2 paragraphs describing the technical implementation. For developer audience.",
      "version_impact": "What specifically changes in {target_version} that affects this module. Reference actual deprecated procedures/APIs found in the code.",
      "work_breakdown": [
        {{"task": "Specific task description", "role": "Role responsible", "effort": "0.5d", "notes": "Dependencies or caveats"}}
      ],
      "testing_requirements": "Specific test cases for this module: happy path, error cases, boundary conditions."
    }}
  ],
  "action_summary": {{
    "carry_forward_count": "[N]",
    "carry_forward_pct": "[N%]",
    "adapt_count": "[N]",
    "adapt_pct": "[N%]",
    "replace_count": "[N]",
    "replace_pct": "[N%]",
    "decommission_count": "[N]",
    "decommission_pct": "[N%]",
    "total": "[N]"
  }},
  "functional_gaps": [
    {{
      "capability": "Business capability name",
      "current_solution": "Current custom module handling this",
      "target_feature": "Standard QAD {target_version} feature",
      "gap_status": "No Gap | Partial Gap | Full Gap Remains",
      "resolution": "Recommended resolution"
    }}
  ],
  "gap_intro": "1-2 paragraphs introducing the gap analysis.",
  "new_features": "1-2 paragraphs on new capabilities in {target_version} the customer should adopt.",
  "migration_approach": "2-3 paragraphs justifying the recommended migration approach (Big Bang / Phased / Parallel Run).",
  "risks": [
    {{
      "id": "R05",
      "description": "Risk description specific to this customer's modules",
      "impact": "Impact if this risk materialises",
      "likelihood": 3,
      "impact_score": 4,
      "score": 12,
      "rating": "HIGH | MEDIUM | LOW",
      "mitigation": "Specific mitigation action"
    }}
  ],
  "test_approach": "Overall testing approach covering environments, responsibilities, entry/exit criteria.",
  "cutover_approach": "Recommended cutover approach: timing, freeze point, rollback triggers, communication plan.",
  "rollback_plan": "Specific rollback steps if cutover fails: authority, time window, restore sequence, user comms.",
  "recommendations_intro": "Introduction to priority recommendations.",
  "recommendations": [
    {{"recommendation": "Specific actionable recommendation", "owner": "Who owns this", "date": "Target date"}}
  ],
  "timeline_narrative": "Narrative timeline: key milestones, project kick-off, phase completion, UAT sign-off, go-live date.",
  "version_change_reference": "Summary of QAD release notes and breaking changes between {current_version} and {target_version}.",
  "source_file_inventory": [
    {{"path": "/path/to/file.p", "ext": ".p", "loc": 450, "module": "module_name", "notes": "Main entry point"}}
  ]
}}

Be specific. Reference actual program file names from the code. Do not be vague.
Generate one module entry per distinct custom module found in the source code.
"""

    raw = await openai_chat(
        "You are a senior QAD ERP modernisation consultant. Return only valid JSON.",
        prompt,
        max_tokens=4096,
        temperature=0.1,
    )

    from app.core.llm import parse_json_response
    try:
        data = parse_json_response(raw)
    except Exception:
        logger.warning("Failed to parse JSON from modernisation LLM, using fallback structure")
        data = {
            "executive_summary": "Migration analysis complete. See document for full details.",
            "modules": [],
        }

    summary = data.get("executive_summary", "Migration analysis complete. See document for full details.")

    doc_url = generate_migration_document(
        current_version=current_version,
        target_version=target_version,
        data=data,
    )

    return {
        "summary": summary,
        "sections": [{"heading": "Analysis Complete", "content": summary, "level": 1}],
        "doc_url": doc_url,
    }