const MODES = [
  { key: "query",         label: "Search & Ask"     },
  { key: "documentation", label: "Generate Docs"    },
  { key: "modernisation", label: "Upgrade Analysis" },
];

export function ModeBar({ mode, onChange }) {
  return (
    <div className="flex gap-1.5">
      {MODES.map((m) => (
        <button
          key={m.key}
          onClick={() => onChange(m.key)}
          className={`px-3 py-1.5 rounded-lg border text-xs font-medium transition
            ${mode === m.key
              ? "bg-[rgba(0,229,200,0.12)] border-[rgba(0,229,200,0.45)] text-[#00e5c8]"
              : "bg-[rgba(10,20,42,0.6)] border-[rgba(0,229,200,0.1)] text-[rgba(180,210,255,0.5)] hover:border-[rgba(0,229,200,0.3)] hover:text-[#e8f4ff]"
            }`}
        >
          {m.label}
        </button>
      ))}
    </div>
  );
}
