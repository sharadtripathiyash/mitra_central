"""Sqlite storage — schema, connection, dedup, common queries.

Tables:
  tagged_files  — one row per source file with its module + function + reasoning
  module_tags   — one row per distinct module with its description

Idempotency:
  sha256 is the primary key on tagged_files → same file content = same row.
  Re-runs skip files whose sha is already present.
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from . import config
from .config import utc_now


SCHEMA = """
CREATE TABLE IF NOT EXISTS tagged_files (
  sha256       TEXT PRIMARY KEY,
  file_path    TEXT NOT NULL,
  module_tag   TEXT NOT NULL,
  function_tag TEXT NOT NULL,
  description  TEXT,
  reasoning    TEXT,
  tagged_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tagged_module ON tagged_files(module_tag);

CREATE TABLE IF NOT EXISTS module_tags (
  module_tag  TEXT PRIMARY KEY,
  module_desc TEXT,
  first_seen  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customer_glossary (
  prefix      TEXT PRIMARY KEY,
  meaning     TEXT NOT NULL,
  confidence  TEXT NOT NULL,
  evidence    TEXT,
  derived_at  TEXT NOT NULL
);
"""


def db_open() -> sqlite3.Connection:
    if config.OUTPUT_DIR is None or config.DB_PATH is None:
        raise RuntimeError(
            "bulk_tagging.config not initialised. "
            "Call config.init_for_job(input_dir, output_dir) first."
        )
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # Soft-upgrade for older DBs that pre-date the reasoning column
    try:
        conn.execute("ALTER TABLE tagged_files ADD COLUMN reasoning TEXT")
    except sqlite3.OperationalError:
        pass
    return conn


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def get_existing_module_tags(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        "SELECT module_tag, module_desc FROM module_tags ORDER BY module_tag"
    ).fetchall()
    return {r["module_tag"]: (r["module_desc"] or "") for r in rows}


def get_already_tagged_shas(conn: sqlite3.Connection) -> set[str]:
    return {row["sha256"] for row in conn.execute("SELECT sha256 FROM tagged_files")}


def upsert_module(conn: sqlite3.Connection, module_tag: str, module_desc: str) -> None:
    """Insert if new; only update description if existing one is empty."""
    now = utc_now()
    conn.execute(
        "INSERT OR IGNORE INTO module_tags (module_tag, module_desc, first_seen) "
        "VALUES (?, ?, ?)",
        (module_tag, module_desc, now),
    )
    if module_desc:
        conn.execute(
            "UPDATE module_tags SET module_desc = ? "
            " WHERE module_tag = ? AND (module_desc IS NULL OR module_desc = '')",
            (module_desc, module_tag),
        )


def upsert_tagged_file(conn: sqlite3.Connection, sha256: str, file_path: str,
                       module_tag: str, function_tag: str,
                       description: str, reasoning: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO tagged_files "
        "(sha256, file_path, module_tag, function_tag, description, reasoning, tagged_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (sha256, file_path, module_tag, function_tag, description, reasoning, utc_now()),
    )


def update_file_module(conn: sqlite3.Connection, file_path: str, module_tag: str) -> bool:
    """Used by Pass 3 corrections. Returns True if a row was actually updated."""
    result = conn.execute(
        "UPDATE tagged_files SET module_tag = ? WHERE file_path = ?",
        (module_tag, file_path),
    )
    return result.rowcount > 0


def cleanup_orphan_modules(conn: sqlite3.Connection) -> int:
    """Remove rows from module_tags that have no tagged_files entries."""
    cur = conn.execute("""
        DELETE FROM module_tags
         WHERE module_tag NOT IN (SELECT DISTINCT module_tag FROM tagged_files)
    """)
    return cur.rowcount


# ── Customer glossary (Pass A) ──────────────────────────────────────────────

def upsert_glossary_entry(conn: sqlite3.Connection, prefix: str, meaning: str,
                          confidence: str, evidence: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO customer_glossary "
        "(prefix, meaning, confidence, evidence, derived_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (prefix.upper(), meaning, confidence, evidence, utc_now()),
    )


def get_customer_glossary(conn: sqlite3.Connection) -> dict[str, dict]:
    """Returns {PREFIX: {meaning, confidence, evidence}}."""
    rows = conn.execute(
        "SELECT prefix, meaning, confidence, evidence FROM customer_glossary"
    ).fetchall()
    return {
        r["prefix"]: {
            "meaning":    r["meaning"],
            "confidence": r["confidence"],
            "evidence":   r["evidence"] or "",
        }
        for r in rows
    }


def get_known_glossary_prefixes(conn: sqlite3.Connection) -> set[str]:
    return {r["prefix"] for r in conn.execute("SELECT prefix FROM customer_glossary")}


def fetch_all_tagged_with_module_desc(conn: sqlite3.Connection) -> list[dict]:
    """Used by Pass 3 and the output writer."""
    rows = conn.execute("""
        SELECT tf.sha256, tf.file_path, tf.module_tag, tf.function_tag,
               tf.description, tf.reasoning, tf.tagged_at, mt.module_desc
          FROM tagged_files tf
          LEFT JOIN module_tags mt ON tf.module_tag = mt.module_tag
         ORDER BY tf.module_tag, tf.file_path
    """).fetchall()
    return [dict(r) for r in rows]
