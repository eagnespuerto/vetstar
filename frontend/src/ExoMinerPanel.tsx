/**
 * ExoMinerPanel.tsx
 *
 * Renders ExoMiner feature extraction results below the standard vetting
 * output, auto-expanding after vetting finishes. Mirrors the HCI panel
 * pattern in App.tsx.
 *
 * Usage (inside ResultsView):
 *   <ExoMinerPanel result={result} />
 *
 * The panel:
 *  - Auto-runs when mounted (result has BLS params already computed).
 *  - Can be manually re-run via a "Re-run" button.
 *  - Shows 7 phase-folded views + scalar diagnostics with σ badges.
 *  - Collapses/expands like the HCI section.
 */
import { runExominer, type ExominerResult } from "./api";
import { useEffect, useRef, useState } from "react";
import type { VettingResult } from "./types";

// ─── Sigma badge ────────────────────────────────────────────────────────────

function SigmaBadge({ value }: { value: number | null | undefined }) {
  if (value == null || isNaN(value))
    return <span className="text-slate-400 font-mono">—</span>;
  const cls =
    value >= 3
      ? "bg-red-100 text-red-800 border border-red-300"
      : "bg-emerald-50 text-emerald-800 border border-emerald-200";
  return (
    <span className={`inline-block px-2 py-0.5 rounded font-mono text-xs ${cls}`}>
      {value.toFixed(2)} σ
    </span>
  );
}

// ─── Progress bar ────────────────────────────────────────────────────────────

function Bar({
  value,
  max = 100,
  color,
}: {
  value: number;
  max?: number;
  color: string;
}) {
  return (
    <div className="h-1.5 bg-slate-200 rounded-full overflow-hidden">
      <div
        className="h-full rounded-full transition-all duration-700"
        style={{ width: `${Math.min(100, (value / max) * 100)}%`, backgroundColor: color }}
      />
    </div>
  );
}

// ─── Scalar row ──────────────────────────────────────────────────────────────

function ScalarRow({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="contents">
      <dt className="text-slate-500 text-xs font-mono">{label}</dt>
      <dd className="text-sm">{value}</dd>
    </div>
  );
}

// ─── View figure ─────────────────────────────────────────────────────────────

function ViewFig({
  b64,
  label,
}: {
  b64: string | null | undefined;
  label: string;
}) {
  if (!b64) return null;
  return (
    <figure className="space-y-1">
      <figcaption className="text-xs text-slate-500 font-medium">{label}</figcaption>
      <img
        src={`data:image/png;base64,${b64}`}
        alt={label}
        className="w-full rounded border shadow-sm"
        loading="lazy"
      />
    </figure>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

const VIEW_ORDER: Array<[keyof ExominerResult["plots"], string]> = [
  ["global_view", "Global view (2001 bins, full phase, SG-detrended)"],
  ["local_view", "Local view (201 bins, ±2× transit duration)"],
  ["secondary_view", "Secondary view (centred at phase 0.5)"],
  ["odd_even_view", "Odd vs even transit views"],
  ["centroid_global_view", "Centroid global view"],
  ["centroid_local_view", "Centroid local view"],
  ["diagnostic_sigmas", "Diagnostic σ summary"],
];

export default function ExoMinerPanel({
  result,
}: {
  result: VettingResult;
}) {
  const [data, setData] = useState<ExominerResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(true);
  const ranRef = useRef(false);

  // Auto-run once when the panel mounts (i.e. after vetting finishes).
  useEffect(() => {
    if (ranRef.current) return;
    ranRef.current = true;
    doRun();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function doRun() {
    if (!result.bls.period || !result.bls.t0 || !result.bls.duration) return;
    setLoading(true);
    setError(null);
    try {
      const res = await runExominer({
        tic_id: result.star.tic_id ?? undefined,
        sector: result.star.sector ?? undefined,
        period: result.bls.period,
        t0: result.bls.t0,
        duration: result.bls.duration,
        crowdsap: result.star.crowdsap ?? undefined,
        // pass enough BLS context for the backend to re-parse the file
        // (the backend caches the last parsed file in-process)
      });
      setData(res);
    } catch (e: any) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  const s = data?.scalars;

  // Interpret diagnostics
  const oe = s?.odd_even_sigma ?? 0;
  const sec = s?.secondary_depth_sigma ?? 0;
  const cen = s?.centroid_shift_sigma ?? 0;
  const signals: string[] = [];
  if (oe >= 3) signals.push(`odd/even depth mismatch (${oe.toFixed(1)}σ)`);
  if (sec >= 3) signals.push(`secondary eclipse (${sec.toFixed(1)}σ)`);
  if (cen >= 3) signals.push(`centroid shift (${cen.toFixed(1)}σ)`);

  return (
    <section className="bg-white rounded-lg shadow overflow-hidden">
      {/* Header row */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-slate-100">
        <div className="flex items-center gap-2">
          <span className="text-lg">🔭</span>
          <h3 className="font-bold text-slate-800">
            ExoMiner Feature Extraction
          </h3>
          <span className="text-xs text-indigo-500 bg-indigo-50 border border-indigo-200 rounded px-2 py-0.5">
            Valizadegan et al. 2022
          </span>
          {loading && (
            <span className="text-xs text-indigo-600 animate-pulse">
              running…
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={doRun}
            disabled={loading}
            className="text-xs px-3 py-1.5 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:bg-slate-300 transition"
          >
            {loading ? "Running…" : data ? "Re-run" : "Run ExoMiner"}
          </button>
          <button
            onClick={() => setExpanded((v) => !v)}
            className="text-slate-400 hover:text-slate-700 text-sm"
          >
            {expanded ? "▲" : "▼"}
          </button>
        </div>
      </div>

      {/* Body */}
      {expanded && (
        <div className="px-5 py-4 space-y-5">
          {/* Error */}
          {error && (
            <div className="bg-red-50 border border-red-300 text-red-800 rounded p-3 text-sm">
              ExoMiner failed: {error}
            </div>
          )}

          {/* Skeleton while loading */}
          {loading && !data && (
            <div className="space-y-2 animate-pulse">
              <div className="h-3 bg-slate-200 rounded w-3/4" />
              <div className="h-3 bg-slate-200 rounded w-1/2" />
              <div className="h-32 bg-slate-100 rounded mt-4" />
            </div>
          )}

          {/* Empty state */}
          {!loading && !data && !error && (
            <p className="text-sm text-slate-500">
              Click <strong>Run ExoMiner</strong> to extract phase-folded views
              and diagnostic scalars from the BLS solution.
            </p>
          )}

          {/* Results */}
          {data && (
            <>
              {/* Interpretation banner */}
              {signals.length > 0 ? (
                <div className="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded p-3">
                  ⚠️ Elevated diagnostics:{" "}
                  {signals.join("; ")} — consistent with an eclipsing binary or
                  background blend rather than a planet.
                </div>
              ) : (
                <div className="text-sm text-emerald-800 bg-emerald-50 border border-emerald-200 rounded p-3">
                  ✓ All diagnostic sigmas below 3σ threshold — no obvious
                  false-positive indicators in the ExoMiner feature set.
                </div>
              )}

              {/* Scalar grid */}
              <div>
                <h4 className="font-semibold text-slate-700 mb-2 text-sm">
                  Scalar diagnostics
                </h4>
                <dl className="grid grid-cols-2 md:grid-cols-3 gap-x-4 gap-y-2">
                  <ScalarRow
                    label="Period"
                    value={<span className="font-mono">{s?.period_d?.toFixed(6)} d</span>}
                  />
                  <ScalarRow
                    label="Duration"
                    value={<span className="font-mono">{s?.duration_h?.toFixed(3)} h</span>}
                  />
                  <ScalarRow
                    label="Depth (local min)"
                    value={
                      <span className="font-mono">
                        {s?.depth_ppm != null
                          ? `${Math.round(s.depth_ppm).toLocaleString()} ppm`
                          : "—"}
                      </span>
                    }
                  />
                  <ScalarRow
                    label="Transit count"
                    value={<span className="font-mono">{s?.transit_count ?? "—"}</span>}
                  />
                  <ScalarRow
                    label="Odd/Even σ"
                    value={<SigmaBadge value={s?.odd_even_sigma} />}
                  />
                  <ScalarRow
                    label="Secondary σ"
                    value={<SigmaBadge value={s?.secondary_depth_sigma} />}
                  />
                  <ScalarRow
                    label="Centroid σ"
                    value={<SigmaBadge value={s?.centroid_shift_sigma} />}
                  />
                  <ScalarRow
                    label="Scatter MAD"
                    value={<span className="font-mono">{s?.scatter_mad?.toFixed(6)}</span>}
                  />
                  <ScalarRow
                    label="CROWDSAP"
                    value={
                      <span className="font-mono">
                        {s?.crowdsap != null ? s.crowdsap.toFixed(3) : "—"}
                      </span>
                    }
                  />
                  <ScalarRow
                    label="SG detrend window"
                    value={
                      <span className="font-mono">{s?.sg_detrend_window_h?.toFixed(2)} h</span>
                    }
                  />
                </dl>

                {/* Sigma bars */}
                <div className="mt-3 space-y-1.5">
                  {[
                    { label: "Odd/Even σ", v: oe },
                    { label: "Secondary σ", v: sec },
                    { label: "Centroid σ", v: cen },
                  ].map(({ label, v }) => {
                    const color = v >= 3 ? "#ef4444" : v >= 1.5 ? "#f59e0b" : "#10b981";
                    return (
                      <div key={label} className="flex items-center gap-2 text-xs">
                        <span className="w-24 text-slate-500 shrink-0">{label}</span>
                        <div className="flex-1">
                          <Bar value={v} max={Math.max(10, v + 1)} color={color} />
                        </div>
                        <span className="w-12 text-right font-mono text-slate-600">
                          {v.toFixed(2)}σ
                        </span>
                        <span className="text-slate-300">|</span>
                        <span className="text-[10px] text-red-400">3σ</span>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Phase-folded views */}
              <div>
                <h4 className="font-semibold text-slate-700 mb-3 text-sm">
                  Phase-folded views
                </h4>
                <div className="space-y-4">
                  {VIEW_ORDER.map(([key, label]) => (
                    <ViewFig
                      key={key}
                      b64={data.plots[key]}
                      label={label}
                    />
                  ))}
                </div>
              </div>

              {/* About box */}
              <details className="text-xs text-slate-500 border-t pt-3">
                <summary className="cursor-pointer hover:text-slate-700">
                  About ExoMiner feature extraction
                </summary>
                <div className="mt-2 space-y-1 leading-relaxed">
                  <p>
                    Replicates the TFRecord feature set from{" "}
                    <strong>Valizadegan et al. 2022</strong> (ExoMiner, ApJ 926 120).
                  </p>
                  <p>
                    <strong>Global view:</strong> 2001 median bins over the full
                    phase range, after Savitzky–Golay detrending with window = 3×
                    transit duration. Normalised so median = 0, deepest point = −1.
                  </p>
                  <p>
                    <strong>Local view:</strong> 201 bins, ±2× transit duration in
                    phase. Same normalisation.
                  </p>
                  <p>
                    <strong>Secondary view:</strong> 201 bins centred at phase 0.5.
                    A visible dip here is a strong eclipsing-binary indicator.
                  </p>
                  <p>
                    <strong>Odd/Even views:</strong> local view computed separately
                    from odd- and even-numbered transit epochs. Differing depths
                    signal an EB.
                  </p>
                  <p>
                    <strong>Centroid views:</strong> phase-folded combined centroid
                    shift (√(Δcol² + Δrow²), signed by column direction). Available
                    only for SPOC 2-min data with MOM_CENTR columns.
                  </p>
                </div>
              </details>
            </>
          )}
        </div>
      )}
    </section>
  );
}
