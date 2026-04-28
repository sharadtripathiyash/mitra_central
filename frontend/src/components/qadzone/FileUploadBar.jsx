/**
 * FileUploadBar — floating input bar with file upload support.
 * Dark teal theme matching the rest of the app.
 */
import { useRef } from "react";
import { Paperclip, FileCode, Send } from "lucide-react";

export function FileUploadBar({ mode, input, setInput, uploadedFiles, onAddFiles, onRemoveFile, onSend, loading }) {
  const fileInputRef = useRef(null);

  const placeholder =
    mode === "documentation"
      ? uploadedFiles.length
        ? "Describe what to document, or press Enter…"
        : "Attach .p / .i / .xml / .zip files to start…"
      : uploadedFiles.length
      ? "Ask about the uploaded code…"
      : "Ask about custom QAD programs…";

  const canSend = (input.trim() || uploadedFiles.length > 0) && !loading;

  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (canSend) onSend();
    }
  }

  const tip =
    mode === "documentation" && uploadedFiles.length === 0
      ? <>Upload .p .i .xml .zip files to generate documentation — uploads are required</>
      : mode === "documentation" && uploadedFiles.length > 0
      ? "Documentation will be generated from your uploaded code"
      : mode === "query" && uploadedFiles.length === 0
      ? <>Tip: Upload code files or ask e.g. <em>"What does xxdoaproc.p do?"</em></>
      : "Ask any question about your uploaded code";

  return (
    <div className="absolute bottom-8 left-1/2 -translate-x-1/2 w-full max-w-2xl px-4">
      <div
        className="relative flex items-center rounded-xl px-3 py-2"
        style={{
          background: "rgba(8,15,32,0.96)",
          border: "1px solid rgba(0,229,200,0.22)",
          boxShadow: "0 8px 32px rgba(0,0,0,0.4), 0 0 0 1px rgba(0,229,200,0.05)",
        }}
      >
        {/* Paperclip */}
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          title="Upload .p .i .xml or .zip"
          className="mr-1 shrink-0 transition"
          style={{ color: "rgba(0,229,200,0.5)" }}
          onMouseOver={e => e.currentTarget.style.color = "#00e5c8"}
          onMouseOut={e => e.currentTarget.style.color = "rgba(0,229,200,0.5)"}
        >
          <Paperclip size={16} />
        </button>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".p,.i,.xml,.zip"
          className="hidden"
          onChange={(e) => { onAddFiles(e.target.files); e.target.value = ""; }}
        />

        {/* File chips */}
        {uploadedFiles.length > 0 && (
          <div className="flex items-center gap-1 mr-1 flex-wrap max-w-[40%]">
            {uploadedFiles.map((f, idx) => (
              <span
                key={idx}
                className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium"
                style={{
                  background: "rgba(0,229,200,0.1)",
                  border: "1px solid rgba(0,229,200,0.25)",
                  color: "#00e5c8",
                }}
              >
                <FileCode size={12} className="shrink-0" />
                <span className="max-w-[80px] truncate">{f.name}</span>
                <button
                  type="button"
                  onClick={() => onRemoveFile(idx)}
                  className="ml-0.5 leading-none"
                  style={{ color: "rgba(0,229,200,0.5)" }}
                  onMouseOver={e => e.currentTarget.style.color = "#00e5c8"}
                  onMouseOut={e => e.currentTarget.style.color = "rgba(0,229,200,0.5)"}
                >
                  &times;
                </button>
              </span>
            ))}
          </div>
        )}

        {/* Text input */}
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          disabled={loading}
          className="flex-1 text-sm outline-none py-1"
          style={{
            background: "transparent",
            color: "#e8f4ff",
            opacity: loading ? 0.4 : 1,
          }}
        />

        {/* Send */}
        <button
          onClick={onSend}
          disabled={!canSend}
          className="ml-2 h-8 w-16 rounded-lg text-sm flex items-center justify-center shrink-0 transition"
          style={{
            background: canSend
              ? "linear-gradient(135deg, #00c9ae, #00e5c8)"
              : "rgba(0,229,200,0.15)",
            color: canSend ? "#060d1a" : "rgba(0,229,200,0.35)",
            cursor: canSend ? "pointer" : "not-allowed",
          }}
        >
          <Send size={14} />
        </button>
      </div>

      <p className="text-center text-xs mt-2" style={{ color: "rgba(140,180,230,0.3)" }}>{tip}</p>
    </div>
  );
}
