/**
 * BulkUploadModal — choice dialog shown on paperclip click.
 *
 * Lets the user pick between two upload flows:
 *   "module"  → existing flow (single module's files, generates one doc)
 *   "bulk"    → new flow (whole customisation ZIP, runs 8-pass tagging,
 *                generates one doc + blueprint per detected module)
 *
 * Dumb component: receives `open`, calls `onChoose(kind)` then `onClose`.
 */
import { FilePlus, Layers, X } from "lucide-react";

export function BulkUploadModal({ open, onClose, onChoose }) {
  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center px-4"
      style={{ background: "rgba(4,8,18,0.78)", backdropFilter: "blur(6px)" }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-xl rounded-2xl p-6 relative"
        style={{
          background: "rgba(10,20,42,0.97)",
          border: "1px solid rgba(0,229,200,0.25)",
          boxShadow: "0 24px 64px rgba(0,0,0,0.5), 0 0 0 1px rgba(0,229,200,0.08)",
        }}
      >
        <button
          onClick={onClose}
          className="absolute top-3 right-3 p-1 rounded-md transition"
          style={{ color: "rgba(140,180,230,0.55)" }}
          onMouseOver={(e) => (e.currentTarget.style.color = "#00e5c8")}
          onMouseOut={(e) => (e.currentTarget.style.color = "rgba(140,180,230,0.55)")}
          aria-label="Close"
        >
          <X size={18} />
        </button>

        <h2 className="text-lg font-semibold mb-1" style={{ color: "#e8f4ff" }}>
          Choose upload mode
        </h2>
        <p className="text-xs mb-5" style={{ color: "rgba(140,180,230,0.6)" }}>
          Pick how you want to send your QAD customisation to the documentation pipeline.
        </p>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <ChoiceCard
            icon={<FilePlus size={20} />}
            title="Module-wise"
            subtitle="Existing flow"
            description="Upload one module's files (.p / .i / .xml or a small .zip). Generates a single System Documentation + Migration Blueprint for that module."
            onClick={() => { onChoose("module"); onClose(); }}
          />
          <ChoiceCard
            icon={<Layers size={20} />}
            title="Bulk Upload"
            subtitle="New — whole codebase"
            description="Upload a single .zip of your full customisation folder. The pipeline auto-segregates files into modules, then generates one doc + blueprint per module."
            onClick={() => { onChoose("bulk"); onClose(); }}
            accent
          />
        </div>

        <p className="text-[11px] mt-4" style={{ color: "rgba(140,180,230,0.35)" }}>
          Bulk Upload runs an 8-pass tagging pipeline first (Pass 0 → 4.5) — for 200-300 files
          expect ~5-15 minutes depending on LLM throughput. Per-module docs stream in as they
          finish; the final ZIP is downloadable when everything is done.
        </p>
      </div>
    </div>
  );
}

function ChoiceCard({ icon, title, subtitle, description, onClick, accent }) {
  return (
    <button
      onClick={onClick}
      className="text-left rounded-xl p-4 transition group"
      style={{
        background: accent ? "rgba(0,229,200,0.08)" : "rgba(8,15,32,0.7)",
        border: `1px solid ${accent ? "rgba(0,229,200,0.35)" : "rgba(0,229,200,0.15)"}`,
      }}
      onMouseOver={(e) => {
        e.currentTarget.style.background = accent ? "rgba(0,229,200,0.15)" : "rgba(0,229,200,0.06)";
        e.currentTarget.style.borderColor = "rgba(0,229,200,0.5)";
      }}
      onMouseOut={(e) => {
        e.currentTarget.style.background = accent ? "rgba(0,229,200,0.08)" : "rgba(8,15,32,0.7)";
        e.currentTarget.style.borderColor = accent ? "rgba(0,229,200,0.35)" : "rgba(0,229,200,0.15)";
      }}
    >
      <div className="flex items-start gap-2 mb-2">
        <span style={{ color: accent ? "#00e5c8" : "rgba(0,229,200,0.6)" }}>{icon}</span>
        <div>
          <div className="text-sm font-semibold" style={{ color: "#e8f4ff" }}>
            {title}
          </div>
          <div className="text-[11px]" style={{ color: accent ? "#00e5c8" : "rgba(140,180,230,0.55)" }}>
            {subtitle}
          </div>
        </div>
      </div>
      <p className="text-xs" style={{ color: "rgba(180,210,255,0.7)" }}>
        {description}
      </p>
    </button>
  );
}
