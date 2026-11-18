/** Small SVG scatter/line chart in the v2 palette (see DESIGN.md, Charts). */
type Num = number | null | undefined;

export interface Series {
  x: Num[];
  y: Num[];
  kind: "dots" | "line";
  /** Tailwind text-colour class; the SVG uses currentColor. */
  color: string;
  label: string;
  size?: number;
}

const W = 640, H = 220, P = { l: 52, r: 12, t: 10, b: 34 };

function extent(vals: Num[]): [number, number] | null {
  let lo = Infinity, hi = -Infinity;
  for (const v of vals) if (typeof v === "number" && Number.isFinite(v)) { if (v < lo) lo = v; if (v > hi) hi = v; }
  return Number.isFinite(lo) ? [lo, hi] : null;
}

function ticks(lo: number, hi: number, n = 5) {
  const step = (hi - lo) / (n - 1 || 1);
  return Array.from({ length: n }, (_, i) => lo + i * step);
}

const fmt = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 1 ? v.toFixed(2) : v.toPrecision(3));

export function MiniChart({ series, xLabel, yLabel, title, logX = false }: {
  series: Series[]; xLabel: string; yLabel: string; title: string; logX?: boolean;
}) {
  const tx = (v: number) => (logX ? Math.log10(v) : v);
  const usableX = (v: Num): v is number => typeof v === "number" && Number.isFinite(v) && (!logX || v > 0);
  const xs = extent(series.flatMap((s) => s.x.filter(usableX).map(tx)));
  const ys = extent(series.flatMap((s) => s.y));
  if (!xs || !ys) return <p className="text-sm text-muted">No data to plot.</p>;
  const yPad = (ys[1] - ys[0]) * 0.08 || 1e-3;
  const [y0, y1] = [ys[0] - yPad, ys[1] + yPad];
  const X = (v: number) => P.l + ((tx(v) - xs[0]) / (xs[1] - xs[0] || 1)) * (W - P.l - P.r);
  const Y = (v: number) => H - P.b - ((v - y0) / (y1 - y0)) * (H - P.t - P.b);
  return (
    <figure className="w-full">
      <figcaption className="text-sm font-semibold text-heading mb-1">{title}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" role="img" aria-label={title}>
        <g className="text-ink-300">
          <line x1={P.l} y1={H - P.b} x2={W - P.r} y2={H - P.b} stroke="currentColor" />
          <line x1={P.l} y1={P.t} x2={P.l} y2={H - P.b} stroke="currentColor" />
        </g>
        <g className="text-muted" fontSize="11" fill="currentColor">
          {ticks(xs[0], xs[1]).map((v, i) => {
            const raw = logX ? 10 ** v : v;
            return <text key={i} x={X(raw)} y={H - P.b + 14} textAnchor="middle">{fmt(raw)}</text>;
          })}
          {ticks(y0, y1).map((v, i) => (
            <text key={i} x={P.l - 6} y={Y(v) + 4} textAnchor="end">{fmt(v)}</text>
          ))}
          <text x={(W + P.l) / 2} y={H - 4} textAnchor="middle">{xLabel}</text>
          <text x={12} y={(H - P.b) / 2} textAnchor="middle" transform={`rotate(-90 12 ${(H - P.b) / 2})`}>{yLabel}</text>
        </g>
        {series.map((s, si) => {
          const pts: [number, number][] = [];
          s.x.forEach((xv, i) => {
            const yv = s.y[i];
            if (typeof xv === "number" && typeof yv === "number" && Number.isFinite(xv) && Number.isFinite(yv) && (!logX || xv > 0))
              pts.push([X(xv), Y(yv)]);
          });
          return (
            <g key={si} className={s.color}>
              {s.kind === "line"
                ? <polyline points={pts.map((p) => p.join(",")).join(" ")} fill="none" stroke="currentColor" strokeWidth={s.size ?? 1.6} />
                : pts.map((p, i) => <circle key={i} cx={p[0]} cy={p[1]} r={s.size ?? 1.3} fill="currentColor" opacity={0.55} />)}
            </g>
          );
        })}
      </svg>
      <div className="flex gap-4 text-xs text-muted mt-1">
        {series.map((s) => (
          <span key={s.label} className={`inline-flex items-center gap-1 ${s.color}`}>
            <span className="inline-block w-3 h-1.5 rounded bg-current" />
            <span className="text-muted">{s.label}</span>
          </span>
        ))}
      </div>
    </figure>
  );
}
