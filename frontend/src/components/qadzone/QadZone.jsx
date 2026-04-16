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
    title: "Material Requisition Note",
    executiveSummary:
      "Custom Progress 4GL application built on QAD ERP providing a structured, multi-level workflow for raising, approving, and executing internal inventory movements — covering inventory issues (ISS-UNP), receipts (RCT-UNP), and inter-site/location transfers (ISS-TR / RCT-TR). Unlike QAD's standard Global Requisition System (GRS), this system handles internal store-to-department movements with a configurable approval hierarchy, purpose-driven GL mapping, lot/serial tracking, and a full audit history trail.",
    keyCapabilities: [
      "Raise internal material requisitions against configurable types and sites",
      "Multi-level authorization: Create → Approve → Execute with user group restrictions",
      "Purpose-based GL account determination (product line or direct account)",
      "Lot/serial number control during inventory execution (full, same-lot, new-lot modes)",
      "Inter-site and inter-location inventory transfer with conflict checks",
      "Validity date enforcement — expired requisition lines cannot be executed",
      "Full audit trail stored in xxmrh_hist",
      "Inventory availability display before transaction entry",
    ],
    replaceability: 72,
    confidence: 85,
    businessImpact:  { label: "High",   bg: "bg-[rgba(239,68,68,0.15)]",    text: "text-[#fca5a5]"  },
    migrationEffort: { label: "Medium", bg: "bg-[rgba(234,179,8,0.15)]",  text: "text-[#fcd34d]"  },
    modules: ["Inventory", "GL / Finance", "Manufacturing"],
    docFilename: "MRN_System_Documentation.docx",
    sources: [
      { label: "QAD Adaptive ERP — Inventory Management Capabilities Guide", url: "https://www.qad.com/products/qad-adaptive-erp" },
      { label: "QAD Global Requisition System (GRS) Feature Comparison", url: "https://www.qad.com/solutions/manufacturing" },
      { label: "QAD Community: Internal Requisition & Transfer Best Practices", url: "https://community.qad.com" },
      { label: "Custom code analysis — 6-phase migration roadmap (internal)", url: null },
    ],
  },
  DOA: {
    title: "Dynamic Approval Orchestration",
    executiveSummary:
      "Universal, rule-driven approval engine built on QAD ERP using Progress 4GL. Not a standalone business application but a shared approval infrastructure layer — a reusable framework consumed by any custom module (MRN, RTDC, SPA, DCREL, MDM, etc.) requiring multi-level approval workflows. DOA decouples approval logic from individual modules; all call a single engine (xxdoaproc.p) that dynamically determines the correct approver sequence based on configurable rules. Integrates with Microsoft Power Automate for email-based approvals, enabling approvers to act directly from their inbox without logging into QAD.",
    keyCapabilities: [
      "Universal approval engine: any module can plug in by calling xxdoaproc.p with rule type, business line, site, and record ID",
      "Configurable rules: up to 15 AND-combined conditions per rule, evaluated dynamically against any QAD database table",
      "Configurable approver chains: up to 10 sequential approvers per rule, referenced by reusable approver codes",
      "Approver code abstraction: codes resolved to actual email addresses at runtime via xxdoaappr_mstr",
      "Alternate approver support: each slot can have a primary and alternate email address",
      "Notify-To (CC) support: up to 10 notification-only recipients per rule",
      "SELF keyword: first approver resolves to the submitting user's email at runtime",
      "Power Automate integration: email-based approval without QAD access",
      "Approval history: every action written to xxdoah_hist with timestamp, approver, and comments",
      "Bulk import/export: rules and approvers exportable to CSV and re-importable via upload utilities",
    ],
    replaceability: 65,
    confidence: 90,
    businessImpact:  { label: "High", bg: "bg-[rgba(239,68,68,0.15)]", text: "text-[#fca5a5]" },
    migrationEffort: { label: "High", bg: "bg-[rgba(239,68,68,0.15)]", text: "text-[#fca5a5]" },
    modules: ["Cross-module", "Workflow", "Integration"],
    docFilename: "DOA_System_Documentation.docx",
    sources: [
      { label: "QAD Adaptive ERP — Workflow & Approval Automation Overview", url: "https://www.qad.com/products/qad-adaptive-erp" },
      { label: "Microsoft Power Automate + QAD Integration Patterns", url: "https://learn.microsoft.com/en-us/power-automate/" },
      { label: "QAD Community: Approval Engine Design Patterns", url: "https://community.qad.com" },
      { label: "Feature gap analysis — 7-phase migration roadmap (internal)", url: null },
    ],
  },
  RTDC: {
    title: "Returnable / Non-Returnable Delivery Challan",
    executiveSummary:
      "Custom Progress 4GL application built on QAD ERP providing a structured, multi-level workflow for creating, approving, and managing delivery challans sent to customers — covering both Returnable (material goes out and comes back) and Non-Returnable (permanent dispatch) scenarios. The system manages inventory tracking, customer credit validation, configurable approval hierarchy, challan cancellation with email notification, material return processing, and full audit trail via QAD's standard transaction history (tr_hist). It integrates directly with QAD's Inventory Transfer API (maintainInventoryTransfer) to move stock to virtual customer locations on challan creation and back upon return or cancellation.",
    keyCapabilities: [
      "Create Delivery Challans with configurable types (Returnable / Non-Returnable) against validated customers",
      "Multi-level approval: Create → Submit → Approve/Reject with email notifications at each step",
      "Inventory reservation: items moved to virtual out-location (RTDC_VIRT1) on creation, intermediate location on approval",
      "Customer credit hold validation — challans cannot be raised against customers on credit hold",
      "Due date enforcement: DC due date capped at Creation Date + 180 days; line dates within header",
      "Material return processing with partial-quantity support, lot/serial tracking, and reason capture",
      "Challan cancellation workflow with criteria checklist, email notification, and inventory reversal",
      "Rejection workflow with HTML email to originating sales person and all previous approvers",
      "Integrated challan print report (xx_rtdcchallan)",
    ],
    replaceability: 72,
    confidence: 85,
    businessImpact:  { label: "High",   bg: "bg-[rgba(239,68,68,0.15)]",    text: "text-[#fca5a5]"  },
    migrationEffort: { label: "Medium", bg: "bg-[rgba(234,179,8,0.15)]",  text: "text-[#fcd34d]"  },
    modules: ["Sales", "Inventory", "Customer Service"],
    docFilename: "RTDC_System_Documentation.docx",
    sources: [
      { label: "QAD Adaptive ERP — Customer Delivery & Shipment Management", url: "https://www.qad.com/products/qad-adaptive-erp" },
      { label: "QAD Inventory Transfer API (maintainInventoryTransfer) Documentation", url: "https://www.qad.com/solutions/distribution" },
      { label: "QAD Community: Delivery Challan & Consignment Tracking", url: "https://community.qad.com" },
      { label: "Custom code analysis — 6-phase migration roadmap (internal)", url: null },
    ],
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
          <circle cx="32" cy="32" r="28" stroke="rgba(0,229,200,0.15)" strokeWidth="6" />
          <path d="M32 4a28 28 0 0 1 28 28" stroke="url(#gradTeal)" strokeWidth="6" strokeLinecap="round" />
          <defs>
            <linearGradient id="gradTeal" x1="32" y1="4" x2="60" y2="32" gradientUnits="userSpaceOnUse">
              <stop stopColor="#00c9ae" /><stop offset="1" stopColor="#00e5c8" />
            </linearGradient>
          </defs>
        </svg>
        <div className="absolute inset-0 flex items-center justify-center text-xl">
          {done ? "✅" : ANALYSIS_STEPS[stepIdx].icon}
        </div>
      </div>

      {/* Title */}
      <div className="text-center">
        <div className="text-base font-bold" style={{ color: "#e8f4ff" }}>
          {done ? "Analysis complete!" : "Analyzing customization…"}
        </div>
        <div className="text-xs mt-1" style={{ color: "rgba(180,210,255,0.45)" }}>{filename}</div>
      </div>

      {/* Step list */}
      <div className="w-full space-y-2">
        {ANALYSIS_STEPS.map((s, i) => {
          const isActive = i === stepIdx && !done;
          const isDone   = i < stepIdx || done;
          return (
            <div key={i}
              className="flex items-center gap-3 px-4 py-2.5 rounded-xl transition-all duration-300"
              style={{
                background: isActive ? "rgba(0,229,200,0.08)" : "transparent",
                border: isActive ? "1px solid rgba(0,229,200,0.3)" : "1px solid transparent",
                opacity: isDone ? 0.55 : isActive ? 1 : 0.2,
              }}>
              <span className="text-base w-5 text-center shrink-0">{isDone || isActive ? s.icon : "○"}</span>
              <span className="text-sm" style={{ color: isActive ? "#00e5c8" : "rgba(180,210,255,0.7)", fontWeight: isActive ? 600 : 400 }}>
                {s.text}
              </span>
              {isDone && !isActive && (
                <span className="ml-auto text-xs font-bold shrink-0" style={{ color: "#00e5c8" }}>✓</span>
              )}
              {isActive && (
                <span className="ml-auto flex gap-0.5 shrink-0">
                  {[0,1,2].map((d) => (
                    <span key={d} className="inline-block w-1 h-1 rounded-full animate-bounce"
                      style={{ background: "#00e5c8", animationDelay: `${d * 150}ms` }} />
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
    { key: "query",         label: "Search & Ask",    sub: "Ask questions about your custom QAD programs in plain English — get instant answers", icon: <IconSearch /> },
    { key: "documentation", label: "Generate Docs",   sub: "Upload your custom code and get a ready-to-share Word document automatically",        icon: <IconDoc />    },
    { key: "modernisation", label: "Upgrade Analysis",sub: "See what will break and what needs updating when moving to a newer QAD version",       icon: <IconUpgrade />},
  ];
  return (
    <div className="h-full flex flex-col items-center justify-center px-6 pb-32">
      {/* Hero icon + title */}
      <div className="text-center mb-10">
        <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl mb-5"
          style={{ background: "rgba(0,229,200,0.1)", border: "1px solid rgba(0,229,200,0.25)", boxShadow: "0 0 32px rgba(0,229,200,0.08)" }}>
          <svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" viewBox="0 0 24 24"
            fill="none" stroke="#00e5c8" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/>
            <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>
          </svg>
        </div>
        <h1 className="text-2xl font-bold tracking-tight"
          style={{ color: "#e8f4ff", fontFamily: "'Syne',sans-serif", letterSpacing: "-0.01em" }}>
          Modernization
        </h1>
        <p className="mt-2 text-sm max-w-xs mx-auto leading-relaxed"
          style={{ color: "rgba(180,210,255,0.45)" }}>
          Your custom code knowledge base — search, document, and modernise with AI.
        </p>
      </div>

      {/* Mode cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 w-full max-w-2xl">
        {cards.map((c) => {
          const isActive = activeMode === c.key;
          return (
            <button key={c.key} onClick={() => onSwitch(c.key)}
              className="group flex flex-col text-left rounded-2xl p-5 transition-all duration-150"
              style={isActive ? {
                background: "rgba(0,229,200,0.08)",
                border: "1.5px solid rgba(0,229,200,0.4)",
                boxShadow: "0 0 20px rgba(0,229,200,0.06)",
              } : {
                background: "rgba(10,20,42,0.6)",
                border: "1.5px solid rgba(0,229,200,0.08)",
              }}
              onMouseOver={e => { if (!isActive) { e.currentTarget.style.borderColor = "rgba(0,229,200,0.25)"; e.currentTarget.style.background = "rgba(10,20,42,0.8)"; }}}
              onMouseOut={e => { if (!isActive) { e.currentTarget.style.borderColor = "rgba(0,229,200,0.08)"; e.currentTarget.style.background = "rgba(10,20,42,0.6)"; }}}
            >
              <div className="h-9 w-9 rounded-xl flex items-center justify-center mb-4 shrink-0"
                style={isActive
                  ? { background: "rgba(0,229,200,0.15)", color: "#00e5c8" }
                  : { background: "rgba(0,229,200,0.07)", color: "rgba(0,229,200,0.6)" }}>
                {c.icon}
              </div>
              <div className="text-sm font-semibold mb-1.5"
                style={{ color: isActive ? "#e8f4ff" : "rgba(220,235,255,0.8)" }}>
                {c.label}
              </div>
              <div className="text-xs leading-relaxed"
                style={{ color: "rgba(180,210,255,0.38)" }}>
                {c.sub}
              </div>
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
      <circle cx={size/2} cy={size/2} r={r} fill="none" stroke="rgba(0,229,200,0.12)" strokeWidth="8" />
      <circle cx={size/2} cy={size/2} r={r} fill="none" stroke={stroke} strokeWidth="8"
        strokeDasharray={`${circ}`} strokeDashoffset={offset} strokeLinecap="round"
        style={{ transform:"rotate(-90deg)", transformOrigin:"50% 50%", transition:"stroke-dashoffset 0.6s ease" }} />
      <text x={size/2} y={size/2} textAnchor="middle" dy="0.35em" fontSize="15" fontWeight="700" fill="#e8f4ff">
        {value}%
      </text>
    </svg>
  );
}

// ── Summary tab ───────────────────────────────────────────────────────────────
const cardDark = { background: "rgba(10,20,42,0.85)", border: "1px solid rgba(0,229,200,0.14)", borderRadius: "16px", padding: "20px" };
const labelDark = { fontSize: "10px", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", color: "rgba(180,210,255,0.45)", marginBottom: "8px" };

function SummaryTab({ data }) {
  return (
    <div className="p-6 space-y-5">
      {/* Executive summary */}
      <div style={cardDark}>
        <div style={labelDark}>Executive Summary</div>
        <p className="text-sm leading-relaxed" style={{ color: "rgba(180,210,255,0.8)" }}>{data.executiveSummary}</p>
        <div className="flex flex-wrap gap-1.5 mt-3">
          {data.modules.map((m) => (
            <span key={m} className="text-xs px-2.5 py-0.5 rounded-full font-medium"
              style={{ background: "rgba(0,229,200,0.1)", border: "1px solid rgba(0,229,200,0.2)", color: "#00e5c8" }}>{m}</span>
          ))}
        </div>
      </div>

      {/* Key capabilities */}
      <div style={cardDark}>
        <div style={labelDark}>Key Business Capabilities</div>
        <ul className="space-y-2">
          {data.keyCapabilities.map((cap, i) => (
            <li key={i} className="flex items-start gap-2 text-sm" style={{ color: "rgba(180,210,255,0.75)" }}>
              <span className="mt-0.5 h-4 w-4 rounded-full text-[10px] font-bold flex items-center justify-center shrink-0"
                style={{ background: "rgba(0,229,200,0.15)", color: "#00e5c8" }}>{i + 1}</span>
              {cap}
            </li>
          ))}
        </ul>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <div className="flex flex-col items-center gap-2 p-4" style={{ ...cardDark, padding: "16px" }}>
          <div style={{ ...labelDark, textAlign: "center", lineHeight: 1.4 }}>QAD Adaptive<br/>Replaceability</div>
          <CircleProgress value={data.replaceability} stroke="#6366f1" />
          <div className="text-[11px] text-center" style={{ color: "rgba(180,210,255,0.45)" }}>
            {data.replaceability >= 70 ? "Highly replaceable" : data.replaceability >= 50 ? "Partially replaceable" : "Custom build needed"}
          </div>
        </div>

        <div className="flex flex-col items-center gap-2 p-4" style={{ ...cardDark, padding: "16px" }}>
          <div style={{ ...labelDark, textAlign: "center", lineHeight: 1.4 }}>Analysis<br/>Confidence</div>
          <CircleProgress value={data.confidence} stroke="#22c55e" />
          <div className="text-[11px] text-center" style={{ color: "rgba(180,210,255,0.45)" }}>
            {data.confidence >= 80 ? "High confidence" : data.confidence >= 60 ? "Moderate confidence" : "Low confidence"}
          </div>
        </div>

        <div className="flex flex-col items-center gap-3 p-4" style={{ ...cardDark, padding: "16px" }}>
          <div style={{ ...labelDark, textAlign: "center", lineHeight: 1.4 }}>Business<br/>Impact</div>
          <div className={`mt-3 px-5 py-2 rounded-xl text-sm font-bold ${data.businessImpact.bg} ${data.businessImpact.text}`}>
            {data.businessImpact.label}
          </div>
          <div className="text-[11px] text-center leading-relaxed" style={{ color: "rgba(180,210,255,0.45)" }}>
            {data.businessImpact.label === "High" ? "Critical to business operations" : data.businessImpact.label === "Medium" ? "Moderate operational dependency" : "Low operational dependency"}
          </div>
        </div>

        <div className="flex flex-col items-center gap-3 p-4" style={{ ...cardDark, padding: "16px" }}>
          <div style={{ ...labelDark, textAlign: "center", lineHeight: 1.4 }}>Migration<br/>Effort</div>
          <div className={`mt-3 px-5 py-2 rounded-xl text-sm font-bold ${data.migrationEffort.bg} ${data.migrationEffort.text}`}>
            {data.migrationEffort.label}
          </div>
          <div className="text-[11px] text-center leading-relaxed" style={{ color: "rgba(180,210,255,0.45)" }}>
            {data.migrationEffort.label === "High" ? "Significant rework required" : data.migrationEffort.label === "Medium" ? "Moderate development effort" : "Minimal changes needed"}
          </div>
        </div>
      </div>

      {/* Sources */}
      {data.sources?.length > 0 && (
        <div style={cardDark}>
          <div style={labelDark}>Analysis Sources</div>
          <div className="space-y-2">
            {data.sources.map((s, i) => (
              <div key={i} className="flex items-start gap-2">
                <span className="mt-0.5 shrink-0" style={{ color: "rgba(0,229,200,0.4)" }}>•</span>
                {s.url ? (
                  <a href={s.url} target="_blank" rel="noopener noreferrer"
                    className="text-xs leading-relaxed hover:underline"
                    style={{ color: "rgba(99,102,241,0.85)" }}>
                    {s.label}
                  </a>
                ) : (
                  <span className="text-xs leading-relaxed" style={{ color: "rgba(180,210,255,0.4)" }}>{s.label}</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
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

  const embedBtnStyle = embedState === "done"
    ? { background: "rgba(34,197,94,0.15)", color: "#86efac", border: "1px solid rgba(34,197,94,0.25)", cursor: "default" }
    : embedState === "loading"
    ? { background: "rgba(0,229,200,0.06)", color: "rgba(0,229,200,0.35)", border: "1px solid rgba(0,229,200,0.1)", cursor: "not-allowed" }
    : { background: "linear-gradient(135deg,#00c9ae,#00e5c8)", color: "#060d1a", border: "none", cursor: "pointer" };

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between gap-4" style={cardDark}>
        <div className="flex items-center gap-3 min-w-0">
          <div className="h-10 w-10 rounded-xl flex items-center justify-center shrink-0"
            style={{ background: "rgba(0,229,200,0.1)", color: "#00e5c8" }}>
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24"
              fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
              <polyline points="14 2 14 8 20 8"/>
              <line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
            </svg>
          </div>
          <div className="min-w-0">
            <div className="text-sm font-semibold truncate" style={{ color: "#e8f4ff" }}>{data.docFilename}</div>
            <div className="text-xs mt-0.5" style={{ color: "rgba(180,210,255,0.45)" }}>{data.title} — System Documentation</div>
          </div>
        </div>
        <a href={DEMO_DOC_URL(custName)} download={data.docFilename}
          className="shrink-0 px-4 py-2 text-xs font-semibold rounded-lg transition flex items-center gap-1.5"
          style={{ background: "linear-gradient(135deg,#00c9ae,#00e5c8)", color: "#060d1a" }}>
          <svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24"
            fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
          </svg>
          Download
        </a>
      </div>

      <div style={cardDark}>
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="text-sm font-semibold" style={{ color: "#e8f4ff" }}>Add to Knowledge Base</div>
            <div className="text-xs mt-0.5" style={{ color: "rgba(180,210,255,0.45)" }}>Embed this document into the Apex search index</div>
          </div>
          <button onClick={handleEmbed} disabled={embedState !== "idle"}
            className="shrink-0 px-4 py-2 text-xs font-semibold rounded-lg transition flex items-center gap-1.5"
            style={embedBtnStyle}>
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
        <div className="h-9 w-9 rounded-xl flex items-center justify-center shrink-0"
          style={{ background: "linear-gradient(135deg,#00c9ae,#00e5c8)" }}>
          <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24"
            fill="none" stroke="#060d1a" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
          </svg>
        </div>
        <div>
          <div className="text-base font-bold" style={{ color: "#e8f4ff" }}>{data.title}</div>
          <div className="text-xs" style={{ color: "rgba(180,210,255,0.45)" }}>Customization Analysis · {custName}</div>
        </div>
      </div>

      <div className="flex gap-1 p-1 rounded-xl w-fit mb-5"
        style={{ background: "rgba(0,229,200,0.06)", border: "1px solid rgba(0,229,200,0.12)" }}>
        {[{key:"summary",label:"Summary"},{key:"documentation",label:"Documentation"}].map((t) => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className="px-5 py-1.5 rounded-lg text-sm font-medium transition"
            style={tab === t.key
              ? { background: "rgba(0,229,200,0.15)", color: "#00e5c8", border: "1px solid rgba(0,229,200,0.35)" }
              : { background: "transparent", color: "rgba(180,210,255,0.5)", border: "1px solid transparent" }}>
            {t.label}
          </button>
        ))}
      </div>

      <div style={{ background: "rgba(6,13,26,0.6)", border: "1px solid rgba(0,229,200,0.12)", borderRadius: "16px" }}>
        {tab === "summary"       && <SummaryTab data={data} />}
        {tab === "documentation" && <DocTab custName={custName} data={data} />}
      </div>

      {/* bottom spacer so last card clears the viewport */}
      <div className="h-10" />
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
  const cachedFilesRef   = useRef([]);
  const closeWsRef       = useRef(null);
  const chatDoneRef      = useRef(false);
  const modernDoneRef    = useRef(false);
  const bottomRef        = useRef(null);
  const demoStartingRef  = useRef(false); // prevents useEffect from cancelling demo on clearFiles()

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, liveHtml, loading]);

  // Reset demo state only when user manually removes all files (not when sendChat clears them)
  useEffect(() => {
    if (uploadedFiles.length === 0) {
      if (demoStartingRef.current) {
        demoStartingRef.current = false; // sendChat just cleared files — ignore this trigger
        return;
      }
      if (demoTimerRef.current) clearTimeout(demoTimerRef.current);
      setDemoLoading(false);
      setDemoMode(null);
    }
  }, [uploadedFiles]);

  // Trigger demo analysis on Send click (called from sendChat)
  function triggerDemoIfKnown(files) {
    const found = detectCustomization(files);
    if (!found) return false;
    if (demoTimerRef.current) clearTimeout(demoTimerRef.current);
    setDemoMode(null);
    setDemoFile(files.find(f => f.name.replace(/\.zip$/i, "").toUpperCase() === found)?.name || "");
    setDemoLoading(true);
    demoStartingRef.current = true; // tell useEffect to skip the next "files cleared" event
    demoTimerRef.current = setTimeout(() => {
      setDemoLoading(false);
      setDemoMode(found);
      demoTimerRef.current = null;
    }, 10000);
    return true;
  }

  function switchMode(key) {
    if (demoTimerRef.current) clearTimeout(demoTimerRef.current);
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
    return html || '<div class="text-sm italic" style="color:rgba(180,210,255,0.4)">No response received.</div>';
  }

  const sendChat = useCallback((overrideQuestion) => {
    const q        = overrideQuestion || input.trim();
    const hasFiles = uploadedFiles.length > 0;
    if (!q && !hasFiles) return;
    if (loading) return;

    // In documentation mode, intercept known ZIPs → show demo panel instead of LLM
    if (mode === "documentation" && hasFiles && !overrideQuestion) {
      if (triggerDemoIfKnown([...uploadedFiles])) {
        clearFiles();
        return;
      }
    }

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
    <div className="flex-1 flex flex-col min-h-0">
      <header className="h-14 shrink-0 px-6 flex items-center justify-between"
        style={{ background: "rgba(5,11,28,0.98)", borderBottom: "1px solid rgba(0,229,200,0.22)", boxShadow: "0 2px 12px rgba(0,0,0,0.3),0 1px 0 rgba(0,229,200,0.06)" }}>
        <div className="flex items-center gap-2.5">
          <div className="h-7 w-7 rounded-lg flex items-center justify-center shrink-0"
            style={{ background: "linear-gradient(135deg,#00c9ae,#00e5c8)" }}>
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24"
              fill="none" stroke="#060d1a" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <ellipse cx="12" cy="5" rx="9" ry="3"/>
              <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/>
              <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>
            </svg>
          </div>
          <span className="text-sm font-semibold" style={{ color: "#e8f4ff" }}>Modernization</span>
          <span style={{ color: "rgba(0,229,200,0.25)" }}>|</span>
          <span className="text-sm hidden sm:inline" style={{ color: "rgba(180,210,255,0.4)" }}>Custom code knowledge base</span>
        </div>
        <ModeBar mode={mode} onChange={switchMode} />
      </header>

      <div className="flex-1 min-h-0 relative overflow-hidden flex flex-col">
        <main className="flex-1 min-h-0 overflow-y-auto">

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
                      <div className="rounded-2xl rounded-br-sm px-5 py-3 max-w-[85%] whitespace-pre-wrap text-sm font-medium"
                        style={{ background: "linear-gradient(135deg,#00c9ae,#00e5c8)", color: "#060d1a" }}>{m.text}</div>
                    </div>
                  ) : (
                    <div className="space-y-2">
                      <div className="rounded-2xl p-5 prose-container"
                        style={{ background: "rgba(10,20,42,0.85)", border: "1px solid rgba(0,229,200,0.14)", color: "#e8f4ff" }}
                        dangerouslySetInnerHTML={{ __html: m.html }} />
                      {m.doc && <DocCard doc={m.doc} onEmbed={handleEmbed} />}
                    </div>
                  )}
                </div>
              ))}
              {(streaming || (loading && statusText)) && (
                <div className="rounded-2xl p-5"
                  style={{ background: "rgba(10,20,42,0.85)", border: "1px solid rgba(0,229,200,0.14)" }}>
                  {statusText && (
                    <div className="flex items-center gap-2 text-sm mb-2" style={{ color: "#00e5c8" }}>
                      <span className="inline-block w-2 h-2 rounded-full animate-pulse" style={{ background: "#00e5c8" }} />
                      <span>{statusText}</span>
                    </div>
                  )}
                  {liveHtml && <div className="prose-container" style={{ color: "#e8f4ff" }} dangerouslySetInnerHTML={{ __html: liveHtml }} />}
                  {streaming && <span className="inline-block w-1.5 h-4 animate-pulse ml-0.5 align-middle" style={{ background: "#00e5c8" }} />}
                </div>
              )}
              {loading && !streaming && !statusText && (
                <div className="flex items-center gap-2 text-sm" style={{ color: "rgba(180,210,255,0.5)" }}>
                  <span className="inline-block w-2 h-2 rounded-full animate-pulse" style={{ background: "#00e5c8" }} />
                  <span>Thinking…</span>
                </div>
              )}
              <div className="h-24" ref={bottomRef} />
            </div>
          )}
        </main>

        {mode !== "modernisation" && !demoMode && !demoLoading && (
          <FileUploadBar mode={mode} input={input} setInput={setInput}
            uploadedFiles={uploadedFiles} onAddFiles={addFiles} onRemoveFile={removeFile}
            onSend={() => sendChat()} loading={loading} />
        )}
      </div>
    </div>
  );
}
