"""Pass 0 — Deterministic prefix-family extraction. ZERO LLM calls.

QAD's customisation naming convention is `xx<module><function>`. We exploit
this with a greedy longest-prefix grouping algorithm:

  1. Strip the leading 'xx' from each filename (case-insensitive).
  2. Take the leading alphabetic run as the file's "core".
  3. Group files by progressively shorter prefixes (longest first).
  4. Any group with >= PREFIX_MIN_FAMILY files becomes a "family".
  5. Files that don't fit any family are returned as singletons for Pass 1
     to classify.

Examples:
  xxdoaapprmt.p, xxdoaproc.p, xxdoanotify.p  →  DOA family
  xxinvappr.p × 8                            →  INVAPPR family
  xxcustapi.p                                →  singleton (no other xxcust*)
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .config import PREFIX_MIN_FAMILY, PREFIX_MIN_LEN


def alpha_core(s: str) -> str:
    """Return the leading alphabetic chars of `s`, lower-cased.

    'doaapprmt'      → 'doaapprmt'
    'invappr.p'      → 'invappr'
    'scpfinupdwt_x'  → 'scpfinupdwt'
    'crjson3do'      → 'crjson'
    """
    out = []
    for c in s:
        if c.isalpha():
            out.append(c)
        else:
            break
    return "".join(out).lower()


def file_core(file_path: str) -> str:
    """Strip directory + 'xx' prefix from a file path; return the alphabetic core."""
    stem = Path(file_path).stem.lower()
    if stem.startswith("xx"):
        stem = stem[2:]
    return alpha_core(stem)


def extract_prefix_families(
    file_paths: list[str],
    min_prefix: int = PREFIX_MIN_LEN,
    min_family_size: int = PREFIX_MIN_FAMILY,
) -> tuple[dict[str, list[str]], list[str]]:
    """Greedy longest-prefix grouping.

    Returns:
      (families, singletons)
        families:   {PREFIX_UPPERCASE: [file_paths]}, each ≥ min_family_size
        singletons: [file_paths] that don't fit any family
    """
    # Step 1: precompute each file's alphabetic core
    cores: dict[str, str] = {fp: file_core(fp) for fp in file_paths}

    # Step 2: greedy longest-prefix assignment
    assigned: dict[str, str] = {}
    max_core = max((len(c) for c in cores.values()), default=0)

    for length in range(max_core, min_prefix - 1, -1):
        groups: dict[str, list[str]] = defaultdict(list)
        for fp, core in cores.items():
            if fp in assigned:
                continue
            if len(core) < length:
                continue
            groups[core[:length]].append(fp)
        for prefix, files in groups.items():
            if len(files) >= min_family_size:
                tag = prefix.upper()
                for fp in files:
                    assigned[fp] = tag

    # Step 3: split into families / singletons (preserve file order)
    families: dict[str, list[str]] = defaultdict(list)
    singletons: list[str] = []
    for fp in file_paths:
        tag = assigned.get(fp)
        if tag is not None:
            families[tag].append(fp)
        else:
            singletons.append(fp)

    # Sort families by size descending (more useful for display)
    sorted_families = dict(
        sorted(families.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    )
    return sorted_families, singletons


def consolidate_parent_prefixes(
    families: dict[str, list[str]],
    min_parent_size: int = 2,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Pass 0.5 — merge sub-prefix families into their parent.

    If family B's prefix starts with family A's prefix (B is a sub-family of
    A), AND A has at least `min_parent_size` files of its own, merge B's
    files into A.

    Example:
      Input families:  DOA (5 files), DOAAPPR (3 files), DOARULE (3 files)
      Output:          DOA (11 files), with merge_log = {DOA: [DOAAPPR, DOARULE]}

    Returns:
      (merged_families, merge_log)
        merge_log records {parent_prefix: [absorbed_child_prefixes...]} for
        diagnostic display.
    """
    # Sort by prefix length ascending so shorter prefixes are considered as parents first
    prefixes_by_len = sorted(families.keys(), key=lambda p: (len(p), p))
    merged: dict[str, list[str]] = {p: list(files) for p, files in families.items()}
    absorbed: set[str] = set()
    merge_log: dict[str, list[str]] = defaultdict(list)

    for parent in prefixes_by_len:
        if parent in absorbed:
            continue
        if len(merged.get(parent, [])) < min_parent_size:
            continue
        # Look for all longer prefixes that start with parent
        for child in list(merged.keys()):
            if child == parent or child in absorbed:
                continue
            if len(child) <= len(parent):
                continue
            if not child.startswith(parent):
                continue
            # Merge child into parent
            merged[parent].extend(merged[child])
            del merged[child]
            absorbed.add(child)
            merge_log[parent].append(child)

    # Sort consolidated families by size desc
    consolidated = dict(
        sorted(merged.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    )
    return consolidated, dict(merge_log)


def print_summary(families: dict[str, list[str]], singletons: list[str]) -> None:
    """Pretty-print the Pass 0 grouping result to stdout."""
    print(f"  Pre-grouped families: {len(families)}")
    for tag, files in families.items():
        sample = ", ".join(Path(f).name for f in files[:3])
        more = "" if len(files) <= 3 else f" + {len(files) - 3} more"
        print(f"    {tag:<10} ({len(files)} files): {sample}{more}")
    if singletons:
        print(f"  Singletons (need LLM classification): {len(singletons)}")
        for fp in singletons[:10]:
            print(f"    - {Path(fp).name}")
        if len(singletons) > 10:
            print(f"    ... and {len(singletons) - 10} more")
