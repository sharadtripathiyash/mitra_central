/**
 * LiveBulkPipelineCard — renders progress for a bulk-upload job.
 *
 * Reads `state` (managed in QadZone.jsx) and shows:
 *   • 8-pass tagging progress (Pass 0 → 0.5 → 0.7 → A → 1 → 2 → 3 → 4 → 4.5)
 *   • Per-module doc + blueprint generation (one row each, doc + blueprint
 *     links appear as their `bulk_module_ready` frames arrive)
 *   • Final result section with a ZIP download button when `bulk_done`
 *     arrives
 *
 * State shape (managed by QadZone)::
 *
 *     {
 *       active:        true,
 *       currentPhase:  "pass2" | "module_doc_start" | ...,
 *       phaseLog:      [{phase, message, ts}, ...],
 *       taggingMeta:   { total_files, total_modules, modules: [...] } | null,
 *       modulesReady:  [{module_tag, title, doc_url, blueprint_url, errors}, ...],
 *       done:          false,
 *       zipUrl:        null,
 *       finalSummary:  null,    // populated on bulk_done
 *       error:         null,
 *     }
 */
import { FileText, Download, CheckCircle2, Loader2, AlertTriangle, Layers } from "lucide-react";

// The 9 named passes the orchestrator emits (in order).
const TAGGING_PHASES = [
  { id: "discover",        label: "Discover files" },
  { id: "pass0",           label: "Pass 0 — Prefix families" },
  { id: "pass0_5",         label: "Pass 0.5 — Parent consolidation" },
  { id: "pass0_7",         label: "Pass 0.7 — Include graph" },
  { id: "pass_a",          label: "Pass A — Customer glossary" },
  { id: "pass1a",          label: "Pass 1a — Taxonomy" },
  { id: "pass1b",          label: "Pass 1b — File assignment" },
  { id: "pass1c",          label: "Pass 1c — Coverage check" },
  { id: "pass2",           label: "Pass 2 — Per-file tagging" },
  { id: "pass3",           label: "Pass 3 — Global review" },
  { id: "pass4",           label: "Pass 4 — Coherence" },
  { id: "pass4_5",         label: "Pass 4.5 — Final merge" },
  { id: "tagging_done",    label: "Tagging complete" },
];

// All phases (including doc-gen / packaging) for "current phase" lookup.
const ALL_PHASE_LABELS = {
  ...Object.fromEntries(TAGGING_PHASES.map((p) => [p.id, p.label])),
  init:               "Initialising job",
  extracted:          "ZIP extracted",
  tagging_complete:   "Tagging complete",
  module_doc_start:   "Generating module docs",
  packaging:          "Building final ZIP",
};


export function LiveBulkPipelineCard({ state }) {
  if (!state) return null;

  const reachedPhases = new Set(state.phaseLog.map((p) => p.phase));
  const currentLabel  = ALL_PHASE_LABELS[state.currentPhase] || state.currentPhase || "Starting…";

  return (
    <div
      className="rounded-2xl p-5 space-y-4"
      style={{
        background: "rgba(10,20,42,0.92)",
        border: "1px solid rgba(0,229,200,0.18)",
      }}
    >
      <Header state={state} currentLabel={currentLabel} />

      {state.error && (
        <div
          className="rounded-lg px-3 py-2 text-xs flex items-start gap-2"
          style={{
            background: "rgba(239,68,68,0.12)",
            border: "1px solid rgba(239,68,68,0.3)",
            color: "#fca5a5",
          }}
        >
          <AlertTriangle size={14} className="shrink-0 mt-0.5" />
          <span>{state.error}</span>
        </div>
      )}

      <TaggingProgress
        reachedPhases={reachedPhases}
        currentPhase={state.currentPhase}
        done={state.done}
      />

      {state.taggingMeta && (
        <TaggingSummary meta={state.taggingMeta} />
      )}

      {(state.taggingMeta || state.modulesReady.length > 0) && (
        <ModuleList
          plannedModules={state.taggingMeta?.modules || []}
          readyModules={state.modulesReady}
          done={state.done}
        />
      )}

      {state.done && state.zipUrl && (
        <FinalDownload zipUrl={state.zipUrl} finalSummary={state.finalSummary} />
      )}
    </div>
  );
}


function Header({ state, currentLabel }) {
  return (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-2">
        <Layers size={18} style={{ color: "#00e5c8" }} />
        <div>
          <div className="text-sm font-semibold" style={{ color: "#e8f4ff" }}>
            Bulk Customisation Documentation
          </div>
          <div className="text-[11px]" style={{ color: "rgba(140,180,230,0.6)" }}>
            {state.done ? "Pipeline complete" : currentLabel}
          </div>
        </div>
      </div>
      {!state.done && !state.error && (
        <Loader2 size={16} className="animate-spin" style={{ color: "#00e5c8" }} />
      )}
      {state.done && (
        <CheckCircle2 size={18} style={{ color: "#22c55e" }} />
      )}
    </div>
  );
}


function TaggingProgress({ reachedPhases, currentPhase, done }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide mb-2"
        style={{ color: "rgba(140,180,230,0.55)" }}>
        Tagging pipeline
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-1.5">
        {TAGGING_PHASES.map((p) => {
          const reached  = reachedPhases.has(p.id) || done;
          const isCurrent = p.id === currentPhase;
          return (
            <div
              key={p.id}
              className="rounded-md px-2 py-1.5 text-[11px] flex items-center gap-1.5"
              style={{
                background: reached
                  ? "rgba(0,229,200,0.08)"
                  : "rgba(8,15,32,0.6)",
                border: `1px solid ${
                  isCurrent ? "rgba(0,229,200,0.55)" :
                  reached   ? "rgba(0,229,200,0.2)"  : "rgba(0,229,200,0.08)"
                }`,
                color: reached ? "#00e5c8" : "rgba(140,180,230,0.45)",
              }}
            >
              {reached ? (
                <CheckCircle2 size={11} />
              ) : isCurrent ? (
                <Loader2 size={11} className="animate-spin" />
              ) : (
                <span className="inline-block w-[7px] h-[7px] rounded-full"
                  style={{ background: "rgba(140,180,230,0.25)" }} />
              )}
              <span className="truncate">{p.label}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}


function TaggingSummary({ meta }) {
  return (
    <div className="grid grid-cols-2 gap-2">
      <div
        className="rounded-md px-3 py-2"
        style={{ background: "rgba(0,229,200,0.06)", border: "1px solid rgba(0,229,200,0.18)" }}
      >
        <div className="text-[10px] uppercase tracking-wide"
          style={{ color: "rgba(140,180,230,0.55)" }}>Files tagged</div>
        <div className="text-xl font-semibold" style={{ color: "#00e5c8" }}>
          {meta.total_files}
        </div>
      </div>
      <div
        className="rounded-md px-3 py-2"
        style={{ background: "rgba(0,229,200,0.06)", border: "1px solid rgba(0,229,200,0.18)" }}
      >
        <div className="text-[10px] uppercase tracking-wide"
          style={{ color: "rgba(140,180,230,0.55)" }}>Modules detected</div>
        <div className="text-xl font-semibold" style={{ color: "#00e5c8" }}>
          {meta.total_modules}
        </div>
      </div>
    </div>
  );
}


function ModuleList({ plannedModules, readyModules, done }) {
  const readyMap = new Map(readyModules.map((m) => [m.module_tag, m]));

  // Build the display list: every planned module gets a row; rows fill in as
  // doc/blueprint URLs arrive. If we don't have a plan yet (rare), fall back
  // to readyModules.
  const rows = plannedModules.length > 0
    ? plannedModules.map((p) => readyMap.get(p.module_tag) || {
        module_tag:   p.module_tag,
        module_desc:  p.module_desc,
        file_count:   p.file_count,
        doc_url:      null,
        blueprint_url: null,
        errors:       [],
      })
    : readyModules;

  if (rows.length === 0) return null;

  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide mb-2 flex items-center justify-between"
        style={{ color: "rgba(140,180,230,0.55)" }}>
        <span>Module documentation</span>
        <span style={{ color: "rgba(0,229,200,0.55)" }}>
          {readyModules.length} / {rows.length}{done ? " — done" : ""}
        </span>
      </div>
      <div className="space-y-1.5 max-h-96 overflow-y-auto pr-1">
        {rows.map((m) => (
          <ModuleRow key={m.module_tag} m={m} />
        ))}
      </div>
    </div>
  );
}


function ModuleRow({ m }) {
  const ready = Boolean(m.doc_url || m.blueprint_url);
  const hasErrors = Array.isArray(m.errors) && m.errors.length > 0;

  return (
    <div
      className="rounded-md px-3 py-2 flex items-center gap-3"
      style={{
        background: ready ? "rgba(0,229,200,0.06)" : "rgba(8,15,32,0.6)",
        border: `1px solid ${
          hasErrors ? "rgba(239,68,68,0.3)"
                    : ready ? "rgba(0,229,200,0.22)" : "rgba(0,229,200,0.08)"
        }`,
      }}
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold" style={{ color: ready ? "#00e5c8" : "rgba(180,210,255,0.75)" }}>
            {m.module_tag}
          </span>
          {m.file_count != null && (
            <span className="text-[10px]" style={{ color: "rgba(140,180,230,0.45)" }}>
              · {m.file_count} file{m.file_count === 1 ? "" : "s"}
            </span>
          )}
          {hasErrors && (
            <span title={m.errors.join(" | ")}>
              <AlertTriangle size={11} style={{ color: "#fca5a5" }} />
            </span>
          )}
        </div>
        {m.module_desc && (
          <div className="text-[11px] truncate" style={{ color: "rgba(180,210,255,0.55)" }}>
            {m.module_desc}
          </div>
        )}
      </div>
      <div className="flex items-center gap-1.5 shrink-0">
        <DocLink url={m.doc_url}        label="Doc"        />
        <DocLink url={m.blueprint_url}  label="Blueprint"  />
        {!ready && !hasErrors && (
          <Loader2 size={11} className="animate-spin" style={{ color: "rgba(0,229,200,0.5)" }} />
        )}
      </div>
    </div>
  );
}


function DocLink({ url, label }) {
  if (!url) {
    return (
      <span
        className="text-[10px] px-2 py-0.5 rounded"
        style={{
          color: "rgba(140,180,230,0.35)",
          background: "rgba(8,15,32,0.5)",
          border: "1px solid rgba(0,229,200,0.08)",
        }}
      >
        {label}
      </span>
    );
  }
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="text-[10px] px-2 py-0.5 rounded inline-flex items-center gap-1 transition"
      style={{
        color: "#00e5c8",
        background: "rgba(0,229,200,0.1)",
        border: "1px solid rgba(0,229,200,0.3)",
      }}
      onMouseOver={(e) => { e.currentTarget.style.background = "rgba(0,229,200,0.2)"; }}
      onMouseOut={(e)  => { e.currentTarget.style.background = "rgba(0,229,200,0.1)"; }}
    >
      <FileText size={9} />
      {label}
    </a>
  );
}


function FinalDownload({ zipUrl, finalSummary }) {
  return (
    <div
      className="rounded-lg p-3 flex items-center justify-between gap-3"
      style={{
        background: "rgba(0,229,200,0.1)",
        border: "1px solid rgba(0,229,200,0.4)",
      }}
    >
      <div>
        <div className="text-xs font-semibold" style={{ color: "#00e5c8" }}>
          All module documents ready
        </div>
        {finalSummary && (
          <div className="text-[11px]" style={{ color: "rgba(180,210,255,0.7)" }}>
            {finalSummary.total_modules} module{finalSummary.total_modules === 1 ? "" : "s"} ·
            {" "}{finalSummary.total_files} file{finalSummary.total_files === 1 ? "" : "s"} tagged
          </div>
        )}
      </div>
      <a
        href={zipUrl}
        className="text-xs px-3 py-1.5 rounded-lg inline-flex items-center gap-2 transition shrink-0"
        style={{
          background: "linear-gradient(135deg,#00c9ae,#00e5c8)",
          color: "#060d1a",
          fontWeight: 600,
        }}
      >
        <Download size={12} />
        Download all
      </a>
    </div>
  );
}
