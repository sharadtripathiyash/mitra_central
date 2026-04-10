/**
 * DocCard — shown after a Word document is generated.
 * Shows: title, download button, and "Embed in Apex?" prompt.
 */
import { useState } from "react";
import { FileText, Download, Sparkles, Check, Loader } from "lucide-react";

export function DocCard({ doc, onEmbed }) {
  const [embedState, setEmbedState] = useState("idle"); // idle | loading | done | error

  if (!doc) return null;
  const { title, url } = doc;

  async function handleEmbed() {
    if (embedState !== "idle") return;
    setEmbedState("loading");
    try {
      await onEmbed?.(doc);
      setEmbedState("done");
    } catch (e) {
      console.error("Embed failed", e);
      setEmbedState("error");
    }
  }

  return (
    <div className="mt-4 rounded-xl border border-brand-200 bg-brand-50 overflow-hidden">
      {/* Download row */}
      <div className="flex items-center gap-3 px-4 py-3">
        <FileText className="w-5 h-5 text-brand-600 shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="text-sm font-semibold text-brand-800 truncate">{title}</div>
          <div className="text-xs text-brand-500">Word document ready</div>
        </div>
        {url && (
          <a
            href={url}
            download
            className="bg-brand-600 hover:bg-brand-700 text-white text-xs font-medium px-3 py-1.5 rounded-lg transition flex items-center gap-1.5"
          >
            <Download className="w-3 h-3" />
            Download
          </a>
        )}
      </div>

      {/* Embed in Apex prompt */}
      <div className="border-t border-brand-200 bg-white px-4 py-2.5 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-brand-500 shrink-0" />
          <span className="text-xs text-slate-600">
            Embed this in Apex for instant Q&amp;A on this module?
          </span>
        </div>

        {embedState === "idle" && (
          <div className="flex gap-2 shrink-0">
            <button
              onClick={handleEmbed}
              className="text-xs font-medium text-white bg-brand-600 hover:bg-brand-700 px-3 py-1 rounded-lg transition"
            >
              Yes, embed
            </button>
            <button
              onClick={() => setEmbedState("dismissed")}
              className="text-xs font-medium text-slate-500 hover:text-slate-700 px-3 py-1 rounded-lg border border-slate-200 hover:border-slate-300 transition"
            >
              Not now
            </button>
          </div>
        )}

        {embedState === "loading" && (
          <div className="flex items-center gap-1.5 text-xs text-brand-600 shrink-0">
            <Loader className="w-3.5 h-3.5 animate-spin" />
            Embedding…
          </div>
        )}

        {embedState === "done" && (
          <div className="flex items-center gap-1.5 text-xs text-emerald-600 shrink-0">
            <Check className="w-3.5 h-3.5" />
            Embedded in Apex
          </div>
        )}

        {embedState === "error" && (
          <div className="flex items-center gap-2 shrink-0">
            <span className="text-xs text-red-500">Embed failed</span>
            <button
              onClick={() => setEmbedState("idle")}
              className="text-xs text-brand-600 underline"
            >
              Retry
            </button>
          </div>
        )}

        {embedState === "dismissed" && (
          <span className="text-xs text-slate-400 shrink-0">Skipped</span>
        )}
      </div>
    </div>
  );
}
