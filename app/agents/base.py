"""Base agent metadata. Used by the registry for sidebar and routing."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentMeta:
    key: str
    name: str
    icon: str
    description: str
    route_prefix: str
    layout: str = "main"  # "main" | "side"
    sample_questions: dict[str, list[str]] = field(default_factory=dict)
