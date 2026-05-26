import { useMemo, useRef, useState } from "react";
import { analyzeManualDip, ManualDip } from "./api";
import { VettingResult } from "./types";

const W = 760;
const H = 220;
const PAD = { l: 48, r: 12, t: 12, b: 28 };

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

  const geom = useMemo(() => {
    if (!lc || !lc.t.length) return null;
    const tMin = Math.min(...lc.t);
    const tMax = Math.max(...lc.t);
    const fMin = Math.min(...lc.f);
    const fMax = Math.max(...lc.f);
    const fPad = (fMax - fMin) * 0.08 || 0.001;
    const xOf = (t: number) =>
      PAD.l + ((t - tMin) / (tMax - tMin || 1)) * (W - PAD.l - PAD.r);
    const yOf = (f: number) =>
      PAD.t + (1 - (f - (fMin - fPad)) / (fMax + fPad - (fMin - fPad))) * (H - PAD.t - PAD.b);
    const tOf = (px: number) =>
      tMin + ((px - PAD.l) / (W - PAD.l - PAD.r)) * (tMax - tMin);
    return { tMin, tMax, fMin, fMax, xOf, yOf, tOf };
  }, [lc]);

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
    return lc.t.map((t, i) => `${geom.xOf(t).toFixed(1)},${geom.yOf(lc.f[i]).toFixed(1)}`).join(" ");
  }, [lc, geom]);

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
      <p className="text-xs text-slate-500 mb-3">
        Drag across a suspected shallow dip the automatic detector skipped.
        Release, then click <strong>Analyze selection</strong>.
      </p>

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
