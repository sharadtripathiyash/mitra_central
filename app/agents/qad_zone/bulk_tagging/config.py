"""Bulk-tagging pipeline configuration (web-app variant).

In the standalone doc-gener-bulk tool, INPUT_DIR / OUTPUT_DIR / DB_PATH were
module-level constants pointing at hard-coded folders. In the Mitra Central
web app each bulk-upload job has its OWN job folder, so we expose a setter
``init_for_job(input_dir, output_dir)`` that callers (the bulk_service) use
to point the pipeline at the right per-job paths before kicking off Pass 0.

The pipeline modules reference these as ``config.INPUT_DIR`` etc. — they look
the value up at call time, NOT import time, so the setter works.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


# ── Per-job paths (set by ``init_for_job`` before each pipeline run) ────────
# Default to None so attempts to use them without initialisation fail loudly
# rather than silently writing into the wrong place.
INPUT_DIR:   Path | None = None
OUTPUT_DIR:  Path | None = None
DB_PATH:     Path | None = None
MODULES_DIR: Path | None = None


def init_for_job(input_dir: Path, output_dir: Path) -> None:
    """Point the pipeline at the per-job input + output folders.

    Called by ``bulk_service`` once per upload, before invoking the
    orchestrator. Mutates module-level paths; safe in this codebase because
    each bulk-upload job runs serially within a single WS connection.
    """
    global INPUT_DIR, OUTPUT_DIR, DB_PATH, MODULES_DIR
    INPUT_DIR   = Path(input_dir)
    OUTPUT_DIR  = Path(output_dir)
    DB_PATH     = OUTPUT_DIR / "tags.db"
    MODULES_DIR = OUTPUT_DIR / "modules"


# Concurrency caps for parallel LLM calls (Pass A and Pass 4)
# Per-pass MODEL choices now live in ../llm_models.py — keep config.py focused
# on tuning constants (concurrency, timeouts, code-budget caps) rather than
# model names.
PASS_A_CONCURRENCY = 5
PASS_4_CONCURRENCY = 5


# ── File scanning ──────────────────────────────────────────────────────────
SUPPORTED_EXTS = {".p", ".i", ".cls", ".w", ".df", ".xml", ".txt"}


# ── LLM call tuning ────────────────────────────────────────────────────────
# 350K chars ≈ 90K tokens. Fits comfortably in:
#   - GPT-5.5             (400K context)
#   - GPT-5.4 Mini        (400K context)
#   - Claude Opus 4.7     (200K context, accommodates ~50K tokens of code +
#                          prompt overhead)
# Previously 200K — silently truncated big modules like INV (18 files).
# With the upgraded models the truncation is no longer needed.
MAX_CODE_CHARS     = 350_000
INTER_CALL_DELAY   = 0.3           # seconds between Pass 2 calls (politeness)
MAX_RETRY_ATTEMPTS = 5


# ── Pass 0 prefix-extraction tuning ────────────────────────────────────────
PREFIX_MIN_LEN     = 3             # minimum prefix length to form a family
PREFIX_MIN_FAMILY  = 2             # minimum family size (files sharing a prefix)


# ── Function tags (only hardcoded enum — module tags are LLM-derived) ──────
FUNCTION_TAGS = [
    "maintenance", "inquiry", "report", "report-print",
    "approval", "rejection", "cancellation", "notification",
    "trigger", "batch", "api-integration",
    "include-helper", "utility", "data-load", "validation",
    "other",
]


# ── Helpers ────────────────────────────────────────────────────────────────

def utc_now() -> str:
    """ISO-8601 UTC timestamp with trailing 'Z' for sqlite + JSON."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
