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
  return `<div style="margin-top:8px;padding-top:8px;border-top:1px solid #e2e8f0;font-size:11px;color:#94a3b8">
    <span style="text-transform:uppercase;letter-spacing:.05em;font-size:9px">Source</span>
    <span style="margin-left:6px;color:#64748b">${label}</span>
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
    <div className="p-5 space-y-3">
      <p className="text-sm text-slate-700 font-medium">What area are you working in?</p>
      <div className="space-y-1">
        {OPTIONS.map((o) => (
          <label key={o.key} className="flex items-center gap-2.5 cursor-pointer p-2 rounded-lg hover:bg-slate-50 select-none">
            <input
              type="checkbox"
              checked={selected.includes(o.key)}
              onChange={() => toggle(o.key)}
              className="rounded border-slate-300 text-brand-600 focus:ring-brand-500"
            />
            <span className="text-sm text-slate-700">{o.label}</span>
          </label>
        ))}
      </div>
      <button
        onClick={() => selected.length && onConfirm(selected)}
        disabled={!selected.length}
        className={`w-full rounded-lg py-2 text-sm font-medium text-white transition
          ${selected.length ? "bg-brand-800 hover:bg-brand-900" : "bg-brand-800 opacity-40 cursor-not-allowed"}`}
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
      <div className={`rounded-2xl px-4 py-2.5 max-w-[85%] text-sm leading-relaxed
        ${isUser ? "bg-brand-700 text-white" : "bg-slate-100 text-slate-800"}`}>
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
          className="bg-brand-800 hover:bg-brand-900 text-white shadow-lg flex items-center justify-center px-2 py-5 rounded-l-lg transition"
          title="Ask Apex"
        >
          <span
            className="text-[11px] font-semibold"
            style={{ writingMode: "vertical-rl", transform: "rotate(180deg)", letterSpacing: "0.18em" }}
          >
            APEX
          </span>
        </button>
      )}

      {/* Panel */}
      {open && (
        <div className="w-96 h-[600px] bg-white rounded-l-2xl shadow-2xl border border-slate-200 flex flex-col overflow-hidden">

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
              <button onClick={() => setOpen(false)} className="p-1 hover:bg-white/10 rounded transition">
                <X size={16} />
              </button>
            </div>
          </div>

          {/* Domain picker */}
          {needsDomain && <DomainPicker onConfirm={confirmDomains} />}

          {/* Messages */}
          {!needsDomain && (
            <div ref={msgsRef} className="flex-1 overflow-y-auto p-4 space-y-3">
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
                  <div className="bg-slate-100 rounded-2xl px-4 py-2.5 text-sm text-slate-500">
                    Thinking…
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Input */}
          {!needsDomain && (
            <div className="p-3 border-t border-slate-200 shrink-0">
              <div className="flex gap-2">
                <input
                  ref={inputRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Ask about user guides…"
                  disabled={loading}
                  className="flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 disabled:opacity-50"
                />
                <button
                  onClick={() => sendMessage()}
                  disabled={!input.trim() || loading}
                  className="bg-brand-800 hover:bg-brand-900 disabled:opacity-40 text-white px-3 rounded-lg transition"
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
