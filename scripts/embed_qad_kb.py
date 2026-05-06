"""One-time (and re-runnable) embedder for the QAD Adaptive knowledge base.

Walks ``data/qad_kb/{features,dev}/`` and ingests three file kinds — TXT
(pre-extracted online help), HTML (Confluence dumps), PDF (standalone QAD
guides) — into two Qdrant collections:

    qad_adaptive_features   ← capability / feature content
        Used by:
          • _research_qad_adaptive in app/agents/qad_zone/service.py
            (Documentation mode → QAD Standard Replacement section,
             Summary tab replaceability/confidence scoring)
          • analyse_modernisation in app/agents/qad_zone/modernisation.py
            (target-version "what's new" lookup)
          • Apex side widget (additive — gives the chatbot QAD knowledge)

    qad_adaptive_dev        ← implementation / setup / customisation content
        Used by:
          • Future Migration Blueprint generator (gap-fill content for the
            second download we currently ship as a hardcoded MRN .docx)
          • analyse_modernisation in app/agents/qad_zone/modernisation.py
            (upgrade path / conversion guide lookup)

Design choices that matter for the downstream pipeline:

1. **Folder routing**: anything under ``features/`` lands in the features
   collection; anything under ``dev/`` lands in the dev collection. No
   manifest required — folder placement is the routing key.

2. **Per-source policies** (SOURCE_POLICIES below) carry per-PDF or per-
   directory metadata: ``source_doc`` tag, optional ``module`` / ``topic``
   default, and a chunking policy (full vs. overview-only). Drop a new PDF
   into the right folder + add one line here = ingested.

3. **Capability-vs-procedure filtering for User Guides**: User Guide PDFs
   marked ``pdf_overview`` only embed sections whose title matches an
   "Overview / Concepts / Introduction / Capabilities" pattern. Their
   field-by-field maintenance + report parameter chapters are skipped —
   that content is procedural noise for replaceability and lives in the
   dev collection (Implementation Guide / Admin Guide) anyway.

4. **Heading-aware chunking**: chunks split at paragraph boundaries first,
   sentence boundaries second, never mid-word. Each chunk's parent section
   title is prefixed so an isolated chunk still carries enough context for
   the LLM to reason about it.

5. **Idempotent re-runs**: state is tracked in ``.embed_state.json`` keyed
   by (source_path, sha256). Re-running the script only re-embeds files
   whose content changed. When a file's chunks need replacing, the script
   deletes by ``source_path`` payload filter then upserts the new chunks.

6. **Batched embeddings**: chunks are embedded ~100 at a time via the
   OpenAI batch embeddings endpoint — ~5 minutes total for the planned
   ~7,200 chunks, vs. ~2 hours sequential.

Usage::

    pip install -r requirements.txt
    python -m scripts.embed_qad_kb                    # full ingest
    python -m scripts.embed_qad_kb --dry-run          # plan only, no API calls
    python -m scripts.embed_qad_kb --force            # re-embed everything
    python -m scripts.embed_qad_kb --only features    # one collection
    python -m scripts.embed_qad_kb --source online_help_2025  # one source

The script reads OPENAI_API_KEY, QDRANT_URL, QDRANT_API_KEY from the same
.env the running app uses.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import httpx
from bs4 import BeautifulSoup
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.stderr.write(
        "PyMuPDF not installed. Run:  pip install pymupdf\n"
        "(or: pip install -r requirements.txt)\n"
    )
    sys.exit(1)

# Project settings — same .env the app uses
from app.core.config import settings


# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s - %(message)s",
)
log = logging.getLogger("embed_qad_kb")


# ── Constants ─────────────────────────────────────────────────────────────────

KB_ROOT      = Path("data/qad_kb")
FEATURES_DIR = KB_ROOT / "features"
DEV_DIR      = KB_ROOT / "dev"
STATE_FILE   = KB_ROOT / ".embed_state.json"

EMBED_DIM       = 3072  # text-embedding-3-large
EMBED_BATCH     = 100   # OpenAI accepts up to 2048; 100 keeps payloads small
QDRANT_BATCH    = 200   # Qdrant upsert batch size

CHUNK_TARGET    = 1000
CHUNK_MAX       = 1500
CHUNK_OVERLAP   = 150
MIN_CHUNK_CHARS = 200

OPENAI_EMBED_URL = "https://api.openai.com/v1/embeddings"


# ── Source policies ───────────────────────────────────────────────────────────
#
# Drive ingestion behaviour per directory or PDF filename. To add a new source,
# drop the file/folder into data/qad_kb/{features|dev}/ and add one entry here.
#
# Required keys:
#   policy      one of: "txt_dir" | "html_dir" | "pdf_full" | "pdf_overview"
#   source_doc  short id used as a Qdrant filter (e.g. "release_notes_2025")
# Optional keys:
#   module      default module label when the source itself has no module info
#               (online help sidecars already carry this; PDFs need a default)
#   topic       default topic label for the dev collection (api / customisation
#               / implementation / security / migration)
#
# Lookup: directory entries match by directory name; PDFs match by filename.

SOURCE_POLICIES: dict[str, dict] = {
    # ── features/ ───────────────────────────────────────────────────────────
    "online_help_2025": {
        "policy":     "txt_dir",
        "source_doc": "online_help_2025",
        # `module` is in each .json sidecar — no default needed
    },
    "QAD_Adaptive_RN_v2025.pdf": {
        "policy":     "pdf_full",
        "source_doc": "release_notes_2025",
        "module":     "All Modules",
    },
    "QAD_Warehousing_2025_Adaptive_User_Guide.pdf": {
        "policy":     "pdf_overview",
        "source_doc": "warehousing_ug_2025",
        "module":     "Warehousing",
    },
    "QAD_Adaptive_Business_Events_1_0_User_Guide.pdf": {
        "policy":     "pdf_overview",
        "source_doc": "business_events_ug_2025",
        "module":     "Integration",
    },

    # ── dev/ ────────────────────────────────────────────────────────────────
    "confluence_QEP250": {
        "policy":     "html_dir",
        "source_doc": "confluence_QEP250",
        "module":     "QAD Enterprise Platform",
        "topic":      "platform_developer",
    },
    "QAD_Adaptive_2025_Implementation_Guide.pdf": {
        "policy":     "pdf_full",
        "source_doc": "implementation_guide_2025",
        "module":     "Platform",
        "topic":      "implementation",
    },
    "QAD_Adaptive_2025_Security_Administration_Guide.pdf": {
        "policy":     "pdf_full",
        "source_doc": "security_admin_guide_2025",
        "module":     "Platform",
        "topic":      "security",
    },
}


# ── Section-name filters for User Guides on pdf_overview policy ───────────────
#
# A topic is kept iff its title (or any ancestor) matches one of OVERVIEW_RE,
# AND no part of the title matches PROCEDURE_RE. This filter implements the
# "capability-only" rule for User Guides without losing chapter intros.

OVERVIEW_RE = re.compile(
    r"(^|\b)(Overview|Introduction|Concepts?|Understanding|About|"
    r"Key\s+Features?|Capabilities|Architecture|Getting\s+Started\s+with)"
    r"(\b|$)",
    re.I,
)

PROCEDURE_RE = re.compile(
    r"(^|\b)(Maintenance|Maintain|Creating|Modifying|Deleting|Procedure|"
    r"Steps?|Field\s+Reference|Browse|Report\s+Parameters?|Reports?|"
    r"How\s+to)(\b|$)",
    re.I,
)

# Universal skip — applied to all PDFs regardless of policy
UNIVERSAL_PDF_SKIP_RE = re.compile(
    r"^(Table\s+of\s+Contents|Index|Glossary|Copyright|"
    r"Document\s+Information|Trademarks?)\s*$",
    re.I,
)


# ── Confluence HTML cleanup selectors ─────────────────────────────────────────

CONFLUENCE_NOISE_SELECTORS = [
    "div.plugin_pagetree",
    "fieldset.hidden",
    'input[type="hidden"]',
    "div.confluence-information-macro-icon",
    "span.aui-icon",
    "div.expand-control",
    "a.confluence-userlink",
    "div.confluence-attachment-list",
    "div.attachment-content",
    "script",
    "style",
    "noscript",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

@dataclass
class Chunk:
    """A single embeddable unit with all the metadata we'll filter on later."""
    text:        str
    title:       str
    breadcrumb:  str
    module:      str
    section:     str
    source_doc:  str
    source_path: str   # POSIX-style relative path under data/qad_kb/
    version:     str = "Adaptive 2025"
    topic:       str = ""
    page:        str = ""
    extras:      dict = field(default_factory=dict)

    def to_payload(self) -> dict:
        p = {
            "text":        self.text,
            "title":       self.title,
            "breadcrumb":  self.breadcrumb,
            "module":      self.module,
            "section":     self.section,
            "source_doc":  self.source_doc,
            "source_path": self.source_path,
            "version":     self.version,
        }
        if self.topic:
            p["topic"] = self.topic
        if self.page:
            p["page"] = self.page
        if self.extras:
            p.update(self.extras)
        return p


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for buf in iter(lambda: f.read(1 << 16), b""):
            h.update(buf)
    return h.hexdigest()


def normalise_text(text: str) -> str:
    """Tighten whitespace without destroying paragraph boundaries."""
    text = text.replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def chunk_text(text: str, *, target: int = CHUNK_TARGET,
               cap: int = CHUNK_MAX, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Greedy paragraph-then-sentence packer. Never splits mid-sentence
    unless a single sentence exceeds ``cap`` (rare). Adds a tail-overlap
    from the previous chunk so adjacent chunks share context.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= target:
        return [text] if len(text) >= MIN_CHUNK_CHARS else []

    paragraphs = split_paragraphs(text) or [text]
    raw_chunks: list[str] = []
    buf = ""
    for p in paragraphs:
        candidate = (buf + "\n\n" + p) if buf else p
        if len(candidate) <= target:
            buf = candidate
            continue

        # Flush current buf
        if buf:
            raw_chunks.append(buf)
            buf = ""

        # Paragraph itself fits within cap → start new buf
        if len(p) <= cap:
            buf = p
            continue

        # Long paragraph → sentence-pack
        sbuf = ""
        for s in split_sentences(p):
            scand = (sbuf + " " + s) if sbuf else s
            if len(scand) <= target:
                sbuf = scand
            else:
                if sbuf:
                    raw_chunks.append(sbuf)
                sbuf = s if len(s) <= cap else s[:cap]
        if sbuf:
            buf = sbuf

    if buf:
        raw_chunks.append(buf)

    # Add overlap with previous chunk's tail
    out: list[str] = []
    for i, c in enumerate(raw_chunks):
        if i == 0 or overlap <= 0:
            out.append(c)
            continue
        tail = raw_chunks[i - 1][-overlap:]
        # Trim the tail to the last sentence boundary
        m = re.search(r"[.!?]\s+", tail)
        if m:
            tail = tail[m.end():]
        merged = (tail.rstrip() + " " + c).strip() if tail.strip() else c
        out.append(merged[:cap])

    return [c for c in out if len(c) >= MIN_CHUNK_CHARS]


def prepend_context(chunk: str, title: str, breadcrumb: str) -> str:
    """Prefix each chunk with title + breadcrumb so it stays self-describing
    when retrieved in isolation. Keeps the first sentences relevant for the
    embedding model and helpful for the LLM context window.
    """
    header = f"# {title}\n[{breadcrumb}]\n\n" if breadcrumb else f"# {title}\n\n"
    return header + chunk


# ── Source ingestors ──────────────────────────────────────────────────────────

def ingest_txt_dir(dir_path: Path, policy: dict) -> Iterable[Chunk]:
    """Ingest a directory of pre-extracted online-help .txt + .json pairs.
    Sidecar JSON carries title, breadcrumb, module, pages, source_doc.
    """
    rel_dir = dir_path.relative_to(KB_ROOT).as_posix()
    txt_files = sorted(dir_path.glob("*.txt"))
    log.info("[%s] found %d .txt topics", rel_dir, len(txt_files))

    for txt_path in txt_files:
        json_path = txt_path.with_suffix(".json")
        if not json_path.exists():
            log.warning("missing sidecar for %s — skipping", txt_path.name)
            continue

        try:
            meta = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("bad sidecar JSON %s: %s — skipping", json_path.name, exc)
            continue

        body = txt_path.read_text(encoding="utf-8", errors="replace")
        # Strip the "# title\n## breadcrumb\n\n" header if present
        body = re.sub(r"^#\s+[^\n]+\n##\s+[^\n]+\n+", "", body, count=1)
        body = normalise_text(body)
        if len(body) < MIN_CHUNK_CHARS:
            continue

        title      = meta.get("title", txt_path.stem)
        breadcrumb = meta.get("breadcrumb", title)
        module     = meta.get("module") or policy.get("module", "Unknown")
        version    = meta.get("version", "Adaptive 2025")
        page       = meta.get("pages", "")

        rel_src = txt_path.relative_to(KB_ROOT).as_posix()

        for c in chunk_text(body):
            yield Chunk(
                text=prepend_context(c, title, breadcrumb),
                title=title,
                breadcrumb=breadcrumb,
                module=module,
                section=title,
                source_doc=policy["source_doc"],
                source_path=rel_src,
                version=version,
                topic=policy.get("topic", ""),
                page=page,
            )


def ingest_html_dir(dir_path: Path, policy: dict) -> Iterable[Chunk]:
    """Ingest a Confluence dump: <id>_<title>.html + <id>_<title>.json pairs.
    Strips macro residue + empty containers, then chunks by heading boundaries.
    """
    rel_dir  = dir_path.relative_to(KB_ROOT).as_posix()
    html_files = sorted(dir_path.glob("*.html"))
    log.info("[%s] found %d .html pages", rel_dir, len(html_files))

    for html_path in html_files:
        json_path = html_path.with_suffix(".json")
        meta: dict = {}
        if json_path.exists():
            try:
                meta = json.loads(json_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        try:
            soup = BeautifulSoup(
                html_path.read_text(encoding="utf-8", errors="replace"),
                "html.parser",
            )
        except Exception as exc:
            log.warning("bad HTML %s: %s — skipping", html_path.name, exc)
            continue

        # Strip noise selectors
        for sel in CONFLUENCE_NOISE_SELECTORS:
            for el in soup.select(sel):
                el.decompose()
        # Remove empty containers
        for el in soup.find_all(["div", "ul", "ol", "p", "section", "span"]):
            if not el.get_text(strip=True) and not el.find(["img", "table", "pre", "code"]):
                el.decompose()

        title      = meta.get("title") or (soup.find("h1").get_text(strip=True) if soup.find("h1") else html_path.stem)
        breadcrumb = meta.get("breadcrumb", title)
        module     = policy.get("module", "QAD Enterprise Platform")

        # Chunk by h1/h2/h3 boundaries — each section becomes its own chunk(s)
        sections: list[tuple[str, str]] = []  # (section_title, text)
        current_title = title
        current_buf   = []

        def flush():
            text = normalise_text("\n\n".join(current_buf))
            if len(text) >= MIN_CHUNK_CHARS:
                sections.append((current_title, text))

        body = soup.body or soup
        for el in body.descendants:
            if not getattr(el, "name", None):
                continue
            if el.name in ("h1", "h2", "h3"):
                flush()
                current_title = el.get_text(" ", strip=True) or current_title
                current_buf   = []
            elif el.name in ("p", "li", "td", "pre", "blockquote"):
                t = el.get_text(" ", strip=True)
                if t:
                    current_buf.append(t)
        flush()

        # If no headings at all, treat whole page as one section
        if not sections:
            text = normalise_text(soup.get_text(" ", strip=True))
            if len(text) >= MIN_CHUNK_CHARS:
                sections.append((title, text))

        rel_src = html_path.relative_to(KB_ROOT).as_posix()
        for sec_title, sec_text in sections:
            for c in chunk_text(sec_text):
                yield Chunk(
                    text=prepend_context(c, sec_title, breadcrumb),
                    title=title,
                    breadcrumb=breadcrumb,
                    module=module,
                    section=sec_title,
                    source_doc=policy["source_doc"],
                    source_path=rel_src,
                    topic=policy.get("topic", ""),
                )


def ingest_pdf(pdf_path: Path, policy: dict) -> Iterable[Chunk]:
    """Ingest a standalone PDF using its outline (bookmarks) for sectioning.
    Applies overview/procedure filters when policy == 'pdf_overview'.
    """
    rel_src = pdf_path.relative_to(KB_ROOT).as_posix()
    overview_only = policy["policy"] == "pdf_overview"

    log.info("[%s] opening (overview_only=%s)", rel_src, overview_only)
    doc = fitz.open(str(pdf_path))
    toc = doc.get_toc()
    if not toc:
        log.warning("[%s] no outline — skipping", rel_src)
        return

    # Build flat entries with breadcrumb + page range
    entries: list[dict] = []
    crumb_stack: list[tuple[int, str]] = []
    for i, (level, title, page) in enumerate(toc):
        while crumb_stack and crumb_stack[-1][0] >= level:
            crumb_stack.pop()
        breadcrumb = [t for _, t in crumb_stack]
        crumb_stack.append((level, title))
        next_page = toc[i + 1][2] if i + 1 < len(toc) else doc.page_count + 1
        entries.append({
            "title":      title.strip(),
            "breadcrumb": breadcrumb,
            "start":      max(0, page - 1),
            "end":        max(0, min(next_page - 2, doc.page_count - 1)),
        })

    base_title = pdf_path.stem.replace("_", " ")
    module     = policy.get("module", "Platform")
    topic      = policy.get("topic", "")
    kept = skipped = 0

    for e in entries:
        title = e["title"]
        # Universal skip
        if UNIVERSAL_PDF_SKIP_RE.match(title):
            skipped += 1
            continue
        # Procedure skip applies to ALL pdf policies
        if PROCEDURE_RE.search(title):
            skipped += 1
            continue
        # Overview-only filter for User Guides
        if overview_only:
            chain = e["breadcrumb"] + [title]
            if not any(OVERVIEW_RE.search(t) for t in chain):
                skipped += 1
                continue

        # Extract text from the topic's pages
        try:
            text = "\n".join(
                doc.load_page(p).get_text("text")
                for p in range(e["start"], e["end"] + 1)
            )
        except Exception as exc:
            log.warning("[%s] text extract failed for '%s': %s", rel_src, title, exc)
            continue

        text = normalise_text(text)
        if len(text) < MIN_CHUNK_CHARS:
            continue

        breadcrumb_str = " > ".join([base_title] + e["breadcrumb"] + [title])
        for c in chunk_text(text):
            yield Chunk(
                text=prepend_context(c, title, breadcrumb_str),
                title=title,
                breadcrumb=breadcrumb_str,
                module=module,
                section=title,
                source_doc=policy["source_doc"],
                source_path=rel_src,
                topic=topic,
                page=f"{e['start']+1}-{e['end']+1}",
            )
        kept += 1

    log.info("[%s] kept=%d skipped=%d", rel_src, kept, skipped)


# ── Embedding (batched) ──────────────────────────────────────────────────────

def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed N strings via OpenAI text-embedding-3-large in a single request.
    Returns vectors in input order. Raises on error.
    """
    payload = {"model": settings.openai_embed_model, "input": texts}
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type":  "application/json",
    }
    with httpx.Client(timeout=120) as client:
        for attempt in range(3):
            try:
                r = client.post(OPENAI_EMBED_URL, json=payload, headers=headers)
                r.raise_for_status()
                data = r.json()
                return [item["embedding"] for item in data["data"]]
            except httpx.HTTPError as exc:
                wait = 2 ** attempt
                log.warning("embed batch attempt %d failed: %s — retry in %ds",
                            attempt + 1, exc, wait)
                time.sleep(wait)
        raise RuntimeError("embed batch failed after 3 attempts")


# ── Qdrant ───────────────────────────────────────────────────────────────────

def get_client() -> QdrantClient:
    return QdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key,
        timeout=60,
    )


def ensure_collection(client: QdrantClient, name: str) -> None:
    existing = {c.name for c in client.get_collections().collections}
    if name not in existing:
        log.info("creating collection '%s' (dim=%d, distance=COSINE)", name, EMBED_DIM)
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
        )
    else:
        log.info("collection '%s' already exists", name)

    # Payload indexes for fast filtering at query time
    for field_name in ("module", "version", "source_doc", "topic", "source_path"):
        try:
            client.create_payload_index(
                collection_name=name,
                field_name=field_name,
                field_schema=PayloadSchemaType.KEYWORD,
            )
            log.info("  payload index ready: %s.%s", name, field_name)
        except Exception:
            pass  # already indexed


def delete_existing_chunks(client: QdrantClient, collection: str, source_path: str) -> None:
    """Remove all chunks tagged with this source_path so re-ingestion is idempotent."""
    try:
        client.delete(
            collection_name=collection,
            points_selector=Filter(must=[
                FieldCondition(key="source_path", match=MatchValue(value=source_path))
            ]),
        )
    except Exception as exc:
        log.warning("delete-by-source_path failed for %s: %s", source_path, exc)


def upsert_chunks(client: QdrantClient, collection: str,
                  chunks: list[Chunk], vectors: list[list[float]]) -> None:
    points: list[PointStruct] = []
    for chunk, vec in zip(chunks, vectors):
        points.append(PointStruct(
            id=str(uuid.uuid4()),
            vector=vec,
            payload=chunk.to_payload(),
        ))
    for i in range(0, len(points), QDRANT_BATCH):
        batch = points[i:i + QDRANT_BATCH]
        client.upsert(collection_name=collection, points=batch, wait=False)


# ── State (idempotency) ──────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def source_signature(name: str, path: Path) -> str:
    """Hash for either a file or a directory of (txt|html|json|pdf) files."""
    if path.is_file():
        return file_sha256(path)
    h = hashlib.sha256()
    for child in sorted(path.rglob("*")):
        if child.is_file() and child.suffix.lower() in (".txt", ".html", ".json", ".pdf"):
            h.update(child.relative_to(path).as_posix().encode())
            h.update(file_sha256(child).encode())
    return h.hexdigest()


# ── Driver ───────────────────────────────────────────────────────────────────

def discover_sources() -> list[tuple[str, Path, str, dict]]:
    """Find every (collection, path, source_name, policy) under data/qad_kb/."""
    sources: list[tuple[str, Path, str, dict]] = []
    for collection_attr, root_dir in [
        (settings.qdrant_collection_features, FEATURES_DIR),
        (settings.qdrant_collection_dev,      DEV_DIR),
    ]:
        if not root_dir.exists():
            log.warning("missing root dir: %s", root_dir)
            continue
        for entry in sorted(root_dir.iterdir()):
            name = entry.name
            policy = SOURCE_POLICIES.get(name)
            if not policy:
                log.warning("no policy for '%s' in %s — skipping. "
                            "Add an entry to SOURCE_POLICIES.",
                            name, root_dir)
                continue
            sources.append((collection_attr, entry, name, policy))
    return sources


def process_source(client: QdrantClient, collection: str, path: Path,
                   source_name: str, policy: dict, dry_run: bool) -> int:
    """Drive the per-policy ingestor, embed, and upsert. Returns chunk count."""
    if policy["policy"] == "txt_dir":
        chunks_iter = ingest_txt_dir(path, policy)
    elif policy["policy"] == "html_dir":
        chunks_iter = ingest_html_dir(path, policy)
    elif policy["policy"] in ("pdf_full", "pdf_overview"):
        chunks_iter = ingest_pdf(path, policy)
    else:
        log.error("unknown policy '%s' for %s", policy["policy"], source_name)
        return 0

    chunks: list[Chunk] = []
    for ch in chunks_iter:
        chunks.append(ch)

    if not chunks:
        log.info("[%s] no embeddable chunks produced", source_name)
        return 0

    # Group by source_path so we can clear old points per file before upserting
    by_source_path: dict[str, list[Chunk]] = {}
    for ch in chunks:
        by_source_path.setdefault(ch.source_path, []).append(ch)
    log.info("[%s] %d chunks across %d files",
             source_name, len(chunks), len(by_source_path))

    if dry_run:
        # Print a sample
        sample = chunks[0]
        log.info("  sample chunk (first %d chars): %s",
                 min(180, len(sample.text)), sample.text[:180].replace("\n", " ⏎ "))
        log.info("  sample payload: module=%s source_doc=%s breadcrumb=%s",
                 sample.module, sample.source_doc, sample.breadcrumb[:80])
        return len(chunks)

    # Clear existing chunks for these source_paths
    for sp in by_source_path:
        delete_existing_chunks(client, collection, sp)

    # Embed in batches and upsert
    total_embedded = 0
    for i in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[i:i + EMBED_BATCH]
        texts = [c.text for c in batch]
        log.info("  embedding batch %d/%d (%d chunks)",
                 (i // EMBED_BATCH) + 1,
                 (len(chunks) + EMBED_BATCH - 1) // EMBED_BATCH,
                 len(batch))
        vectors = embed_batch(texts)
        upsert_chunks(client, collection, batch, vectors)
        total_embedded += len(batch)
    log.info("[%s] upserted %d chunks → %s", source_name, total_embedded, collection)
    return total_embedded


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="Plan + sample chunks without API calls")
    ap.add_argument("--force", action="store_true",
                    help="Re-embed everything even if hash hasn't changed")
    ap.add_argument("--only", choices=("features", "dev"),
                    help="Process only one collection")
    ap.add_argument("--source", help="Process only sources matching this name (substring match)")
    args = ap.parse_args()

    if not settings.openai_api_key:
        sys.stderr.write("OPENAI_API_KEY not set in environment.\n")
        return 2
    if not args.dry_run and not settings.qdrant_url:
        sys.stderr.write("QDRANT_URL not set in environment.\n")
        return 2

    sources = discover_sources()
    if not sources:
        log.warning("no sources found under %s — drop files in features/ and dev/", KB_ROOT)
        return 1

    if args.only:
        wanted = (settings.qdrant_collection_features if args.only == "features"
                  else settings.qdrant_collection_dev)
        sources = [s for s in sources if s[0] == wanted]
    if args.source:
        sources = [s for s in sources if args.source.lower() in s[2].lower()]

    log.info("=" * 70)
    log.info("Sources to process: %d", len(sources))
    for coll, path, name, policy in sources:
        log.info("  %-45s  → %-25s  (%s)", name, coll, policy["policy"])
    log.info("=" * 70)

    state = load_state()
    client = None if args.dry_run else get_client()

    if not args.dry_run:
        # Ensure both collections exist (idempotent)
        for coll in (settings.qdrant_collection_features, settings.qdrant_collection_dev):
            ensure_collection(client, coll)

    grand_total = 0
    skipped_unchanged = 0
    for coll, path, name, policy in sources:
        sig = source_signature(name, path)
        cached = state.get(name)
        if not args.force and cached and cached.get("sha256") == sig and not args.dry_run:
            log.info("[%s] unchanged since last run (sha256 match) — skipping", name)
            skipped_unchanged += 1
            continue

        try:
            n = process_source(client, coll, path, name, policy, args.dry_run)
            grand_total += n
            if not args.dry_run and n > 0:
                state[name] = {
                    "sha256":     sig,
                    "collection": coll,
                    "chunks":     n,
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                save_state(state)
        except Exception as exc:
            log.exception("[%s] FAILED: %s", name, exc)

    log.info("=" * 70)
    log.info("DONE.  embedded=%d  skipped_unchanged=%d  sources=%d",
             grand_total, skipped_unchanged, len(sources))
    log.info("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
