"""QAD bulk-tagging pipeline (web-app integration).

Ported from doc-gener-bulk into the QAD-Zone agent. Runs an 8-pass tagging
pipeline (Pass 0 → 0.5 → 0.7 → A → 1 → 2 → 3 → 4 → 4.5) over an extracted
customer customisation ZIP to segregate files into business modules.

Entry point: ``orchestrator.run_tagging_pipeline``.

Per-job storage is rooted at ``<job_dir>/input/`` and ``<job_dir>/`` for the
sqlite tag DB. The caller (``bulk_service``) sets the per-job paths via
``config.init_for_job(input_dir, output_dir)`` before invoking the pipeline.
"""
__version__ = "0.3.0-mitra"
