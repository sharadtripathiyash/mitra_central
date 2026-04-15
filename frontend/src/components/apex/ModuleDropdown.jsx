/**
 * ModuleDropdown — in-session domain/module switcher for Apex.
 * Dark teal theme.
 */
import { useState, useRef, useEffect } from "react";
import { ChevronDown, Check, X } from "lucide-react";

const ALL_DOMAINS = [
  { key: "sales",          label: "Sales"           },
  { key: "purchasing",     label: "Purchasing"       },
  { key: "manufacturing",  label: "Manufacturing"    },
  { key: "custom_docs",    label: "Custom Modules"   },
];

export function ModuleDropdown({ selected, onChange }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

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
        className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition"
        style={{ background: "rgba(255,255,255,0.1)", color: "white", border: "none" }}
        onMouseOver={e => e.currentTarget.style.background = "rgba(255,255,255,0.18)"}
        onMouseOut={e => e.currentTarget.style.background = "rgba(255,255,255,0.1)"}
        title="Switch module"
      >
        <span className="max-w-[120px] truncate">
          {labels.length ? labels.join(", ") : "All modules"}
        </span>
        <ChevronDown size={12} className={`transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div
          className="absolute right-0 top-full mt-1 w-48 rounded-xl py-1 z-50"
          style={{
            background: "rgba(8,15,32,0.98)",
            border: "1px solid rgba(0,229,200,0.2)",
            boxShadow: "0 20px 40px rgba(0,0,0,0.5)",
          }}
        >
          <div style={{ padding: "4px 12px 8px", fontSize: "9px", fontWeight: 700,
                        letterSpacing: "0.1em", textTransform: "uppercase",
                        color: "rgba(140,180,230,0.35)" }}>
            Filter by module
          </div>
          {ALL_DOMAINS.map((d) => {
            const active = selected.includes(d.key);
            return (
              <button
                key={d.key}
                onClick={() => toggle(d.key)}
                className="w-full flex items-center justify-between px-3 py-2 text-sm transition"
                style={{ background: "transparent", border: "none", cursor: "pointer",
                         color: active ? "#00e5c8" : "rgba(180,210,255,0.65)", textAlign: "left" }}
                onMouseOver={e => e.currentTarget.style.background = "rgba(0,229,200,0.06)"}
                onMouseOut={e => e.currentTarget.style.background = "transparent"}
              >
                <span>{d.label}</span>
                {active && <Check size={14} style={{ color: "#00e5c8" }} />}
              </button>
            );
          })}
          <div style={{ borderTop: "1px solid rgba(0,229,200,0.1)", marginTop: "4px", paddingTop: "4px" }}>
            <button
              onClick={() => { onChange([]); setOpen(false); }}
              className="w-full flex items-center gap-2 px-3 py-1.5 text-xs transition"
              style={{ background: "transparent", border: "none", cursor: "pointer",
                       color: "rgba(180,210,255,0.4)" }}
              onMouseOver={e => e.currentTarget.style.color = "rgba(180,210,255,0.8)"}
              onMouseOut={e => e.currentTarget.style.color = "rgba(180,210,255,0.4)"}
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
