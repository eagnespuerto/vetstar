import { useMemo, useRef, useState } from "react";
import { analyzeManualDip, ManualDip } from "./api";
import { VettingResult } from "./types";

const W = 760;
const H = 220;
const PAD = { l: 48, r: 12, t: 12, b: 28 };

/**
 * O(n) rolling-median flatten in a windowDays window. Same idea as the
 * server-side helper but in JS, kept local so the SVG stays snappy when the
 * user toggles. Uses a windowed copy + nth-element-style median (sort the
 * window — simplest, plenty fast for typical TESS LC sizes ≤ a few thousand).
 */
function rollingMedianDetrend(t: number[], f: number[], windowDays = 1.0): number[] {
  const n = t.length;
  if (n < 8) return f.slice();
  const out = new Array<number>(n);
  const half = windowDays / 2;
  let lo = 0;
  let hi = 0;
  const buf: number[] = [];
  for (let i = 0; i < n; i++) {
    const tLo = t[i] - half;
    const tHi = t[i] + half;
    while (lo < n && t[lo] < tLo) lo++;
    while (hi < n && t[hi] <= tHi) hi++;
    buf.length = 0;
    for (let j = lo; j < hi; j++) buf.push(f[j]);
    buf.sort((a, b) => a - b);
    const m = buf.length;
    out[i] = m === 0 ? 1 : m % 2 === 1 ? buf[(m - 1) >> 1] : 0.5 * (buf[m / 2 - 1] + buf[m / 2]);
  }
  let medGlobal = 0;
  const flat = new Array<number>(n);
  for (let i = 0; i < n; i++) {
    flat[i] = out[i] !== 0 ? f[i] / out[i] : f[i];
  }
  const sorted = flat.slice().sort((a, b) => a - b);
  medGlobal = sorted.length % 2 === 1
    ? sorted[(sorted.length - 1) >> 1]
    : 0.5 * (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]);
  if (!isFinite(medGlobal) || medGlobal === 0) return flat;
  return flat.map((x) => x / medGlobal);
}

/** Bin (t,f) into equal-width time bins of binMinutes. Returns the bin centres + means. */
function timeBin(t: number[], f: number[], binMinutes: number): { t: number[]; f: number[] } {
  if (!binMinutes || binMinutes <= 0 || t.length === 0) return { t: [], f: [] };
  const binDays = binMinutes / 1440;
  const tMin = t[0];
  const bins = new Map<number, { sumT: number; sumF: number; n: number }>();
  for (let i = 0; i < t.length; i++) {
    const tv = t[i];
    const fv = f[i];
    if (!isFinite(tv) || !isFinite(fv)) continue;
    const k = Math.floor((tv - tMin) / binDays);
    let b = bins.get(k);
    if (!b) {
      b = { sumT: 0, sumF: 0, n: 0 };
      bins.set(k, b);
    }
    b.sumT += tv;
    b.sumF += fv;
    b.n += 1;
  }
  const keys = [...bins.keys()].sort((a, b) => a - b);
  return {
    t: keys.map((k) => bins.get(k)!.sumT / bins.get(k)!.n),
    f: keys.map((k) => bins.get(k)!.sumF / bins.get(k)!.n),
  };
}

/**
 * Drag across the light curve to mark a candidate dip the automatic
 * detector may have skipped. The backend characterises depth, duration,
 * U/V shape and (when centroid moments are cached) an on-target test.
 */
export default function ManualDipSelector({ result }: { result: VettingResult }) {
  const lc = result.lightcurve;
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [sel, setSel] = useState<{ a: number; b: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [dip, setDip] = useState<ManualDip | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // View toggles — apply purely client-side; the analysis backend still
  // runs on the raw flux when the user clicks Analyze selection.
  const [viewDetrend, setViewDetrend] = useState<boolean>(true);
  const [viewBinMinutes, setViewBinMinutes] = useState<number | null>(null);

  // Memoise the displayed flux so toggle clicks stay snappy.
  const displayed = useMemo(() => {
    if (!lc || !lc.t.length) return null;
    const t = lc.t as number[];
    const fRaw = lc.f as number[];
    const f = viewDetrend ? rollingMedianDetrend(t, fRaw, 1.0) : fRaw;
    return { t, f };
  }, [lc, viewDetrend]);

  const binned = useMemo(() => {
    if (!displayed || !viewBinMinutes) return null;
    return timeBin(displayed.t, displayed.f, viewBinMinutes);
  }, [displayed, viewBinMinutes]);

  const geom = useMemo(() => {
    if (!displayed || !displayed.t.length) return null;
    const t = displayed.t;
    const f = displayed.f;
    const tMin = Math.min(...t);
    const tMax = Math.max(...t);
    const fMin = Math.min(...f);
    const fMax = Math.max(...f);
    const fPad = (fMax - fMin) * 0.08 || 0.001;
    const xOf = (tt: number) =>
      PAD.l + ((tt - tMin) / (tMax - tMin || 1)) * (W - PAD.l - PAD.r);
    const yOf = (ff: number) =>
      PAD.t + (1 - (ff - (fMin - fPad)) / (fMax + fPad - (fMin - fPad))) * (H - PAD.t - PAD.b);
    const tOf = (px: number) =>
      tMin + ((px - PAD.l) / (W - PAD.l - PAD.r)) * (tMax - tMin);
    return { tMin, tMax, fMin, fMax, xOf, yOf, tOf };
  }, [displayed]);

  if (!lc || !lc.t.length || !geom) {
    return (
      <section className="bg-white rounded-lg shadow p-5">
        <h3 className="font-bold mb-2">🔍 Manual tiny-dip selector</h3>
        <p className="text-sm text-slate-500">
          No light-curve samples were returned for this target, so manual
          selection isn’t available.
        </p>
      </section>
    );
  }

  const pxFromEvent = (e: React.PointerEvent) => {
    const rect = svgRef.current!.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * W;
    return Math.max(PAD.l, Math.min(W - PAD.r, x));
  };

  const onDown = (e: React.PointerEvent) => {
    const x = pxFromEvent(e);
    setSel({ a: x, b: x });
    setDragging(true);
    setDip(null);
    setErr(null);
    (e.target as Element).setPointerCapture?.(e.pointerId);
  };
  const onMove = (e: React.PointerEvent) => {
    if (!dragging || !sel) return;
    setSel({ ...sel, b: pxFromEvent(e) });
  };
  const onUp = () => setDragging(false);

  const points = useMemo(() => {
    if (!displayed) return "";
    return displayed.t
      .map((tt, i) => `${geom.xOf(tt).toFixed(1)},${geom.yOf(displayed.f[i]).toFixed(1)}`)
      .join(" ");
  }, [displayed, geom]);

  const selWindow = sel
    ? {
        t0: geom.tOf(Math.min(sel.a, sel.b)),
        t1: geom.tOf(Math.max(sel.a, sel.b)),
        x0: Math.min(sel.a, sel.b),
        x1: Math.max(sel.a, sel.b),
      }
    : null;

  const runAnalysis = async () => {
    if (!selWindow || selWindow.x1 - selWindow.x0 < 4) {
      setErr("Drag a wider window across the dip first.");
      return;
    }
    setLoading(true);
    setErr(null);
    try {
      const d = await analyzeManualDip({
        tic_id: result.star.tic_id ?? undefined,
        sector: result.star.sector ?? undefined,
        t_start: selWindow.t0,
        t_end: selWindow.t1,
        crowdsap: result.star.crowdsap ?? undefined,
      });
      setDip(d);
    } catch (e: any) {
      setErr(e.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="bg-white rounded-lg shadow p-5">
      <h3 className="font-bold mb-1">🔍 Manual tiny-dip selector</h3>
      <p className="text-xs text-slate-500 mb-2">
        Drag across a suspected shallow dip the automatic detector skipped.
        Release, then click <strong>Analyze selection</strong>.
      </p>

      <div
        className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-600 mb-2"
        aria-label="View controls (purely visual — analysis still uses raw flux)"
      >
        <label className="flex items-center gap-1 cursor-pointer">
          <input
            type="checkbox"
            checked={viewDetrend}
            onChange={(e) => setViewDetrend(e.target.checked)}
            className="h-3.5 w-3.5"
          />
          <span>Detrend view</span>
        </label>
        <span className="text-slate-300">|</span>
        <span className="text-slate-500">Bin:</span>
        {([null, 10, 30] as const).map((m) => {
          const active = viewBinMinutes === m;
          return (
            <button
              key={String(m)}
              type="button"
              onClick={() => setViewBinMinutes(m)}
              className={
                "px-2 py-0.5 rounded border text-xs " +
                (active
                  ? "bg-blue-600 border-blue-600 text-white"
                  : "bg-white border-slate-300 text-slate-700 hover:bg-slate-100")
              }
            >
              {m === null ? "off" : `${m} min`}
            </button>
          );
        })}
        <span className="text-slate-400 italic ml-2">
          View only — analysis runs on raw cadences.
        </span>
      </div>

      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className="w-full border rounded bg-slate-50 touch-none select-none cursor-crosshair"
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerLeave={onUp}
      >
        {/* baseline at f = 1 */}
        <line
          x1={PAD.l}
          x2={W - PAD.r}
          y1={geom.yOf(1)}
          y2={geom.yOf(1)}
          stroke="#cbd5e1"
          strokeDasharray="3 3"
        />
        {/* selection band */}
        {selWindow && (
          <rect
            x={selWindow.x0}
            width={Math.max(0, selWindow.x1 - selWindow.x0)}
            y={PAD.t}
            height={H - PAD.t - PAD.b}
            fill="#3b82f6"
            opacity={0.15}
          />
        )}
        <polyline points={points} fill="none" stroke="#0f172a" strokeWidth={0.7} opacity={0.7} />
        {/* Binned overlay */}
        {binned && binned.t.map((tt, i) => (
          <circle
            key={i}
            cx={geom.xOf(tt)}
            cy={geom.yOf(binned.f[i])}
            r={2.2}
            fill="#dc2626"
            opacity={0.9}
          />
        ))}
        {/* axes labels */}
        <text x={PAD.l} y={H - 8} fontSize="10" fill="#64748b">
          {geom.tMin.toFixed(2)}
        </text>
        <text x={W - PAD.r} y={H - 8} fontSize="10" fill="#64748b" textAnchor="end">
          {geom.tMax.toFixed(2)} (time)
        </text>
      </svg>

      <div className="flex items-center gap-3 mt-3">
        <button
          onClick={runAnalysis}
          disabled={loading || !selWindow}
          className="text-sm px-3 py-1.5 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:bg-slate-300"
        >
          {loading ? "Analyzing…" : "Analyze selection"}
        </button>
        {selWindow && (
          <span className="text-xs text-slate-500 font-mono">
            {selWindow.t0.toFixed(3)} → {selWindow.t1.toFixed(3)} d
          </span>
        )}
        {sel && (
          <button
            onClick={() => {
              setSel(null);
              setDip(null);
              setErr(null);
            }}
            className="text-xs text-slate-500 hover:underline"
          >
            clear
          </button>
        )}
      </div>

      {err && <p className="text-sm text-red-700 mt-2">{err}</p>}

      {dip && (
        <div className="mt-4 grid grid-cols-2 md:grid-cols-3 gap-x-4 gap-y-1 text-sm">
          <Stat label="Depth" value={`${dip.depth_pct.toFixed(3)} %`} />
          <Stat label="Duration" value={`${dip.duration_hr.toFixed(2)} h`} />
          <Stat label="Points in window" value={String(dip.n_points)} />
          {dip.shape?.available && (
            <>
              <Stat label="Shape" value={dip.shape.shape_class} />
              <Stat label="T14" value={`${(dip.shape.t14_hours ?? 0).toFixed(2)} h`} />
              <Stat label="T23 / T14" value={(dip.shape.t23_over_t14 ?? 0).toFixed(2)} />
            </>
          )}
          {dip.centroid?.available && (
            <>
              <Stat
                label="Centroid col shift"
                value={`${dip.centroid.shift_col_sigma.toFixed(2)} σ`}
              />
              <Stat
                label="Centroid row shift"
                value={`${dip.centroid.shift_row_sigma.toFixed(2)} σ`}
              />
              <Stat
                label="On target?"
                value={dip.centroid.on_target ? "yes" : "no — possible blend"}
              />
            </>
          )}
        </div>
      )}
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-xs text-slate-500">{label}</span>
      <span className="font-mono">{value}</span>
    </div>
  );
}
