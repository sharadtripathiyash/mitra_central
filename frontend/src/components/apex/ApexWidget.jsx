/**
 * ApexWidget.jsx — Apex floating RAG chatbot as React component.
 *
 * Features:
 *  - Floating ribbon launcher (right side of screen)
 *  - Domain picker on first open
 *  - ModuleDropdown in header for in-session domain switching (no refresh)
 *  - WebSocket streaming with follow-up chips
 *  - custom_docs domain queries the qad_custom_docs Qdrant collection
 */
import { useState, useEffect, useRef, useCallback } from "react";
import { Sparkles, X, Send } from "lucide-react";
import { ModuleDropdown } from "./ModuleDropdown";
import { renderMarkdown, escapeHtml, buildWsUrl } from "../../utils/helpers";

const WS_PATH = "/agents/apex/ws";

function buildSourcesHtml(sources) {
  if (!sources?.length) return "";
  const top = sources[0];
  const mod   = top.module   ? `<span style="text-transform:capitalize">${escapeHtml(top.module)}</span>` : "";
  const file  = top.filename ? escapeHtml(top.filename) : "";
  const score = top.score    ? `<span style="opacity:0.6">${Math.round(top.score * 100)}% match</span>` : "";
  const label = [mod, file, score].filter(Boolean).join(" · ");
  return `<div style="margin-top:8px;padding-top:8px;border-top:1px solid rgba(0,229,200,0.15);font-size:11px;color:rgba(140,180,230,0.5)">
    <span style="text-transform:uppercase;letter-spacing:.05em;font-size:9px;color:rgba(0,229,200,0.6)">Source</span>
    <span style="margin-left:6px">${label}</span>
  </div>`;
}

function buildFollowupsHtml(list) {
  if (!list?.length) return "";
  const chips = list.map((q) =>
    `<button class="apex-followup" data-apex-followup="${escapeHtml(q)}">
      <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24"
        fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"
        stroke-linejoin="round" style="flex-shrink:0">
        <path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>
      </svg>
      <span>${escapeHtml(q)}</span>
    </button>`
  ).join("");
  return `<div class="mt-2 flex flex-col gap-1">${chips}</div>`;
}

// ── Domain picker ─────────────────────────────────────────────────────────────
function DomainPicker({ onConfirm }) {
  const [selected, setSelected] = useState([]);
  const OPTIONS = [
    { key: "sales",         label: "Sales"         },
    { key: "purchasing",    label: "Purchasing"     },
    { key: "manufacturing", label: "Manufacturing"  },
    { key: "custom_docs",   label: "Custom Modules" },
  ];

  function toggle(k) {
    setSelected((p) => p.includes(k) ? p.filter((x) => x !== k) : [...p, k]);
  }

  return (
    <div className="p-5 space-y-3" style={{ background: "rgba(8,15,32,0.98)" }}>
      <p className="text-sm font-medium" style={{ color: "rgba(180,210,255,0.7)" }}>What area are you working in?</p>
      <div className="space-y-1">
        {OPTIONS.map((o) => (
          <label key={o.key}
            className="flex items-center gap-2.5 cursor-pointer p-2 rounded-lg select-none apex-smooth apex-widget-select-row"
            style={{ color: selected.includes(o.key) ? "#00e5c8" : "rgba(180,210,255,0.6)" }}
          >
            <input
              type="checkbox"
              checked={selected.includes(o.key)}
              onChange={() => toggle(o.key)}
              style={{ accentColor: "#00e5c8" }}
            />
            <span className="text-sm">{o.label}</span>
          </label>
        ))}
      </div>
      <button
        onClick={() => selected.length && onConfirm(selected)}
        disabled={!selected.length}
        className="w-full rounded-lg py-2 text-sm font-bold apex-smooth apex-widget-primary-btn"
        style={{
          background: selected.length
            ? "linear-gradient(135deg, #00c9ae, #00e5c8)"
            : "rgba(0,229,200,0.1)",
          color: selected.length ? "#060d1a" : "rgba(0,229,200,0.3)",
          border: "none",
          cursor: selected.length ? "pointer" : "not-allowed",
        }}
      >
        Continue
      </button>
    </div>
  );
}

// ── Message bubble ────────────────────────────────────────────────────────────
function Bubble({ msg }) {
  const isUser = msg.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div className="rounded-2xl px-4 py-2.5 max-w-[85%] text-sm leading-relaxed"
        style={isUser
          ? { background: "linear-gradient(135deg,#00c9ae,#00e5c8)", color: "#060d1a", fontWeight: 500 }
          : { background: "rgba(10,20,42,0.9)", border: "1px solid rgba(0,229,200,0.14)", color: "#e8f4ff" }
        }>
        <div
          className="prose prose-sm max-w-none"
          dangerouslySetInnerHTML={{ __html: msg.html || escapeHtml(msg.text || "") }}
        />
      </div>
    </div>
  );
}

// ── Main ApexWidget ───────────────────────────────────────────────────────────
export function ApexWidget() {
  const [open, setOpen]               = useState(false);
  const [needsDomain, setNeedsDomain] = useState(true);
  const [domains, setDomains]         = useState([]);
  const [messages, setMessages]       = useState([]);
  const [input, setInput]             = useState("");
  const [loading, setLoading]         = useState(false);
  const [streaming, setStreaming]     = useState(false);
  const [liveHtml, setLiveHtml]       = useState("");

  const msgsRef  = useRef(null);
  const inputRef = useRef(null);
  const panelRef = useRef(null);
  const doneRef  = useRef(false);   // prevents double-finalise
  const domainsRef = useRef([]);    // always-current domains for WS callbacks

  // Keep domainsRef in sync
  useEffect(() => { domainsRef.current = domains; }, [domains]);

  // Scroll to bottom
  useEffect(() => {
    if (msgsRef.current) msgsRef.current.scrollTop = msgsRef.current.scrollHeight;
  }, [messages, liveHtml]);

  // Load saved context
  useEffect(() => {
    fetch("/agents/apex/context", { credentials: "same-origin" })
      .then((r) => r.ok ? r.json() : null)
      .then((d) => {
        if (d?.domains?.length) {
          setDomains(d.domains);
          domainsRef.current = d.domains;
          setNeedsDomain(false);
        }
      })
      .catch(() => {});
  }, []);

  // Focus input when panel opens
  useEffect(() => {
    if (open && !needsDomain) setTimeout(() => inputRef.current?.focus(), 120);
  }, [open, needsDomain]);

  // Close panel when clicking outside
  useEffect(() => {
    if (!open) return;

    function handlePointerDown(e) {
      if (panelRef.current && !panelRef.current.contains(e.target)) {
        setOpen(false);
      }
    }

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("touchstart", handlePointerDown);

    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("touchstart", handlePointerDown);
    };
  }, [open]);

  // Follow-up chip delegation — use data-apex-followup to scope to Apex only
  useEffect(() => {
    function handler(e) {
      const chip = e.target.closest("[data-apex-followup]");
      if (chip && !loading && open && !needsDomain) {
        sendMessage(chip.dataset.apexFollowup);
      }
    }
    document.addEventListener("click", handler);
    return () => document.removeEventListener("click", handler);
  }, [loading, open, needsDomain]);

  // ── Domain confirmed ────────────────────────────────────────────────────────
  function confirmDomains(selected) {
    setDomains(selected);
    domainsRef.current = selected;
    setNeedsDomain(false);
  }

  // ── Domain changed mid-session ──────────────────────────────────────────────
  function handleDomainChange(newDomains) {
    setDomains(newDomains);
    domainsRef.current = newDomains;
    fetch("/agents/apex/context", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ domains: newDomains }),
    }).catch(() => {});
  }

  // ── Send message ────────────────────────────────────────────────────────────
  const sendMessage = useCallback((textOverride) => {
    const q = (textOverride || input || "").trim();
    if (!q || loading) return;
    setInput("");
    setMessages((prev) => [...prev, { role: "user", text: q }]);
    setLoading(true); setStreaming(false); setLiveHtml("");

    let acc         = "";
    let accSources  = null;
    let accFollowups = null;
    doneRef.current  = false;

    const ws = new WebSocket(buildWsUrl(WS_PATH));

    ws.onopen = () => ws.send(JSON.stringify({ question: q, domains: domainsRef.current }));

    ws.onmessage = (evt) => {
      let frame;
      try { frame = JSON.parse(evt.data); } catch (_) { return; }
      const { type, data } = frame;
      switch (type) {
        case "token":
          setStreaming(true);
          acc += data;
          setLiveHtml(renderMarkdown(acc));
          break;
        case "sources":  accSources   = data; break;
        case "followup": accFollowups = data; break;
        case "error":    finish(`<span class="text-red-600 text-sm">${escapeHtml(data)}</span>`); break;
        case "done":     finish(buildFinalHtml()); break;
      }
    };

    ws.onerror  = () => finish('<span class="text-red-600 text-sm">Connection error.</span>');
    ws.onclose  = () => { if (!doneRef.current) finish(buildFinalHtml()); };

    function buildFinalHtml() {
      let html = acc ? renderMarkdown(acc) : "";
      html += buildSourcesHtml(accSources);
      html += buildFollowupsHtml(accFollowups);
      return html || "(no response)";
    }

    function finish(html) {
      if (doneRef.current) return;
      doneRef.current = true;
      setMessages((prev) => [...prev, { role: "assistant", html }]);
      setLoading(false); setStreaming(false); setLiveHtml("");
      try { ws.close(); } catch (_) {}
    }
  }, [input, loading]);

  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  }

  // ── Render ──────────────────────────────────────────────────────────────────
  return (
    <div className="fixed right-0 top-1/2 -translate-y-1/2 z-50 flex items-center">

      {/* Ribbon */}
      {!open && (
        <button
          onClick={() => setOpen(true)}
          className="bg-brand-800 text-white shadow-lg flex items-center justify-center px-2 py-5 rounded-l-lg apex-smooth apex-widget-ribbon"
          title="Ask Apex"
        >
          <span
            className="text-[11px] font-semibold"
            style={{ writingMode: "vertical-rl", transform: "rotate(180deg)", letterSpacing: "0.18em" }}
          >
            Apex Assistant
          </span>
        </button>
      )}

      {/* Panel */}
      {open && (
        <div ref={panelRef} className="w-96 h-[600px] rounded-l-2xl shadow-2xl flex flex-col overflow-hidden"
          style={{ background: "rgba(8,15,32,0.98)", border: "1px solid rgba(0,229,200,0.2)", borderRight: "none" }}>

          {/* Header */}
          <div className="h-14 px-4 flex items-center justify-between bg-brand-800 text-white shrink-0">
            <div className="flex items-center gap-2">
              <Sparkles size={18} />
              <div>
                <div className="font-semibold text-sm leading-tight">Apex</div>
                <div className="text-[10px] opacity-75">User guide assistant</div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              {!needsDomain && (
                <ModuleDropdown selected={domains} onChange={handleDomainChange} />
              )}
              <button onClick={() => setOpen(false)} className="p-1 rounded apex-smooth apex-widget-icon-btn">
                <X size={16} />
              </button>
            </div>
          </div>

          {/* Domain picker */}
          {needsDomain && <DomainPicker onConfirm={confirmDomains} />}

          {/* Messages */}
          {!needsDomain && (
            <div ref={msgsRef} className="flex-1 overflow-y-auto p-4 space-y-3"
              style={{ background: "rgba(6,13,26,0.6)" }}>
              {messages.map((m, i) => <Bubble key={i} msg={m} />)}

              {/* Streaming bubble */}
              {streaming && (
                <div className="flex justify-start">
                  <div className="bg-slate-100 text-slate-800 rounded-2xl px-4 py-2.5 max-w-[85%] text-sm">
                    <div
                      className="prose prose-sm max-w-none"
                      dangerouslySetInnerHTML={{ __html: liveHtml }}
                    />
                    <span className="inline-block w-1 h-3 bg-brand-500 animate-pulse align-middle ml-0.5" />
                  </div>
                </div>
              )}

              {loading && !streaming && (
                <div className="flex justify-start">
                  <div className="rounded-2xl px-4 py-2.5 text-sm"
                    style={{ background: "rgba(10,20,42,0.9)", border: "1px solid rgba(0,229,200,0.14)", color: "rgba(180,210,255,0.5)" }}>
                    Thinking…
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Input */}
          {!needsDomain && (
            <div className="p-3 shrink-0"
              style={{ borderTop: "1px solid rgba(0,229,200,0.12)", background: "rgba(6,13,26,0.8)" }}>
              <div className="flex gap-2">
                <input
                  ref={inputRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Ask about user guides…"
                  disabled={loading}
                  className="flex-1 rounded-lg px-3 py-2 text-sm outline-none apex-widget-input"
                  style={{
                    background: "rgba(5,15,35,0.8)",
                    border: "1px solid rgba(0,229,200,0.15)",
                    color: "#e8f4ff",
                    opacity: loading ? 0.5 : 1,
                  }}
                  onFocus={e => e.target.style.borderColor = "rgba(0,229,200,0.5)"}
                  onBlur={e => e.target.style.borderColor = "rgba(0,229,200,0.15)"}
                />
                <button
                  onClick={() => sendMessage()}
                  disabled={!input.trim() || loading}
                  className="px-3 rounded-lg apex-smooth apex-widget-send"
                  style={{
                    background: (input.trim() && !loading)
                      ? "linear-gradient(135deg,#00c9ae,#00e5c8)"
                      : "rgba(0,229,200,0.12)",
                    color: (input.trim() && !loading) ? "#060d1a" : "rgba(0,229,200,0.3)",
                    border: "none",
                    cursor: (input.trim() && !loading) ? "pointer" : "not-allowed",
                  }}
                >
                  <Send size={16} />
                </button>
              </div>
            </div>
          )}

        </div>
      )}
    </div>
  );
}
