# QAD Adaptive Knowledge Base

Authoritative QAD documentation indexed into Qdrant for the QAD-Zone
Documentation and Modernisation pipelines. Content is **not** committed to
git (large + license-restricted) — only this README and the folder structure.

## Layout

```
data/qad_kb/
├── features/       → indexed into Qdrant collection `qad_adaptive_features`
│                     "What does QAD natively do?" — used for replaceability
│                     scoring and gap analysis.
│
├── dev/            → indexed into Qdrant collection `qad_adaptive_dev`
│                     "How do I configure / build / migrate?" — used for
│                     gap-fill (Migration Blueprint) and modernisation mode.
│
└── .embed_state.json   → tracks per-source sha256 for idempotent re-runs
```

## What goes where

### `features/` — capability content
- **Release Notes** (full embed) — what's new per version
- **Online Help** (per-topic .txt + .json sidecars) — module-level capabilities
- **User Guides** (overview chapters only — Introduction / Concepts /
  Capabilities). The procedure / field-reference / report-parameter chapters
  are filtered out at ingest time because they're noise for replaceability
  questions.

### `dev/` — implementation content
- **Implementation Guide** (full)
- **Administration Guide** (full)
- **Integration Guide** (full)
- **Conversion Guide** (full)
- **Technical Reference** (selective — customisation framework, REST API,
  business events programming model)
- **Confluence developer pages** (HTML dumps with macro residue stripped)

## Adding a new source

1. Drop the file or folder into the right subfolder (`features/` or `dev/`).
2. Open `scripts/embed_qad_kb.py` and add an entry to `SOURCE_POLICIES`
   keyed by the file/folder name. Pick a `policy`:
   - `txt_dir`       — pre-extracted .txt + .json pairs (e.g. online help)
   - `html_dir`      — Confluence-style .html + .json pairs
   - `pdf_full`      — embed every section in the PDF
   - `pdf_overview`  — only sections matching Overview/Concepts/etc. patterns
3. Run `python -m scripts.embed_qad_kb`. The script is idempotent — it only
   re-embeds files whose content (sha256) changed since the last run.

## Re-embedding

```bash
# Normal run — only changed files
python -m scripts.embed_qad_kb

# Plan + sample chunks without API calls
python -m scripts.embed_qad_kb --dry-run

# Force re-embed everything
python -m scripts.embed_qad_kb --force

# One collection only
python -m scripts.embed_qad_kb --only features

# One source only (substring match on name)
python -m scripts.embed_qad_kb --source online_help
```
