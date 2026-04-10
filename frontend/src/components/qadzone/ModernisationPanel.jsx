/**
 * ModernisationPanel — version picker + migration result display.
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
      <div className="bg-white border border-slate-200 rounded-xl shadow-sm p-8 mb-6">
        <div className="mb-6">
          <h2 className="text-lg font-semibold text-slate-800">Migration Analysis</h2>
          <p className="text-sm text-slate-500 mt-1">
            Select your QAD versions to generate a comprehensive migration plan
          </p>
        </div>

        <div className="space-y-5">
          {/* Current version */}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-2">
              Current QAD Version
            </label>
            <select
              value={form.currentVersion}
              onChange={(e) => setForm((f) => ({ ...f, currentVersion: e.target.value }))}
              className="w-full rounded-lg border border-slate-200 px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 bg-white"
            >
              <option value="">Select current version…</option>
              {VERSIONS.map((g) => (
                <optgroup key={g.label} label={g.label}>
                  {g.items.map((it) => (
                    <option key={it.value} value={it.value}>{it.value}</option>
                  ))}
                </optgroup>
              ))}
            </select>
            <input
              value={form.currentCustom}
              onChange={(e) => setForm((f) => ({ ...f, currentCustom: e.target.value }))}
              placeholder="Or type a custom version (e.g. QAD EE 2.1 SP3)"
              className="mt-2 w-full rounded-lg border border-slate-200 px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            />
          </div>

          {/* Divider */}
          <div className="flex items-center gap-3 text-slate-300">
            <div className="h-px flex-1 bg-slate-200" />
            <span className="text-slate-400 text-sm">↓</span>
            <div className="h-px flex-1 bg-slate-200" />
          </div>

          {/* Target version */}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-2">
              Target QAD Version
            </label>
            <select
              value={form.targetVersion}
              onChange={(e) => setForm((f) => ({ ...f, targetVersion: e.target.value }))}
              className="w-full rounded-lg border border-slate-200 px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 bg-white"
            >
              <option value="">Select target version…</option>
              {VERSIONS.map((g) => (
                <optgroup key={g.label} label={g.label}>
                  {g.items
                    .filter((it) => curOrder < 0 || it.order > curOrder)
                    .map((it) => (
                      <option key={it.value} value={it.value}>{it.value}</option>
                    ))}
                </optgroup>
              ))}
            </select>
            <input
              value={form.targetCustom}
              onChange={(e) => setForm((f) => ({ ...f, targetCustom: e.target.value }))}
              placeholder="Or type a custom version"
              className="mt-2 w-full rounded-lg border border-slate-200 px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            />
          </div>
        </div>

        <button
          onClick={onSubmit}
          disabled={!canSubmit}
          className="mt-6 w-full bg-brand-800 hover:bg-brand-900 disabled:opacity-40 disabled:cursor-not-allowed text-white font-medium py-3 rounded-lg flex items-center justify-center gap-2 transition text-sm"
        >
          {loading ? (
            <>
              <span className="inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
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
        <div className="bg-white border border-slate-200 rounded-xl shadow-sm p-6">
          <StatusIndicator text={statusText} />
          {currentHtml && (
            <div
              className="prose-container"
              dangerouslySetInnerHTML={{ __html: currentHtml }}
            />
          )}
          {streaming && (
            <span className="inline-block w-1.5 h-4 bg-brand-500 animate-pulse ml-0.5 align-middle" />
          )}
          {!loading && result && (
            <div className="prose-container" dangerouslySetInnerHTML={{ __html: result }} />
          )}
        </div>
      )}
    </div>
  );
}
