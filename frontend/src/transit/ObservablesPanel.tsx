import { useEffect, useRef, useState } from "react";
import { fetchObservables } from "../api";
import type { VettingResult } from "../types";
import { CyclingLoader } from "../Loaders";

export function ObservablesSection({ result }: { result: VettingResult }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(true);
  const ranRef = useRef(false);

  useEffect(() => {
    if (ranRef.current) return;
    ranRef.current = true;
    compute();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function compute() {
    setLoading(true);
    setError(null);
    try {
      // Prefer SPOC DV-fitted parameters when a DVT file was found alongside
      // the light curve.  These carry a fitted impact parameter and a/R★ that
      // replace the b=0 central-transit assumption used by the BLS-only path.
      const dvtTce = result.dvt?.tce;
      const vv: Record<string, any> = {
        _bls_period: result.bls?.period ?? null,
        _period: result.bls?.period ?? null,
        _depth: result.physics?.observed_depth ?? result.events?.[0]?.depth ?? null,
        _bls_depth: result.bls?.depth ?? null,
        _t14_d: result.shape?.t14_d ?? null,
        _bls_duration: result.bls?.duration ?? null,
        _shape_class: result.shape?.shape_class ?? null,
        _R_companion_Rjup: result.physics?.R_companion_Rjup ?? null,
        _events: result.events,
        // DVT-fitted parameters (null when DVT unavailable — backend ignores null)
        _dvt_period_d:   dvtTce?.period_d   ?? null,
        _dvt_duration_h: dvtTce?.duration_h ?? null,
        _dvt_depth_frac: dvtTce?.depth_frac ?? null,
        _dvt_impact_b:   dvtTce?.impact_b   ?? null,
        _dvt_a_over_rs:  dvtTce?.a_over_rs  ?? null,
      };
      const obs = await fetchObservables({
        tic_id: result.star.tic_id ?? undefined,
        stellar_teff: result.star.teff ?? undefined,
        stellar_radius_sun: result.star.radius ?? undefined,
        stellar_mass_sun: result.star.mass ?? undefined,
        // Prefer DVT period (multi-sector fold) over single-sector BLS peak.
        orbital_period_d: dvtTce?.period_d ?? result.bls?.period ?? undefined,
        rp_rjup: result.physics?.R_companion_Rjup ?? undefined,
        transit_depth_frac: dvtTce?.depth_frac ?? result.physics?.observed_depth ?? result.bls?.depth ?? undefined,
        vetting_verdict: vv,
      });
      setData(obs);
    } catch (e: any) {
      setError(e?.message ?? "Observables request failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="bg-white rounded-lg shadow p-5">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="font-bold text-slate-800">Observables, parameters &amp; TLCM</h3>
          <p className="text-xs text-slate-400">
            POE forward model, transit geometry, and ExoFOP-TESS TOI parameters — independent of HCI
          </p>
        </div>
        <div className="flex items-center gap-2">
          {!loading && (
            <button onClick={compute} className="text-xs text-slate-500 hover:text-slate-800 underline">
              Re-run
            </button>
          )}
          <button
            onClick={() => setExpanded((x) => !x)}
            className="text-slate-400 hover:text-slate-700 text-sm"
            aria-label={expanded ? "Collapse" : "Expand"}
          >
            {expanded ? "▾" : "▸"}
          </button>
        </div>
      </div>

      {expanded && (
        <>
          {loading && (
            <div className="rounded bg-slate-50 border border-slate-200 p-3 text-sm">
              <CyclingLoader
                messages={[
                  "Forward-modelling observables…",
                  "Working out the transit geometry…",
                  "Mapping ExoFOP TOI fields…",
                ]}
              />
            </div>
          )}
          {!loading && error && (
            <p className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded p-3">
              {error}
            </p>
          )}
          {!loading && !error && data && (
            <ObservablesPanel
              obs={data}
              tlcm={data.tlcm}
              aSource={data.semi_major_axis_source}
              result={result}
            />
          )}
        </>
      )}
    </section>
  );
}

export function ObservablesPanel({ obs, tlcm, aSource, result }: { obs: any; tlcm?: any; aSource?: string; result?: any }) {
  if (!obs) return null;
  const hz = obs.habitable_zone || {};
  const fmt = (v: any, d = 4) =>
    v === null || v === undefined ? "—" : typeof v === "number" ? Number(v.toPrecision(d)) : String(v);
  const rows: [string, any][] = [
    ["Luminosity (L⊙)", fmt(obs.luminosity_lsun)],
    ["HZ inner / centre / outer (AU)", `${fmt(hz.inner_au, 3)} / ${fmt(hz.center_au, 3)} / ${fmt(hz.outer_au, 3)}`],
    ["HZ centre (mas)", hz.center_mas !== undefined ? fmt(hz.center_mas, 3) : "— (needs distance)"],
    ["Semi-major axis a (AU)", `${fmt(obs.orbit?.semi_major_axis_au)}${aSource ? `  (${aSource})` : ""}`],
    ["Orbital period P (d)", fmt(obs.orbit?.orbital_period_d)],
    ["Insolation S (S⊕)", fmt(obs.insolation_searth)],
    ["Planet radius (R⊕ / R♃)", `${fmt(obs.planet?.rp_earth, 3)} / ${fmt(obs.planet?.rp_rjup, 3)}`],
    ["Planet mass (M♃)", `${fmt(obs.planet?.mp_mjup, 3)}  (${obs.planet?.mass_source})`],
    ...(obs.planet?.mass_estimates_earth ? [["M–R estimates (M⊕): C&K / power-law",
      `${fmt(obs.planet.mass_estimates_earth.chen_kipping, 3)} / ${fmt(obs.planet.mass_estimates_earth.powerlaw, 3)}`] as [string, any]] : []),
    ["RV semi-amplitude K (m/s)", obs.radial_velocity?.K_ms_textbook !== undefined
      ? `${fmt(obs.radial_velocity?.K_ms, 4)}  (textbook ${fmt(obs.radial_velocity?.K_ms_textbook, 4)}, Δ${fmt(obs.radial_velocity?.K_agreement_pct, 2)}%)`
      : fmt(obs.radial_velocity?.K_ms)],
    ["Astrometric Δθ (μas)", obs.astrometric?.theta_uas !== undefined ? fmt(obs.astrometric.theta_uas, 4) : "— (needs distance)"],
    ["Predicted transit depth (%)", fmt(obs.transit?.depth_pct, 4)],
    ["Max projected separation (″)", fmt(obs.max_projected_separation_arcsec)],
  ];
  const tl = tlcm || {};
  const rv = tl.radial_velocity || {};
  // Show the a/Rs source in the label: SPOC DV model fit is preferred over the
  // b=0 duration-inversion when a DVT file was available.
  const isSpocDv = (tl.a_over_rs_assumption ?? "").includes("SPOC DV");
  const aRsLabel = isSpocDv
    ? "Scaled semi-major axis a/Rs (SPOC DV / dynamical)"
    : "Scaled semi-major axis a/Rs (duration / dynamical)";
  const tlRows: [string, any][] = [
    ["Radius ratio k = Rp/Rs", fmt(tl.radius_ratio_k, 4)],
    [aRsLabel,
      tl.a_over_rs_dynamical != null
        ? `${fmt(tl.a_over_rs, 4)} / ${fmt(tl.a_over_rs_dynamical, 4)}${tl.a_over_rs_agreement_pct != null ? ` (Δ${fmt(tl.a_over_rs_agreement_pct, 1)}%)` : ""}`
        : fmt(tl.a_over_rs, 4)],
    ["Stellar density (g/cm³ · ρ⊙)", `${fmt(tl.stellar_density_gcc, 3)} · ${fmt(tl.stellar_density_rho_sun, 3)}`],
    ["M★ from density (M⊙)", fmt(tl.mstar_from_density_sun, 3)],
    ["Impact parameter b / i (°)", `${fmt(tl.impact_parameter_b, 3)} / ${fmt(tl.inclination_deg, 4)}`],
    ["Absolute mass from RV (M♃)", rv.mp_mjup !== undefined ? fmt(rv.mp_mjup, 4) : "— (needs RV K)"],
  ];
  const showTlcm = tl.radius_ratio_k != null || tl.a_over_rs != null || tl.a_over_rs_dynamical != null;

  // ExoFOP-TESS TOI parameters: pipeline-measured values + derived observables.
  // Transit epoch is converted BTJD → BJD (BJD = BTJD + 2,457,000).
  // Prefer SPOC DV-fitted values (DVT) over BLS estimates when available —
  // they are fitted from a Mandel-Agol model across all processed sectors.
  const dvtTce = result?.dvt?.tce;
  const t0btjd = result?.bls?.t0;
  const depthFrac = dvtTce?.depth_frac ?? result?.physics?.observed_depth ?? result?.bls?.depth;
  const durationH = dvtTce?.duration_h ?? result?.shape?.t14_hours ?? (result?.bls?.duration != null ? result.bls.duration * 24 : null);
  const insol = obs.insolation_searth;
  const teq = insol != null && insol > 0 ? 278.3 * Math.pow(insol, 0.25) : null;
  const efmt = (v: any, unit: string) => {
    if (v === null || v === undefined) return "—";
    if (unit === "BJD") return Number(v).toFixed(5);
    if (unit === "ppm") return Number(v).toFixed(1);
    if (unit === "m/s") return Number(v).toFixed(3);
    return typeof v === "number" ? Number(v.toPrecision(5)) : String(v);
  };
  const exofopRows: { label: string; value: any; unit: string; req: boolean }[] = [
    { label: "Orbital Period", unit: "days", req: true,
      value: dvtTce?.period_d ?? result?.bls?.period ?? obs.orbit?.orbital_period_d },
    { label: "Transit Epoch", unit: "BJD", req: true,
      value: dvtTce?.epoch_btjd != null ? dvtTce.epoch_btjd + 2457000 : (t0btjd != null ? t0btjd + 2457000 : null) },
    { label: "Transit Depth", unit: "ppm", req: true, value: depthFrac != null ? depthFrac * 1e6 : null },
    { label: "Transit Duration", unit: "hrs", req: true, value: durationH },
    { label: "Inclination", unit: "deg", req: false, value: tl.inclination_deg },
    { label: "Impact Parameter b", unit: "", req: false, value: tl.impact_parameter_b },
    { label: "R_planet/R_star", unit: "", req: false, value: tl.radius_ratio_k },
    { label: "a/R_star", unit: "", req: false, value: tl.a_over_rs },
    { label: "Radius", unit: "R_Earth", req: false, value: obs.planet?.rp_earth },
    { label: "Mass", unit: "M_Earth", req: false, value: obs.planet?.mp_earth },
    { label: "Equilibrium Temperature", unit: "K", req: false, value: teq },
    { label: "Insolation Flux", unit: "Flux_Earth", req: false, value: insol },
    { label: "Fitted Stellar Density", unit: "g/cm³", req: false, value: tl.stellar_density_gcc },
    { label: "Semi-major Axis", unit: "AU", req: false, value: obs.orbit?.semi_major_axis_au },
    { label: "Eccentricity", unit: "", req: false, value: obs.orbit?.eccentricity ?? 0 },
    { label: "Argument of Periastron ω", unit: "deg", req: false, value: null },
    { label: "Time of Periastron", unit: "BJD", req: false, value: null },
    { label: "Velocity Semi-amplitude", unit: "m/s", req: false, value: obs.radial_velocity?.K_ms },
  ];

  return (
    <div className="rounded-lg border border-slate-300 bg-white p-4 space-y-2">
      <div className="flex items-center justify-between">
        <h4 className="font-bold text-slate-800">Predicted Observables (POE)</h4>
        <span className="text-xs text-slate-400">NASA Exoplanet Archive POE equations</span>
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
        {rows.map(([k, v]) => (
          <div className="contents" key={k}>
            <dt className="text-slate-600">{k}</dt>
            <dd className="mono text-slate-900">{v}</dd>
          </div>
        ))}
      </dl>
      {showTlcm && (
        <>
          <div className="flex items-center justify-between pt-2 border-t mt-2">
            <h4 className="font-bold text-slate-800">Transit geometry (TLCM)</h4>
            <span className="text-xs text-slate-400">Csizmadia 2020</span>
          </div>
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
            {tlRows.map(([k, v]) => (
              <div className="contents" key={k}>
                <dt className="text-slate-600">{k}</dt>
                <dd className="mono text-slate-900">{v}</dd>
              </div>
            ))}
          </dl>
        </>
      )}
      {obs.orbit?.derivation && obs.orbit.derivation !== "as supplied" && (
        <p className="text-xs text-slate-500">{obs.orbit.derivation}.</p>
      )}

      {/* ExoFOP-TESS TOI parameters for submission */}
      <div className="pt-2 border-t mt-2">
        <div className="flex items-center justify-between">
          <h4 className="font-bold text-slate-800">ExoFOP-TESS TOI parameters</h4>
          <span className="text-xs text-slate-400">*** = required for submission</span>
        </div>
        <p className="text-xs text-slate-500 mt-1">
          Transit epoch in BJD (BTJD + 2,457,000). Non-required fields are
          derived estimates from the observables / TLCM analyses.
        </p>
        <table className="w-full text-sm mt-2">
          <thead className="text-left text-slate-500 border-b">
            <tr><th className="py-1 font-medium">Parameter</th><th className="py-1 font-medium">Value</th><th className="py-1 font-medium">Unit</th></tr>
          </thead>
          <tbody>
            {exofopRows.map((r) => (
              <tr key={r.label} className="border-b border-slate-100">
                <td className="py-1 text-slate-600">
                  {r.label}
                  {r.req && <span className="text-sky-600 font-bold"> ***</span>}
                </td>
                <td className="py-1 mono text-slate-900">{efmt(r.value, r.unit)}</td>
                <td className="py-1 text-slate-400">{r.unit || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {[...(obs.caveats || []), ...((tlcm && tlcm.caveats) || [])].length > 0 && (
        <ul className="text-xs text-slate-500 list-disc pl-4 space-y-0.5">
          {[...(obs.caveats || []), ...((tlcm && tlcm.caveats) || [])].map((c: string, i: number) => <li key={i}>{c}</li>)}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SPOC DVT fetch-status indicator (single + multi-sector)
// ---------------------------------------------------------------------------
