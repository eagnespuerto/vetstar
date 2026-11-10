import type { MicrolensingFitResponse } from "../api";
import type { ExofopRow } from "../api";
import { fmt, fmtErr } from "./format";

export function ObservablesPanel({ obs }: { obs: NonNullable<MicrolensingFitResponse["observables"]> }) {
  const rows: Array<[string, string, string?]> = [
    ["t₀ (BTJD)",       fmtErr(obs.t0_btjd, obs.t0_btjd_err, 5)],
    ["t₀ (BJD)",        fmt(obs.t0_bjd, 5)],
    ["Einstein tE (d)", fmtErr(obs.einstein_timescale_d, obs.einstein_timescale_err_d, 3)],
    ["Impact u₀",       fmtErr(obs.impact_parameter_u0, obs.impact_parameter_err, 4)],
    ["Peak magnification A_max",           fmt(obs.peak_magnification, 3)],
    ["Blended peak magnification (obs)",   fmt(obs.peak_magnification_observed, 3)],
    ["Peak brightening (mag)",             fmt(obs.peak_brightening_mag, 3)],
    ["Einstein-crossing duration (d)",     fmt(obs.einstein_crossing_duration_d, 3)],
    ["Magnification FWHM (d)",             fmt(obs.magnification_fwhm_d, 3)],
    ["Source flux fraction f_s/(f_s+f_b)", fmt(obs.source_flux_fraction, 3)],
    ["Blend flux fraction",                fmt(obs.blend_flux_fraction, 3)],
    ["μ_rel (mas/yr, fiducial θ_E=0.5 mas)", fmt(obs.mu_rel_mas_per_yr_fiducial, 3),
      obs.mu_rel_note],
  ];
  return (
    <section>
      <h5 className="text-sm font-semibold text-slate-700 mb-1">
        Observable parameters
      </h5>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-x-4 gap-y-1 text-xs">
        {rows.map(([label, val, tip], i) => (
          <div key={i} className="flex flex-col" title={tip}>
            <span className="text-slate-500">{label}</span>
            <span className="font-mono">{val}</span>
          </div>
        ))}
      </div>
      <p className="text-[10px] text-slate-500 mt-1 italic">
        {obs.mu_rel_note}
      </p>
    </section>
  );
}

export function PlanetPredictionsPanel({ pp }: { pp: NonNullable<MicrolensingFitResponse["planet_predictions"]> }) {
  const rows: Array<[string, string]> = [
    ["Fiducial lens mass M_L (M☉)",           fmt(pp.fiducial_lens_mass_solar, 2)],
    ["Fiducial lens distance D_L (kpc)",      fmt(pp.fiducial_lens_distance_kpc, 2)],
    ["Fiducial source distance D_S (kpc)",    fmt(pp.fiducial_source_distance_kpc, 2)],
    ["θ_E (mas)",                             fmt(pp.theta_E_mas_fiducial, 4)],
    ["Physical Einstein radius r_E (AU)",     fmt(pp.einstein_radius_au_fiducial, 3)],
    ["v_rel (km/s)",                          fmt(pp.v_rel_km_s_fiducial, 1)],
    ["Closest approach u₀·r_E (AU)",          fmt(pp.closest_approach_au_fiducial, 3)],
    ["Detection floor q_min = M_p/M_L",       fmt(pp.planet_q_min_detectable, 6)],
    ["Planet mass floor (M⊕ @ fiducial M_L)", fmt(pp.planet_mass_floor_m_earth_fiducial, 3)],
    ["Planet mass floor (M♃ @ fiducial M_L)", fmt(pp.planet_mass_floor_m_jupiter_fiducial, 4)],
  ];
  return (
    <section className="rounded border border-indigo-200 bg-indigo-50/40 p-3">
      <h5 className="text-sm font-semibold text-indigo-900 mb-1">
        Predicted planet parameters <span className="text-xs font-normal text-indigo-700">(under fiducial bulge-lens priors)</span>
      </h5>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-x-4 gap-y-1 text-xs">
        {rows.map(([label, val], i) => (
          <div key={i} className="flex flex-col">
            <span className="text-slate-600">{label}</span>
            <span className="font-mono">{val}</span>
          </div>
        ))}
      </div>
      <p className="text-[10px] text-indigo-800/80 mt-2 italic">
        {pp.assumption}
      </p>
      <p className="text-[10px] text-indigo-800/80 mt-1 italic">
        {pp.planet_sensitivity_note}
      </p>
    </section>
  );
}

// ============================================================================
// Gaia loader + joint-fit results
// ============================================================================

export function ExofopPlanetParamsPanel({ rows }: { rows: ExofopRow[] }) {
  return (
    <section>
      <h5 className="text-sm font-semibold text-slate-700 mb-1">
        ExoFOP planet parameters <span className="text-xs font-normal text-slate-500">(microlensing-derivable subset — mirrors the Transit tab)</span>
      </h5>
      <div className="overflow-x-auto rounded border border-slate-200 bg-white">
        <table className="min-w-full text-xs">
          <thead className="bg-slate-100 text-slate-700">
            <tr>
              <th className="px-2 py-1 text-left font-semibold">Parameter</th>
              <th className="px-2 py-1 text-right font-semibold">Value</th>
              <th className="px-2 py-1 text-left font-semibold">Unit</th>
              <th className="px-2 py-1 text-center font-semibold">Req.</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr
                key={r.label + i}
                className={
                  (r.value == null ? "text-slate-400 " : "text-slate-800 ") +
                  "border-t border-slate-100 hover:bg-slate-50"
                }
              >
                <td className="px-2 py-1">{r.label}</td>
                <td className="px-2 py-1 text-right font-mono">{fmt(r.value, 4)}</td>
                <td className="px-2 py-1 font-mono text-slate-500">{r.unit || "—"}</td>
                <td className="px-2 py-1 text-center">{r.required ? "*" : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[10px] text-slate-500 mt-1 italic">
        Fields marked <b>*</b> are required for an ExoFOP-TESS submission.
        Rows shown as <b>—</b> are not derivable from a single-lens fit; they
        need RV follow-up or a binary-lens caustic-crossing detection.
      </p>
    </section>
  );
}
