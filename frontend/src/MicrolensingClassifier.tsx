import { useEffect, useMemo, useRef, useState } from "react";
import { fetchLightcurveByCoords, fetchLightcurveByTic, fitMicrolensing, type LcByCoordsResponse, type MicrolensingFitResponse } from "./api";
import { fetchGaiaAlertLightcurve, fetchMicrolensingFfi, fitMicrolensingJoint, searchGaiaAlertsNear, type FfiCutoutResponse, type GaiaAlertEntry, type GaiaLightcurve, type JointFitResponse } from "./api";
import { CyclingLoader, MICROLENSING_FIT_MSGS, MICROLENSING_JOINT_MSGS } from "./Loaders";
import type { ClassifierPrefill, LC, ModelKey } from "./microlensing/types";
import { parseCsv, parseJson, makeDemoPspl, makeDemoFlare } from "./microlensing/lightCurve";
import { W, H_MAIN, PAD, makeGeom, _MODEL_COLORS, pspFluxLocal, flareFluxLocal, denseCurve, Axes, DataDots, ModelPolyline } from "./microlensing/plot";
import { PrefillBanner } from "./microlensing/PrefillBanner";
import { DataLoader } from "./microlensing/DataLoader";
import { ResultsPanel } from "./microlensing/ResultsPanel";
import { FfiCutoutSection, GaiaLoader, JointResultsPanel } from "./microlensing/GaiaPanels";

export type { ClassifierPrefill } from "./microlensing/types";

interface ClassifierProps {
  prefill?: ClassifierPrefill | null;
  onDismissPrefill?: () => void;
}

export default function MicrolensingClassifier({ prefill, onDismissPrefill }: ClassifierProps = {}) {
  const [lc, setLc] = useState<LC | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);

  // Window selection state (in time units).
  const [sel, setSel] = useState<{ a: number; b: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const svgRef = useRef<SVGSVGElement | null>(null);

  // When a prefill lands AND an LC is loaded that covers the prefill t0, auto-
  // set a selection window centered on t0 with width ≈ 4 * tE. If no LC yet, the
  // banner explains what to load; the selection is applied once the LC arrives.
  useEffect(() => {
    if (!prefill || !lc) return;
    const half = Math.max(2 * prefill.tE, 0.5);
    const tMin = Math.min(...lc.time);
    const tMax = Math.max(...lc.time);
    if (prefill.t0 < tMin || prefill.t0 > tMax) return;  // t0 outside loaded LC
    const t_start = Math.max(tMin, prefill.t0 - half);
    const t_end = Math.min(tMax, prefill.t0 + half);
    setSel({ a: t_start, b: t_end });
  }, [prefill, lc]);

  // Fit state.
  const [fitting, setFitting] = useState(false);
  const [fitErr, setFitErr] = useState<string | null>(null);
  const [result, setResult] = useState<MicrolensingFitResponse | null>(null);
  const [residualModel, setResidualModel] = useState<ModelKey>("pspl");
  const [visible, setVisible] = useState<Record<ModelKey, boolean>>({
    pspl: true, flare: true, null: true,
  });

  // Compact whole-LC display geometry.
  const geomMain = useMemo(() => (lc ? makeGeom(lc.time, lc.flux, H_MAIN) : null), [lc]);

  const onFile = async (file: File) => {
    setLoadErr(null); setResult(null); setSel(null);
    try {
      const text = await file.text();
      const lower = file.name.toLowerCase();
      const parsed = lower.endsWith(".json") ? parseJson(text) : parseCsv(text);
      if (parsed.time.length < 12)
        throw new Error(`Need at least 12 data points; got ${parsed.time.length}.`);
      setLc({ ...parsed, label: `${parsed.label}: ${file.name}` });
    } catch (e: any) {
      setLoadErr(e.message || String(e));
    }
  };

  const loadDemo = (which: "pspl" | "flare") => {
    setLoadErr(null); setResult(null); setSel(null);
    setLc(which === "pspl" ? makeDemoPspl() : makeDemoFlare());
  };

  // TIC+sector fetch (parallels the transit tab's MAST mode).
  const fetchFromTic = async (ticId: number, sector: number | null) => {
    setLoadErr(null); setResult(null); setSel(null);
    try {
      const r = await fetchLightcurveByTic(ticId, sector);
      const bits = [
        `TIC ${r.tic_id}`,
        `S${String(r.sector).padStart(3, "0")}`,
        r.provider,
        `${r.n_cadences} pts`,
      ].filter(Boolean).join(" · ");
      setLc({
        time: r.time,
        flux: r.flux,
        flux_err: r.flux_err,
        label: `MAST fetch — ${bits}`,
      });
      // Piggyback on lastAutoload so the ExoFOP exporter picks up TIC/sector/provider.
      setLastAutoload({
        time: r.time, flux: r.flux, flux_err: r.flux_err,
        tic_id: r.tic_id, sector: r.sector,
        provider: r.provider, filename: r.filename, n_cadences: r.n_cadences,
        // These four have no meaning for a TIC-keyed fetch; supply neutral values
        // that satisfy the LcByCoordsResponse shape without misrepresenting the source.
        resolved_ra: 0, resolved_dec: 0, separation_arcsec: 0, tmag: null,
      });
    } catch (e: any) {
      setLoadErr(e.message || String(e));
    }
  };

  // FFI cutout + Gaia overlay (auto-fetched when target coords + sector known).
  const [ffiData, setFfiData] = useState<FfiCutoutResponse | null>(null);
  const [ffiBusy, setFfiBusy] = useState(false);
  const [ffiErr, setFfiErr] = useState<string | null>(null);

  // Gaia baseline + joint fit state (Harris+2026 workflow).
  const [gaiaLc, setGaiaLc] = useState<GaiaLightcurve | null>(null);
  const [gaiaBusy, setGaiaBusy] = useState(false);
  const [gaiaErr, setGaiaErr] = useState<string | null>(null);
  const [nearbyAlerts, setNearbyAlerts] = useState<GaiaAlertEntry[] | null>(null);
  const [nearbyBusy, setNearbyBusy] = useState(false);
  const [jointResult, setJointResult] = useState<JointFitResponse | null>(null);
  const [jointBusy, setJointBusy] = useState(false);
  const [jointErr, setJointErr] = useState<string | null>(null);

  const [autoloadBusy, setAutoloadBusy] = useState(false);
  // Kept around after the LC loads so the ExoFOP exporter can name the
  // package (TIC + sector + provider) even if the prefill was dismissed.
  const [lastAutoload, setLastAutoload] = useState<LcByCoordsResponse | null>(null);
  const autoloadFromCoords = async () => {
    if (!prefill) return;
    setAutoloadBusy(true); setLoadErr(null); setResult(null); setSel(null);
    try {
      const r = await fetchLightcurveByCoords({
        ra: prefill.ra,
        dec: prefill.dec,
        sector: prefill.sector,
      });
      const providerBits = [
        `TIC ${r.tic_id}`,
        `S${String(r.sector).padStart(3, "0")}`,
        r.provider,
        `${r.n_cadences} pts`,
        `Δ ${r.separation_arcsec.toFixed(1)}"`,
      ].filter(Boolean).join(" · ");
      setLc({
        time: r.time,
        flux: r.flux,
        flux_err: r.flux_err,
        label: `MAST autoload (${prefill.event_id}) — ${providerBits}`,
      });
      setLastAutoload(r);
    } catch (e: any) {
      setLoadErr(e.message || String(e));
    } finally {
      setAutoloadBusy(false);
    }
  };

  // Drag-to-select window on the main plot.
  const pxFromEvent = (e: React.PointerEvent) => {
    const rect = svgRef.current!.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * W;
    return Math.max(PAD.l, Math.min(W - PAD.r, x));
  };
  const onDown = (e: React.PointerEvent) => {
    if (!geomMain) return;
    const t = geomMain.tOf(pxFromEvent(e));
    setSel({ a: t, b: t });
    setDragging(true);
    setResult(null); setFitErr(null);
    (e.target as Element).setPointerCapture?.(e.pointerId);
  };
  const onMove = (e: React.PointerEvent) => {
    if (!dragging || !sel || !geomMain) return;
    setSel({ ...sel, b: geomMain.tOf(pxFromEvent(e)) });
  };
  const onUp = () => setDragging(false);

  const selWindow = sel
    ? { t_start: Math.min(sel.a, sel.b), t_end: Math.max(sel.a, sel.b) }
    : null;

  // Auto-fetch FFI cutout when we have coords + sector.
  const ffiFetchKey = `${lastAutoload?.resolved_ra ?? prefill?.ra ?? ""}` +
    `|${lastAutoload?.resolved_dec ?? prefill?.dec ?? ""}` +
    `|${lastAutoload?.sector ?? prefill?.sector ?? ""}` +
    `|${lastAutoload?.tic_id ?? ""}`;
  useEffect(() => {
    const ra = lastAutoload?.resolved_ra ?? prefill?.ra;
    const dec = lastAutoload?.resolved_dec ?? prefill?.dec;
    const sector = lastAutoload?.sector ?? prefill?.sector ?? null;
    const tic_id = lastAutoload?.tic_id ?? null;
    if (ra == null || dec == null) return;
    setFfiBusy(true); setFfiErr(null); setFfiData(null);
    let cancelled = false;
    fetchMicrolensingFfi({ ra, dec, sector, tic_id })
      .then((r) => { if (!cancelled) setFfiData(r); })
      .catch((e) => { if (!cancelled) setFfiErr(e.message || String(e)); })
      .finally(() => { if (!cancelled) setFfiBusy(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ffiFetchKey]);

  // ---- Gaia handlers ----
  const loadGaiaByAlertId = async (alertId: string) => {
    setGaiaBusy(true); setGaiaErr(null); setJointResult(null);
    try {
      const g = await fetchGaiaAlertLightcurve(alertId);
      setGaiaLc(g);
    } catch (e: any) {
      setGaiaErr(e.message || String(e));
    } finally {
      setGaiaBusy(false);
    }
  };
  const searchGaiaNearby = async () => {
    const ra = prefill?.ra ?? lastAutoload?.resolved_ra;
    const dec = prefill?.dec ?? lastAutoload?.resolved_dec;
    if (ra == null || dec == null) {
      setGaiaErr("No target coordinates known — load a light curve via TIC+sector or the Coverage handoff first.");
      return;
    }
    setNearbyBusy(true); setGaiaErr(null);
    try {
      const r = await searchGaiaAlertsNear(ra, dec, 90.0, true);
      setNearbyAlerts(r.alerts);
      if (r.alerts.length === 0) setGaiaErr(`No microlensing Gaia alerts within 90″ of RA=${ra.toFixed(4)}, Dec=${dec.toFixed(4)}.`);
    } catch (e: any) {
      setGaiaErr(e.message || String(e));
    } finally {
      setNearbyBusy(false);
    }
  };

  const runJointFit = async () => {
    if (!lc || !gaiaLc || !selWindow) return;
    setJointBusy(true); setJointErr(null); setJointResult(null);
    const t0_guess = 0.5 * (selWindow.t_start + selWindow.t_end);
    try {
      const r = await fitMicrolensingJoint({
        tess_time: lc.time, tess_flux: lc.flux, tess_flux_err: lc.flux_err,
        gaia_time_jd: gaiaLc.time_jd, gaia_mag: gaiaLc.mag, gaia_mag_err: gaiaLc.mag_err,
        window: selWindow, t0_guess,
      });
      setJointResult(r);
    } catch (e: any) {
      setJointErr(e.message || String(e));
    } finally {
      setJointBusy(false);
    }
  };

  const runFit = async () => {
    if (!lc || !selWindow) return;
    if (selWindow.t_end - selWindow.t_start < (geomMain?.tMax! - geomMain?.tMin!) * 0.02) {
      setFitErr("Selection too narrow — drag a wider window across the excursion.");
      return;
    }
    setFitting(true); setFitErr(null); setResult(null);
    const t0_guess = 0.5 * (selWindow.t_start + selWindow.t_end);
    try {
      const r = await fitMicrolensing({
        time: lc.time, flux: lc.flux, flux_err: lc.flux_err,
        window: selWindow, t0_guess,
      });
      setResult(r);
    } catch (e: any) {
      setFitErr(e.message || String(e));
    } finally {
      setFitting(false);
    }
  };

  return (
    <section className="space-y-5">
      {prefill && (
        <PrefillBanner
          prefill={prefill}
          onDismiss={onDismissPrefill}
          lcLoaded={!!lc}
          onAutoload={autoloadFromCoords}
          autoloadBusy={autoloadBusy}
        />
      )}

      <DataLoader
        onFile={onFile}
        onDemo={loadDemo}
        onFetchTic={fetchFromTic}
        loadErr={loadErr}
        lcLabel={lc?.label}
        lcPoints={lc?.time.length ?? 0}
      />

      {(ffiData || ffiBusy || ffiErr) && (
        <FfiCutoutSection ffiData={ffiData} busy={ffiBusy} err={ffiErr} />
      )}

      <GaiaLoader
        gaiaLc={gaiaLc}
        gaiaBusy={gaiaBusy}
        gaiaErr={gaiaErr}
        nearbyAlerts={nearbyAlerts}
        nearbyBusy={nearbyBusy}
        canSearchNearby={
          prefill?.ra != null || lastAutoload?.resolved_ra != null
        }
        onFetchAlert={loadGaiaByAlertId}
        onSearchNearby={searchGaiaNearby}
        onClear={() => { setGaiaLc(null); setNearbyAlerts(null); setJointResult(null); setJointErr(null); }}
      />

      {lc && geomMain && (
        <div className="rounded-lg border border-slate-200 bg-white p-4 space-y-3">
          <div className="flex items-baseline justify-between flex-wrap gap-2">
            <h3 className="font-semibold text-slate-800">Flag the positive excursion</h3>
            <p className="text-xs text-slate-500">
              Drag across the bright peak. The window and its midpoint (as{" "}
              <code>t0_guess</code>) get sent to the fit.
            </p>
          </div>

          <svg
            ref={svgRef}
            viewBox={`0 0 ${W} ${H_MAIN}`}
            className="w-full border rounded bg-slate-50 touch-none select-none cursor-crosshair"
            onPointerDown={onDown}
            onPointerMove={onMove}
            onPointerUp={onUp}
            onPointerLeave={onUp}
          >
            <Axes geom={geomMain} height={H_MAIN} />
            {/* Data */}
            <DataDots
              t={lc.time}
              f={lc.flux}
              geom={geomMain}
              tStart={selWindow?.t_start}
              tEnd={selWindow?.t_end}
            />
            {/* Selection band */}
            {selWindow && (
              <rect
                x={geomMain.xOf(selWindow.t_start)}
                width={Math.max(0, geomMain.xOf(selWindow.t_end) - geomMain.xOf(selWindow.t_start))}
                y={PAD.t}
                height={H_MAIN - PAD.t - PAD.b}
                fill="#3b82f6"
                opacity={0.12}
              />
            )}
            {/* Model overlays (only after fit) */}
            {result && visible.pspl && (
              <ModelPolyline
                data={denseCurve(
                  result.window.t_start,
                  result.window.t_end,
                  (tt) =>
                    pspFluxLocal(
                      tt,
                      result.models.pspl.params.t0,
                      result.models.pspl.params.tE,
                      result.models.pspl.params.u0,
                      result.models.pspl.params.f_s,
                      result.models.pspl.params.f_b
                    ) * result.window.baseline_flux
                )}
                geom={geomMain}
                color={_MODEL_COLORS.pspl}
              />
            )}
            {result && visible.flare && (
              <ModelPolyline
                data={denseCurve(
                  result.window.t_start,
                  result.window.t_end,
                  (tt) =>
                    flareFluxLocal(
                      tt,
                      result.models.flare.params.t_peak,
                      result.models.flare.params.amplitude,
                      result.models.flare.params.fwhm
                    ) * result.window.baseline_flux
                )}
                geom={geomMain}
                color={_MODEL_COLORS.flare}
              />
            )}
            {result && visible.null && (
              <line
                x1={geomMain.xOf(result.window.t_start)}
                x2={geomMain.xOf(result.window.t_end)}
                y1={geomMain.yOf(result.models.null.params.baseline * result.window.baseline_flux)}
                y2={geomMain.yOf(result.models.null.params.baseline * result.window.baseline_flux)}
                stroke={_MODEL_COLORS.null}
                strokeWidth={1.5}
                strokeDasharray="6 4"
              />
            )}
          </svg>

          <div className="flex flex-wrap items-center gap-3">
            <button
              onClick={runFit}
              disabled={!selWindow || fitting}
              className="px-4 py-1.5 text-sm font-semibold bg-blue-600 text-white rounded hover:bg-blue-700 disabled:bg-slate-300 min-w-[10rem]"
            >
              {fitting
                ? <CyclingLoader messages={MICROLENSING_FIT_MSGS} className="text-xs" />
                : "Fit PSPL / flare / null"}
            </button>
            {gaiaLc && (
              <button
                onClick={runJointFit}
                disabled={!selWindow || jointBusy}
                className="px-4 py-1.5 text-sm font-semibold bg-fuchsia-600 text-white rounded hover:bg-fuchsia-700 disabled:bg-slate-300 min-w-[11rem]"
                title="Joint TESS + Gaia PSPL fit (shared t0/tE/u0, per-band blending). Harris et al. 2026."
              >
                {jointBusy
                  ? <CyclingLoader messages={MICROLENSING_JOINT_MSGS} className="text-xs" />
                  : "Fit joint (TESS + Gaia)"}
              </button>
            )}
            {selWindow && (
              <span className="text-xs text-slate-600 font-mono">
                window: {selWindow.t_start.toFixed(3)} → {selWindow.t_end.toFixed(3)}{" "}
                (Δt = {(selWindow.t_end - selWindow.t_start).toFixed(3)})
              </span>
            )}
            {(sel || result) && (
              <button
                className="text-xs text-slate-500 hover:underline"
                onClick={() => { setSel(null); setResult(null); setFitErr(null); }}
              >
                clear selection
              </button>
            )}
          </div>

          {fitErr && (
            <p className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-2">
              {fitErr}
            </p>
          )}
          {jointErr && (
            <p className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-2">
              Joint fit: {jointErr}
            </p>
          )}
          {jointResult && <JointResultsPanel jr={jointResult} />}

          {result && (
            <ResultsPanel
              result={result}
              visible={visible}
              setVisible={setVisible}
              residualModel={residualModel}
              setResidualModel={setResidualModel}
              svgRef={svgRef}
              ffiPng={ffiData?.image ?? null}
              exportMetadata={{
                event_id: prefill?.event_id ?? null,
                tic_id: lastAutoload?.tic_id ?? null,
                ra: prefill?.ra ?? lastAutoload?.resolved_ra ?? null,
                dec: prefill?.dec ?? lastAutoload?.resolved_dec ?? null,
                sector: lastAutoload?.sector ?? prefill?.sector ?? null,
                provider: lastAutoload?.provider ?? null,
              }}
            />
          )}
        </div>
      )}
    </section>
  );
}

// ============================================================================
// Prefill banner — surfaces the target handed off from the Coverage table
// ============================================================================
