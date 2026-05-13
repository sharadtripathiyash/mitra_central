"""Pass 0.7 — Include / RUN graph analysis.

Parses Progress 4GL source files for cross-references:
  - `{us/xx/<name>.i}` or `{us/<dir>/<name>.i}` include statements
  - `RUN <program>.p` direct calls
  - `RUN VALUE("<program>.p")` indirect calls

Builds an undirected graph: edge = "file A references file B (or vice versa)".
Computes connected components. Files in the same component are very likely
part of the same business module (same author, same data flow).

Output is consumed by Pass 1's prompt as an additional signal beyond prefix.

Zero LLM cost. Pure regex + BFS.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from . import config

# {us/xx/xxname.i}        — most common include style
# {us/anydir/xxname.i}    — sub-dir variations
# {us/xx/xxname}          — without .i extension (rare but legal)
_INCLUDE_RE = re.compile(r"\{[^}]*?(?:/|\\)([\w_-]+)\.?i?\b\}", re.IGNORECASE)

# RUN xxname.p / RUN xxname.r / RUN VALUE("xxname.p") / run-set
_RUN_RE = re.compile(
    r'\bRUN\s+(?:VALUE\s*\(\s*["\']?)?([\w_-]+)\.[pr]\b',
    re.IGNORECASE,
)


def _normalise_name(name: str) -> str:
    """Match index — strip extension + lowercase."""
    return Path(name).stem.lower()


def parse_references(file_path: Path) -> set[str]:
    """Return the set of referenced file-stems (lowercased) found inside file_path."""
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return set()
    refs: set[str] = set()
    for m in _INCLUDE_RE.finditer(text):
        refs.add(_normalise_name(m.group(1)))
    for m in _RUN_RE.finditer(text):
        refs.add(_normalise_name(m.group(1)))
    return refs


def build_graph(rel_paths: list[str]) -> dict[str, set[str]]:
    """Returns adjacency map {file_rel_path: {referenced_file_rel_path, ...}}.

    Only edges where BOTH endpoints are inside our input file set are kept —
    we don't track references to QAD standard files.
    """
    # Index our files by lowercase stem so refs can resolve
    stem_to_relpath: dict[str, str] = {
        Path(rp).stem.lower(): rp for rp in rel_paths
    }

    adj: dict[str, set[str]] = defaultdict(set)
    for rp in rel_paths:
        full = config.INPUT_DIR / rp
        refs = parse_references(full)
        for ref_stem in refs:
            target = stem_to_relpath.get(ref_stem)
            if target and target != rp:
                adj[rp].add(target)
                adj[target].add(rp)  # undirected
    return adj


def connected_components(adj: dict[str, set[str]], all_nodes: list[str]) -> list[list[str]]:
    """BFS to find connected components. Files with no edges are own component."""
    seen: set[str] = set()
    components: list[list[str]] = []
    for start in all_nodes:
        if start in seen:
            continue
        # BFS
        queue = [start]
        comp: list[str] = []
        while queue:
            node = queue.pop()
            if node in seen:
                continue
            seen.add(node)
            comp.append(node)
            for neighbour in adj.get(node, ()):
                if neighbour not in seen:
                    queue.append(neighbour)
        components.append(sorted(comp))
    return components


def analyse_includes(rel_paths: list[str]) -> dict:
    """Top-level entry. Returns a dict with:
        components_multi:  [[file, file, ...], ...]    only components with >= 2 files
        isolates:          [file, ...]                  files with no detected refs
        total_edges:       int
    """
    adj = build_graph(rel_paths)
    components = connected_components(adj, rel_paths)
    multi = [sorted(c) for c in components if len(c) >= 2]
    isolates = [c[0] for c in components if len(c) == 1]
    total_edges = sum(len(v) for v in adj.values()) // 2
    return {
        "components_multi": sorted(multi, key=lambda c: -len(c)),
        "isolates":         isolates,
        "total_edges":      total_edges,
    }


def print_summary(result: dict) -> None:
    multi = result["components_multi"]
    print(f"  Include/RUN edges found: {result['total_edges']}")
    print(f"  Multi-file connected components: {len(multi)}")
    for comp in multi[:10]:
        sample = ", ".join(Path(f).name for f in comp[:4])
        more = "" if len(comp) <= 4 else f" + {len(comp) - 4} more"
        print(f"    [{len(comp)} files] {sample}{more}")
    if len(multi) > 10:
        print(f"    ... and {len(multi) - 10} more components")
    print(f"  Isolated files (no cross-refs detected): {len(result['isolates'])}")
