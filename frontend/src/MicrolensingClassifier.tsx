import { useEffect, useMemo, useRef, useState } from "react";
import {
  fetchLightcurveByCoords,
  fitMicrolensing,
  type LcByCoordsResponse,
  type MicrolensingFitResponse,
  type MicrolensingModelFit,
} from "./api";
import { ShareToImgbbButton } from "./ShareButton";
import { buildExofopPackage, svgToPng } from "./microlensingExport";

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
        loadErr={loadErr}
        lcLabel={lc?.label}
        lcPoints={lc?.time.length ?? 0}
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
              className="px-4 py-1.5 text-sm font-semibold bg-blue-600 text-white rounded hover:bg-blue-700 disabled:bg-slate-300"
            >
              {fitting ? "Fitting…" : "Fit PSPL / flare / null"}
            </button>
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

          {result && (
            <ResultsPanel
              result={result}
              visible={visible}
              setVisible={setVisible}
              residualModel={residualModel}
              setResidualModel={setResidualModel}
              svgRef={svgRef}
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
              className="px-3 py-1 text-xs font-semibold bg-blue-600 text-white rounded hover:bg-blue-700 disabled:bg-slate-300"
              title={`Resolve RA=${prefill.ra.toFixed(4)}, Dec=${prefill.dec.toFixed(4)} → nearest TIC and download the light curve from MAST`}
            >
              {autoloadBusy
                ? "Fetching from MAST…"
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
  onFile, onDemo, loadErr, lcLabel, lcPoints,
}: {
  onFile: (f: File) => void;
  onDemo: (which: "pspl" | "flare") => void;
  loadErr: string | null;
  lcLabel?: string;
  lcPoints: number;
}) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <h3 className="font-semibold text-slate-800 mb-2">1. Load a light curve</h3>
      <p className="text-xs text-slate-600 mb-3">
        Upload a JSON <code>{"{time, flux, flux_err}"}</code> file or a CSV with{" "}
        <code>time,flux,flux_err</code> columns. Or start with a synthetic demo
        to see the pipeline end-to-end.
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
  svgRef, exportMetadata,
}: {
  result: MicrolensingFitResponse;
  visible: Record<ModelKey, boolean>;
  setVisible: (v: Record<ModelKey, boolean>) => void;
  residualModel: ModelKey;
  setResidualModel: (m: ModelKey) => void;
  svgRef: React.RefObject<SVGSVGElement>;
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
  result, svgRef, exportMetadata,
}: {
  result: MicrolensingFitResponse;
  svgRef: React.RefObject<SVGSVGElement>;
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

      {/* ExoFOP-style ZIP */}
      <button
        onClick={downloadZip}
        disabled={zipBusy}
        className="px-2 py-1 rounded bg-emerald-600 hover:bg-emerald-700 text-white font-medium disabled:bg-slate-300"
        title="Download a ZIP with the fit summary, windowed light curve, full JSON, and plot PNG — ready to attach to an ExoFOP note"
      >
        {zipBusy ? "packaging…" : "⬇ Download ExoFOP package"}
      </button>

      {renderErr && <span className="text-red-700">plot render: {renderErr}</span>}
      {zipErr && <span className="text-red-700">export: {zipErr}</span>}
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
