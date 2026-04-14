/**
 * QadZone.jsx — Main QAD-Zone React component.
 *
 * Modes:
 *   query          — RAG Q&A over custom programs, supports file upload
 *   documentation  — Generate Word doc from code OR show demo panel for known ZIPs
 *   modernisation  — Version migration analysis
 *
 * Demo panel (documentation mode):
 *   Upload MRN.zip / DOA.zip / RTDC.zip → shows two tabs:
 *     Summary       — Visual dashboard (replaceability %, confidence, impact, effort)
 *     Documentation — Download pre-stored docx + embed to knowledge base
 *
 * Original LLM doc-generation still works for unknown ZIPs (not removed).
 */
import { useState, useCallback, useRef, useEffect } from "react";
import { ModeBar } from "./ModeBar";
import { FileUploadBar } from "./FileUploadBar";
import { ModernisationPanel } from "./ModernisationPanel";
import { DocCard } from "../shared/DocCard";
import { useFileUpload } from "../../hooks/useFileUpload";
import { renderMarkdown, escapeHtml, buildWsUrl } from "../../utils/helpers";

const WS_PATH        = window.__QADZONE_WS_PATH__ || "/agents/qadzone/ws";
const EMBED_URL      = "/agents/qadzone/embed";
const DEMO_EMBED_URL = "/agents/qadzone/demo-embed";
const DEMO_DOC_URL   = (name) => `/agents/qadzone/demo-doc/${name}`;

// ── Demo data (hardcoded for 3 known customizations) ─────────────────────────
const DEMO_DATA = {
  MRN: {
    title: "Material Return Note",
    description: "Manages customer product returns, replacement processing and automatic credit note generation within QAD ERP.",
    replaceability: 72,
    confidence: 85,
    businessImpact:  { label: "High",   bg: "bg-red-100",    text: "text-red-700"    },
    migrationEffort: { label: "Medium", bg: "bg-yellow-100", text: "text-yellow-700" },
    modules: ["Sales", "Inventory", "Finance"],
    docFilename: "MRN_System_Documentation.docx",
  },
  DOA: {
    title: "Dead on Arrival Processing",
    description: "Handles defective goods received from suppliers, manages supplier claims and initiates return-to-vendor workflows.",
    replaceability: 58,
    confidence: 78,
    businessImpact:  { label: "Medium", bg: "bg-yellow-100", text: "text-yellow-700" },
    migrationEffort: { label: "Low",    bg: "bg-green-100",  text: "text-green-700"  },
    modules: ["Purchasing", "Inventory", "Quality"],
    docFilename: "DOA_System_Documentation.docx",
  },
  RTDC: {
    title: "Real-Time Data Collection",
    description: "Captures production floor transactions in real time with barcode integration, shift management and live shop-floor reporting.",
    replaceability: 45,
    confidence: 70,
    businessImpact:  { label: "High", bg: "bg-red-100", text: "text-red-700" },
    migrationEffort: { label: "High", bg: "bg-red-100", text: "text-red-700" },
    modules: ["Manufacturing", "WIP", "Inventory"],
    docFilename: "RTDC_System_Documentation.docx",
  },
};

function detectCustomization(files) {
  for (const f of files) {
    const base = f.name.replace(/\.zip$/i, "").toUpperCase();
    if (DEMO_DATA[base]) return base;
  }
  return null;
}

// ── Analysis steps shown during 10s fake loading ─────────────────────────────
const ANALYSIS_STEPS = [
  { icon: "📂", text: "Reading uploaded files…"              },
  { icon: "🔍", text: "Scanning program structure…"          },
  { icon: "🧠", text: "Identifying customization type…"      },
  { icon: "📊", text: "Evaluating QAD Adaptive coverage…"    },
  { icon: "⚙️",  text: "Assessing migration complexity…"     },
  { icon: "💼", text: "Measuring business impact…"           },
  { icon: "✅", text: "Building analysis report…"            },
];

function DemoLoader({ filename }) {
  const [stepIdx, setStepIdx] = useState(0);
  const [done, setDone]       = useState(false);

  useEffect(() => {
    const interval = setInterval(() => {
      setStepIdx((prev) => {
        if (prev >= ANALYSIS_STEPS.length - 1) { clearInterval(interval); return prev; }
        return prev + 1;
      });
    }, 1300);
    const doneTimer = setTimeout(() => setDone(true), 9500);
    return () => { clearInterval(interval); clearTimeout(doneTimer); };
  }, []);

  return (
    <div className="max-w-xl mx-auto px-4 py-16 flex flex-col items-center gap-6">
      {/* Spinning ring */}
      <div className="relative h-16 w-16">
        <svg className="animate-spin" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
          <circle cx="32" cy="32" r="28" stroke="#e2e8f0" strokeWidth="6" />
          <path d="M32 4a28 28 0 0 1 28 28" stroke="url(#grad)" strokeWidth="6" strokeLinecap="round" />
          <defs>
            <linearGradient id="grad" x1="32" y1="4" x2="60" y2="32" gradientUnits="userSpaceOnUse">
              <stop stopColor="#6366f1" /><stop offset="1" stopColor="#3b82f6" />
            </linearGradient>
          </defs>
        </svg>
        <div className="absolute inset-0 flex items-center justify-center text-xl">
          {done ? "✅" : ANALYSIS_STEPS[stepIdx].icon}
        </div>
      </div>

      {/* Title */}
      <div className="text-center">
        <div className="text-base font-bold text-slate-800">
          {done ? "Analysis complete!" : "Analyzing customization…"}
        </div>
        <div className="text-xs text-slate-500 mt-1">{filename}</div>
      </div>

      {/* Step list */}
      <div className="w-full space-y-2">
        {ANALYSIS_STEPS.map((s, i) => {
          const isActive = i === stepIdx && !done;
          const isDone   = i < stepIdx || done;
          return (
            <div key={i} className={`flex items-center gap-3 px-4 py-2.5 rounded-xl transition-all duration-300
              ${isActive ? "bg-indigo-50 border border-indigo-200 shadow-sm" : isDone ? "opacity-50" : "opacity-20"}`}>
              <span className="text-base w-5 text-center shrink-0">{isDone || isActive ? s.icon : "○"}</span>
              <span className={`text-sm ${isActive ? "font-semibold text-indigo-700" : "text-slate-600"}`}>
                {s.text}
              </span>
              {isDone && !isActive && (
                <span className="ml-auto text-green-500 text-xs font-bold shrink-0">✓</span>
              )}
              {isActive && (
                <span className="ml-auto flex gap-0.5 shrink-0">
                  {[0,1,2].map((d) => (
                    <span key={d} className="inline-block w-1 h-1 rounded-full bg-indigo-400 animate-bounce"
                      style={{ animationDelay: `${d * 150}ms` }} />
                  ))}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Card icons ────────────────────────────────────────────────────────────────
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
    { key: "query",         label: "Search & Ask",    sub: "Ask questions about your custom QAD programs in plain English — get instant answers", icon: <IconSearch />, color: "bg-blue-50 text-blue-600",    activeColor: "border-blue-300 bg-blue-50"     },
    { key: "documentation", label: "Generate Docs",   sub: "Upload your custom code and get a ready-to-share Word document automatically",        icon: <IconDoc />,    color: "bg-violet-50 text-violet-600", activeColor: "border-violet-300 bg-violet-50"  },
    { key: "modernisation", label: "Upgrade Analysis",sub: "See what will break and what needs updating when moving to a newer QAD version",       icon: <IconUpgrade />,color: "bg-emerald-50 text-emerald-600",activeColor: "border-emerald-300 bg-emerald-50"},
  ];
  return (
    <div className="h-full flex flex-col items-center justify-center px-6 pb-32">
      <div className="text-center mb-10">
        <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-gradient-to-br from-blue-600 to-violet-600 shadow-lg mb-4">
          <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24"
            fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/>
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
            <button key={c.key} onClick={() => onSwitch(c.key)}
              className={`group flex flex-col text-left rounded-2xl p-5 border-2 transition-all duration-150 ${isActive ? `${c.activeColor} shadow-sm` : "bg-white border-slate-200 hover:border-slate-300 hover:shadow-md"}`}>
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

// ── SVG circular progress ─────────────────────────────────────────────────────
function CircleProgress({ value, stroke = "#6366f1", size = 96 }) {
  const r    = (size - 16) / 2;
  const circ = 2 * Math.PI * r;
  const offset = circ - (value / 100) * circ;
  return (
    <svg width={size} height={size} className="block">
      <circle cx={size/2} cy={size/2} r={r} fill="none" stroke="#e2e8f0" strokeWidth="8" />
      <circle cx={size/2} cy={size/2} r={r} fill="none" stroke={stroke} strokeWidth="8"
        strokeDasharray={`${circ}`} strokeDashoffset={offset} strokeLinecap="round"
        style={{ transform:"rotate(-90deg)", transformOrigin:"50% 50%", transition:"stroke-dashoffset 0.6s ease" }} />
      <text x={size/2} y={size/2} textAnchor="middle" dy="0.35em" fontSize="15" fontWeight="700" fill="#1e293b">
        {value}%
      </text>
    </svg>
  );
}

// ── Summary tab ───────────────────────────────────────────────────────────────
function SummaryTab({ data }) {
  return (
    <div className="p-6 space-y-5">
      <div className="bg-white rounded-2xl border border-slate-200 p-5 shadow-sm">
        <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1">What this customization does</div>
        <p className="text-slate-700 text-sm leading-relaxed">{data.description}</p>
        <div className="flex flex-wrap gap-1.5 mt-3">
          {data.modules.map((m) => (
            <span key={m} className="text-xs bg-slate-100 text-slate-600 px-2.5 py-0.5 rounded-full font-medium">{m}</span>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <div className="bg-white rounded-2xl border border-slate-200 p-4 shadow-sm flex flex-col items-center gap-2">
          <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400 text-center leading-tight">QAD Adaptive<br/>Replaceability</div>
          <CircleProgress value={data.replaceability} stroke="#6366f1" />
          <div className="text-[11px] text-slate-500 text-center">
            {data.replaceability >= 70 ? "Highly replaceable" : data.replaceability >= 50 ? "Partially replaceable" : "Custom build needed"}
          </div>
        </div>

        <div className="bg-white rounded-2xl border border-slate-200 p-4 shadow-sm flex flex-col items-center gap-2">
          <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400 text-center leading-tight">Analysis<br/>Confidence</div>
          <CircleProgress value={data.confidence} stroke="#22c55e" />
          <div className="text-[11px] text-slate-500 text-center">
            {data.confidence >= 80 ? "High confidence" : data.confidence >= 60 ? "Moderate confidence" : "Low confidence"}
          </div>
        </div>

        <div className="bg-white rounded-2xl border border-slate-200 p-4 shadow-sm flex flex-col items-center gap-3">
          <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400 text-center leading-tight">Business<br/>Impact</div>
          <div className={`mt-3 px-5 py-2 rounded-xl text-sm font-bold ${data.businessImpact.bg} ${data.businessImpact.text}`}>
            {data.businessImpact.label}
          </div>
          <div className="text-[11px] text-slate-500 text-center leading-relaxed">
            {data.businessImpact.label === "High" ? "Critical to business operations" : data.businessImpact.label === "Medium" ? "Moderate operational dependency" : "Low operational dependency"}
          </div>
        </div>

        <div className="bg-white rounded-2xl border border-slate-200 p-4 shadow-sm flex flex-col items-center gap-3">
          <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400 text-center leading-tight">Migration<br/>Effort</div>
          <div className={`mt-3 px-5 py-2 rounded-xl text-sm font-bold ${data.migrationEffort.bg} ${data.migrationEffort.text}`}>
            {data.migrationEffort.label}
          </div>
          <div className="text-[11px] text-slate-500 text-center leading-relaxed">
            {data.migrationEffort.label === "High" ? "Significant rework required" : data.migrationEffort.label === "Medium" ? "Moderate development effort" : "Minimal changes needed"}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Documentation tab ─────────────────────────────────────────────────────────
function DocTab({ custName, data }) {
  const [embedState, setEmbedState] = useState("idle");

  function handleEmbed() {
    setEmbedState("loading");
    fetch(DEMO_EMBED_URL, { method: "POST", credentials: "same-origin" }).catch(() => {});
    setTimeout(() => setEmbedState("done"), 3000);
  }

  return (
    <div className="p-6 space-y-4">
      <div className="bg-white rounded-2xl border border-slate-200 p-5 shadow-sm flex items-center justify-between gap-4">
        <div className="flex items-center gap-3 min-w-0">
          <div className="h-10 w-10 rounded-xl bg-blue-50 text-blue-600 flex items-center justify-center shrink-0">
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24"
              fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
              <polyline points="14 2 14 8 20 8"/>
              <line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
            </svg>
          </div>
          <div className="min-w-0">
            <div className="text-sm font-semibold text-slate-800 truncate">{data.docFilename}</div>
            <div className="text-xs text-slate-500">{data.title} — System Documentation</div>
          </div>
        </div>
        <a href={DEMO_DOC_URL(custName)} download={data.docFilename}
          className="shrink-0 px-4 py-2 bg-brand-800 hover:bg-brand-900 text-white text-xs font-semibold rounded-lg transition flex items-center gap-1.5">
          <svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24"
            fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
          </svg>
          Download
        </a>
      </div>

      <div className="bg-white rounded-2xl border border-slate-200 p-5 shadow-sm">
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="text-sm font-semibold text-slate-800">Add to Knowledge Base</div>
            <div className="text-xs text-slate-500 mt-0.5">Embed this document into the Apex search index</div>
          </div>
          <button onClick={handleEmbed} disabled={embedState !== "idle"}
            className={`shrink-0 px-4 py-2 text-xs font-semibold rounded-lg transition flex items-center gap-1.5 ${embedState === "done" ? "bg-green-100 text-green-700 cursor-default" : embedState === "loading" ? "bg-slate-100 text-slate-500 cursor-not-allowed" : "bg-violet-600 hover:bg-violet-700 text-white"}`}>
            {embedState === "loading" && (
              <svg className="animate-spin" xmlns="http://www.w3.org/2000/svg" width="13" height="13"
                viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
                strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
              </svg>
            )}
            {embedState === "done" && "✓ Done"}
            {embedState === "loading" && "Embedding…"}
            {embedState === "idle" && "Embed"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Demo panel (two tabs) ─────────────────────────────────────────────────────
function DemoPanel({ custName }) {
  const [tab, setTab] = useState("summary");
  const data = DEMO_DATA[custName];
  return (
    <div className="max-w-4xl mx-auto px-4 py-6">
      <div className="mb-5 flex items-center gap-3">
        <div className="h-9 w-9 rounded-xl bg-gradient-to-br from-blue-600 to-violet-600 flex items-center justify-center shrink-0">
          <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24"
            fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
          </svg>
        </div>
        <div>
          <div className="text-base font-bold text-slate-800">{data.title}</div>
          <div className="text-xs text-slate-500">Customization Analysis · {custName}</div>
        </div>
      </div>

      <div className="flex gap-1 bg-slate-100 p-1 rounded-xl w-fit mb-5">
        {[{key:"summary",label:"Summary"},{key:"documentation",label:"Documentation"}].map((t) => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`px-5 py-1.5 rounded-lg text-sm font-medium transition ${tab === t.key ? "bg-white text-slate-800 shadow-sm" : "text-slate-500 hover:text-slate-700"}`}>
            {t.label}
          </button>
        ))}
      </div>

      <div className="bg-slate-50 rounded-2xl border border-slate-200 overflow-hidden">
        {tab === "summary"       && <SummaryTab data={data} />}
        {tab === "documentation" && <DocTab custName={custName} data={data} />}
      </div>
    </div>
  );
}

// ── WS send helper ────────────────────────────────────────────────────────────
function openWs(payload, { onToken, onStatus, onFrame, onDone, onError }) {
  let acc = "";
  const ws = new WebSocket(buildWsUrl(WS_PATH));
  ws.onopen    = () => ws.send(JSON.stringify(payload));
  ws.onmessage = (evt) => {
    let frame;
    try { frame = JSON.parse(evt.data); } catch (_) { return; }
    const { type, data } = frame;
    switch (type) {
      case "token":  acc += data; onToken(acc);  break;
      case "status": onStatus(data);             break;
      case "error":  onError(data);              break;
      case "done":   onDone(acc);                break;
      default:       onFrame(type, data);        break;
    }
  };
  ws.onerror = () => onError("Connection error. Please try again.");
  ws.onclose = () => onDone(acc);
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
  const [demoMode, setDemoMode]       = useState(null);
  const [demoLoading, setDemoLoading] = useState(false);
  const [demoFile, setDemoFile]       = useState("");
  const demoTimerRef                  = useRef(null);

  const [modernForm, setModernForm] = useState({ currentVersion:"", currentCustom:"", targetVersion:"", targetCustom:"" });
  const [modernLoading, setModernLoading]    = useState(false);
  const [modernStreaming, setModernStreaming] = useState(false);
  const [modernStatus, setModernStatus]      = useState("");
  const [modernLiveHtml, setModernLiveHtml]  = useState("");
  const [modernResult, setModernResult]      = useState(null);

  const { uploadedFiles, addFiles, removeFile, clearFiles } = useFileUpload();
  const cachedFilesRef = useRef([]);
  const closeWsRef     = useRef(null);
  const chatDoneRef    = useRef(false);
  const modernDoneRef  = useRef(false);
  const bottomRef      = useRef(null);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, liveHtml, loading]);

  // Detect known ZIP → show 10s analysis animation → reveal demo panel
  useEffect(() => {
    if (demoTimerRef.current) clearTimeout(demoTimerRef.current);

    if (mode === "documentation" && uploadedFiles.length > 0) {
      const found = detectCustomization(uploadedFiles);
      if (found) {
        setDemoMode(null);
        setDemoFile(uploadedFiles.find(f => f.name.replace(/\.zip$/i,"").toUpperCase() === found)?.name || "");
        setDemoLoading(true);
        demoTimerRef.current = setTimeout(() => {
          setDemoLoading(false);
          setDemoMode(found);
        }, 10000);
      } else {
        setDemoLoading(false);
        setDemoMode(null);
      }
    } else {
      setDemoLoading(false);
      setDemoMode(null);
    }
    return () => { if (demoTimerRef.current) clearTimeout(demoTimerRef.current); };
  }, [uploadedFiles, mode]);

  function switchMode(key) {
    setMode(key); setDemoMode(null); setDemoLoading(false); clearFiles();
    setLiveHtml(""); setStatus(""); setStreaming(false);
  }

  async function handleEmbed(doc) {
    const res = await fetch(EMBED_URL, {
      method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin",
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
            fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0">
            <path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>
          </svg><span>${escapeHtml(q)}</span></button>`).join("");
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
    const question    = q || (mode === "documentation" ? "Generate documentation for the uploaded code" : "Analyse and explain the uploaded code");
    const filesToSend = hasFiles ? [...uploadedFiles] : (cachedFilesRef.current || []);
    setInput(""); clearFiles();
    setMessages((prev) => [...prev, { role: "user", text: displayText }]);
    setLoading(true); setStreaming(false); setStatus(""); setLiveHtml("");
    let extraDoc = null, extraFollowups = null;
    chatDoneRef.current = false;
    const closeWs = openWs({ mode, question, uploaded_files: filesToSend }, {
      onToken:  (acc) => { setStreaming(true); setLiveHtml(renderMarkdown(acc)); },
      onStatus: (msg) => setStatus(msg),
      onFrame:  (type, data) => { if (type === "doc") extraDoc = data; if (type === "followup") extraFollowups = data; },
      onDone: (acc) => {
        if (chatDoneRef.current) return; chatDoneRef.current = true;
        setMessages((prev) => [...prev, { role: "assistant", html: buildHtml(acc, { followups: extraFollowups }), doc: extraDoc }]);
        setLoading(false); setStreaming(false); setStatus(""); setLiveHtml(""); closeWsRef.current = null;
      },
      onError: (msg) => {
        if (chatDoneRef.current) return; chatDoneRef.current = true;
        setMessages((prev) => [...prev, { role: "assistant", html: `<div class="text-red-600 text-sm">${escapeHtml(msg)}</div>`, doc: null }]);
        setLoading(false); setStreaming(false); setStatus(""); setLiveHtml(""); closeWsRef.current = null;
      },
    });
    closeWsRef.current = closeWs;
  }, [input, uploadedFiles, mode, loading, clearFiles]);

  useEffect(() => {
    function handler(e) {
      const chip = e.target.closest("[data-followup]");
      if (!chip || e.target.closest("#apex-root")) return;
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
    let extraDoc = null; modernDoneRef.current = false;
    const closeWs = openWs({ mode: "modernisation", current_version: current, target_version: target }, {
      onToken:  (acc) => { setModernStreaming(true); setModernLiveHtml(renderMarkdown(acc)); },
      onStatus: (msg) => setModernStatus(msg),
      onFrame:  (type, data) => { if (type === "doc") extraDoc = data; },
      onDone: (acc) => {
        if (modernDoneRef.current) return; modernDoneRef.current = true;
        const html = buildHtml(acc, {});
        setModernResult(html);
        if (extraDoc) setMessages((prev) => [...prev, { role: "assistant", html, doc: extraDoc }]);
        setModernLoading(false); setModernStreaming(false); setModernStatus(""); setModernLiveHtml(""); closeWsRef.current = null;
      },
      onError: (msg) => {
        if (modernDoneRef.current) return; modernDoneRef.current = true;
        setModernResult(`<div class="text-red-600 text-sm">${escapeHtml(msg)}</div>`);
        setModernLoading(false); setModernStreaming(false); setModernStatus(""); setModernLiveHtml(""); closeWsRef.current = null;
      },
    });
    closeWsRef.current = closeWs;
  }

  const showEmpty = messages.length === 0 && mode !== "modernisation" && !demoMode && !demoLoading;

  return (
    <div className="flex-1 flex flex-col">
      <header className="h-14 shrink-0 bg-white border-b border-slate-200 px-6 flex items-center justify-between">
        <div className="flex items-center gap-2.5">
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
          <span className="text-sm text-slate-400 hidden sm:inline">Custom code knowledge base</span>
        </div>
        <ModeBar mode={mode} onChange={switchMode} />
      </header>

      <div className="flex-1 relative overflow-hidden">
        <main className="h-full overflow-y-auto">

          {showEmpty && <EmptyState onSwitch={switchMode} activeMode={mode} />}

          {demoLoading && mode === "documentation" && <DemoLoader filename={demoFile} />}
          {!demoLoading && demoMode && mode === "documentation" && <DemoPanel custName={demoMode} />}

          {mode === "modernisation" && (
            <ModernisationPanel form={modernForm} setForm={setModernForm} onSubmit={sendModernisation}
              loading={modernLoading} streaming={modernStreaming} statusText={modernStatus}
              currentHtml={modernLiveHtml} result={modernResult} />
          )}

          {mode !== "modernisation" && !demoMode && messages.length > 0 && (
            <div className="max-w-4xl mx-auto px-6 py-6 space-y-6">
              {messages.map((m, i) => (
                <div key={i}>
                  {m.role === "user" ? (
                    <div className="flex justify-end">
                      <div className="bg-brand-600 text-white rounded-2xl rounded-br-sm px-5 py-3 max-w-[85%] whitespace-pre-wrap text-sm">{m.text}</div>
                    </div>
                  ) : (
                    <div className="space-y-2">
                      <div className="bg-white rounded-2xl shadow-sm border border-slate-200 p-5 prose-container" dangerouslySetInnerHTML={{ __html: m.html }} />
                      {m.doc && <DocCard doc={m.doc} onEmbed={handleEmbed} />}
                    </div>
                  )}
                </div>
              ))}
              {(streaming || (loading && statusText)) && (
                <div className="bg-white rounded-2xl shadow-sm border border-slate-200 p-5">
                  {statusText && <div className="flex items-center gap-2 text-sm text-brand-600 mb-2"><span className="inline-block w-2 h-2 rounded-full bg-brand-500 animate-pulse" /><span>{statusText}</span></div>}
                  {liveHtml && <div className="prose-container" dangerouslySetInnerHTML={{ __html: liveHtml }} />}
                  {streaming && <span className="inline-block w-1.5 h-4 bg-brand-500 animate-pulse ml-0.5 align-middle" />}
                </div>
              )}
              {loading && !streaming && !statusText && (
                <div className="flex items-center gap-2 text-slate-500 text-sm">
                  <span className="inline-block w-2 h-2 rounded-full bg-brand-500 animate-pulse" /><span>Thinking…</span>
                </div>
              )}
              <div className="h-24" ref={bottomRef} />
            </div>
          )}
        </main>

        {mode !== "modernisation" && (
          <FileUploadBar mode={mode} input={input} setInput={setInput}
            uploadedFiles={uploadedFiles} onAddFiles={addFiles} onRemoveFile={removeFile}
            onSend={() => sendChat()} loading={loading} />
        )}
      </div>
    </div>
  );
}
