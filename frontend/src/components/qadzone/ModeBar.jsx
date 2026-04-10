const MODES = [
  { key: "query",         label: "Search & Ask" },
  { key: "documentation", label: "Generate Docs" },
  { key: "modernisation", label: "Upgrade Analysis" },
];

export function ModeBar({ mode, onChange }) {
  return (
    <div className="flex gap-1.5">
      {MODES.map((m) => (
        <button
          key={m.key}
          onClick={() => onChange(m.key)}
          className={`px-3 py-1.5 rounded-lg border text-xs transition
            ${mode === m.key
              ? "bg-slate-100 border-slate-300 text-slate-800 font-semibold"
              : "bg-white border-slate-200 text-slate-500 hover:border-slate-300 hover:text-slate-700"
            }`}
        >
          {m.label}
        </button>
      ))}
    </div>
  );
}
