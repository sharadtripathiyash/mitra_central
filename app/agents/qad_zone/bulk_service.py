"""Bulk-upload service handler (QAD-Zone mode == ``bulk-upload``).

Flow
----
1. Receive a base64-encoded ZIP from the WS payload.
2. Create a per-job folder ``app/static/bulk-jobs/<job_id>/`` with::

     input/                — extracted customer files (filtered to QAD source types)
     tags.db               — sqlite from the 8-pass tagging pipeline
     modules/<TAG - Meaning>/
                           — per-module folder, holds doc + blueprint + metadata
     <job_id>_modules.zip  — final downloadable bundle

3. Stream WS progress frames:

     {"type": "bulk_progress", "data": {"phase": "...", "message": "..."}}
     {"type": "bulk_module_ready", "data": {
         "module_tag": "RTDC",
         "title": "...",
         "doc_url": "/static/bulk-jobs/.../docs/RTDC - .../RTDC - ... - documentation.docx",
         "blueprint_url": "/static/bulk-jobs/.../docs/.../...blueprint.docx",
         "errors": [...]
     }}
     {"type": "bulk_done", "data": {
         "job_id": "...",
         "zip_url": "/static/bulk-jobs/<job_id>/<job_id>_modules.zip",
         "total_files": 251,
         "total_modules": 22,
         "modules": [...],
     }}

Re-using the per-feature ``doc_generator.generate_document`` and
``blueprint_doc_generator.generate_blueprint_document`` means the .docx
artefacts land in ``app/static/downloads/`` initially (their hard-coded
output). The bulk service then MOVES each docx into the per-module
sub-folder under ``app/static/bulk-jobs/<job_id>/modules/`` so the final
ZIP layout matches what the user asked for: one folder per module, both
docs inside.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import re
import shutil
import uuid
import zipfile
from pathlib import Path

from fastapi import WebSocket

from app.core.session import append_turn
from app.core.ws import send_done, send_error, send_frame, send_status

from app.agents.qad_zone.bulk_module_pipeline import (
    generate_doc_and_blueprint_for_module,
)
from app.agents.qad_zone.bulk_tagging import config as bulk_config
from app.agents.qad_zone.bulk_tagging.orchestrator import run_tagging_pipeline


logger = logging.getLogger(__name__)

AGENT_KEY = "qadzone"

# Where per-job folders live. Static-served at /static/bulk-jobs/<job_id>/.
BULK_JOBS_ROOT = Path("app/static/bulk-jobs")

# The per-feature doc generators hard-code their output to app/static/downloads/.
# Bulk uploads will MOVE the docx into the per-module folder under bulk-jobs/.
DOWNLOADS_DIR = Path("app/static/downloads")

# Source file types we'll keep when extracting the customer's ZIP.
_SUPPORTED_EXTS = {".p", ".i", ".cls", ".w", ".df", ".xml", ".txt"}

# Safety caps for the uploaded ZIP — protects the server from runaway uploads.
_MAX_ZIP_BYTES   = 200 * 1024 * 1024   # 200 MB raw zip
_MAX_FILES       = 5000                 # max files extracted from inside
_MAX_TOTAL_BYTES = 500 * 1024 * 1024    # max total uncompressed size

# Concurrency cap for per-module doc generation. Each module triggers
# 3 LLM calls (Pass 1 + Pass 2/Summary parallel + Pass 3) — too many in
# flight at once will swamp rate limits.
# Sequential by default. Per-module Pass 1 (facts) + Pass 2 (doc gen) each
# run Opus 4.7 with xhigh adaptive thinking — that's ~3-6 minutes per call
# on a heavyweight merged module. Running two in parallel doesn't make the
# overall batch faster (Opus is the bottleneck either way) and it makes
# transient failures (timeouts, rate-limits) harder to reason about.
# Sequential is the more predictable choice. Bump to 2 only after measuring.
_MODULE_DOC_CONCURRENCY = 1


# ── Helpers ─────────────────────────────────────────────────────────────────


def _sanitise_folder_name(raw: str, *, max_len: int = 60) -> str:
    """Make ``raw`` safe for use as a folder name on Windows + POSIX.

    Default ``max_len=60`` chosen so the full on-disk path stays under
    Windows' ~260-char MAX_PATH limit even with deeply-nested job folders.
    A typical bulk job's full path looks like::

        D:\\...\\app\\static\\bulk-jobs\\<job_id>\\modules\\<folder>\\<file>.docx
        ~95 chars                                          ~60 chars  ~70 chars

    Total ~225 chars — safely under 260. Names longer than 60 chars are
    truncated; the full description still lives in the module's metadata.json.
    """
    if not raw:
        return "module"
    # Strip / replace illegal characters
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:max_len] or "module"


def _new_job_id() -> str:
    return uuid.uuid4().hex[:12]


def _extract_zip_to_dir(raw_bytes: bytes, target: Path) -> tuple[int, int]:
    """Extract supported source files from a ZIP into ``target``.

    Returns ``(files_extracted, total_bytes)``.

    Skips entries whose extension isn't in ``_SUPPORTED_EXTS``. Enforces
    file-count and total-size caps. Path traversal protection: rejects
    any entry that would resolve outside ``target``.
    """
    target.mkdir(parents=True, exist_ok=True)

    files_extracted = 0
    total_bytes     = 0

    with zipfile.ZipFile(io.BytesIO(raw_bytes), "r") as zf:
        for entry in zf.infolist():
            if entry.is_dir():
                continue
            if files_extracted >= _MAX_FILES:
                logger.warning("ZIP extraction hit _MAX_FILES cap (%d)", _MAX_FILES)
                break
            if total_bytes >= _MAX_TOTAL_BYTES:
                logger.warning("ZIP extraction hit _MAX_TOTAL_BYTES cap")
                break

            inner_path = Path(entry.filename)
            inner_ext  = inner_path.suffix.lower()
            if inner_ext not in _SUPPORTED_EXTS:
                continue

            # Path-traversal guard
            dest = (target / inner_path).resolve()
            try:
                dest.relative_to(target.resolve())
            except ValueError:
                logger.warning("Skipping zip entry with traversal: %s", entry.filename)
                continue

            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                data = zf.read(entry.filename)
            except Exception as exc:
                logger.warning("Skipping unreadable zip entry %s: %s", entry.filename, exc)
                continue

            dest.write_bytes(data)
            files_extracted += 1
            total_bytes += len(data)

    return files_extracted, total_bytes


def _collect_zip_payload(uploaded_files: list[dict]) -> bytes | None:
    """Find the first ``.zip`` entry in the uploaded_files payload and
    return its decoded bytes. Returns None if no ZIP was uploaded.

    Bulk-upload mode REQUIRES a ZIP — individual files won't trigger the
    pipeline (the per-feature flow already handles those).
    """
    for entry in uploaded_files:
        name = (entry.get("name") or "").lower()
        if not name.endswith(".zip"):
            continue
        data_b64 = entry.get("data") or ""
        if not data_b64:
            continue
        try:
            return base64.b64decode(data_b64)
        except Exception as exc:
            logger.warning("Failed to decode uploaded ZIP %s: %s", entry.get("name"), exc)
    return None


def _move_doc_into_module_folder(
    abs_url: str | None,
    module_folder: Path,
    suffix: str,
    pretty_label: str,
) -> str | None:
    """Move a docx from ``app/static/downloads/<file>`` into the per-module
    folder under bulk-jobs/, renamed to ``<pretty_label> - <suffix>.docx``.

    ``abs_url`` looks like ``/static/downloads/Foo_a1b2c3.docx`` — derived
    from ``generate_document``/``generate_blueprint_document``. We turn it
    back into a filesystem path, move the file, and return the NEW URL
    pointing under /static/bulk-jobs/.
    """
    if not abs_url:
        return None
    if not abs_url.startswith("/static/downloads/"):
        logger.warning("Unexpected doc URL shape (won't move): %s", abs_url)
        return abs_url

    src_name = abs_url.rsplit("/", 1)[-1]
    src_path = DOWNLOADS_DIR / src_name
    if not src_path.exists():
        logger.warning("Doc file missing on disk (won't move): %s", src_path)
        return abs_url

    module_folder.mkdir(parents=True, exist_ok=True)
    # Match the folder-name truncation so file paths stay under Windows'
    # 260-char limit. Folder + file each ≤ 60 chars after sanitisation.
    safe_label = _sanitise_folder_name(pretty_label, max_len=60)
    new_name = f"{safe_label} - {suffix}.docx"
    dst_path = module_folder / new_name

    # If the dst already exists (re-run on same job), overwrite atomically.
    if dst_path.exists():
        try:
            dst_path.unlink()
        except Exception:
            pass

    shutil.move(str(src_path), str(dst_path))

    # Compute the public URL from the path relative to "app/" (FastAPI mounts
    # app/static at /static). We resolve BOTH sides to absolute paths because
    # `dst_path` is already absolute (came from `(BULK_JOBS_ROOT/job_id).resolve()`
    # in `handle_bulk_upload`) and `Path("app")` is relative — Path.relative_to
    # requires the same anchor on both sides.
    rel = dst_path.resolve().relative_to(Path("app").resolve()).as_posix()
    return "/" + rel  # e.g. "/static/bulk-jobs/<job>/modules/<TAG>/<TAG> - documentation.docx"


def _build_result_zip(job_dir: Path, job_id: str) -> Path:
    """Bundle the per-module docs folder + the summary into one ZIP.

    Layout inside the zip mirrors the on-disk folder layout::

        modules/<TAG - Meaning>/<TAG - Meaning> - documentation.docx
        modules/<TAG - Meaning>/<TAG - Meaning> - migration_blueprint.docx
        _summary.json
    """
    zip_path = job_dir / f"{job_id}_modules.zip"
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        modules_dir = job_dir / "modules"
        if modules_dir.is_dir():
            for p in modules_dir.rglob("*"):
                if p.is_file():
                    zf.write(p, p.relative_to(job_dir).as_posix())
        summary_path = job_dir / "_summary.json"
        if summary_path.exists():
            zf.write(summary_path, "_summary.json")

    return zip_path


# ── Top-level WS handler ────────────────────────────────────────────────────


async def handle_bulk_upload(
    ws: WebSocket,
    session_id: str,
    uploaded_files: list[dict] | None,
) -> None:
    """Top-level handler for ``mode == "bulk-upload"``.

    Drives:  receive ZIP → extract → tag pipeline → per-module doc generation
             → assemble ZIP → final bulk_done frame.

    Emits progress through ``send_status`` and custom frames so the React
    UI's ``LiveBulkPipelineCard`` can render live phase + per-module rows.
    """
    if not uploaded_files:
        await send_error(ws, "Bulk upload requires a .zip containing your customisation code.")
        return

    zip_bytes = _collect_zip_payload(uploaded_files)
    if zip_bytes is None:
        await send_error(ws, "No .zip file found in the upload. Bulk Upload expects a single .zip.")
        return
    if len(zip_bytes) > _MAX_ZIP_BYTES:
        await send_error(
            ws,
            f"Uploaded ZIP is {len(zip_bytes)//1024//1024} MB — bulk upload is capped at "
            f"{_MAX_ZIP_BYTES//1024//1024} MB.",
        )
        return

    # ── Provision per-job folder ────────────────────────────────────────────
    job_id  = _new_job_id()
    job_dir = (BULK_JOBS_ROOT / job_id).resolve()
    job_dir.mkdir(parents=True, exist_ok=True)
    input_dir   = job_dir / "input"
    modules_dir = job_dir / "modules"
    input_dir.mkdir(parents=True, exist_ok=True)
    modules_dir.mkdir(parents=True, exist_ok=True)

    await send_frame(ws, "bulk_progress", {
        "phase":   "init",
        "message": f"Job {job_id} — extracting ZIP…",
    })
    logger.info("Bulk upload job %s starting", job_id)

    # ── Extract ZIP ──────────────────────────────────────────────────────────
    try:
        files_extracted, total_bytes = _extract_zip_to_dir(zip_bytes, input_dir)
    except zipfile.BadZipFile:
        await send_error(ws, "Uploaded file is not a valid ZIP archive.")
        return
    except Exception as exc:
        logger.exception("ZIP extraction failed for job %s", job_id)
        await send_error(ws, f"Failed to read the uploaded ZIP: {exc}")
        return

    if files_extracted == 0:
        await send_error(
            ws,
            "The ZIP didn't contain any supported source files. "
            "Bulk Upload accepts: .p .i .cls .w .df .xml .txt inside a .zip.",
        )
        return

    await send_frame(ws, "bulk_progress", {
        "phase":   "extracted",
        "message": f"Extracted {files_extracted} source file(s) "
                   f"({total_bytes // 1024} KB) into per-job storage.",
    })

    # ── Run the 8-pass tagging pipeline ─────────────────────────────────────
    bulk_config.init_for_job(input_dir=input_dir, output_dir=job_dir)

    async def _on_status(phase: str, msg: str) -> None:
        await send_frame(ws, "bulk_progress", {"phase": phase, "message": msg})

    try:
        tagging_result = await run_tagging_pipeline(on_status=_on_status)
    except Exception as exc:
        logger.exception("Tagging pipeline failed for job %s", job_id)
        await send_error(ws, f"Tagging pipeline failed: {exc}")
        return

    modules = tagging_result["modules"]
    customer_glossary = tagging_result["customer_glossary"]
    total_files = tagging_result["total_files"]

    if not modules:
        await send_error(ws, "Tagging produced no modules — nothing to document.")
        return

    await send_frame(ws, "bulk_progress", {
        "phase":   "tagging_complete",
        "message": f"Tagging complete: {total_files} file(s) across {len(modules)} module(s). "
                   f"Generating documentation…",
        "modules": [
            {"module_tag": m["module_tag"],
             "module_desc": m["module_desc"],
             "file_count": len(m["files"])}
            for m in modules
        ],
    })

    # ── Per-module doc + blueprint generation ───────────────────────────────
    sem = asyncio.Semaphore(_MODULE_DOC_CONCURRENCY)
    completed_modules: list[dict] = []

    async def _run_one(idx: int, m: dict) -> None:
        async with sem:
            module_tag = m["module_tag"]
            module_desc = m["module_desc"] or module_tag

            await send_frame(ws, "bulk_progress", {
                "phase":   "module_doc_start",
                "message": f"[{idx + 1}/{len(modules)}] Generating doc + blueprint for {module_tag}…",
                "module_tag": module_tag,
            })

            try:
                result = await generate_doc_and_blueprint_for_module(
                    module_tag, module_desc, m["files"], input_dir,
                )
            except Exception as exc:
                logger.exception("Module %s: doc generation crashed", module_tag)
                result = {
                    "module_tag":    module_tag,
                    "title":         module_desc,
                    "doc_url":       None,
                    "blueprint_url": None,
                    "summary_data":  None,
                    "errors":        [f"crashed: {exc}"],
                }

            # Pick the folder label: "<TAG> - <Meaning>" (sanitised)
            label = f"{module_tag} - {module_desc}" if module_desc else module_tag
            # 60-char cap keeps the full on-disk path safely under Windows'
            # 260-char MAX_PATH limit. Full description survives in metadata.json.
            folder_label = _sanitise_folder_name(label, max_len=60)
            module_folder = modules_dir / folder_label

            # Move the rendered docs into the per-module folder + rename.
            # IMPORTANT: a failure here (e.g. unexpected URL shape, missing source
            # file, file-system permission issue) must NOT abort the whole job —
            # we capture it in this module's errors list and keep the URL as None
            # so the row shows up in the UI with a clear ⚠ icon, while the rest
            # of the modules continue to process.
            new_doc_url: str | None = None
            new_blueprint_url: str | None = None
            try:
                new_doc_url = _move_doc_into_module_folder(
                    result["doc_url"], module_folder, "documentation", label
                )
            except Exception as exc:
                logger.exception("Module %s: move (doc) failed", module_tag)
                result["errors"] = list(result.get("errors") or []) + [
                    f"move (doc) failed: {exc}"
                ]
            try:
                new_blueprint_url = _move_doc_into_module_folder(
                    result["blueprint_url"], module_folder, "migration_blueprint", label
                )
            except Exception as exc:
                logger.exception("Module %s: move (blueprint) failed", module_tag)
                result["errors"] = list(result.get("errors") or []) + [
                    f"move (blueprint) failed: {exc}"
                ]

            # Drop a small metadata.json next to the docs
            metadata = {
                "module_tag":     module_tag,
                "module_desc":    module_desc,
                "title":          result["title"],
                "file_count":     len(m["files"]),
                "files":          m["files"],
                "summary_data":   result["summary_data"],
                "errors":         result["errors"],
                "doc_url":        new_doc_url,
                "blueprint_url":  new_blueprint_url,
            }
            try:
                module_folder.mkdir(parents=True, exist_ok=True)
                (module_folder / f"{folder_label} - metadata.json").write_text(
                    _safe_json_dumps(metadata),
                    encoding="utf-8",
                )
            except Exception as exc:
                logger.warning("Module %s: failed to write metadata.json: %s", module_tag, exc)

            entry = {
                "module_tag":    module_tag,
                "title":         result["title"],
                "module_desc":   module_desc,
                "file_count":    len(m["files"]),
                "doc_url":       new_doc_url,
                "blueprint_url": new_blueprint_url,
                "errors":        result["errors"],
            }
            completed_modules.append(entry)

            await send_frame(ws, "bulk_module_ready", entry)

    # `return_exceptions=True` — defence in depth. The per-module work is now
    # also internally wrapped, but if a task does throw unexpectedly we'd rather
    # collect the failure and continue than abort the whole batch and waste
    # everything already done.
    task_results = await asyncio.gather(
        *[_run_one(i, m) for i, m in enumerate(modules)],
        return_exceptions=True,
    )
    for i, r in enumerate(task_results):
        if isinstance(r, Exception):
            logger.exception(
                "Module %s: per-module task crashed unexpectedly: %s",
                modules[i]["module_tag"], r,
            )

    # ── Write the cross-module summary JSON ─────────────────────────────────
    summary = {
        "job_id":         job_id,
        "total_files":    total_files,
        "total_modules":  len(modules),
        "customer_glossary": customer_glossary,
        "modules":        sorted(completed_modules, key=lambda x: x["module_tag"]),
    }
    try:
        (job_dir / "_summary.json").write_text(
            _safe_json_dumps(summary),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Job %s: failed to write _summary.json: %s", job_id, exc)

    # ── Assemble final ZIP ──────────────────────────────────────────────────
    await send_frame(ws, "bulk_progress", {
        "phase":   "packaging",
        "message": "Assembling final ZIP bundle…",
    })
    try:
        zip_path = _build_result_zip(job_dir, job_id)
        # Resolve both sides so the relative_to works regardless of CWD —
        # same fix as in _move_doc_into_module_folder above.
        zip_url = "/" + zip_path.resolve().relative_to(Path("app").resolve()).as_posix()
    except Exception as exc:
        logger.exception("Job %s: ZIP packaging failed", job_id)
        await send_error(ws, f"ZIP packaging failed: {exc}")
        return

    await send_frame(ws, "bulk_done", {
        "job_id":         job_id,
        "zip_url":        zip_url,
        "total_files":    total_files,
        "total_modules":  len(modules),
        "modules":        sorted(completed_modules, key=lambda x: x["module_tag"]),
        "customer_glossary": customer_glossary,
    })

    append_turn(session_id, AGENT_KEY, {
        "q":              f"Bulk Upload — {total_files} files",
        "a":              f"Generated {len(modules)} module documents",
        "mode":           "bulk-upload",
        "bulk_job_id":    job_id,
        "bulk_zip_url":   zip_url,
        "bulk_summary":   {
            "total_files":   total_files,
            "total_modules": len(modules),
        },
    })

    logger.info("Bulk upload job %s complete: %d files, %d modules, zip=%s",
                job_id, total_files, len(modules), zip_url)


def _safe_json_dumps(obj) -> str:
    """JSON encode with str fallback so unexpected objects don't crash the
    summary writer.  Defensive; the inputs are usually clean dicts."""
    import json
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)
