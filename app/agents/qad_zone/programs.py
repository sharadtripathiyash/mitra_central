"""QAD custom program file loader.

Loads Progress 4GL source files (.p, .i, .xml, .df, etc.) from data/qad_programs/
organised by module folders (e.g., e-invoice/, doa/, shared/).

ZIP archives inside module folders are automatically extracted and their
text-based contents are included in the code load.

The AI decides which module is relevant based on the user question — NOT
based on the user's login role selection.
"""
from __future__ import annotations

import logging
import os
import zipfile
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

PROGRAMS_DIR = Path("data/qad_programs")

# All text-based file types to read (including .df for schema definitions)
SUPPORTED_EXTENSIONS = {".p", ".i", ".xml", ".cls", ".w", ".df", ".txt", ".r"}


def list_modules() -> list[str]:
    """Return list of available module folders."""
    if not PROGRAMS_DIR.exists():
        return []
    return sorted(
        d.name for d in PROGRAMS_DIR.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )


def _iter_zip_files(zip_path: Path) -> list[dict]:
    """Extract text files from a ZIP and return as {filename, content, source_zip} list."""
    results = []
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for entry in zf.infolist():
                if entry.is_dir():
                    continue
                ext = Path(entry.filename).suffix.lower()
                if ext not in SUPPORTED_EXTENSIONS:
                    continue
                try:
                    raw = zf.read(entry.filename)
                    content = raw.decode("utf-8", errors="replace")
                    results.append({
                        "filename": Path(entry.filename).name,
                        "full_path_in_zip": entry.filename,
                        "content": content,
                        "source_zip": zip_path.name,
                        "size_bytes": len(raw),
                    })
                except Exception as exc:
                    logger.warning("Failed to read %s from %s: %s", entry.filename, zip_path, exc)
    except Exception as exc:
        logger.warning("Failed to open ZIP %s: %s", zip_path, exc)
    return results


def list_programs(module: str | None = None) -> list[dict]:
    """List all programs, optionally filtered by module.

    Returns list of {module, filename, path, size_bytes, from_zip (optional)}.
    ZIP files are listed as archives with an entry per inner file.
    """
    programs = []
    base = PROGRAMS_DIR / module if module else PROGRAMS_DIR
    if not base.exists():
        return programs

    for root, _, files in os.walk(base):
        for f in files:
            fp = Path(root) / f
            ext = fp.suffix.lower()

            rel = fp.relative_to(PROGRAMS_DIR)
            parts = rel.parts
            mod = parts[0] if len(parts) > 1 else "shared"

            if ext == ".zip":
                # List inner files without extracting
                try:
                    with zipfile.ZipFile(fp, "r") as zf:
                        for entry in zf.infolist():
                            if entry.is_dir():
                                continue
                            inner_ext = Path(entry.filename).suffix.lower()
                            if inner_ext in SUPPORTED_EXTENSIONS:
                                programs.append({
                                    "module": mod,
                                    "filename": Path(entry.filename).name,
                                    "path": str(fp),
                                    "size_bytes": entry.file_size,
                                    "from_zip": fp.name,
                                    "zip_inner_path": entry.filename,
                                })
                except Exception as exc:
                    logger.warning("Cannot list ZIP %s: %s", fp, exc)

            elif ext in SUPPORTED_EXTENSIONS:
                programs.append({
                    "module": mod,
                    "filename": f,
                    "path": str(fp),
                    "size_bytes": fp.stat().st_size,
                })

    return programs


def load_module_code(module: str, max_chars: int = 120_000) -> str:
    """Load all source code from a module folder, concatenated.

    Handles regular files AND extracts content from ZIP archives.
    Truncates to max_chars to fit LLM context windows.
    """
    base = PROGRAMS_DIR / module if module else PROGRAMS_DIR
    if not base.exists():
        # Fallback to shared
        base = PROGRAMS_DIR / "shared"
        if not base.exists():
            return "No custom program files found for this module."

    parts = []
    total = 0

    def _add_chunk(header: str, content: str) -> bool:
        nonlocal total
        chunk = header + content
        if total + len(chunk) > max_chars:
            remaining = max_chars - total
            if remaining > 200:
                parts.append(chunk[:remaining] + "\n// ... TRUNCATED ...")
            return False  # stop loading
        parts.append(chunk)
        total += len(chunk)
        return True

    for root, _, files in os.walk(base):
        for f in sorted(files):
            fp = Path(root) / f
            ext = fp.suffix.lower()

            if ext == ".zip":
                # Extract and load text files from inside the ZIP
                inner_files = _iter_zip_files(fp)
                for inner in inner_files:
                    header = (
                        f"\n{'='*60}\n"
                        f"// FILE: {module}/{inner['filename']} "
                        f"(from {inner['source_zip']})\n"
                        f"{'='*60}\n"
                    )
                    if not _add_chunk(header, inner["content"]):
                        return "\n".join(parts)

            elif ext in SUPPORTED_EXTENSIONS:
                try:
                    content = fp.read_text(encoding="utf-8", errors="replace")
                    header = (
                        f"\n{'='*60}\n"
                        f"// FILE: {module}/{fp.name}\n"
                        f"{'='*60}\n"
                    )
                    if not _add_chunk(header, content):
                        return "\n".join(parts)
                except Exception as exc:
                    logger.warning("Failed to read %s: %s", fp, exc)

    return "\n".join(parts) if parts else "No custom program files found for this module."


def load_all_code_summary() -> str:
    """Load a brief summary of ALL custom programs across all modules.

    Lists each module, its files (including files inside ZIPs), and sizes.
    """
    modules = list_modules()
    lines = ["CUSTOM QAD PROGRAMS INVENTORY:\n"]
    for mod in modules:
        programs = list_programs(mod)
        zip_count = sum(1 for p in programs if p.get("from_zip"))
        direct_count = len(programs) - zip_count
        lines.append(f"\nModule: {mod} ({len(programs)} files — {direct_count} direct, {zip_count} in ZIPs)")
        for p in programs:
            size_kb = p["size_bytes"] / 1024
            zip_note = f" [in {p['from_zip']}]" if p.get("from_zip") else ""
            lines.append(f"  - {p['filename']} ({size_kb:.1f} KB){zip_note}")
    return "\n".join(lines) if len(lines) > 1 else "No custom programs found."