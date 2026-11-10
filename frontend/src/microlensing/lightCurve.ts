import type { LC } from "./types";

/** Parse a CSV with a header line time,flux,flux_err (order-flexible). */
export function parseCsv(text: string): LC {
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
export function parseJson(text: string): LC {
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
export function pspl(t: number, t0: number, tE: number, u0: number): number {
  const tau = (t - t0) / tE;
  const u = Math.sqrt(u0 * u0 + tau * tau);
  return (u * u + 2) / (u * Math.sqrt(u * u + 4));
}

/** Deterministic Box-Muller Gaussian noise. */
export function gauss(rng: () => number): number {
  const u = Math.max(rng(), 1e-12);
  const v = rng();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

export function mulberry32(seed: number) {
  let a = seed >>> 0;
  return function () {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t ^= t + Math.imul(t ^ (t >>> 7), 61 | t);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function makeDemoPspl(seed = 1): LC {
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

export function makeDemoFlare(seed = 2): LC {
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
