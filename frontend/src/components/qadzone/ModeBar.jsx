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
              ? "bg-blue-50 border-blue-200 text-blue-700"
              : "bg-white border-slate-200 text-slate-500 hover:border-slate-300 hover:text-slate-700"
            }`}
        >
          {m.label}
        </button>
      ))}
    </div>
  );
}