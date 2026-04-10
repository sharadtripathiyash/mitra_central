/**
 * QadZone.jsx — Main QAD-Zone React component.
 *
 * Modes:
 *   query        — RAG Q&A over custom programs, supports file upload
 *   documentation — Generate Word doc from code, supports file upload + Apex embed
 *   modernisation — Version migration analysis
 *
 * Upload logic:
 *   Files present + send → always sends uploaded_files in payload
 *   After doc is generated, cached code is reused for follow-up queries
 *   No files, text only → normal module-routing query
 */
import { useState, useCallback, useRef, useEffect } from "react";
import { ModeBar } from "./ModeBar";
import { FileUploadBar } from "./FileUploadBar";
import { ModernisationPanel } from "./ModernisationPanel";
import { DocCard } from "../shared/DocCard";
import { useFileUpload } from "../../hooks/useFileUpload";
import { renderMarkdown, escapeHtml, buildWsUrl } from "../../utils/helpers";

const WS_PATH   = window.__QADZONE_WS_PATH__ || "/agents/qadzone/ws";
const EMBED_URL = "/agents/qadzone/embed";

// ── Empty state ───────────────────────────────────────────────────────────────
function EmptyState({ onSwitch }) {
  const cards = [
  {
    key: "query",
    label: "Search & Ask",
    sub: "Ask questions about your custom QAD programs in plain English — get instant answers"
  },
  {
    key: "documentation",
    label: "Generate Docs",
    sub: "Upload your custom code and get a ready-to-share Word document automatically"
  },
  {
    key: "modernisation",
    label: "Upgrade Analysis",
    sub: "See what will break and what needs updating when moving to a newer QAD version"
  },
];
  return (
    <div className="h-full flex flex-col items-center justify-center px-6 pb-32">
      <div className="text-center mb-8">
        <h1 className="text-3xl font-semibold text-slate-800 tracking-tight">QAD-Zone</h1>
        <p className="mt-2 text-sm text-slate-500 max-w-xl mx-auto leading-relaxed">
          Custom code knowledge base, documentation &amp; modernisation.
        </p>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 w-full max-w-2xl">
        {cards.map((c) => (
          <button key={c.key} onClick={() => onSwitch(c.key)}
            className="group flex items-center gap-3 text-left bg-white border border-slate-200 rounded-lg p-4 hover:bg-slate-50 hover:border-slate-300 transition-all duration-150">
            <div className="h-10 w-10 rounded bg-slate-100 group-hover:bg-slate-200 shrink-0 flex items-center justify-center text-slate-600 font-bold text-sm">
              {c.label[0]}
            </div>
            <div>
              <div className="text-sm font-medium text-slate-700">{c.label}</div>
              <div className="text-xs text-slate-400 mt-0.5">{c.sub}</div>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

// ── WS send helper — returns cleanup fn ───────────────────────────────────────
function openWs(payload, { onToken, onStatus, onFrame, onDone, onError }) {
  let acc = "";
  const ws = new WebSocket(buildWsUrl(WS_PATH));

  ws.onopen = () => ws.send(JSON.stringify(payload));

  ws.onmessage = (evt) => {
    let frame;
    try { frame = JSON.parse(evt.data); } catch (_) { return; }
    const { type, data } = frame;
    switch (type) {
      case "token":   acc += data; onToken(acc);       break;
      case "status":  onStatus(data);                  break;
      case "error":   onError(data);                   break;
      case "done":    onDone(acc);                     break;
      default:        onFrame(type, data);             break;
    }
  };

  ws.onerror  = () => onError("Connection error. Please try again.");
  ws.onclose  = () => onDone(acc);   // safe — onDone is idempotent

  return () => { try { ws.close(); } catch (_) {} };
}

// ── Main component ────────────────────────────────────────────────────────────
export function QadZone() {
  const [mode, setMode]           = useState("query");
  const [input, setInput]         = useState("");
  const [messages, setMessages]   = useState([]);
  const [loading, setLoading]     = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [statusText, setStatus]   = useState("");
  const [liveHtml, setLiveHtml]   = useState("");

  // Modernisation
  const [modernForm, setModernForm] = useState({
    currentVersion: "", currentCustom: "", targetVersion: "", targetCustom: "",
  });
  const [modernLoading, setModernLoading]   = useState(false);
  const [modernStreaming, setModernStreaming] = useState(false);
  const [modernStatus, setModernStatus]     = useState("");
  const [modernLiveHtml, setModernLiveHtml] = useState("");
  const [modernResult, setModernResult]     = useState(null);

  // File upload
  const { uploadedFiles, addFiles, removeFile, clearFiles } = useFileUpload();

  // Cached uploaded code for follow-up queries after doc generation
  const cachedFilesRef = useRef([]);

  // WS cleanup ref
  const closeWsRef = useRef(null);

  // Done-guard refs — prevent double-finalise from onclose + done frame
  const chatDoneRef   = useRef(false);
  const modernDoneRef = useRef(false);

  // Scroll anchor
  const bottomRef = useRef(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, liveHtml, loading]);

  // ── mode switch ────────────────────────────────────────────────────────────
  function switchMode(key) {
    setMode(key);
    clearFiles();
    setLiveHtml(""); setStatus(""); setStreaming(false);
  }

  // ── Apex embed ─────────────────────────────────────────────────────────────
  async function handleEmbed(doc) {
    const res = await fetch(EMBED_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ doc_url: doc.url, title: doc.title }),
    });
    if (!res.ok) throw new Error(`Embed failed: ${res.status}`);
  }

  // ── build final HTML ───────────────────────────────────────────────────────
  function buildHtml(text, extras) {
    let html = "";
    if (text) html += `<div class="prose-content">${renderMarkdown(text)}</div>`;
    if (extras.followups?.length) {
      const chips = extras.followups.map((q) =>
        `<button class="apex-followup" data-followup="${escapeHtml(q)}">
          <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24"
            fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"
            stroke-linejoin="round" style="flex-shrink:0">
            <path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>
          </svg>
          <span>${escapeHtml(q)}</span>
        </button>`).join("");
      html += `<div class="mt-2 flex flex-col gap-1">${chips}</div>`;
    }
    return html || '<div class="text-slate-500 text-sm italic">No response received.</div>';
  }

  // ── send chat ──────────────────────────────────────────────────────────────
  const sendChat = useCallback((overrideQuestion) => {
    const q       = overrideQuestion || input.trim();
    const hasFiles = uploadedFiles.length > 0;

    if (!q && !hasFiles) return;
    if (loading) return;

    // Cache files for follow-up use
    if (hasFiles) cachedFilesRef.current = [...uploadedFiles];

    const displayText = overrideQuestion || q || `📎 ${uploadedFiles.map((f) => f.name).join(", ")}`;
    const question    = q || (mode === "documentation"
      ? "Generate documentation for the uploaded code"
      : "Analyse and explain the uploaded code");

    const filesToSend = hasFiles
      ? [...uploadedFiles]
      : (cachedFilesRef.current || []);

    setInput(""); clearFiles();
    setMessages((prev) => [...prev, { role: "user", text: displayText }]);
    setLoading(true); setStreaming(false); setStatus(""); setLiveHtml("");

    let extraDoc      = null;
    let extraFollowups = null;
    chatDoneRef.current = false;

    const closeWs = openWs(
      { mode, question, uploaded_files: filesToSend },
      {
        onToken:  (acc) => { setStreaming(true); setLiveHtml(renderMarkdown(acc)); },
        onStatus: (msg) => setStatus(msg),
        onFrame:  (type, data) => {
          if (type === "doc")      extraDoc       = data;
          if (type === "followup") extraFollowups = data;
        },
        onDone: (acc) => {
          if (chatDoneRef.current) return;
          chatDoneRef.current = true;
          const html = buildHtml(acc, { followups: extraFollowups });
          setMessages((prev) => [...prev, { role: "assistant", html, doc: extraDoc }]);
          setLoading(false); setStreaming(false); setStatus(""); setLiveHtml("");
          closeWsRef.current = null;
        },
        onError: (msg) => {
          if (chatDoneRef.current) return;
          chatDoneRef.current = true;
          setMessages((prev) => [...prev, {
            role: "assistant",
            html: `<div class="text-red-600 text-sm">${escapeHtml(msg)}</div>`,
            doc: null,
          }]);
          setLoading(false); setStreaming(false); setStatus(""); setLiveHtml("");
          closeWsRef.current = null;
        },
      }
    );
    closeWsRef.current = closeWs;
  }, [input, uploadedFiles, mode, loading, clearFiles]);

  // ── follow-up chip click delegation ───────────────────────────────────────
  useEffect(() => {
    function handler(e) {
      const chip = e.target.closest("[data-followup]");
      if (!chip) return;
      // Only handle clicks in the chat area (not inside Apex widget)
      if (e.target.closest("#apex-root")) return;
      const q = chip.dataset.followup;
      if (q) sendChat(q);
    }
    document.addEventListener("click", handler);
    return () => document.removeEventListener("click", handler);
  }, [sendChat]);

  // ── send modernisation ─────────────────────────────────────────────────────
  function sendModernisation() {
    const current = (modernForm.currentCustom || modernForm.currentVersion || "").trim();
    const target  = (modernForm.targetCustom  || modernForm.targetVersion  || "").trim();
    if (!current || !target || modernLoading) return;

    setModernLoading(true); setModernStreaming(false);
    setModernStatus(""); setModernLiveHtml(""); setModernResult(null);

    let extraDoc = null;
    modernDoneRef.current = false;

    const closeWs = openWs(
      { mode: "modernisation", current_version: current, target_version: target },
      {
        onToken:  (acc) => { setModernStreaming(true); setModernLiveHtml(renderMarkdown(acc)); },
        onStatus: (msg) => setModernStatus(msg),
        onFrame:  (type, data) => { if (type === "doc") extraDoc = data; },
        onDone: (acc) => {
          if (modernDoneRef.current) return;
          modernDoneRef.current = true;
          const html = buildHtml(acc, {});
          setModernResult(html);
          if (extraDoc) setMessages((prev) => [...prev, { role: "assistant", html, doc: extraDoc }]);
          setModernLoading(false); setModernStreaming(false);
          setModernStatus(""); setModernLiveHtml("");
          closeWsRef.current = null;
        },
        onError: (msg) => {
          if (modernDoneRef.current) return;
          modernDoneRef.current = true;
          setModernResult(`<div class="text-red-600 text-sm">${escapeHtml(msg)}</div>`);
          setModernLoading(false); setModernStreaming(false);
          setModernStatus(""); setModernLiveHtml("");
          closeWsRef.current = null;
        },
      }
    );
    closeWsRef.current = closeWs;
  }

  // ── render ─────────────────────────────────────────────────────────────────
  const showEmpty = messages.length === 0 && mode !== "modernisation";

  return (
    <div className="flex-1 flex flex-col">

      {/* Header */}
      <header className="h-14 shrink-0 bg-white border-b border-slate-200 px-6 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="text-sm font-semibold text-slate-700">QAD-Zone</span>
          <span className="text-slate-300">|</span>
          <span className="text-sm text-slate-400">
            Custom code knowledge base, documentation &amp; modernisation.
          </span>
        </div>
        <ModeBar mode={mode} onChange={switchMode} />
      </header>

      {/* Body */}
      <div className="flex-1 relative overflow-hidden">
        <main className="h-full overflow-y-auto">

          {/* Empty state */}
          {showEmpty && <EmptyState onSwitch={switchMode} />}

          {/* Modernisation panel */}
          {mode === "modernisation" && (
            <ModernisationPanel
              form={modernForm} setForm={setModernForm}
              onSubmit={sendModernisation}
              loading={modernLoading}
              streaming={modernStreaming}
              statusText={modernStatus}
              currentHtml={modernLiveHtml}
              result={modernResult}
            />
          )}

          {/* Chat thread — query + docs */}
          {mode !== "modernisation" && messages.length > 0 && (
            <div className="max-w-4xl mx-auto px-6 py-6 space-y-6">
              {messages.map((m, i) => (
                <div key={i}>
                  {m.role === "user" ? (
                    <div className="flex justify-end">
                      <div className="bg-brand-600 text-white rounded-2xl rounded-br-sm px-5 py-3 max-w-[85%] whitespace-pre-wrap text-sm">
                        {m.text}
                      </div>
                    </div>
                  ) : (
                    <div className="space-y-2">
                      <div
                        className="bg-white rounded-2xl shadow-sm border border-slate-200 p-5 prose-container"
                        dangerouslySetInnerHTML={{ __html: m.html }}
                      />
                      {/* DocCard with Apex embed prompt — rendered as React */}
                      {m.doc && <DocCard doc={m.doc} onEmbed={handleEmbed} />}
                    </div>
                  )}
                </div>
              ))}

              {/* Live streaming bubble */}
              {(streaming || (loading && statusText)) && (
                <div className="bg-white rounded-2xl shadow-sm border border-slate-200 p-5">
                  {statusText && (
                    <div className="flex items-center gap-2 text-sm text-brand-600 mb-2">
                      <span className="inline-block w-2 h-2 rounded-full bg-brand-500 animate-pulse" />
                      <span>{statusText}</span>
                    </div>
                  )}
                  {liveHtml && (
                    <div className="prose-container" dangerouslySetInnerHTML={{ __html: liveHtml }} />
                  )}
                  {streaming && (
                    <span className="inline-block w-1.5 h-4 bg-brand-500 animate-pulse ml-0.5 align-middle" />
                  )}
                </div>
              )}

              {/* Thinking */}
              {loading && !streaming && !statusText && (
                <div className="flex items-center gap-2 text-slate-500 text-sm">
                  <span className="inline-block w-2 h-2 rounded-full bg-brand-500 animate-pulse" />
                  <span>Thinking…</span>
                </div>
              )}

              <div className="h-24" ref={bottomRef} />
            </div>
          )}
        </main>

        {/* Floating input bar */}
        {mode !== "modernisation" && (
          <FileUploadBar
            mode={mode}
            input={input}
            setInput={setInput}
            uploadedFiles={uploadedFiles}
            onAddFiles={addFiles}
            onRemoveFile={removeFile}
            onSend={() => sendChat()}
            loading={loading}
          />
        )}
      </div>
    </div>
  );
}
