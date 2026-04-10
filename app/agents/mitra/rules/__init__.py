"""Business rules per domain.

Each module exports a ``RULES`` dict mapping rule names to dicts with:

- ``keywords``  list[str]  — lowercase keyword phrases for matching
- ``patterns``  list[str]  — regex patterns (compiled at import)
- ``logic``     str | None — business logic description (passed to LLM as hint)
- ``sql``       str | None — pre-built SQL (used directly if present)
- ``followup``  str | None — follow-up question to show after results
"""
from __future__ import annotations

import re
from typing import Any

from app.agents.mitra.rules import inventory, sales, purchase, manufacturing

ALL_RULE_MODULES = {
    "inventory": inventory.RULES,
    "sales": sales.RULES,
    "purchase": purchase.RULES,
    "manufacturing": manufacturing.RULES,
}


def find_matching_rule(
    question: str, user_roles: list[str] | None = None
) -> dict[str, Any] | None:
    """Search all domain rule files for a matching business rule.

    Returns the first match with keys: name, domain, logic, sql, followup.
    Returns None if no rule matches.
    """
    normalized = question.lower().strip()

    # Search order: prioritise the user's selected domains, then all
    search_order: list[str] = []
    for role in user_roles or []:
        if role in ALL_RULE_MODULES:
            search_order.append(role)
    for domain in ALL_RULE_MODULES:
        if domain not in search_order:
            search_order.append(domain)

    for domain in search_order:
        rules = ALL_RULE_MODULES[domain]
        for rule_name, rule in rules.items():
            # Keyword match
            kw_match = any(kw in normalized for kw in rule.get("keywords", []))
            # Regex match
            pat_match = any(
                re.search(p, normalized, re.IGNORECASE)
                for p in rule.get("patterns", [])
            )
            if kw_match or pat_match:
                return {
                    "name": rule_name,
                    "domain": domain,
                    "logic": rule.get("logic"),
                    "sql": rule.get("sql"),
                    "followup": rule.get("followup"),
                }
    return None
