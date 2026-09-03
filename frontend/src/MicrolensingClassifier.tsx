import { useEffect, useMemo, useRef, useState } from "react";
import {
  fetchLightcurveByCoords,
  fetchLightcurveByTic,
  fitMicrolensing,
  mastSectors,
  type LcByCoordsResponse,
  type MicrolensingFitResponse,
  type MicrolensingModelFit,
  type SectorInfo,
} from "./api";
import { ShareToImgbbButton } from "./ShareButton";
import ExofopBulkPanel, { type ExofopImage } from "./ExofopBulkPanel";
import { buildExofopPackage, svgToPng } from "./microlensingExport";
import {
  fetchGaiaAlertLightcurve,
  fetchMicrolensingFfi,
  fetchMicrolensingReport,
  fitMicrolensingJoint,
  searchGaiaAlertsNear,
  type ExofopRow,
  type FfiCutoutResponse,
  type GaiaAlertEntry,
  type GaiaLightcurve,
  type JointFitResponse,
} from "./api";
import {
  CyclingLoader,
  COORD_LC_MSGS,
  FFI_CUTOUT_MSGS,
  GAIA_FETCH_MSGS,
  GAIA_SEARCH_MSGS,
  MICROLENSING_FIT_MSGS,
  MICROLENSING_JOINT_MSGS,
  PDF_MSGS,
  TIC_LC_MSGS,
} from "./Loaders";

// Prefill payload from the Coverage table's "Analyze in Module A" action.
// The LC itself isn't auto-fetched — that would need a full MAST coord→TIC
// resolver — but we pre-select the window around t0 and surface the target
// metadata so the user knows what they're looking at.
export interface ClassifierPrefill {
  source: "coverage";
  event_id: string;
  ra: number;
  dec: number;
  t0: number;
  tE: number;
  sector: number | null;
  camera: number | null;
  ccd: number | null;
  observable: boolean;
}

// ============================================================================
// Data types + loaders
// ============================================================================

type LC = { time: number[]; flux: number[]; flux_err: number[]; label: string };

/** Parse a CSV with a header line time,flux,flux_err (order-flexible). */
function parseCsv(text: string): LC {
  const rows = text.trim().split(/\r?\n/).filter(Boolean);
  if (rows.length < 2) throw new Error("CSV needs a header line + data rows.");
  const header = rows[0].split(",").map((h) => h.trim().toLowerCase());
  const iT = header.indexOf("time");
  const iF = header.indexOf("flux");
  const iE = header.findIndex((h) => h === "flux_err" || h === "flux_error");
  if (iT < 0 || iF < 0) throw new Error("CSV must have `time` and `flux` columns.");
  const time: number[] = [], flux: number[] = [], flux_err: number[] = [];
  for (let r = 1; r < rows.length; r++) {
    const cols = rows[r].split(",");
    const t = parseFloat(cols[iT]);
    const f = parseFloat(cols[iF]);
    const e = iE >= 0 ? parseFloat(cols[iE]) : NaN;
    if (Number.isFinite(t) && Number.isFinite(f)) {
      time.push(t); flux.push(f);
      flux_err.push(Number.isFinite(e) && e > 0 ? e : Math.max(Math.abs(f) * 1e-4, 1e-6));
    }
  }
  return { time, flux, flux_err, label: "uploaded CSV" };
}

/** Parse a JSON blob with {time, flux, flux_err}. */
function parseJson(text: string): LC {
  const obj = JSON.parse(text);
  const time = obj.time as number[];
  const flux = obj.flux as number[];
  let flux_err = obj.flux_err as number[] | undefined;
  if (!Array.isArray(time) || !Array.isArray(flux))
    throw new Error("JSON must contain arrays `time` and `flux`.");
  if (!Array.isArray(flux_err) || flux_err.length !== time.length) {
    const sigma = Math.max(1e-6, 1e-4);
    flux_err = flux.map(() => sigma);
  }
  return { time, flux, flux_err, label: "uploaded JSON" };
}

/** Closed-form PSPL magnification, mirroring backend. */
function pspl(t: number, t0: number, tE: number, u0: number): number {
  const tau = (t - t0) / tE;
  const u = Math.sqrt(u0 * u0 + tau * tau);
  return (u * u + 2) / (u * Math.sqrt(u * u + 4));
}

/** Deterministic Box-Muller Gaussian noise. */
function gauss(rng: () => number): number {
  const u = Math.max(rng(), 1e-12);
  const v = rng();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}
function mulberry32(seed: number) {
  let a = seed >>> 0;
  return function () {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t ^= t + Math.imul(t ^ (t >>> 7), 61 | t);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function makeDemoPspl(seed = 1): LC {
  const time: number[] = [], flux: number[] = [], flux_err: number[] = [];
  const t0 = 1220.0, tE = 5.0, u0 = 0.15, sigma = 3e-4;
  const rng = mulberry32(seed);
  for (let i = 0; i < 400; i++) {
    const t = 1200 + (40 * i) / 399;
    const f = pspl(t, t0, tE, u0) + sigma * gauss(rng);
    time.push(t); flux.push(f); flux_err.push(sigma);
  }
  return { time, flux, flux_err, label: `synthetic PSPL (t0=${t0}, tE=${tE}, u0=${u0})` };
}

function makeDemoFlare(seed = 2): LC {
  const time: number[] = [], flux: number[] = [], flux_err: number[] = [];
  const t_peak = 1201.5, amp = 0.02, fwhm = 0.4, sigma = 1.5e-4;
  const rng = mulberry32(seed);
  const rise = (tn: number) =>
    1 + 1.941 * tn - 0.175 * tn ** 2 - 2.246 * tn ** 3 - 1.125 * tn ** 4;
  const decay = (tn: number) => 0.689 * Math.exp(-1.6 * tn) + 0.303 * Math.exp(-0.2783 * tn);
  for (let i = 0; i < 300; i++) {
    const t = 1200 + (10 * i) / 299;
    const tn = (t - t_peak) / fwhm;
    let tpl = 0;
    if (tn >= -1 && tn <= 0) tpl = rise(tn);
    else if (tn > 0) tpl = decay(tn);
    const f = 1 + amp * tpl + sigma * gauss(rng);
    time.push(t); flux.push(f); flux_err.push(sigma);
  }
  return { time, flux, flux_err, label: `synthetic flare (t_peak=${t_peak}, amp=${amp}, fwhm=${fwhm})` };
}

// ============================================================================
// Plot geometry helpers
// ============================================================================

const W = 780;
const H_MAIN = 260;
const H_RESID = 130;
const PAD = { l: 56, r: 14, t: 12, b: 30 };

type Geom = {
  xOf: (t: number) => number;
  yOf: (f: number) => number;
  tOf: (px: number) => number;
  fOf: (py: number) => number;
  tMin: number; tMax: number; fMin: number; fMax: number;
};

function makeGeom(t: number[], f: number[], height: number): Geom | null {
  if (!t.length) return null;
  let tMin = Infinity, tMax = -Infinity, fMin = Infinity, fMax = -Infinity;
  for (let i = 0; i < t.length; i++) {
    if (Number.isFinite(t[i])) { if (t[i] < tMin) tMin = t[i]; if (t[i] > tMax) tMax = t[i]; }
    if (Number.isFinite(f[i])) { if (f[i] < fMin) fMin = f[i]; if (f[i] > fMax) fMax = f[i]; }
  }
  if (!Number.isFinite(tMin) || !Number.isFinite(fMin)) return null;
  const fPad = (fMax - fMin) * 0.1 || 0.001;
  const xOf = (tt: number) =>
    PAD.l + ((tt - tMin) / (tMax - tMin || 1)) * (W - PAD.l - PAD.r);
  const yOf = (ff: number) =>
    PAD.t + (1 - (ff - (fMin - fPad)) / (fMax + fPad - (fMin - fPad))) * (height - PAD.t - PAD.b);
  const tOf = (px: number) =>
    tMin + ((px - PAD.l) / (W - PAD.l - PAD.r)) * (tMax - tMin);
  const fOf = (py: number) =>
    (fMax + fPad) - ((py - PAD.t) / (height - PAD.t - PAD.b)) * (fMax + fPad - (fMin - fPad));
  return { xOf, yOf, tOf, fOf, tMin, tMax, fMin, fMax };
}

// ============================================================================
// Model overlays — evaluated on a dense grid for smooth curves
// ============================================================================

const _FLARE_DENSE = 200;
const _MODEL_COLORS: Record<string, string> = {
  pspl: "#2563eb",   // blue
  flare: "#f97316",  // orange
  null: "#64748b",   // slate
};

function pspFluxLocal(t: number, t0: number, tE: number, u0: number, fs: number, fb: number) {
  return fs * pspl(t, t0, tE, u0) + fb;
}
function flareFluxLocal(t: number, t_peak: number, amp: number, fwhm: number) {
  const tn = (t - t_peak) / Math.max(Math.abs(fwhm), 1e-9);
  let tpl = 0;
  if (tn >= -1 && tn <= 0)
    tpl = 1 + 1.941 * tn - 0.175 * tn ** 2 - 2.246 * tn ** 3 - 1.125 * tn ** 4;
  else if (tn > 0)
    tpl = 0.689 * Math.exp(-1.6 * tn) + 0.303 * Math.exp(-0.2783 * tn);
  return 1.0 + amp * tpl;
}

function denseCurve(
  tStart: number,
  tEnd: number,
  eval_: (t: number) => number,
  n = _FLARE_DENSE
): { t: number[]; f: number[] } {
  const t: number[] = [], f: number[] = [];
  for (let i = 0; i < n; i++) {
    const tt = tStart + ((tEnd - tStart) * i) / (n - 1);
    t.push(tt); f.push(eval_(tt));
  }
  return { t, f };
}

// ============================================================================
// The panel
// ============================================================================

type ModelKey = "pspl" | "flare" | "null";

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

function PrefillBanner({
  prefill, onDismiss, lcLoaded, onAutoload, autoloadBusy,
}: {
  prefill: ClassifierPrefill;
  onDismiss?: () => void;
  lcLoaded: boolean;
  onAutoload?: () => void;
  autoloadBusy?: boolean;
}) {
  // Encode a MAST portal search link for the RA/Dec so the user can pull the
  // FITS light curve manually without leaving the app.
  const mastUrl =
    `https://mast.stsci.edu/portal/Mashup/Clients/Mast/Portal.html?searchQuery=` +
    encodeURIComponent(`${prefill.ra} ${prefill.dec}`);
  return (
    <div className="rounded-lg border-l-4 border-blue-500 bg-blue-50 p-3 flex items-start justify-between gap-3">
      <div className="text-sm text-blue-900">
        <p className="font-semibold">
          Handed off from coverage table: <span className="font-mono">{prefill.event_id}</span>
        </p>
        <p className="text-xs mt-1 leading-relaxed">
          RA <span className="font-mono">{prefill.ra.toFixed(4)}</span>°, Dec{" "}
          <span className="font-mono">{prefill.dec.toFixed(4)}</span>° · t₀ ={" "}
          <span className="font-mono">{prefill.t0.toFixed(3)}</span> BTJD · tE ={" "}
          <span className="font-mono">{prefill.tE.toFixed(2)}</span> d
          {prefill.sector != null && (
            <>
              {" "}· TESS S{String(prefill.sector).padStart(3, "0")}
              {prefill.camera != null && `/cam${prefill.camera}/ccd${prefill.ccd}`}
            </>
          )}
        </p>
        <p className="text-xs mt-1">
          {lcLoaded
            ? "Selection window pre-set around t₀ ± 2·tE. Fine-tune by dragging, then fit."
            : "Pull the TESS light curve for this target automatically, or upload one manually below."}
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {onAutoload && (
            <button
              onClick={onAutoload}
              disabled={autoloadBusy}
              className="px-3 py-1 text-xs font-semibold bg-blue-600 text-white rounded hover:bg-blue-700 disabled:bg-slate-300 min-w-[11rem]"
              title={`Resolve RA=${prefill.ra.toFixed(4)}, Dec=${prefill.dec.toFixed(4)} → nearest TIC and download the light curve from MAST`}
            >
              {autoloadBusy
                ? <CyclingLoader messages={COORD_LC_MSGS} className="text-[10px]" />
                : (lcLoaded ? "Re-fetch from MAST" : "Fetch TESS light curve")}
            </button>
          )}
          <a
            href={mastUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-blue-700 underline"
          >
            Open on MAST portal →
          </a>
        </div>
      </div>
      {onDismiss && (
        <button
          onClick={onDismiss}
          className="text-xs text-blue-700 hover:underline shrink-0"
          title="Clear the handoff"
        >
          dismiss
        </button>
      )}
    </div>
  );
}

// ============================================================================
// Data loader UI
// ============================================================================

function DataLoader({
  onFile, onDemo, onFetchTic, loadErr, lcLabel, lcPoints,
}: {
  onFile: (f: File) => void;
  onDemo: (which: "pspl" | "flare") => void;
  onFetchTic: (ticId: number, sector: number | null) => Promise<void>;
  loadErr: string | null;
  lcLabel?: string;
  lcPoints: number;
}) {
  const [ticInput, setTicInput] = useState("");
  const [sectorInput, setSectorInput] = useState("");
  const [sectors, setSectors] = useState<SectorInfo[] | null>(null);
  const [sectorsBusy, setSectorsBusy] = useState(false);
  const [sectorErr, setSectorErr] = useState<string | null>(null);
  const [fetchBusy, setFetchBusy] = useState(false);

  const lookupSectors = async () => {
    const tic = parseInt(ticInput);
    if (!tic) return;
    setSectorsBusy(true); setSectorErr(null);
    try {
      const s = await mastSectors(tic);
      setSectors(s);
      if (s.length && !sectorInput) setSectorInput(String(s[s.length - 1].sector));
    } catch (e: any) {
      setSectorErr(e.message || String(e));
      setSectors(null);
    } finally {
      setSectorsBusy(false);
    }
  };

  const runFetch = async () => {
    const tic = parseInt(ticInput);
    if (!tic) return;
    const sec = sectorInput.trim() ? parseInt(sectorInput) : null;
    setFetchBusy(true);
    try {
      await onFetchTic(tic, sec);
    } finally {
      setFetchBusy(false);
    }
  };

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4 space-y-4">
      <div>
        <h3 className="font-semibold text-slate-800 mb-2">1. Load a light curve</h3>
        <p className="text-xs text-slate-600 mb-3">
          Three ways to get data into the classifier: <strong>upload</strong> a
          FITS-equivalent JSON/CSV file, <strong>fetch from MAST</strong> by
          TIC + sector (same UX as the Transit tab's MAST mode), or start with
          a <strong>synthetic demo</strong>.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <input
            type="file"
            accept=".json,.csv,.txt"
            onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])}
            className="text-sm"
          />
          <span className="text-slate-300">|</span>
          <button
            onClick={() => onDemo("pspl")}
            className="px-3 py-1 text-xs font-medium bg-slate-100 hover:bg-slate-200 rounded border border-slate-200"
          >
            Load PSPL demo
          </button>
          <button
            onClick={() => onDemo("flare")}
            className="px-3 py-1 text-xs font-medium bg-slate-100 hover:bg-slate-200 rounded border border-slate-200"
          >
            Load flare demo
          </button>
        </div>
      </div>

      {/* MAST fetch — TIC + sector */}
      <div className="border-t border-slate-100 pt-3">
        <p className="text-xs font-semibold text-slate-700 mb-2">
          or fetch from MAST by TIC + sector
        </p>
        <div className="grid sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto_auto] gap-2 items-end">
          <div>
            <label className="block text-[10px] text-slate-600 mb-0.5">TIC ID</label>
            <input
              type="number"
              placeholder="e.g. 261136679"
              value={ticInput}
              onChange={(e) => setTicInput(e.target.value)}
              className="w-full border rounded px-2 py-1 font-mono text-xs"
            />
          </div>
          <div>
            <label className="block text-[10px] text-slate-600 mb-0.5">
              Sector <span className="text-slate-400">(blank = newest available)</span>
            </label>
            <input
              type="number"
              placeholder="auto"
              value={sectorInput}
              onChange={(e) => setSectorInput(e.target.value)}
              className="w-full border rounded px-2 py-1 font-mono text-xs"
            />
          </div>
          <button
            onClick={lookupSectors}
            disabled={!ticInput || sectorsBusy}
            className="px-3 py-1 text-xs font-medium bg-slate-700 text-white rounded hover:bg-slate-800 disabled:bg-slate-300"
          >
            {sectorsBusy ? "…" : "List sectors"}
          </button>
          <button
            onClick={runFetch}
            disabled={!ticInput || fetchBusy}
            className="px-3 py-1 text-xs font-semibold bg-blue-600 text-white rounded hover:bg-blue-700 disabled:bg-slate-300 min-w-[9rem]"
          >
            {fetchBusy
              ? <CyclingLoader messages={TIC_LC_MSGS} className="text-[10px]" />
              : "Fetch light curve"}
          </button>
        </div>
        {sectorErr && (
          <p className="text-xs text-red-700 mt-2">{sectorErr}</p>
        )}
        {sectors && sectors.length === 0 && (
          <p className="text-xs text-slate-500 mt-2">No TESS sectors found for this TIC.</p>
        )}
        {sectors && sectors.length > 0 && (
          <div className="mt-2">
            <p className="text-[10px] text-slate-500 mb-1">
              Click a sector to pick it. SPOC (2-min) = grey; FFI-only (TESS-SPOC/QLP) = amber.
            </p>
            <div className="flex flex-wrap gap-1">
              {sectors.map((si) => {
                const selected = String(si.sector) === sectorInput;
                const hasSpoc = si.providers.includes("SPOC");
                return (
                  <button
                    key={si.sector}
                    onClick={() => setSectorInput(String(si.sector))}
                    title={si.providers.length ? `Providers: ${si.providers.join(", ")}` : ""}
                    className={
                      "px-2 py-0.5 rounded font-mono text-[10px] " +
                      (selected
                        ? "bg-blue-600 text-white"
                        : hasSpoc
                        ? "bg-slate-100 hover:bg-slate-200"
                        : "bg-amber-50 hover:bg-amber-100 text-amber-900")
                    }
                  >
                    S{String(si.sector).padStart(3, "0")}
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {loadErr && (
        <p className="text-sm text-red-700 mt-2">{loadErr}</p>
      )}
      {lcLabel && (
        <p className="text-xs text-slate-500 mt-2 font-mono">
          Loaded: {lcLabel} ({lcPoints} points)
        </p>
      )}
    </section>
  );
}

// ============================================================================
// Plot primitives
// ============================================================================

function Axes({ geom, height }: { geom: Geom; height: number }) {
  const yTicks = 4;
  const ticks: number[] = [];
  for (let i = 0; i <= yTicks; i++) {
    ticks.push(geom.fMin + (geom.fMax - geom.fMin) * (i / yTicks));
  }
  return (
    <g>
      <line
        x1={PAD.l} x2={W - PAD.r}
        y1={geom.yOf(1)} y2={geom.yOf(1)}
        stroke="#cbd5e1"
        strokeDasharray="3 3"
      />
      {ticks.map((v, i) => (
        <g key={i}>
          <text
            x={PAD.l - 6}
            y={geom.yOf(v) + 3}
            textAnchor="end"
            fontSize="9"
            fill="#64748b"
            fontFamily="monospace"
          >
            {v.toFixed(4)}
          </text>
          <line
            x1={PAD.l - 2} x2={PAD.l}
            y1={geom.yOf(v)} y2={geom.yOf(v)}
            stroke="#94a3b8"
          />
        </g>
      ))}
      <text x={PAD.l} y={height - 8} fontSize="10" fill="#64748b" fontFamily="monospace">
        {geom.tMin.toFixed(2)}
      </text>
      <text
        x={W - PAD.r}
        y={height - 8}
        textAnchor="end"
        fontSize="10"
        fill="#64748b"
        fontFamily="monospace"
      >
        {geom.tMax.toFixed(2)} (time)
      </text>
    </g>
  );
}

function DataDots({
  t, f, geom, tStart, tEnd,
}: {
  t: number[]; f: number[]; geom: Geom;
  tStart?: number; tEnd?: number;
}) {
  const inSel = (tt: number) =>
    tStart != null && tEnd != null && tt >= tStart && tt <= tEnd;
  return (
    <g>
      {t.map((tt, i) => (
        <circle
          key={i}
          cx={geom.xOf(tt)}
          cy={geom.yOf(f[i])}
          r={inSel(tt) ? 1.4 : 1.0}
          fill={inSel(tt) ? "#0f172a" : "#94a3b8"}
          opacity={inSel(tt) ? 0.95 : 0.7}
        />
      ))}
    </g>
  );
}

function ModelPolyline({
  data, geom, color,
}: {
  data: { t: number[]; f: number[] };
  geom: Geom;
  color: string;
}) {
  const points = data.t
    .map((tt, i) => `${geom.xOf(tt).toFixed(1)},${geom.yOf(data.f[i]).toFixed(1)}`)
    .join(" ");
  return (
    <polyline points={points} fill="none" stroke={color} strokeWidth={1.8} opacity={0.95} />
  );
}

// ============================================================================
// Results panel
// ============================================================================

const VERDICT_STYLE: Record<string, string> = {
  microlensing: "bg-emerald-100 border-emerald-400 text-emerald-900",
  flare: "bg-amber-100 border-amber-400 text-amber-900",
  null: "bg-slate-100 border-slate-400 text-slate-800",
  ambiguous: "bg-sky-100 border-sky-400 text-sky-900",
};

function ResultsPanel({
  result, visible, setVisible, residualModel, setResidualModel,
  svgRef, ffiPng, exportMetadata,
}: {
  result: MicrolensingFitResponse;
  visible: Record<ModelKey, boolean>;
  setVisible: (v: Record<ModelKey, boolean>) => void;
  residualModel: ModelKey;
  setResidualModel: (m: ModelKey) => void;
  svgRef: React.RefObject<SVGSVGElement | null>;
  ffiPng: string | null;
  exportMetadata: {
    event_id: string | null;
    tic_id: number | null;
    ra: number | null;
    dec: number | null;
    sector: number | null;
    provider: string | null;
  };
}) {
  const pspl = result.models.pspl;
  const flare = result.models.flare;
  const nul = result.models.null;
  const sym = result.symmetry_score;

  const bics: Array<{ key: ModelKey; label: string; bic: number | null }> = [
    { key: "pspl", label: "PSPL", bic: pspl.bic },
    { key: "flare", label: "Flare", bic: flare.bic },
    { key: "null", label: "Null", bic: nul.bic },
  ];
  const finiteBics = bics.filter((b) => b.bic != null && Number.isFinite(b.bic)) as Array<{ key: ModelKey; label: string; bic: number }>;
  const bestBic = finiteBics.length ? Math.min(...finiteBics.map((b) => b.bic)) : 0;
  const worstDelta = finiteBics.length ? Math.max(...finiteBics.map((b) => b.bic - bestBic)) : 1;

  return (
    <div className="space-y-4 border-t border-slate-200 pt-4">
      {/* Export / share toolbar */}
      <ExportToolbar
        result={result}
        svgRef={svgRef}
        ffiPng={ffiPng}
        exportMetadata={exportMetadata}
      />

      {/* Verdict badge */}
      <section
        className={`rounded-lg border-2 p-4 ${VERDICT_STYLE[result.verdict] || VERDICT_STYLE.ambiguous}`}
      >
        <p className="text-[10px] uppercase tracking-wide opacity-70">Verdict</p>
        <h4 className="text-xl font-bold mt-0.5">{result.verdict.toUpperCase()}</h4>
        <p className="text-sm mt-1">
          Confidence: <span className="font-mono">{(result.confidence * 100).toFixed(0)}%</span>{" "}
          &nbsp;·&nbsp; window: {result.window.n_points} points, baseline{" "}
          <span className="font-mono">{result.window.baseline_flux.toExponential(3)}</span>
        </p>
      </section>

      {/* ΔBIC bar */}
      <section>
        <h5 className="text-sm font-semibold text-slate-700 mb-1">
          Bayesian Information Criterion (lower is better)
        </h5>
        <div className="space-y-1.5">
          {bics.map(({ key, label, bic }) => {
            const delta = bic != null && Number.isFinite(bic) ? bic - bestBic : null;
            const barPct = delta != null && worstDelta > 0 ? (delta / worstDelta) * 100 : 0;
            const isBest = delta === 0;
            return (
              <div key={key} className="flex items-center gap-2 text-xs">
                <label className="w-14 flex items-center gap-1">
                  <input
                    type="checkbox"
                    checked={visible[key]}
                    onChange={(e) => setVisible({ ...visible, [key]: e.target.checked })}
                    className="h-3 w-3"
                  />
                  <span className="font-medium">{label}</span>
                </label>
                <span
                  className="inline-block w-3 h-3 rounded-sm"
                  style={{ background: _MODEL_COLORS[key] }}
                />
                <div className="flex-1 h-4 bg-slate-100 rounded overflow-hidden">
                  <div
                    className={`h-full ${isBest ? "bg-emerald-500" : "bg-slate-400"}`}
                    style={{ width: `${Math.min(100, Math.max(2, barPct))}%` }}
                  />
                </div>
                <span className="font-mono w-32 text-right">
                  {bic != null && Number.isFinite(bic)
                    ? `BIC=${bic.toFixed(1)} (Δ=${(bic - bestBic).toFixed(1)})`
                    : "fit failed"}
                </span>
              </div>
            );
          })}
        </div>
        <p className="text-[10px] text-slate-500 mt-1.5">
          Δnull−PSPL = {fmtDelta(result.delta_bic.null_minus_pspl)} ·
          Δflare−PSPL = {fmtDelta(result.delta_bic.flare_minus_pspl)}.
          Rule: PSPL strong if Δnull−PSPL &gt; 10; ambiguous if |Δflare−PSPL| &lt; 6.
        </p>
      </section>

      {/* PSPL params + errors */}
      <section>
        <h5 className="text-sm font-semibold text-slate-700 mb-1">
          PSPL best-fit parameters
        </h5>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-x-4 gap-y-1 text-xs">
          {Object.entries(pspl.params).map(([k, v]) => (
            <ParamStat
              key={k}
              label={k}
              value={v}
              err={pspl.param_err[k] as number | null}
            />
          ))}
        </div>
        <p className="text-[10px] text-slate-500 mt-1">
          Errors from the least-squares Jacobian (linearized 1σ, may be optimistic).
        </p>
      </section>

      {/* Observable parameters — physical quantities derived from PSPL alone */}
      {result.observables && <ObservablesPanel obs={result.observables} />}

      {/* Predicted planet parameters — fiducial-lens scales + sensitivity floor */}
      {result.planet_predictions && (
        <PlanetPredictionsPanel pp={result.planet_predictions} />
      )}

      {/* ExoFOP planet parameters — microlensing-derivable subset, mirrors
          the transit tab's ExoFOP TOI parameters block in shape + naming. */}
      {result.exofop_rows && result.exofop_rows.length > 0 && (
        <ExofopPlanetParamsPanel rows={result.exofop_rows} />
      )}

      {/* Symmetry + notes */}
      <section className="grid md:grid-cols-2 gap-3 text-xs">
        <div className="rounded border border-slate-200 bg-slate-50 p-2">
          <span className="text-slate-500">Symmetry score (PSPL residuals, folded about t₀)</span>
          <div className="text-lg font-mono">
            {sym == null ? "—" : sym.toFixed(3)}
          </div>
          <p className="text-[10px] text-slate-500 mt-1">
            +1 = perfectly symmetric wings (PSPL-like). ~0 = uncorrelated (good fit
            or noise-dominated). &lt;0 = anti-symmetric.
          </p>
        </div>
        <div className="rounded border border-amber-200 bg-amber-50 p-2">
          <span className="text-slate-500 font-medium">Diagnostic notes</span>
          <ul className="mt-1 space-y-0.5">
            {result.notes.map((n, i) => (
              <li key={i} className="text-slate-700">• {n}</li>
            ))}
          </ul>
        </div>
      </section>

      {/* Residuals sub-plot */}
      <ResidualPlot
        result={result}
        model={residualModel}
        setModel={setResidualModel}
      />
    </div>
  );
}

function fmtDelta(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toFixed(1);
}

function ParamStat({ label, value, err }: { label: string; value: number; err: number | null }) {
  return (
    <div className="flex flex-col">
      <span className="text-slate-500 font-mono">{label}</span>
      <span className="font-mono text-sm">
        {value.toPrecision(5)}
        {err != null && Number.isFinite(err) && (
          <span className="text-slate-400"> ± {err.toPrecision(3)}</span>
        )}
      </span>
    </div>
  );
}

function ExportToolbar({
  result, svgRef, ffiPng, exportMetadata,
}: {
  result: MicrolensingFitResponse;
  svgRef: React.RefObject<SVGSVGElement | null>;
  ffiPng: string | null;
  exportMetadata: {
    event_id: string | null;
    tic_id: number | null;
    ra: number | null;
    dec: number | null;
    sector: number | null;
    provider: string | null;
  };
}) {
  const [pngB64, setPngB64] = useState<string | null>(null);
  const [renderErr, setRenderErr] = useState<string | null>(null);
  const [rendering, setRendering] = useState(false);
  const [zipBusy, setZipBusy] = useState(false);
  const [zipErr, setZipErr] = useState<string | null>(null);
  const [pdfBusy, setPdfBusy] = useState(false);
  const [pdfErr, setPdfErr] = useState<string | null>(null);

  // Rasterise the current SVG on demand (lazily — first click of Share).
  const renderPng = async (): Promise<string | null> => {
    if (pngB64) return pngB64;
    if (!svgRef.current) {
      setRenderErr("Plot not ready yet — try again after the light curve renders.");
      return null;
    }
    setRendering(true); setRenderErr(null);
    try {
      const b64 = await svgToPng(svgRef.current, 2);
      setPngB64(b64);
      return b64;
    } catch (e: any) {
      setRenderErr(e.message || String(e));
      return null;
    } finally {
      setRendering(false);
    }
  };

  const downloadPdf = async () => {
    setPdfBusy(true); setPdfErr(null);
    try {
      let png: string | null = pngB64;
      if (!png && svgRef.current) {
        try { png = await svgToPng(svgRef.current, 2); setPngB64(png); }
        catch { png = null; }
      }
      const blob = await fetchMicrolensingReport(result, exportMetadata, png, ffiPng);
      const url = URL.createObjectURL(blob);
      const stem = ([
        exportMetadata.event_id,
        exportMetadata.tic_id != null ? `TIC${exportMetadata.tic_id}` : null,
        exportMetadata.sector != null ? `S${String(exportMetadata.sector).padStart(3, "0")}` : null,
      ].filter(Boolean).join("_") || "microlensing_event")
        .replace(/[^a-zA-Z0-9_-]/g, "_");
      const a = document.createElement("a");
      a.href = url; a.download = `${stem}_report.pdf`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      setPdfErr(e.message || String(e));
    } finally {
      setPdfBusy(false);
    }
  };

  const downloadZip = async () => {
    setZipBusy(true); setZipErr(null);
    try {
      // Try to attach the plot PNG if we can — helps ExoFOP notes readers
      // see the fit at a glance. Non-fatal if the render fails.
      let png: string | null = pngB64;
      if (!png && svgRef.current) {
        try { png = await svgToPng(svgRef.current, 2); setPngB64(png); }
        catch { png = null; }
      }
      const { blob, filename } = await buildExofopPackage(result, exportMetadata, png);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = filename;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      setZipErr(e.message || String(e));
    } finally {
      setZipBusy(false);
    }
  };

  const shareLabel = exportMetadata.event_id
    ? `microlensing ${exportMetadata.event_id}`
    : "microlensing fit";
  const shareTitle = [
    "vetstar_microlensing",
    exportMetadata.event_id,
    exportMetadata.tic_id != null ? `TIC${exportMetadata.tic_id}` : null,
    exportMetadata.sector != null ? `S${String(exportMetadata.sector).padStart(3, "0")}` : null,
  ].filter(Boolean).join("_");

  return (
    <section className="rounded border border-slate-200 bg-slate-50 p-3 flex flex-wrap items-center gap-3 text-xs">
      <span className="font-semibold text-slate-700">Export &amp; share:</span>

      {/* ImgBB share — rasterise SVG on first click, then hand off to
          ShareToImgbbButton once we have the base64 in state. */}
      {!pngB64 ? (
        <button
          onClick={renderPng}
          disabled={rendering}
          className="px-2 py-1 rounded bg-slate-100 hover:bg-purple-100 text-slate-700 hover:text-purple-700 flex items-center gap-1 transition"
          title="Rasterise the plot and upload to ImgBB for a shareable link"
        >
          {rendering ? "rendering…" : "📤 Share plot"}
        </button>
      ) : (
        <ShareToImgbbButton base64={pngB64} title={shareTitle} label={shareLabel} />
      )}

      {/* Vetting-report PDF */}
      <button
        onClick={downloadPdf}
        disabled={pdfBusy}
        className="px-2 py-1 rounded bg-sky-700 hover:bg-sky-800 text-white font-medium disabled:bg-slate-300 min-w-[9rem]"
        title="Generate a PDF vetting report — verdict, observables, planet predictions, ExoFOP rows, FFI + Gaia overlay, and the plot"
      >
        {pdfBusy
          ? <CyclingLoader messages={PDF_MSGS} className="text-[10px]" />
          : "📄 Download PDF report"}
      </button>

      {/* Analysis bundle — plot + LC data + full JSON + notes (not the same
          format as ExoFOP-TESS bulk upload; that's the separate panel below). */}
      <button
        onClick={downloadZip}
        disabled={zipBusy}
        className="px-2 py-1 rounded bg-emerald-600 hover:bg-emerald-700 text-white font-medium disabled:bg-slate-300 min-w-[9rem]"
        title="ZIP with summary.csv, lightcurve_windowed.csv, fit_full.json, notes.md, and the plot PNG — for local archiving and reproducibility"
      >
        {zipBusy ? "packaging…" : "⬇ Analysis bundle"}
      </button>

      {renderErr && <span className="text-red-700">plot render: {renderErr}</span>}
      {zipErr && <span className="text-red-700">export: {zipErr}</span>}
      {pdfErr && <span className="text-red-700">PDF: {pdfErr}</span>}

      {/* Real ExoFOP-TESS bulk-upload panel — same convention as the transit
          tab uses. Requires a TIC ID for the filename scheme. */}
      {exportMetadata.tic_id != null && (
        <div className="w-full mt-2">
          <ExofopBulkPanel
            ticId={exportMetadata.tic_id}
            sector={exportMetadata.sector ?? undefined}
            images={_buildExofopImages(pngB64, ffiPng)}
            title="⬇ Build ExoFOP-TESS bulk-upload ZIP (transit-tab convention)"
            caption={
              <p className="text-slate-500 mt-2">
                Bundles the microlensing fit plot + FFI/Gaia cutout as
                <code> TIC{`{id}`}O-xxYYYYMMDD.slug.png</code> inside a
                <code> xxYYYYMMDD-nnn.zip</code> with a matching
                <code> .txt</code> descriptor — the same
                <a
                  className="text-blue-600 hover:underline mx-1"
                  href="https://exofop.ipac.caltech.edu/tess/script_upload_help.php"
                  target="_blank" rel="noopener noreferrer"
                >
                  ExoFOP-TESS bulk file upload
                </a>
                convention the Transit tab uses. Initials + data tag are
                remembered between runs.
              </p>
            }
          />
        </div>
      )}
    </section>
  );
}

/** Assemble the ExofopBulkPanel image list — classifier plot first (if
 *  rasterised), FFI+Gaia cutout second (if fetched). Both are optional. */
function _buildExofopImages(plotPng: string | null, ffiPng: string | null): ExofopImage[] {
  const out: ExofopImage[] = [];
  if (plotPng) {
    out.push({ key: "microlensing-lc-fit", label: "Microlensing PSPL/flare/null overlay",
                b64: plotPng, code: "O" });
  }
  if (ffiPng) {
    out.push({ key: "ffi-gaia-cutout", label: "TESScut FFI + Gaia DR3 overlay",
                b64: ffiPng, code: "O" });
  }
  return out;
}

// ============================================================================
// Observables + planet-prediction panels
// ============================================================================

function ObservablesPanel({ obs }: { obs: NonNullable<MicrolensingFitResponse["observables"]> }) {
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

function PlanetPredictionsPanel({ pp }: { pp: NonNullable<MicrolensingFitResponse["planet_predictions"]> }) {
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

function FfiCutoutSection({
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

function GaiaLoader({
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

function JointResultsPanel({ jr }: { jr: JointFitResponse }) {
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

function fmt(v: number | null | undefined, nd: number): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  if (abs !== 0 && (abs < 1e-3 || abs >= 1e6)) return v.toExponential(Math.max(2, nd - 1));
  return v.toFixed(nd);
}
function fmtErr(v: number | null | undefined, err: number | null | undefined, nd: number): string {
  const base = fmt(v, nd);
  if (err == null || !Number.isFinite(err)) return base;
  return `${base} ± ${fmt(err, Math.min(nd, 4))}`;
}

function ExofopPlanetParamsPanel({ rows }: { rows: ExofopRow[] }) {
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

function ResidualPlot({
  result, model, setModel,
}: {
  result: MicrolensingFitResponse;
  model: ModelKey;
  setModel: (m: ModelKey) => void;
}) {
  const fit = result.models[model];
  const t = result.time_windowed;
  const f = result.flux_normalized;
  const residuals = useMemo(() => {
    if (!fit.model_flux) return null;
    return f.map((v, i) => v - fit.model_flux![i]);
  }, [fit.model_flux, f]);

  if (!residuals) {
    return (
      <section className="rounded border border-slate-200 bg-slate-50 p-3 text-xs text-slate-500">
        Residuals unavailable — the {model} fit did not converge.
      </section>
    );
  }

  const geom = makeGeom(t, residuals, H_RESID);
  if (!geom) return null;

  return (
    <section>
      <div className="flex items-center gap-2 text-xs mb-1">
        <span className="font-semibold text-slate-700">Residuals</span>
        <span className="text-slate-500">(data − model):</span>
        {(["pspl", "flare", "null"] as ModelKey[]).map((k) => (
          <button
            key={k}
            onClick={() => setModel(k)}
            className={`px-2 py-0.5 rounded border ${
              model === k
                ? "bg-slate-800 text-white border-slate-800"
                : "bg-white text-slate-600 border-slate-300 hover:bg-slate-100"
            }`}
          >
            {k}
          </button>
        ))}
      </div>
      <svg viewBox={`0 0 ${W} ${H_RESID}`} className="w-full border rounded bg-white">
        <Axes geom={geom} height={H_RESID} />
        <line
          x1={PAD.l}
          x2={W - PAD.r}
          y1={geom.yOf(0)}
          y2={geom.yOf(0)}
          stroke="#0f172a"
          strokeDasharray="2 4"
        />
        {t.map((tt, i) => (
          <circle
            key={i}
            cx={geom.xOf(tt)}
            cy={geom.yOf(residuals[i])}
            r={1.2}
            fill={_MODEL_COLORS[model]}
            opacity={0.85}
          />
        ))}
      </svg>
    </section>
  );
}
