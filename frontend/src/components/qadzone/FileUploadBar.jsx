/**
 * FileUploadBar — floating input bar with file upload support.
 * Used in both Query and Docs modes of QAD-Zone.
 */
import { useRef } from "react";
import { Paperclip, FileCode, Send } from "lucide-react";

export function FileUploadBar({ mode, input, setInput, uploadedFiles, onAddFiles, onRemoveFile, onSend, loading }) {
  const fileInputRef = useRef(null);

  const placeholder =
    mode === "documentation"
      ? uploadedFiles.length
        ? "Describe what to document, or press Enter…"
        : "e.g. Create documentation for the DOA module…"
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
      ? <>Tip: Upload .p .i .xml .zip files or specify a module, e.g. <em>"Document the DOA approval module"</em></>
      : mode === "documentation" && uploadedFiles.length > 0
      ? "Documentation will be generated from your uploaded code"
      : mode === "query" && uploadedFiles.length === 0
      ? <>Tip: Upload code files or ask e.g. <em>"What does xxdoaproc.p do?"</em></>
      : "Ask any question about your uploaded code";

  return (
    <div className="absolute bottom-8 left-1/2 -translate-x-1/2 w-full max-w-2xl px-4">
      <div className="relative flex items-center bg-white border border-slate-200 rounded-xl shadow-sm px-3 py-2">

        {/* Paperclip */}
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          title="Upload .p .i .xml or .zip"
          className="mr-1 shrink-0 text-slate-400 hover:text-slate-600 transition"
        >
          <Paperclip size={16} />
        </button>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".p,.i,.xml,.zip"
          className="hidden"
          onChange={(e) => {
            onAddFiles(e.target.files);
            e.target.value = "";
          }}
        />

        {/* File chips */}
        {uploadedFiles.length > 0 && (
          <div className="flex items-center gap-1 mr-1 flex-wrap max-w-[40%]">
            {uploadedFiles.map((f, idx) => (
              <span
                key={idx}
                className="inline-flex items-center gap-1 bg-brand-50 border border-brand-200 text-brand-700 rounded px-1.5 py-0.5 text-xs font-medium"
              >
                <FileCode size={12} className="shrink-0" />
                <span className="max-w-[80px] truncate">{f.name}</span>
                <button
                  type="button"
                  onClick={() => onRemoveFile(idx)}
                  className="ml-0.5 text-brand-400 hover:text-brand-700 leading-none"
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
          className="flex-1 text-sm outline-none bg-transparent placeholder:text-slate-400 disabled:opacity-50 py-1"
        />

        {/* Send */}
        <button
          onClick={onSend}
          disabled={!canSend}
          className="ml-2 h-8 w-16 rounded-lg bg-brand-800 hover:bg-brand-900 disabled:opacity-40 text-white text-sm flex items-center justify-center shrink-0 transition"
        >
          <Send size={14} />
        </button>
      </div>

      <p className="text-center text-xs text-slate-400 mt-2">{tip}</p>
    </div>
  );
}
