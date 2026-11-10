import { useEffect, useState } from "react";
import { fetchHabitability, fetchMultisector, fitsDownloadUrl } from "../api";
import type { VettingResult } from "../types";
import ManualDipSelector from "../ManualDipSelector";
import { ShareToImgbbButton } from "../ShareButton";
import { CyclingLoader } from "../Loaders";
import { HCI_LOADING_MSGS } from "./loadingMessages";
import { RVPanel } from "./RVPanel";
import { ObservablesSection } from "./ObservablesPanel";
import { DvtStatus, DvtPanel } from "./DvtPanel";
import { HabitabilityPanel } from "./HabitabilityPanel";
import { MultisectorPanel } from "./MultisectorPanel";
import { PlotsSection } from "./PlotsSection";
import { GlossaryPanel } from "./GlossaryPanel";
import { KV } from "./ui";

export interface LcViewControls {
  detrend: boolean;
  binMinutes: number | null;
  onChange: (next: { detrend: boolean; binMinutes: number | null }) => void;
  busy: boolean;
}

export function LcViewToggleBar({ controls }: { controls: LcViewControls }) {
  const binChoices: Array<{ label: string; value: number | null }> = [
    { label: "off", value: null },
    { label: "10 min", value: 10 },
    { label: "30 min", value: 30 },
  ];
  return (
    <div
      className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-slate-600 mb-2 px-2 py-1.5 bg-slate-50 border border-slate-200 rounded"
      aria-label="Light-curve plot controls"
    >
      <label className="flex items-center gap-1.5 cursor-pointer">
        <input
          type="checkbox"
          checked={controls.detrend}
          disabled={controls.busy}
          onChange={(e) => controls.onChange({ detrend: e.target.checked, binMinutes: controls.binMinutes })}
          className="h-3.5 w-3.5"
        />
        <span>Detrend (1-day rolling median)</span>
      </label>
      <span className="text-slate-300">|</span>
      <span className="text-slate-500">Bin overlay:</span>
      <div className="flex items-center gap-1">
        {binChoices.map((c) => {
          const active = controls.binMinutes === c.value;
          return (
            <button
              key={String(c.value)}
              type="button"
              disabled={controls.busy}
              onClick={() => controls.onChange({ detrend: controls.detrend, binMinutes: c.value })}
              className={
                "px-2 py-0.5 rounded border text-xs transition-colors " +
                (active
                  ? "bg-blue-600 border-blue-600 text-white"
                  : "bg-white border-slate-300 text-slate-700 hover:bg-slate-100") +
                (controls.busy ? " opacity-50 cursor-not-allowed" : "")
              }
            >
              {c.label}
            </button>
          );
        })}
      </div>
      {controls.busy && (
        <span className="text-blue-700 italic">re-rendering…</span>
      )}
    </div>
  );
}

export function ResultsView({ result, lcControls }: { result: VettingResult; lcControls?: LcViewControls }) {
  const [hciData, setHciData] = useState<any>(null);
  const [hciLoading, setHciLoading] = useState(false);
  const [hciError, setHciError] = useState<string | null>(null);
  const [multisectorData, setMultisectorData] = useState<any>(null);
  const [msLoading, setMsLoading] = useState(false);
  const [msError, setMsError] = useState<string | null>(null);

  // Auto-run HCI as soon as a result with a TIC ID is available.
  // Deps are scalars, so this fires once per (tic, sector) — plot-toggle
  // re-renders that swap in a new ``result`` object don't retrigger it.
  useEffect(() => {
    if (result.star.tic_id && !hciData && !hciLoading && !hciError) {
      runHci();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result.star.tic_id, result.star.sector]);

  const runHci = async (massEarth?: number) => {
    if (!result.star.tic_id) return;
    setHciLoading(true); setHciError(null);
    try {
      const dvtTce = result.dvt?.tce;
      const enrichedVerdict = {
        ...result.verdict,
        _depth: result.events?.[0]?.depth ?? null,
        _bls_depth: result.bls?.depth ?? null,
        _t14_d: result.shape?.t14_d ?? null,
        _bls_duration: result.bls?.duration ?? null,
        _shape_class: result.shape?.shape_class ?? null,
        _events: result.events,
        // DVT-fitted parameters let the backend use SPOC's b and a/R★
        _dvt_period_d:   dvtTce?.period_d   ?? null,
        _dvt_duration_h: dvtTce?.duration_h ?? null,
        _dvt_depth_frac: dvtTce?.depth_frac ?? null,
        _dvt_impact_b:   dvtTce?.impact_b   ?? null,
        _dvt_a_over_rs:  dvtTce?.a_over_rs  ?? null,
      };
      const data = await fetchHabitability(result.star.tic_id, {
        stellar_teff: result.star.teff ?? undefined,
        stellar_radius_sun: result.star.radius ?? undefined,
        stellar_mass_sun: result.star.mass ?? undefined,
        orbital_period_d: dvtTce?.period_d ?? result.bls?.period ?? undefined,
        R_companion_Rjup: result.physics?.R_companion_Rjup ?? undefined,
        planet_mass_earth: massEarth ?? undefined,
        vetting_verdict: enrichedVerdict,
      });
      setHciData(data);
    } catch (e: any) {
      setHciError(e.message);
    } finally {
      setHciLoading(false);
    }
  };

  const runMultisector = async () => {
    if (!result.star.tic_id) return;
    setMsLoading(true); setMsError(null);
    try {
      const data = await fetchMultisector(result.star.tic_id);
      setMultisectorData(data);
    } catch (e: any) {
      setMsError(e.message);
    } finally {
      setMsLoading(false);
    }
  };

  const verdictColor = {
    planet_candidate: "bg-emerald-100 border-emerald-400 text-emerald-900",
    eclipsing_binary_candidate: "bg-amber-100 border-amber-400 text-amber-900",
    false_positive_blend: "bg-rose-100 border-rose-400 text-rose-900",
    ambiguous: "bg-slate-100 border-slate-400 text-slate-900",
    no_signal: "bg-slate-50 border-slate-300 text-slate-700",
  }[result.verdict.category] || "bg-slate-100 border-slate-400 text-slate-900";

  return (
    <div className="space-y-6">
      {result.mast && (
        <div
          className={`text-sm rounded border p-3 ${
            result.mast.fallback
              ? "bg-amber-50 border-amber-300 text-amber-900"
              : "bg-blue-50 border-blue-200 text-blue-900"
          }`}
        >
          <strong>Data source:</strong>{" "}
          <code>{result.mast.author}</code> ({Math.round(result.mast.exptime ?? 0)} s
          cadence) &middot; <span className="font-mono text-xs">{result.mast.filename}</span>
          {result.star?.tic_id != null && result.star?.sector != null && (
            <a
              href={fitsDownloadUrl(result.star.tic_id, result.star.sector)}
              className="ml-3 inline-block px-2 py-0.5 rounded bg-slate-700 text-white text-xs font-medium hover:bg-slate-800 no-underline"
              download
            >
              ↓ Download FITS
            </a>
          )}
          {result.mast.fallback && (
            <span className="block mt-1 text-xs">
              SPOC 2-min wasn't available for this TIC+sector. Falling back to{" "}
              {result.mast.author} — the centroid (background-blend) test will be
              skipped because FFI products don't include centroid columns.
            </span>
          )}
          <div className="mt-2">
            <DvtStatus dvt={result.dvt} />
          </div>
        </div>
      )}
      {/* Glossary */}
      <GlossaryPanel />

      {/* Verdict */}
      <section className={`rounded-lg border-2 p-5 ${verdictColor}`}>
        <p className="text-xs uppercase tracking-wide opacity-70">Verdict</p>
        <h2 className="text-2xl font-bold mt-1">{result.verdict.headline}</h2>
        <p className="text-sm mt-1">
          Category: <code>{result.verdict.category}</code> &nbsp;·&nbsp; Confidence:{" "}
          {(result.verdict.confidence * 100).toFixed(0)}%
        </p>
        <ul className="mt-3 space-y-1 text-sm">
          {result.verdict.reasons.map((r, i) => (
            <li key={i}>• {r}</li>
          ))}
        </ul>
        {result.verdict.flags.length > 0 && (
          <p className="mt-2 text-xs">
            Flags:{" "}
            {result.verdict.flags.map((f) => (
              <span
                key={f}
                className="inline-block mr-1 px-2 py-0.5 bg-white/60 rounded font-mono"
              >
                {f}
              </span>
            ))}
          </p>
        )}
      </section>

      {/* BTJD → BJD conversion guide */}
      <section className="rounded-lg border border-sky-200 bg-sky-50 p-4 text-sm text-slate-700">
        <div className="flex items-center gap-2 font-semibold text-sky-800">
          <span>🕑</span> Convert BTJD → BJD
        </div>
        <p className="mt-1">
          TESS times (including the transit epoch <code>t0</code> in this
          report) are in <strong>BTJD</strong>. ExoFOP-TESS expects the transit
          epoch in full <strong>BJD</strong>. Convert with:
        </p>
        <p className="mt-2 font-mono text-slate-900 bg-white border border-sky-200 rounded px-3 py-2">
          BJD = BTJD + 2,457,000
        </p>
        {result.bls?.t0 != null && (
          <p className="mt-2 text-xs text-slate-600">
            For this candidate: t0 = {Number(result.bls.t0).toFixed(5)} BTJD
            {"  →  "}
            <span className="font-mono text-slate-900">
              {(Number(result.bls.t0) + 2457000).toFixed(5)} BJD
            </span>
          </p>
        )}
      </section>

      <div className="grid md:grid-cols-2 gap-4">
        <KV title="Stellar parameters" data={result.star} />
        <KV title="Light curve summary" data={result.summary} />
      </div>

      {/* Plots */}
      {result.detrend?.applied && result.plots.detrend && (
        <figure className="space-y-1 mb-4">
          <div className="flex items-center justify-between">
            <figcaption className="text-xs text-slate-500 font-medium">
              Stellar variability detrend — P ={" "}
              {result.detrend.period_days?.toFixed(4)} d, amplitude{" "}
              {result.detrend.amplitude_ppm?.toFixed(0)} ppm, RMS reduced{" "}
              {result.detrend.rms_reduction_pct?.toFixed(1)}%
            </figcaption>
            <ShareToImgbbButton
              base64={result.plots.detrend}
              title={`detrend_TIC${result.star.tic_id ?? ""}_S${result.star.sector ?? ""}`}
              label="Stellar variability detrend"
            />
          </div>
          <img
            src={`data:image/png;base64,${result.plots.detrend}`}
            alt="Stellar variability detrend"
            className="w-full rounded border border-slate-200"
          />
        </figure>
      )}
      {result.detrend?.reason === "skipped_low_amplitude" && (
        <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded p-2 mb-3">
          Detrending was requested but the fitted sinusoid amplitude was below the
          per-cadence noise floor — no detrend applied.
        </p>
      )}
      {lcControls && <LcViewToggleBar controls={lcControls} />}
      <PlotsSection
        plots={result.plots}
        ticId={result.star.tic_id}
        sector={result.star.sector}
        extras={(() => {
          const e: Record<string, string> = {};
          if (result.dvt?.available && result.dvt.tce?.phase_fold_plot) {
            e.spoc_dv_phase_fold = result.dvt.tce.phase_fold_plot;
          }
          if (hciData?.hci_image) {
            e.hci_summary = hciData.hci_image;
          }
          return Object.keys(e).length ? e : undefined;
        })()}
      />

      {/* SPOC DVT phase-fold (MAST results only, when DVT is available) */}
      {result.dvt?.available && (
        <DvtPanel
          dvt={result.dvt}
          ticId={result.star.tic_id}
          sector={result.star.sector}
        />
      )}

      {/* Tests */}
      <div className="grid md:grid-cols-2 gap-4">
        <div>
          {result.bls?.constrained && (
            <div className="mb-2 text-xs">
              <span className="inline-block rounded bg-blue-100 px-2 py-0.5 text-blue-800">
                Constrained search (±2% around{" "}
                {result.bls.known_period_input_days?.toFixed(4)} d, matched{" "}
                {result.bls.matched_harmonic})
              </span>
              {result.bls.constrained_fallback_reason && (
                <p className="mt-1 text-amber-700">
                  Constrained search fell back to blind sweep:{" "}
                  {result.bls.constrained_fallback_reason}
                </p>
              )}
            </div>
          )}
          <KV title="BLS (Box Least Squares)" data={result.bls} hide={["_periodogram"]} />
        </div>
        <KV title="Lomb-Scargle periodogram" data={result.lomb_scargle} hide={["top_peaks"]} />
        <KV title="Centroid (background-blend test)" data={result.centroid} />
        <KV title="Transit shape (U vs V)" data={result.shape} />
        <KV title="Odd / even depths (EB test)" data={result.odd_even} />
        <KV title="Secondary eclipse search" data={result.secondary} />
        <KV title="Physical interpretation" data={result.physics} />
      </div>

      {/* Events */}
      <section className="bg-white rounded-lg shadow p-5">
        <h3 className="font-bold mb-3">Discrete dip events ({result.events.length})</h3>
        {result.events.length === 0 ? (
          <p className="text-sm text-slate-500">No discrete events detected.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b text-left text-slate-600">
              <tr>
                <th className="py-1">#</th>
                <th>t_start</th>
                <th>t_end</th>
                <th>Duration (h)</th>
                <th>Depth (%)</th>
              </tr>
            </thead>
            <tbody>
              {result.events.map((e, i) => (
                <tr key={i} className="border-b">
                  <td className="py-1">{i + 1}</td>
                  <td className="font-mono">{e.t_start.toFixed(3)}</td>
                  <td className="font-mono">{e.t_end.toFixed(3)}</td>
                  <td>{(e.duration_d * 24).toFixed(2)}</td>
                  <td>{(e.depth * 100).toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* Manual tiny-dip selector */}
      <ManualDipSelector result={result} />

      {/* Observables, parameters & TLCM — standalone, independent of HCI */}
      <ObservablesSection result={result} />

      {/* Habitability Chance Index */}
      <section className="bg-white rounded-lg shadow p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-bold text-slate-800">
            🌍 Habitability Chance Index
            <span className="ml-2 text-xs font-normal text-slate-500">
              based on Hill et al. (2026) STEHM
            </span>
          </h3>
          <div className="flex gap-2">
            {result.star.tic_id && (
              <button
                onClick={runMultisector}
                disabled={msLoading}
                className="text-sm px-3 py-1.5 bg-violet-600 text-white rounded hover:bg-violet-700 disabled:bg-slate-300"
              >
                {msLoading ? "Fetching sectors…" : "Multi-sector analysis"}
              </button>
            )}
          </div>
        </div>

        {hciError && (
          <div className="flex items-center gap-2 text-sm text-red-700 mb-2">
            <span>HCI error: {hciError}</span>
            <button
              onClick={() => { setHciError(null); runHci(); }}
              className="text-xs px-2 py-0.5 rounded border border-red-300 hover:bg-red-50"
            >
              retry
            </button>
          </div>
        )}
        {msError && <p className="text-sm text-red-700 mb-2">Multi-sector error: {msError}</p>}

        {hciLoading && (
          <div className="rounded bg-teal-50 border border-teal-200 p-3 text-teal-900 text-sm mb-2">
            <CyclingLoader messages={HCI_LOADING_MSGS} />
          </div>
        )}

        {hciData && <HabitabilityPanel data={hciData} />}
        {hciData && <RVPanel ticId={result.star.tic_id} periodD={result.bls?.period ?? null} mstar={hciData?.planet?.stellar_mass_sun ?? null} onUseMass={(m) => runHci(m)} massInHci={hciData?.planet?.mass_earth ?? null} massSource={hciData?.planet?.mass_source ?? null} />}
        {multisectorData && <MultisectorPanel data={multisectorData} />}
      </section>
    </div>
  );
}
