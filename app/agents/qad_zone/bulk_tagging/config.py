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


# ── OpenAI model settings ───────────────────────────────────────────────────
# Re-use the same models the per-feature flow uses — gpt-4o-mini for per-file
# classification (Pass 2, Pass A) and gpt-4o for the global-view passes
# (Pass 1 / Pass 3 / Pass 4 / Pass 4.5).
OPENAI_MODEL_MINI = "gpt-4o-mini"
OPENAI_MODEL_PRO  = "gpt-4o"
OPENAI_MODEL      = OPENAI_MODEL_MINI  # back-compat default

# Concurrency caps for parallel LLM calls (Pass A and Pass 4)
PASS_A_CONCURRENCY = 5
PASS_4_CONCURRENCY = 5


# ── File scanning ──────────────────────────────────────────────────────────
SUPPORTED_EXTS = {".p", ".i", ".cls", ".w", ".df", ".xml", ".txt"}


# ── LLM call tuning ────────────────────────────────────────────────────────
MAX_CODE_CHARS     = 200_000       # full file in practice — only huge files truncated
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
