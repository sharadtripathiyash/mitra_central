"""Central per-pass LLM model routing for the QAD-Zone agent.

WHY THIS FILE EXISTS
--------------------
The QAD documentation pipeline makes ~10 different kinds of LLM calls
(per-file tagging, taxonomy proposal, doc generation, blueprint, etc.),
each with very different requirements:

  - High-volume cheap classification     → small model (gpt-5.4-mini)
  - Critical one-shot reasoning          → top model + extended thinking
                                            (Claude Opus 4.7)
  - Long-form technical narrative        → Claude Opus 4.7 (better on
                                            documentation than GPT)
  - Code generation (TypeScript)         → GPT-5.5 (better on code)

Picking the right model per pass is the difference between $3 and $40 per
bulk run, AND between mediocre and excellent doc quality. Centralising the
picks here means:

  1. Tweaking a pass's model is a one-line edit, not a hunt through 8 files.
  2. New developers can see the whole routing strategy in one place.
  3. A/B testing a model swap is trivial (just change the constant).

MODEL SPEC FORMAT
-----------------
Each constant is a string the ``app.core.llm.chat()`` dispatcher understands:

    "openai:<model>"
    "anthropic:<model>"
    "anthropic:<model>:effort=<level>"      # enable adaptive thinking at
                                            # <low|medium|high|xhigh|max>

Note: as of Opus 4.7 (April 2026), the old explicit ``thinking.budget_tokens``
mechanism was removed in favour of "adaptive thinking" + an effort level. The
model decides how much to think; ``effort`` caps the spend.

See ``app/core/llm.py::chat()`` for the parser.
"""
from __future__ import annotations


# ─────────────────────────────────────────────────────────────────────────────
# 1. BULK-TAGGING PIPELINE — Pass A through Pass 4.5
#
# These run on the customer's full ZIP (~250 files). Mix of high-volume cheap
# calls (per-file tagging, glossary derivation) and critical one-shot calls
# (taxonomy, merge sweep).
# ─────────────────────────────────────────────────────────────────────────────

# Pass A — Customer glossary derivation
# 50× parallel small calls, one per prefix family. ~600 max_tokens each.
# Classification-ish task — gpt-5.4-mini is plenty + cheapest.
MODEL_PASS_A = "openai:gpt-5.4-mini"

# Pass 1a — Propose the module taxonomy (single LLM call)
# Critical: this decides the 15-25 module structure for the whole codebase.
# Adaptive thinking at "high" effort helps the model reason through merges
# before committing.
MODEL_PASS_1A = "anthropic:claude-opus-4-7:effort=high"

# Pass 1b — Assign 250 files to the locked taxonomy (single LLM call)
# Large structured-JSON output. GPT-5.5 has 400K context + strict JSON mode
# + handles big arrays reliably. Cheaper than Opus for this call shape.
MODEL_PASS_1B = "openai:gpt-5.5"

# Pass 2 — Per-file LLM tagging (250 calls!)
# Volume dominates. gpt-5.4-mini is much better than gpt-4o-mini at the same
# price tier and fast enough for the 250-call workload.
MODEL_PASS_2_TAGGING = "openai:gpt-5.4-mini"

# Pass 3 — Global review (single LLM call seeing all 250 tagged files)
# Pattern recognition across the whole dataset. Adaptive thinking at "high"
# effort lets Opus actually think about cross-module patterns instead of
# pattern-matching superficially.
MODEL_PASS_3 = "anthropic:claude-opus-4-7:effort=high"

# Pass 4 — Per-module coherence verification (~25 parallel calls)
# Read 8 file samples per module, flag outliers. GPT-5.5 is fast + the
# decision is bounded enough that thinking isn't necessary.
MODEL_PASS_4 = "openai:gpt-5.5"

# Pass 4.5 — Final merge sweep (single LLM call — most important for module count)
# THE decisive call for over-fragmentation. Adaptive thinking at "high" effort
# ensures the model commits to merges instead of hedging.
MODEL_PASS_4_5 = "anthropic:claude-opus-4-7:effort=high"


# ─────────────────────────────────────────────────────────────────────────────
# 2. PER-MODULE DOCUMENT GENERATION — Pass 1 (facts) + Pass 2 (doc) + Pass 3
#    (blueprint) + Summary. Called once per detected module.
# ─────────────────────────────────────────────────────────────────────────────

# Pass 1 — Extract structured facts from concatenated module code
# Deep code reading → 25-field JSON. Wrong facts here ripple through every
# downstream pass. Opus + adaptive thinking at "xhigh" — Anthropic's
# recommended effort for "legacy code migration" and "large codebase
# reviews", which is exactly what we're doing.
MODEL_DOC_FACTS = "anthropic:claude-opus-4-7:effort=xhigh"

# Pass 2 — Generate the System Documentation JSON (the actual doc text)
# Long-form structured technical writing — Opus's strongest category.
# "xhigh" effort gives the model room to plan section structure before writing.
MODEL_DOC_GENERATE = "anthropic:claude-opus-4-7:effort=xhigh"

# Pass 3 — Migration Blueprint with TypeScript code, configuration steps,
# API integrations. The TypeScript code part is the biggest single chunk
# of the document — GPT-5.5 leads Opus on Terminal-Bench by 13 points.
MODEL_DOC_BLUEPRINT = "openai:gpt-5.5"

# Summary — Executive summary JSON shown in the UI
# Short, structured, with scoring fields. GPT-5.5 is plenty + token-efficient.
MODEL_DOC_SUMMARY = "openai:gpt-5.5"


# ─────────────────────────────────────────────────────────────────────────────
# 3. ADAPTIVE TARGET BAND for Pass 4.5
#
# Don't hardcode "≤ 25 modules" — that scales wrong. For a 100-file upload
# the target should be ~10 modules; for a 1000-file upload, ~75.
#
# Heuristic: aim for an AVERAGE of 8-15 files per module. This produces a
# sensible band whether the codebase is 50 files or 5000.
# ─────────────────────────────────────────────────────────────────────────────

def compute_target_band(file_count: int) -> tuple[int, int]:
    """Return ``(target_low, target_high)`` for the desired module count
    based on ``file_count``.

    Examples::

        50 files   →  3-7 modules
        100 files  →  6-13 modules
        250 files  →  16-32 modules
        500 files  →  33-63 modules
        1000 files →  66-126 modules

    The Pass 4.5 LLM is told to aim for this range. If after the first
    Pass 4.5 call we're STILL above ``target_high``, the pipeline runs
    Pass 4.5 once more (more aggressive) — but never force-splits below
    ``target_low`` (under-merging is rare and never destructive).
    """
    target_low  = max(3, file_count // 15)
    target_high = max(5, file_count // 8 + 1)
    return target_low, target_high
