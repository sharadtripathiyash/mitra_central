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

// ── Card icons as inline SVGs ─────────────────────────────────────────────────
function IconSearch() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24"
      fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
    </svg>
  );
}
function IconDoc() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24"
      fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
      <polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/>
      <line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/>
    </svg>
  );
}
function IconUpgrade() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24"
      fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/>
      <polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>
    </svg>
  );
}

// ── Empty state ───────────────────────────────────────────────────────────────
function EmptyState({ onSwitch, activeMode }) {
  const cards = [
    {
      key: "query",
      label: "Search & Ask",
      sub: "Ask questions about your custom QAD programs in plain English — get instant answers",
      icon: <IconSearch />,
      color: "bg-blue-50 text-blue-600",
      activeColor: "border-blue-300 bg-blue-50",
    },
    {
      key: "documentation",
      label: "Generate Docs",
      sub: "Upload your custom code and get a ready-to-share Word document automatically",
      icon: <IconDoc />,
      color: "bg-violet-50 text-violet-600",
      activeColor: "border-violet-300 bg-violet-50",
    },
    {
      key: "modernisation",
      label: "Upgrade Analysis",
      sub: "See what will break and what needs updating when moving to a newer QAD version",
      icon: <IconUpgrade />,
      color: "bg-emerald-50 text-emerald-600",
      activeColor: "border-emerald-300 bg-emerald-50",
    },
  ];

  return (
    <div className="h-full flex flex-col items-center justify-center px-6 pb-32">
      <div className="text-center mb-10">
        <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-gradient-to-br from-blue-600 to-violet-600 shadow-lg mb-4">
          <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24"
            fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <ellipse cx="12" cy="5" rx="9" ry="3"/>
            <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/>
            <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>
          </svg>
        </div>
        <h1 className="text-3xl font-bold text-slate-800 tracking-tight">Modernization</h1>
        <p className="mt-2 text-sm text-slate-500 max-w-sm mx-auto leading-relaxed">
          Your custom code knowledge base — search, document, and modernise with AI.
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 w-full max-w-2xl">
        {cards.map((c) => {
          const isActive = activeMode === c.key;
          return (
            <button
              key={c.key}
              onClick={() => onSwitch(c.key)}
              className={`group flex flex-col text-left rounded-2xl p-5 border-2 transition-all duration-150
                ${isActive
                  ? `${c.activeColor} shadow-sm`
                  : "bg-white border-slate-200 hover:border-slate-300 hover:shadow-md"
                }`}
            >
              <div className={`h-10 w-10 rounded-xl ${c.color} shrink-0 flex items-center justify-center mb-3 transition-transform duration-150 ${isActive ? "scale-110" : "group-hover:scale-110"}`}>
                {c.icon}
              </div>
              <div className="text-sm font-semibold text-slate-800 mb-1">{c.label}</div>
              <div className="text-xs text-slate-400 leading-relaxed">{c.sub}</div>
            </button>
          );
        })}
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
  ws.onclose  = () => onDone(acc);

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

  const [modernForm, setModernForm] = useState({
    currentVersion: "", currentCustom: "", targetVersion: "", targetCustom: "",
  });
  const [modernLoading, setModernLoading]   = useState(false);
  const [modernStreaming, setModernStreaming] = useState(false);
  const [modernStatus, setModernStatus]     = useState("");
  const [modernLiveHtml, setModernLiveHtml] = useState("");
  const [modernResult, setModernResult]     = useState(null);

  const { uploadedFiles, addFiles, removeFile, clearFiles } = useFileUpload();
  const cachedFilesRef = useRef([]);
  const closeWsRef     = useRef(null);
  const chatDoneRef    = useRef(false);
  const modernDoneRef  = useRef(false);
  const bottomRef      = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, liveHtml, loading]);

  function switchMode(key) {
    setMode(key);
    clearFiles();
    setLiveHtml(""); setStatus(""); setStreaming(false);
  }

  async function handleEmbed(doc) {
    const res = await fetch(EMBED_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ doc_url: doc.url, title: doc.title }),
    });
    if (!res.ok) throw new Error(`Embed failed: ${res.status}`);
  }

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

  const sendChat = useCallback((overrideQuestion) => {
    const q        = overrideQuestion || input.trim();
    const hasFiles = uploadedFiles.length > 0;

    if (!q && !hasFiles) return;
    if (loading) return;

    if (hasFiles) cachedFilesRef.current = [...uploadedFiles];

    const displayText = overrideQuestion || q || `📎 ${uploadedFiles.map((f) => f.name).join(", ")}`;
    const question    = q || (mode === "documentation"
      ? "Generate documentation for the uploaded code"
      : "Analyse and explain the uploaded code");

    const filesToSend = hasFiles ? [...uploadedFiles] : (cachedFilesRef.current || []);

    setInput(""); clearFiles();
    setMessages((prev) => [...prev, { role: "user", text: displayText }]);
    setLoading(true); setStreaming(false); setStatus(""); setLiveHtml("");

    let extraDoc       = null;
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

  useEffect(() => {
    function handler(e) {
      const chip = e.target.closest("[data-followup]");
      if (!chip) return;
      if (e.target.closest("#apex-root")) return;
      const q = chip.dataset.followup;
      if (q) sendChat(q);
    }
    document.addEventListener("click", handler);
    return () => document.removeEventListener("click", handler);
  }, [sendChat]);

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

  const showEmpty = messages.length === 0 && mode !== "modernisation";

  return (
    <div className="flex-1 flex flex-col">

      {/* Header */}
      <header className="h-14 shrink-0 bg-white border-b border-slate-200 px-6 flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          {/* Mini logo in header */}
          <div className="h-7 w-7 rounded-lg bg-gradient-to-br from-blue-600 to-violet-600 flex items-center justify-center shrink-0">
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24"
              fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <ellipse cx="12" cy="5" rx="9" ry="3"/>
              <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/>
              <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>
            </svg>
          </div>
          <span className="text-sm font-semibold text-slate-700">Modernization</span>
          <span className="text-slate-300">|</span>
          <span className="text-sm text-slate-400 hidden sm:inline">
            Custom code knowledge base
          </span>
        </div>
        <ModeBar mode={mode} onChange={switchMode} />
      </header>

      {/* Body */}
      <div className="flex-1 relative overflow-hidden">
        <main className="h-full overflow-y-auto">

          {showEmpty && <EmptyState onSwitch={switchMode} activeMode={mode} />}

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
                      {m.doc && <DocCard doc={m.doc} onEmbed={handleEmbed} />}
                    </div>
                  )}
                </div>
              ))}

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