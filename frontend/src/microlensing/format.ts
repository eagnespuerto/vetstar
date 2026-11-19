

export function fmt(v: number | null | undefined, nd: number): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  if (abs !== 0 && (abs < 1e-3 || abs >= 1e6)) return v.toExponential(Math.max(2, nd - 1));
  return v.toFixed(nd);
}

export function fmtErr(v: number | null | undefined, err: number | null | undefined, nd: number): string {
  const base = fmt(v, nd);
  if (err == null || !Number.isFinite(err)) return base;
  return `${base} ± ${fmt(err, Math.min(nd, 4))}`;
}
