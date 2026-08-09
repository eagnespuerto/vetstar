import { useMemo, useState } from "react";
import {
  uploadCoverageCsv,
  type CoverageEvent,
  type CoverageResponse,
} from "./api";

type SortKey =
  | "event_id"
  | "ra"
  | "dec"
  | "ecl_lat"
  | "t0"
  | "tE"
  | "n_sectors"
  | "observable";

interface Props {
  onAnalyzeInModuleA?: (evt: CoverageEvent) => void;  // Step 5 handoff
}

export default function MicrolensingCoverage({ onAnalyzeInModuleA }: Props) {
  const [data, setData] = useState<CoverageResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [marginTe, setMarginTe] = useState<number>(1.0);
  const [observableOnly, setObservableOnly] = useState<boolean>(false);
  const [sortKey, setSortKey] = useState<SortKey>("observable");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const run = async () => {
    if (!file) return;
    setBusy(true); setErr(null); setData(null);
    try {
      const d = await uploadCoverageCsv(file, marginTe);
      setData(d);
    } catch (e: any) {
      setErr(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const rows = useMemo(() => {
    if (!data) return [];
    const filtered = observableOnly ? data.events.filter((e) => e.observable) : data.events;
    const keyFn: Record<SortKey, (e: CoverageEvent) => number | string> = {
      event_id: (e) => e.event_id,
      ra: (e) => e.ra,
      dec: (e) => e.dec,
      ecl_lat: (e) => e.ecliptic_latitude_deg,
      t0: (e) => e.t0,
      tE: (e) => e.tE,
      n_sectors: (e) => e.sectors.length,
      observable: (e) => (e.observable ? 1 : 0),
    };
    const sign = sortDir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const va = keyFn[sortKey](a);
      const vb = keyFn[sortKey](b);
      if (typeof va === "string" || typeof vb === "string")
        return sign * String(va).localeCompare(String(vb));
      return sign * ((va as number) - (vb as number));
    });
  }, [data, observableOnly, sortKey, sortDir]);

  const toggleSort = (k: SortKey) => {
    if (sortKey === k) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(k); setSortDir("desc"); }
  };

  return (
    <section className="space-y-4">
      {/* Caveat banner — always visible so low yield doesn't confuse anyone. */}
      <div className="rounded border-l-4 border-amber-500 bg-amber-50 p-3 text-sm text-amber-900">
        <p className="font-semibold">⚠ TESS coverage caveat</p>
        <p className="mt-1 text-xs leading-relaxed">
          The Galactic bulge — where microlensing rates are highest — sits at
          ecliptic latitude ≈ −5.5°, right in TESS's <strong>thinnest</strong>{" "}
          coverage zone (cameras start ~6° off the ecliptic). So most classic
          bulge events will come back <strong>not observable</strong>. This is
          expected. Realistic yield: events happening in TESS's better-covered
          mid/high ecliptic latitudes, or bulge-adjacent fields in the specific
          sectors that dipped lowest.
        </p>
      </div>

      {/* Upload form */}
      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <h3 className="font-semibold text-slate-800 mb-2">1. Upload event catalog</h3>
        <p className="text-xs text-slate-600 mb-3">
          CSV with columns <code>event_id, ra, dec, t0, tE</code>. Accepts
          Gaia microlensing alerts, OGLE / MOA / KMTNet event lists — all
          publish RA/Dec/t0. <code>tE</code> is optional (defaults to 20 days).
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <input
            type="file"
            accept=".csv,.txt"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="text-sm"
          />
          <label className="text-xs text-slate-600 flex items-center gap-1">
            <span>Wing margin (× tE):</span>
            <input
              type="number"
              min="0"
              step="0.5"
              value={marginTe}
              onChange={(e) => setMarginTe(Math.max(0, parseFloat(e.target.value) || 0))}
              className="border rounded px-2 py-0.5 font-mono w-16 text-xs"
            />
          </label>
          <button
            onClick={run}
            disabled={!file || busy}
            className="px-4 py-1.5 text-sm font-semibold bg-blue-600 text-white rounded hover:bg-blue-700 disabled:bg-slate-300"
          >
            {busy ? "Checking coverage…" : "Check TESS coverage"}
          </button>
        </div>
        {err && (
          <p className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-2 mt-2">
            {err}
          </p>
        )}
      </section>

      {/* Results */}
      {data && (
        <>
          <section className="grid grid-cols-2 md:grid-cols-5 gap-2 text-xs">
            <SummaryCard label="Total" value={data.summary.n_total} />
            <SummaryCard
              label="Observable (t₀ in sector)"
              value={data.summary.n_observable}
              highlight={data.summary.n_observable > 0}
            />
            <SummaryCard
              label="With wings in-window"
              value={data.summary.n_observable_with_wings}
            />
            <SummaryCard
              label="In bulge blind zone"
              value={data.summary.n_in_bulge_blind_zone}
              warn={data.summary.n_in_bulge_blind_zone > 0}
            />
            <SummaryCard
              label="No tess-point sectors"
              value={data.summary.n_no_tess_point}
              warn={data.summary.n_no_tess_point > 0}
            />
          </section>

          {data.notes.length > 0 && (
            <ul className="text-xs text-slate-600 bg-slate-50 border border-slate-200 rounded p-2 space-y-0.5">
              {data.notes.map((n, i) => (
                <li key={i}>• {n}</li>
              ))}
            </ul>
          )}

          <div className="flex items-center justify-between flex-wrap gap-2">
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={observableOnly}
                onChange={(e) => setObservableOnly(e.target.checked)}
                className="h-4 w-4"
              />
              Show observable rows only
            </label>
            <span className="text-xs text-slate-500">
              {rows.length} of {data.summary.n_total} rows shown
            </span>
          </div>

          <div className="overflow-x-auto rounded border border-slate-200 bg-white">
            <table className="min-w-full text-xs">
              <thead className="bg-slate-100 text-slate-700">
                <tr>
                  <Th sortKey="event_id" active={sortKey} dir={sortDir} onToggle={toggleSort}>Event</Th>
                  <Th sortKey="ra" active={sortKey} dir={sortDir} onToggle={toggleSort}>RA</Th>
                  <Th sortKey="dec" active={sortKey} dir={sortDir} onToggle={toggleSort}>Dec</Th>
                  <Th sortKey="ecl_lat" active={sortKey} dir={sortDir} onToggle={toggleSort}>Ecl. lat</Th>
                  <Th sortKey="t0" active={sortKey} dir={sortDir} onToggle={toggleSort}>t₀ (BTJD)</Th>
                  <Th sortKey="tE" active={sortKey} dir={sortDir} onToggle={toggleSort}>tE (d)</Th>
                  <Th sortKey="n_sectors" active={sortKey} dir={sortDir} onToggle={toggleSort}>Sectors covering RA/Dec</Th>
                  <Th sortKey="observable" active={sortKey} dir={sortDir} onToggle={toggleSort}>Observable?</Th>
                  {onAnalyzeInModuleA && <th className="px-2 py-1.5 text-left">Action</th>}
                </tr>
              </thead>
              <tbody>
                {rows.map((e) => (
                  <tr
                    key={e.event_id}
                    className={
                      e.observable
                        ? "bg-emerald-50/40 hover:bg-emerald-50 border-t border-slate-100"
                        : "hover:bg-slate-50 border-t border-slate-100"
                    }
                  >
                    <td className="px-2 py-1 font-mono">{e.event_id}</td>
                    <td className="px-2 py-1 font-mono">{e.ra.toFixed(3)}</td>
                    <td className="px-2 py-1 font-mono">{e.dec.toFixed(3)}</td>
                    <td
                      className={`px-2 py-1 font-mono ${
                        e.in_bulge_blind_zone ? "text-amber-800 font-semibold" : ""
                      }`}
                      title={e.in_bulge_blind_zone ? "In bulge blind zone (TESS thinnest coverage)" : ""}
                    >
                      {e.ecliptic_latitude_deg.toFixed(2)}°
                    </td>
                    <td className="px-2 py-1 font-mono">{e.t0.toFixed(2)}</td>
                    <td className="px-2 py-1 font-mono">{e.tE.toFixed(1)}</td>
                    <td className="px-2 py-1">
                      {e.sectors.length === 0 ? (
                        <span className="text-slate-400 italic">
                          {e.no_tess_point ? "no tess-point" : "none"}
                        </span>
                      ) : (
                        <SectorPills sectors={e.sectors} t0={e.t0} />
                      )}
                    </td>
                    <td className="px-2 py-1">
                      {e.observable ? (
                        <span className="inline-block px-2 py-0.5 rounded bg-emerald-600 text-white font-medium">
                          {e.observable_with_wings ? "yes (wings in)" : "yes (t₀ only)"}
                        </span>
                      ) : (
                        <span className="inline-block px-2 py-0.5 rounded bg-slate-200 text-slate-600">
                          no
                        </span>
                      )}
                    </td>
                    {onAnalyzeInModuleA && (
                      <td className="px-2 py-1">
                        <button
                          onClick={() => onAnalyzeInModuleA(e)}
                          disabled={!e.observable}
                          className="px-2 py-0.5 text-[11px] font-medium bg-blue-600 text-white rounded hover:bg-blue-700 disabled:bg-slate-300"
                          title={e.observable ? "Load into Module A" : "Not observable — nothing to analyze"}
                        >
                          Analyze →
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

function SummaryCard({
  label, value, highlight, warn,
}: {
  label: string; value: number; highlight?: boolean; warn?: boolean;
}) {
  const cls = highlight
    ? "border-emerald-400 bg-emerald-50"
    : warn
    ? "border-amber-400 bg-amber-50"
    : "border-slate-200 bg-white";
  return (
    <div className={`rounded border p-2 ${cls}`}>
      <div className="text-[10px] text-slate-500 uppercase tracking-wide">{label}</div>
      <div className="text-xl font-mono font-semibold text-slate-800">{value}</div>
    </div>
  );
}

function Th({
  sortKey, active, dir, onToggle, children,
}: {
  sortKey: SortKey;
  active: SortKey;
  dir: "asc" | "desc";
  onToggle: (k: SortKey) => void;
  children: React.ReactNode;
}) {
  const isActive = active === sortKey;
  return (
    <th
      className="px-2 py-1.5 text-left font-semibold cursor-pointer select-none hover:bg-slate-200"
      onClick={() => onToggle(sortKey)}
      title="Click to sort"
    >
      <span className="inline-flex items-center gap-1">
        {children}
        {isActive && <span className="text-slate-500">{dir === "asc" ? "▲" : "▼"}</span>}
      </span>
    </th>
  );
}

function SectorPills({ sectors, t0 }: { sectors: any[]; t0: number }) {
  return (
    <div className="flex flex-wrap gap-0.5">
      {sectors.map((s, i) => {
        const inWin = s.t0_in_window;
        const nominal = s.window?.nominal;
        return (
          <span
            key={`${s.sector}-${i}`}
            className={
              inWin
                ? "inline-block px-1.5 py-0.5 rounded font-mono text-[10px] bg-emerald-600 text-white"
                : "inline-block px-1.5 py-0.5 rounded font-mono text-[10px] bg-slate-200 text-slate-600"
            }
            title={
              s.window
                ? `S${s.sector} cam${s.camera}/ccd${s.ccd}: BTJD ${s.window.start_btjd.toFixed(2)}–${s.window.end_btjd.toFixed(2)}` +
                  (nominal ? " (nominal)" : " (calendar)") +
                  ` · t₀=${t0.toFixed(2)} ${inWin ? "IN window" : "outside"}`
                : `S${s.sector}: window unknown`
            }
          >
            S{s.sector}
            {nominal && <span className="opacity-60">*</span>}
          </span>
        );
      })}
    </div>
  );
}
