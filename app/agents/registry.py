"""Central agent registry.

The sidebar, routing, and template resolution are all driven from
the AGENTS list. Adding a new agent = add an entry here + its module.
"""
from __future__ import annotations

from app.agents.base import AgentMeta

AGENTS: list[AgentMeta] = [
    AgentMeta(
        key="mitra", name="Mitra", icon="message-square",
        description="Natural‑Language Intelligence for QAD Data.",
        route_prefix="/agents/mitra", layout="main",
    ),
    AgentMeta(
        key="visual", name="Visual Intelligence", icon="bar-chart-3",
        description="KPIs, charts and analytics from live QAD data.",
        route_prefix="/agents/visual", layout="main",
    ),
    AgentMeta(
        key="qadzone", name="QAD-Zone", icon="wrench",
        description="Custom code knowledge base, docs & modernisation.",
        route_prefix="/agents/qadzone", layout="main",
    ),
    AgentMeta(
        key="apex", name="Apex", icon="sparkles",
        description="User guide assistant.",
        route_prefix="/agents/apex", layout="side",
    ),
]

BY_KEY: dict[str, AgentMeta] = {a.key: a for a in AGENTS}


def sidebar_agents() -> list[AgentMeta]:
    return [a for a in AGENTS if a.layout == "main"]


def floating_agents() -> list[AgentMeta]:
    return [a for a in AGENTS if a.layout == "side"]
