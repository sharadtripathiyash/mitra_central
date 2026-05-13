"""Bulk-tagging pipeline orchestrator (web-app variant).

The standalone version of this lived in ``main.py`` and printed to stdout.
This version exposes ``run_tagging_pipeline`` as an async coroutine that:

  • Reads inputs from ``config.INPUT_DIR`` (set per job by ``bulk_service``).
  • Streams progress through an ``on_status`` callback so the bulk_service
    can forward them as WS frames to the React UI.
  • Returns a structured result dict listing the modules + their files +
    descriptions + the customer glossary, ready for the doc-generation
    phase to consume.

The pipeline itself is unchanged from doc-gener-bulk — same Pass 0 through
Pass 4.5 logic, same prompts, same DB schema.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable

from . import config
from .db import db_open, fetch_all_tagged_with_module_desc, upsert_module
from .include_graph import analyse_includes
from .llm import normalise_module_tag
from .pass0_prefix import consolidate_parent_prefixes, extract_prefix_families
from .pass1_taxonomy import (
    pass1a_propose_taxonomy,
    pass1b_assign_files,
    pass1c_coverage_check,
)
from .pass2_tagging import run_pass2
from .pass3_review import apply_corrections, review_tags
from .pass4_coherence import apply_outlier_moves, verify_all_modules
from .pass4_5_merge import run_pass4_5
from .pass_a_glossary import derive_customer_glossary


logger = logging.getLogger(__name__)


# Progress callback signature — async or sync, takes (phase_id, message).
# phase_id is one of: pass0, pass0_5, pass0_7, pass_a, pass1a, pass1b, pass1c,
# pass2, pass3, pass4, pass4_5.
ProgressCb = Callable[[str, str], Awaitable[None] | None]


async def _emit(on_status: ProgressCb | None, phase: str, msg: str) -> None:
    if on_status is None:
        return
    try:
        result = on_status(phase, msg)
        if hasattr(result, "__await__"):
            await result
    except Exception:
        logger.exception("on_status callback raised; continuing pipeline")


async def run_tagging_pipeline(
    on_status: ProgressCb | None = None,
) -> dict[str, Any]:
    """Run the full 8-pass tagging pipeline against ``config.INPUT_DIR``.

    Returns::

        {
          "modules": [
            {
              "module_tag":  "RTDC",
              "module_desc": "Returnable / Non-Returnable Delivery Challan workflow",
              "files": [
                {
                  "file_path":   "us/xx/xxrtdc.p",   # relative to INPUT_DIR
                  "function_tag": "maintenance",
                  "description":  "Main RTDC entry program",
                  "reasoning":    "...",
                  "sha256":       "<hex>",
                },
                ...
              ],
            },
            ...
          ],
          "customer_glossary": {"RTDC": {"meaning": "...", "confidence": "...", "evidence": "..."}, ...},
          "total_files": 251,
          "total_modules": 22,
        }

    Caller is responsible for:
      • Setting ``config.init_for_job(input_dir, output_dir)`` first.
      • Routing the ``on_status`` callback to WS frames.

    All passes that fail mid-flight degrade gracefully (logged) so the
    pipeline always returns SOMETHING the caller can package.
    """
    if config.INPUT_DIR is None:
        raise RuntimeError(
            "bulk_tagging.config not initialised. "
            "Call config.init_for_job(input_dir, output_dir) before run_tagging_pipeline()."
        )

    # ── Discover files ──────────────────────────────────────────────────────
    candidates = sorted(
        p for p in config.INPUT_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() in config.SUPPORTED_EXTS
    )
    rel_paths = [
        str(p.relative_to(config.INPUT_DIR)).replace("\\", "/")
        for p in candidates
    ]
    await _emit(on_status, "discover", f"Discovered {len(candidates)} source file(s)")
    logger.info("Bulk tagging discover: %d files under %s", len(candidates), config.INPUT_DIR)

    if not candidates:
        return {
            "modules": [],
            "customer_glossary": {},
            "total_files": 0,
            "total_modules": 0,
        }

    conn = db_open()

    try:
        # ── PASS 0 ───────────────────────────────────────────────────────────
        await _emit(on_status, "pass0", "Pass 0 — extracting prefix families…")
        families, singletons = extract_prefix_families(rel_paths)
        logger.info("Pass 0: %d families, %d singletons", len(families), len(singletons))

        # ── PASS 0.5 ─────────────────────────────────────────────────────────
        await _emit(on_status, "pass0_5", "Pass 0.5 — consolidating parent prefixes…")
        merged_families, merge_log = consolidate_parent_prefixes(families)
        logger.info("Pass 0.5: %d → %d families", len(families), len(merged_families))

        # ── PASS 0.7 ─────────────────────────────────────────────────────────
        await _emit(on_status, "pass0_7", "Pass 0.7 — analysing include / RUN graph…")
        graph_result = analyse_includes(rel_paths)
        components = graph_result["components_multi"]
        logger.info("Pass 0.7: %d multi-file components", len(components))

        # ── PASS A — Customer glossary derivation ───────────────────────────
        await _emit(on_status, "pass_a",
                    f"Pass A — deriving customer glossary for {len(merged_families)} prefix families…")
        # The pipeline functions take a client arg for backward compat — our
        # shim ignores it, so pass None.
        customer_glossary = await derive_customer_glossary(None, conn, merged_families)
        logger.info("Pass A: %d glossary entries", len(customer_glossary))

        # ── PASS 1a ─────────────────────────────────────────────────────────
        await _emit(on_status, "pass1a", "Pass 1a — proposing taxonomy…")
        try:
            taxonomy = await pass1a_propose_taxonomy(
                None, merged_families, singletons,
                merge_log, components, customer_glossary,
            )
        except Exception as exc:
            logger.exception("Pass 1a failed; falling back to raw families: %s", exc)
            taxonomy = [
                {"module_tag": tag, "module_desc": ""}
                for tag in merged_families
            ]

        for m in taxonomy:
            tag = normalise_module_tag(m["module_tag"])
            if tag:
                upsert_module(conn, tag, m.get("module_desc", ""))
        conn.commit()

        # ── PASS 1b ─────────────────────────────────────────────────────────
        await _emit(on_status, "pass1b", f"Pass 1b — assigning {len(rel_paths)} files to modules…")
        try:
            file_to_module = await pass1b_assign_files(
                None, rel_paths, taxonomy, merged_families, customer_glossary,
            )
        except Exception as exc:
            logger.exception("Pass 1b failed: %s", exc)
            file_to_module = {}

        # ── PASS 1c ─────────────────────────────────────────────────────────
        unassigned = pass1c_coverage_check(rel_paths, file_to_module)
        if unassigned:
            await _emit(on_status, "pass1c",
                        f"Pass 1c — {len(unassigned)} unplaced file(s) routed to UNCLASSIFIED")
            upsert_module(
                conn, "UNCLASSIFIED",
                "Files Pass 1b couldn't place; Pass 2 reclassifies from code",
            )
            conn.commit()
            for fp in unassigned:
                file_to_module[fp] = "UNCLASSIFIED"
        else:
            await _emit(on_status, "pass1c", "Pass 1c — every file is assigned")

        # ── PASS 2 ──────────────────────────────────────────────────────────
        await _emit(on_status, "pass2",
                    f"Pass 2 — per-file LLM tagging ({len(candidates)} files)…")
        await run_pass2(None, conn, candidates, file_to_module)

        # ── PASS 3 ──────────────────────────────────────────────────────────
        tagged_count = conn.execute(
            "SELECT COUNT(*) AS n FROM tagged_files"
        ).fetchone()["n"]
        if tagged_count > 0:
            await _emit(on_status, "pass3",
                        f"Pass 3 — global review ({tagged_count} files in scope)…")
            try:
                corrections = await review_tags(None, conn)
                if corrections:
                    applied, noops = apply_corrections(conn, corrections)
                    logger.info("Pass 3: %d correction(s) applied, %d no-ops", applied, noops)
            except Exception as exc:
                logger.exception("Pass 3 failed: %s", exc)

        # ── PASS 4 ──────────────────────────────────────────────────────────
        await _emit(on_status, "pass4", "Pass 4 — per-module coherence verification…")
        try:
            coherence = await verify_all_modules(None, conn)
            if coherence:
                total_outliers = sum(len(r.get("outliers", [])) for r in coherence)
                if total_outliers:
                    moved = apply_outlier_moves(conn, coherence)
                    logger.info("Pass 4: %d outlier(s) flagged, %d moved", total_outliers, moved)
        except Exception as exc:
            logger.exception("Pass 4 failed: %s", exc)

        # ── PASS 4.5 ────────────────────────────────────────────────────────
        module_count = conn.execute(
            "SELECT COUNT(*) AS n FROM module_tags"
        ).fetchone()["n"]
        await _emit(on_status, "pass4_5",
                    f"Pass 4.5 — final merge sweep ({module_count} modules)…")
        try:
            merged, files_moved = await run_pass4_5(None, conn)
            if merged:
                logger.info("Pass 4.5: %d modules merged, %d files retagged",
                            merged, files_moved)
        except Exception as exc:
            logger.exception("Pass 4.5 failed: %s", exc)

        # ── Assemble final result ───────────────────────────────────────────
        await _emit(on_status, "finalise", "Assembling per-module file listings…")
        rows = fetch_all_tagged_with_module_desc(conn)
        by_mod: dict[str, list[dict]] = defaultdict(list)
        descriptions: dict[str, str] = {}
        for r in rows:
            by_mod[r["module_tag"]].append({
                "file_path":    r["file_path"],
                "function_tag": r["function_tag"],
                "description":  r["description"],
                "reasoning":    r["reasoning"],
                "sha256":       r["sha256"][:12],
            })
            descriptions[r["module_tag"]] = r["module_desc"] or ""

        modules = [
            {
                "module_tag":  tag,
                "module_desc": descriptions.get(tag, ""),
                "files":       sorted(files, key=lambda f: f["file_path"]),
            }
            for tag, files in sorted(by_mod.items())
        ]

        result = {
            "modules":           modules,
            "customer_glossary": customer_glossary,
            "total_files":       sum(len(m["files"]) for m in modules),
            "total_modules":     len(modules),
        }
        await _emit(on_status, "tagging_done",
                    f"Tagging complete: {result['total_files']} file(s) across "
                    f"{result['total_modules']} module(s)")
        return result
    finally:
        conn.close()
