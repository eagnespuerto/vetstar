import { useMemo } from "react";
import type { MicrolensingFitResponse } from "../api";
import type { ModelKey } from "./types";
import { pspl } from "./lightCurve";

export const W = 780;

export const H_MAIN = 260;

export const H_RESID = 130;

export const PAD = { l: 56, r: 14, t: 12, b: 30 };

export type Geom = {
  xOf: (t: number) => number;
  yOf: (f: number) => number;
  tOf: (px: number) => number;
  fOf: (py: number) => number;
  tMin: number; tMax: number; fMin: number; fMax: number;
};

export function makeGeom(t: number[], f: number[], height: number): Geom | null {
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

export const _FLARE_DENSE = 200;

export const _MODEL_COLORS: Record<string, string> = {
  pspl: "#2563eb",   // blue
  flare: "#f97316",  // orange
  null: "#64748b",   // slate
};

export function pspFluxLocal(t: number, t0: number, tE: number, u0: number, fs: number, fb: number) {
  return fs * pspl(t, t0, tE, u0) + fb;
}

export function flareFluxLocal(t: number, t_peak: number, amp: number, fwhm: number) {
  const tn = (t - t_peak) / Math.max(Math.abs(fwhm), 1e-9);
  let tpl = 0;
  if (tn >= -1 && tn <= 0)
    tpl = 1 + 1.941 * tn - 0.175 * tn ** 2 - 2.246 * tn ** 3 - 1.125 * tn ** 4;
  else if (tn > 0)
    tpl = 0.689 * Math.exp(-1.6 * tn) + 0.303 * Math.exp(-0.2783 * tn);
  return 1.0 + amp * tpl;
}

export function denseCurve(
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

export function Axes({ geom, height }: { geom: Geom; height: number }) {
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

export function DataDots({
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

export function ModelPolyline({
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

export function ResidualPlot({
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
