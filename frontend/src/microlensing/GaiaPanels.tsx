import { useState } from "react";
import type { FfiCutoutResponse, GaiaAlertEntry, GaiaLightcurve, JointFitResponse } from "../api";
import { CyclingLoader, FFI_CUTOUT_MSGS, GAIA_FETCH_MSGS, GAIA_SEARCH_MSGS } from "../Loaders";
import { ObservablesPanel, PlanetPredictionsPanel } from "./DerivedPanels";
import { fmt, fmtErr } from "./format";

export function FfiCutoutSection({
  ffiData, busy, err,
}: {
  ffiData: FfiCutoutResponse | null;
  busy: boolean;
  err: string | null;
}) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4 space-y-2">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <h3 className="font-semibold text-slate-800">
          TESScut FFI + Gaia DR3 overlay
        </h3>
        <p className="text-xs text-slate-500">
          Median-stacked cutout of the TESS 21″ pixels around the target,
          with catalog neighbours plotted so you can eyeball blending.
        </p>
      </div>
      {busy && (
        <div className="rounded bg-blue-50 border border-blue-200 p-2 text-xs text-blue-800">
          <CyclingLoader messages={FFI_CUTOUT_MSGS} />
        </div>
      )}
      {err && !busy && (
        <p className="text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2">
          FFI cutout: {err}
        </p>
      )}
      {ffiData && (
        <div className="flex flex-wrap gap-4">
          <img
            src={`data:image/png;base64,${ffiData.image}`}
            alt="TESS FFI cutout with Gaia overlay"
            className="max-w-md rounded border border-slate-200"
          />
          <div className="text-xs text-slate-700 flex-1 min-w-[15rem]">
            <p><span className="text-slate-500">Sector:</span> <span className="font-mono">S{String(ffiData.sector ?? 0).padStart(3, "0")}</span></p>
            <p><span className="text-slate-500">Frames stacked:</span> <span className="font-mono">{ffiData.n_frames ?? "—"}</span></p>
            <p><span className="text-slate-500">FOV radius (Gaia):</span> <span className="font-mono">{ffiData.gaia_fov_radius_arcsec.toFixed(1)}″</span></p>
            <p><span className="text-slate-500">Gaia sources in FOV:</span> <span className="font-mono">{ffiData.gaia_n_sources}</span></p>
            {ffiData.gaia_sources.length > 0 && (
              <details className="mt-2">
                <summary className="cursor-pointer text-slate-600 hover:text-slate-800">
                  brightest {Math.min(6, ffiData.gaia_sources.length)}
                </summary>
                <ul className="mt-1 space-y-0.5 font-mono text-[10px]">
                  {ffiData.gaia_sources.slice(0, 6).map((s) => (
                    <li key={s.source_id}>
                      G={s.phot_g_mean_mag != null ? s.phot_g_mean_mag.toFixed(2) : "—"} ·
                      Δ {s.separation_arcsec.toFixed(1)}″
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

export function GaiaLoader({
  gaiaLc, gaiaBusy, gaiaErr, nearbyAlerts, nearbyBusy, canSearchNearby,
  onFetchAlert, onSearchNearby, onClear,
}: {
  gaiaLc: GaiaLightcurve | null;
  gaiaBusy: boolean;
  gaiaErr: string | null;
  nearbyAlerts: GaiaAlertEntry[] | null;
  nearbyBusy: boolean;
  canSearchNearby: boolean;
  onFetchAlert: (id: string) => void;
  onSearchNearby: () => void;
  onClear: () => void;
}) {
  const [alertId, setAlertId] = useState("");
  return (
    <section className="rounded-lg border border-fuchsia-200 bg-fuchsia-50/40 p-4 space-y-3">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <h3 className="font-semibold text-fuchsia-900">
          Gaia baseline <span className="text-xs font-normal text-fuchsia-700">
            (optional — breaks tE ↔ u₀ degeneracy per Harris et al. 2026)
          </span>
        </h3>
        {gaiaLc && (
          <button
            className="text-xs text-fuchsia-700 hover:underline"
            onClick={onClear}
          >
            clear Gaia LC
          </button>
        )}
      </div>
      <p className="text-xs text-slate-700">
        Pull a Gaia G-band light curve from the{" "}
        <a
          href="https://gsaweb.ast.cam.ac.uk/alerts/alertsindex"
          target="_blank"
          rel="noopener noreferrer"
          className="underline text-slate-800"
        >
          Gaia Alerts feed
        </a>
        {" "}by alert ID (e.g. <code>Gaia23bra</code>), or search for known
        microlensing alerts near the target coordinates.
      </p>
      <div className="flex flex-wrap items-end gap-2">
        <div>
          <label className="block text-[10px] text-slate-600 mb-0.5">Gaia alert ID</label>
          <input
            type="text"
            placeholder="e.g. Gaia23bra"
            value={alertId}
            onChange={(e) => setAlertId(e.target.value.trim())}
            className="border rounded px-2 py-1 font-mono text-xs w-48"
          />
        </div>
        <button
          onClick={() => alertId && onFetchAlert(alertId)}
          disabled={!alertId || gaiaBusy}
          className="px-3 py-1 text-xs font-semibold bg-fuchsia-600 text-white rounded hover:bg-fuchsia-700 disabled:bg-slate-300 min-w-[8rem]"
        >
          {gaiaBusy
            ? <CyclingLoader messages={GAIA_FETCH_MSGS} className="text-[10px]" />
            : "Fetch Gaia LC"}
        </button>
        <button
          onClick={onSearchNearby}
          disabled={!canSearchNearby || nearbyBusy}
          className="px-3 py-1 text-xs font-medium bg-fuchsia-100 hover:bg-fuchsia-200 text-fuchsia-800 rounded border border-fuchsia-300 disabled:bg-slate-100 disabled:text-slate-400 min-w-[10rem]"
          title={canSearchNearby ? "Search Gaia Alerts within 90″ of the current target" : "Load a TESS LC via TIC or the Coverage handoff first — coordinates are needed for the cone search."}
        >
          {nearbyBusy
            ? <CyclingLoader messages={GAIA_SEARCH_MSGS} className="text-[10px]" />
            : "Search near target coords"}
        </button>
      </div>
      {gaiaErr && (
        <p className="text-xs text-red-700 bg-red-50 border border-red-200 rounded p-1.5">
          {gaiaErr}
        </p>
      )}
      {nearbyAlerts && nearbyAlerts.length > 0 && (
        <div className="text-xs">
          <p className="text-slate-700 mb-1 font-medium">
            {nearbyAlerts.length} nearby alert{nearbyAlerts.length === 1 ? "" : "s"} — click to load:
          </p>
          <ul className="space-y-0.5">
            {nearbyAlerts.map((a) => (
              <li key={a.alert_id}>
                <button
                  className="text-left hover:underline text-fuchsia-800 font-mono"
                  onClick={() => onFetchAlert(a.alert_id)}
                >
                  {a.alert_id}
                </button>
                <span className="text-slate-500 ml-2">
                  Δ {a.separation_arcsec.toFixed(1)}″ · {a.classification} · {a.date}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {gaiaLc && (
        <div className="text-xs bg-white border border-fuchsia-200 rounded p-2">
          <p className="font-semibold text-fuchsia-900">
            Loaded: <span className="font-mono">{gaiaLc.alert_id}</span>
          </p>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-0.5 mt-1">
            <span>Points: <span className="font-mono">{gaiaLc.n_points}</span></span>
            <span>Baseline JD: <span className="font-mono">{gaiaLc.time_jd[0].toFixed(1)}</span></span>
            <span>Latest JD: <span className="font-mono">{gaiaLc.time_jd[gaiaLc.time_jd.length - 1].toFixed(1)}</span></span>
            <span>
              G range: <span className="font-mono">
                {Math.min(...gaiaLc.mag).toFixed(2)}…{Math.max(...gaiaLc.mag).toFixed(2)}
              </span>
            </span>
          </div>
          <p className="text-[10px] text-slate-500 mt-1 italic">
            Errors inflated per Kruszyńska et al. 2022 approximation
            (σ² = (1.5·σ_reported)² + (3 mmag)²). Fit-time BTJD = JD − 2 457 000.
          </p>
        </div>
      )}
    </section>
  );
}

export function JointResultsPanel({ jr }: { jr: JointFitResponse }) {
  const p = jr.joint_fit.params;
  const e = jr.joint_fit.param_err;
  return (
    <section className="rounded-lg border-2 border-fuchsia-400 bg-fuchsia-50/60 p-4 space-y-3">
      <div>
        <p className="text-[10px] uppercase tracking-wide text-fuchsia-800/70">
          Joint fit — TESS + Gaia (Harris et al. 2026 workflow)
        </p>
        <h4 className="text-lg font-bold text-fuchsia-900 mt-0.5">
          Shared PSPL geometry across both bands
        </h4>
        <p className="text-xs text-slate-700 mt-1">
          {jr.window.n_tess} TESS points + {jr.window.n_gaia} Gaia points ·
          Gaia baseline G = <span className="font-mono">{jr.window.gaia_baseline_mag.toFixed(3)}</span>
        </p>
      </div>

      <div>
        <h5 className="text-sm font-semibold text-fuchsia-900 mb-1">Joint best-fit parameters</h5>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-1 text-xs">
          {(["t0", "tE", "u0", "f_s_T", "f_b_T", "f_s_G", "f_b_G"] as const).map((k) => (
            <div key={k} className="flex flex-col">
              <span className="text-slate-500 font-mono">{k}</span>
              <span className="font-mono text-sm">
                {fmtErr(p[k], e[k] ?? null, 5)}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div>
        <h5 className="text-sm font-semibold text-fuchsia-900 mb-1">Per-band goodness</h5>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-0.5 text-xs">
          <div>χ²(TESS): <span className="font-mono">{fmt(jr.joint_fit.chi2_tess, 2)}</span></div>
          <div>χ²(Gaia): <span className="font-mono">{fmt(jr.joint_fit.chi2_gaia, 2)}</span></div>
          <div>χ²(total): <span className="font-mono">{fmt(jr.joint_fit.chi2_total, 2)}</span></div>
          <div>BIC: <span className="font-mono">{fmt(jr.joint_fit.bic, 2)}</span></div>
          <div>χ²_red: <span className="font-mono">{fmt(jr.joint_fit.chi2_red, 3)}</span></div>
          <div>k (free): <span className="font-mono">{jr.joint_fit.n_params}</span></div>
          <div>N points: <span className="font-mono">{jr.joint_fit.n_points}</span></div>
        </div>
      </div>

      {jr.observables && (
        <div>
          <h5 className="text-sm font-semibold text-fuchsia-900 mb-1">
            Observable parameters (from joint fit)
          </h5>
          <ObservablesPanel obs={jr.observables} />
        </div>
      )}

      {jr.planet_predictions && (
        <div>
          <h5 className="text-sm font-semibold text-fuchsia-900 mb-1">
            Predicted planet parameters (from joint fit)
          </h5>
          <PlanetPredictionsPanel pp={jr.planet_predictions} />
        </div>
      )}

      <div>
        <h5 className="text-sm font-semibold text-fuchsia-900 mb-1">Notes</h5>
        <ul className="text-[11px] text-slate-700 space-y-0.5">
          {jr.notes.map((n, i) => <li key={i}>• {n}</li>)}
        </ul>
      </div>
    </section>
  );
}
