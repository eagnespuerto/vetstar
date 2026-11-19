import { useState } from "react";
import type { DvtResult } from "../types";
import { ShareToImgbbButton } from "../ShareButton";

export function DvtStatus({ dvt }: { dvt?: DvtResult | null }) {
  const ok = !!dvt?.available;
  const tce = dvt?.tce;
  const bits: string[] = [];
  if (tce?.period_d != null) bits.push(`P = ${Number(tce.period_d).toPrecision(6)} d`);
  if (tce?.a_over_rs != null) bits.push(`a/R★ = ${Number(tce.a_over_rs).toPrecision(4)}`);
  if (tce?.impact_b != null) bits.push(`b = ${Number(tce.impact_b).toPrecision(3)}`);
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-xs font-medium border ${
        ok
          ? "bg-emerald-50 border-emerald-300 text-emerald-800"
          : "bg-slate-100 border-slate-300 text-slate-500"
      }`}
      title={
        ok
          ? "SPOC Data Validation time series fetched — fitted a/R★ and impact parameter are in use."
          : "No SPOC DV time series available for this target (FFI-only target or a very recent sector). The geometry falls back to the BLS-derived estimate."
      }
    >
      {ok ? "✓" : "—"} SPOC DV time series:{" "}
      <span className="font-semibold">{ok ? "fetched" : "not available"}</span>
      {ok && bits.length > 0 && (
        <span className="font-mono text-emerald-700/80">· {bits.join("  ")}</span>
      )}
    </span>
  );
}

// ---------------------------------------------------------------------------
// SPOC DVT phase-fold panel
// ---------------------------------------------------------------------------

export function DvtPanel({
  dvt,
  ticId,
  sector,
}: {
  dvt: DvtResult;
  ticId?: number | null;
  sector?: number | null;
}) {
  const [open, setOpen] = useState(true);
  const tce = dvt.tce;
  if (!tce) return null;
  const exportTitle = ticId
    ? `TIC_${ticId}${sector ? `_S${sector}` : ""}_SPOC_DV_phase_fold`
    : "SPOC_DV_phase_fold";

  const fmt = (v: any, d = 4) =>
    v === null || v === undefined ? "—" : typeof v === "number" ? Number(v.toPrecision(d)) : String(v);

  const params: [string, string][] = [
    ["Period (d)",         fmt(tce.period_d, 6)],
    ["Duration T₁₄ (h)",  fmt(tce.duration_h, 5)],
    ["Depth (ppm)",        tce.depth_ppm != null ? tce.depth_ppm.toFixed(0) : "—"],
    ["a/R★ (ARAT)",       fmt(tce.a_over_rs, 4)],
    ["Impact param b",    fmt(tce.impact_b, 3)],
    ["Inclination (°)",   fmt(tce.inclination_deg, 4)],
    ["Rp/Rs",             fmt(tce.rprs, 4)],
    ["SNR",               fmt(tce.snr, 3)],
    ["N transits",        tce.n_transits != null ? String(Math.round(tce.n_transits)) : "—"],
  ].filter(([, v]) => v !== "—") as [string, string][];

  return (
    <section className="bg-white rounded-lg shadow p-5">
      <div className="flex items-center justify-between mb-2">
        <div>
          <h3 className="font-bold text-slate-800">
            SPOC DV phase-fold
            {dvt.n_tces > 1 && (
              <span className="ml-2 text-xs font-normal text-slate-500">
                TCE 1 of {dvt.n_tces}
              </span>
            )}
          </h3>
          <p className="text-xs text-slate-400">
            SPOC Data Validation fit — period, a/R★ (ARAT), and impact parameter b
            replace the b=0 BLS assumption in the TLCM geometry and semi-major axis.
          </p>
        </div>
        <button
          onClick={() => setOpen((x) => !x)}
          className="text-slate-400 hover:text-slate-700 text-sm ml-4"
          aria-label={open ? "Collapse" : "Expand"}
        >
          {open ? "▾" : "▸"}
        </button>
      </div>

      {open && (
        <div className="space-y-3">
          {/* Fitted parameter chips */}
          <div className="flex flex-wrap gap-2">
            {params.map(([k, v]) => (
              <span
                key={k}
                className="inline-flex items-center gap-1 rounded bg-indigo-50 border border-indigo-200 px-2 py-0.5 text-xs"
              >
                <span className="text-indigo-500 font-medium">{k}</span>
                <span className="font-mono text-slate-800">{v}</span>
              </span>
            ))}
            {tce.a_over_rs != null && (
              <span className="inline-flex items-center gap-1 rounded bg-emerald-50 border border-emerald-200 px-2 py-0.5 text-xs text-emerald-700 font-medium">
                ✓ SPOC DV a/R★ used in TLCM
              </span>
            )}
            {tce.impact_b != null && (
              <span className="inline-flex items-center gap-1 rounded bg-emerald-50 border border-emerald-200 px-2 py-0.5 text-xs text-emerald-700 font-medium">
                ✓ SPOC DV b used in TLCM
              </span>
            )}
          </div>

          {/* Phase-fold plot with SPOC transit model */}
          {tce.phase_fold_plot && (
            <div>
              <div className="flex items-center justify-between gap-2 mb-1">
                <p className="text-xs text-slate-500">
                  Phase-folded light curve (LC_INIT) with SPOC Mandel-Agol model overlay (MODEL_INIT)
                </p>
                <ShareToImgbbButton
                  base64={tce.phase_fold_plot}
                  title={exportTitle}
                  label="SPOC DV phase-fold"
                />
              </div>
              <img
                src={`data:image/png;base64,${tce.phase_fold_plot}`}
                alt="SPOC DV phase-fold with transit model"
                className="w-full rounded border border-slate-200"
              />
            </div>
          )}
        </div>
      )}
    </section>
  );
}
