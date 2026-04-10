/**
 * ModuleDropdown — in-session domain/module switcher for Apex.
 * Shows current active domains as pills + a dropdown to add/remove.
 * Changing selection takes effect on the NEXT message — no session reset.
 */
import { useState, useRef, useEffect } from "react";
import { ChevronDown, Check, X } from "lucide-react";

// Standard QAD modules + custom docs collection
const ALL_DOMAINS = [
  { key: "sales",          label: "Sales"           },
  { key: "purchasing",     label: "Purchasing"       },
  { key: "manufacturing",  label: "Manufacturing"    },
  { key: "custom_docs",    label: "Custom Modules"   },
];

export function ModuleDropdown({ selected, onChange }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  // Close on outside click
  useEffect(() => {
    function handler(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  function toggle(key) {
    const next = selected.includes(key)
      ? selected.filter((k) => k !== key)
      : [...selected, key];
    onChange(next);
  }

  const labels = ALL_DOMAINS.filter((d) => selected.includes(d.key)).map((d) => d.label);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-white/10 hover:bg-white/20 text-white text-xs font-medium transition"
        title="Switch module"
      >
        <span className="max-w-[120px] truncate">
          {labels.length ? labels.join(", ") : "All modules"}
        </span>
        <ChevronDown size={12} className={`transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-1 w-48 bg-white rounded-xl shadow-xl border border-slate-200 py-1 z-50">
          <div className="px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-400">
            Filter by module
          </div>
          {ALL_DOMAINS.map((d) => {
            const active = selected.includes(d.key);
            return (
              <button
                key={d.key}
                onClick={() => toggle(d.key)}
                className="w-full flex items-center justify-between px-3 py-2 text-sm text-slate-700 hover:bg-slate-50 transition"
              >
                <span>{d.label}</span>
                {active && <Check size={14} className="text-brand-600" />}
              </button>
            );
          })}
          <div className="border-t border-slate-100 mt-1 pt-1">
            <button
              onClick={() => { onChange([]); setOpen(false); }}
              className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-slate-500 hover:text-slate-700 hover:bg-slate-50 transition"
            >
              <X size={11} />
              Clear filters (search all)
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
