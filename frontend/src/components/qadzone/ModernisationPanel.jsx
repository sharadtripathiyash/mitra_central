/**
 * ModernisationPanel — version picker + migration result display.
 * Dark teal theme.
 */
import { Rocket } from "lucide-react";
import { StatusIndicator } from "../shared/StatusIndicator";

const VERSIONS = [
  {
    label: "QAD Enterprise Edition",
    items: [
      { value: "QAD EE 2.0",   order: 0  },
      { value: "QAD EE 2018",  order: 1  },
      { value: "QAD EE 2019",  order: 2  },
      { value: "QAD EE 2020",  order: 3  },
      { value: "QAD EE 2021",  order: 4  },
      { value: "QAD EE 2022",  order: 5  },
      { value: "QAD EE 2023",  order: 6  },
    ],
  },
  {
    label: "QAD Community Edition",
    items: [
      { value: "QAD CE 2018", order: 10 },
      { value: "QAD CE 2019", order: 11 },
      { value: "QAD CE 2020", order: 12 },
      { value: "QAD CE 2021", order: 13 },
      { value: "QAD CE 2022", order: 14 },
    ],
  },
  {
    label: "QAD Adaptive ERP",
    items: [
      { value: "QAD Adaptive ERP 2022", order: 20 },
      { value: "QAD Adaptive ERP 2023", order: 21 },
      { value: "QAD Adaptive ERP 2024", order: 22 },
    ],
  },
];

const cardStyle = {
  background: "rgba(10,20,42,0.85)",
  border: "1px solid rgba(0,229,200,0.14)",
  borderRadius: "16px",
  padding: "28px",
  boxShadow: "0 8px 32px rgba(0,0,0,0.3)",
};

const inputStyle = {
  width: "100%",
  background: "rgba(5,15,35,0.7)",
  border: "1px solid rgba(0,229,200,0.15)",
  borderRadius: "10px",
  padding: "11px 14px",
  fontSize: "0.875rem",
  color: "#e8f4ff",
  outline: "none",
};

const selectStyle = {
  ...inputStyle,
  cursor: "pointer",
};

function versionOrder(value) {
  for (const g of VERSIONS) {
    for (const it of g.items) {
      if (it.value === value) return it.order;
    }
  }
  return -1;
}

export function ModernisationPanel({
  form, setForm,
  onSubmit,
  loading,
  streaming,
  statusText,
  currentHtml,
  result,
}) {
  const curOrder = versionOrder(form.currentVersion);

  const canSubmit =
    !loading &&
    (form.currentVersion || form.currentCustom) &&
    (form.targetVersion || form.targetCustom);

  return (
    <div className="max-w-2xl mx-auto px-6 py-8">
      <div style={cardStyle} className="mb-6">
        <div className="mb-6">
          <h2 style={{ fontSize: "1.1rem", fontWeight: 700, color: "#e8f4ff" }}>
            Migration Analysis
          </h2>
          <p style={{ fontSize: "0.85rem", color: "rgba(180,210,255,0.5)", marginTop: "4px" }}>
            Select your QAD versions to generate a comprehensive migration plan
          </p>
        </div>

        <div className="space-y-5">
          {/* Current version */}
          <div>
            <label style={{ display: "block", fontSize: "11px", fontWeight: 600,
                            letterSpacing: "0.06em", textTransform: "uppercase",
                            color: "rgba(180,210,255,0.55)", marginBottom: "8px" }}>
              Current QAD Version
            </label>
            <select
              value={form.currentVersion}
              onChange={(e) => setForm((f) => ({ ...f, currentVersion: e.target.value }))}
              style={selectStyle}
            >
              <option value="" style={{ background: "#080f20" }}>Select current version…</option>
              {VERSIONS.map((g) => (
                <optgroup key={g.label} label={g.label}>
                  {g.items.map((it) => (
                    <option key={it.value} value={it.value} style={{ background: "#080f20" }}>{it.value}</option>
                  ))}
                </optgroup>
              ))}
            </select>
            <input
              value={form.currentCustom}
              onChange={(e) => setForm((f) => ({ ...f, currentCustom: e.target.value }))}
              placeholder="Or type a custom version (e.g. QAD EE 2.1 SP3)"
              style={{ ...inputStyle, marginTop: "8px" }}
            />
          </div>

          {/* Divider */}
          <div className="flex items-center gap-3">
            <div style={{ height: "1px", flex: 1, background: "rgba(0,229,200,0.1)" }} />
            <span style={{ color: "#00e5c8", fontSize: "1.2rem" }}>↓</span>
            <div style={{ height: "1px", flex: 1, background: "rgba(0,229,200,0.1)" }} />
          </div>

          {/* Target version */}
          <div>
            <label style={{ display: "block", fontSize: "11px", fontWeight: 600,
                            letterSpacing: "0.06em", textTransform: "uppercase",
                            color: "rgba(180,210,255,0.55)", marginBottom: "8px" }}>
              Target QAD Version
            </label>
            <select
              value={form.targetVersion}
              onChange={(e) => setForm((f) => ({ ...f, targetVersion: e.target.value }))}
              style={selectStyle}
            >
              <option value="" style={{ background: "#080f20" }}>Select target version…</option>
              {VERSIONS.map((g) => (
                <optgroup key={g.label} label={g.label}>
                  {g.items
                    .filter((it) => curOrder < 0 || it.order > curOrder)
                    .map((it) => (
                      <option key={it.value} value={it.value} style={{ background: "#080f20" }}>{it.value}</option>
                    ))}
                </optgroup>
              ))}
            </select>
            <input
              value={form.targetCustom}
              onChange={(e) => setForm((f) => ({ ...f, targetCustom: e.target.value }))}
              placeholder="Or type a custom version"
              style={{ ...inputStyle, marginTop: "8px" }}
            />
          </div>
        </div>

        <button
          onClick={onSubmit}
          disabled={!canSubmit}
          className="mt-6 w-full font-medium py-3 rounded-xl flex items-center justify-center gap-2 transition text-sm"
          style={{
            background: canSubmit
              ? "linear-gradient(135deg, #00c9ae, #00e5c8)"
              : "rgba(0,229,200,0.1)",
            color: canSubmit ? "#060d1a" : "rgba(0,229,200,0.3)",
            border: "none",
            cursor: canSubmit ? "pointer" : "not-allowed",
            fontWeight: 700,
          }}
        >
          {loading ? (
            <>
              <span className="inline-block w-4 h-4 border-2 border-[#060d1a] border-t-transparent rounded-full animate-spin" />
              Analysing…
            </>
          ) : (
            <>
              <Rocket size={16} />
              Generate Migration Plan
            </>
          )}
        </button>
      </div>

      {/* Result */}
      {(result || loading) && (
        <div style={cardStyle}>
          <StatusIndicator text={statusText} />
          {currentHtml && (
            <div className="prose-container" dangerouslySetInnerHTML={{ __html: currentHtml }} />
          )}
          {streaming && (
            <span style={{
              display: "inline-block", width: "6px", height: "16px",
              background: "#00e5c8", opacity: 0.8, animation: "blink 1s steps(1) infinite",
              marginLeft: "2px", verticalAlign: "middle", borderRadius: "1px"
            }} />
          )}
          {!loading && result && (
            <div className="prose-container" dangerouslySetInnerHTML={{ __html: result }} />
          )}
        </div>
      )}
    </div>
  );
}
