export const API_BASE = import.meta.env.VITE_API_URL || "";

export interface DetectParams {
  threshold: number;   // 0.95..0.999
  minSnr: number;      // 1..20
}

const DEFAULT_PARAMS: DetectParams = { threshold: 0.997, minSnr: 4.0 };

function qs(params: DetectParams = DEFAULT_PARAMS): string {
  return `?detect_threshold=${params.threshold}&detect_min_snr=${params.minSnr}`;
}

export async function analyze(file: File, params: DetectParams = DEFAULT_PARAMS) {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(`${API_BASE}/api/analyze${qs(params)}`, { method: "POST", body: fd });
  if (!r.ok) throw new Error(`Analyze failed (${r.status}): ${await r.text()}`);
  return r.json();
}

export async function downloadReport(file: File, params: DetectParams = DEFAULT_PARAMS): Promise<Blob> {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(`${API_BASE}/api/report${qs(params)}`, { method: "POST", body: fd });
  if (!r.ok) throw new Error(`Report failed (${r.status}): ${await r.text()}`);
  return r.blob();
}

export interface SectorInfo {
  sector: number;
  providers: string[];
}

export async function mastSectors(ticId: number): Promise<SectorInfo[]> {
  const r = await fetch(`${API_BASE}/api/mast/sectors/${ticId}`);
  if (!r.ok) throw new Error(`Sector lookup failed: ${await r.text()}`);
  const data = await r.json();
  if (Array.isArray(data.sectors) && data.sectors.length > 0 && typeof data.sectors[0] === "number") {
    return (data.sectors as number[]).map((s) => ({ sector: s, providers: [] }));
  }
  return data.sectors as SectorInfo[];
}

export async function mastAnalyze(ticId: number, sector: number, params: DetectParams = DEFAULT_PARAMS) {
  const r = await fetch(`${API_BASE}/api/mast/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      tic_id: ticId, sector,
      detect_threshold: params.threshold, detect_min_snr: params.minSnr,
    }),
  });
  if (!r.ok) throw new Error(`MAST analyze failed (${r.status}): ${await r.text()}`);
  return r.json();
}

export async function fetchHabitability(
  ticId: number,
  overrides: Partial<{
    radius_earth: number;
    semi_major_axis_au: number;
    orbital_period_d: number;
    stellar_teff: number;
    stellar_radius_sun: number;
    stellar_mass_sun: number;
    R_companion_Rjup: number;
    n_sectors_with_detections: number;
    n_sectors_observed: number;
    vetting_verdict: Record<string, any>;
  }> = {}
) {
  const r = await fetch(`${API_BASE}/api/habitability`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tic_id: ticId, ...overrides }),
  });
  if (!r.ok) throw new Error(`Habitability fetch failed (${r.status}): ${await r.text()}`);
  return r.json();
}

export async function fetchMultisector(ticId: number, params: DetectParams = { threshold: 0.997, minSnr: 4.0 }) {
  const r = await fetch(`${API_BASE}/api/mast/multisector`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      tic_id: ticId,
      detect_threshold: params.threshold,
      detect_min_snr: params.minSnr,
    }),
  });
  if (!r.ok) throw new Error(`Multi-sector fetch failed (${r.status}): ${await r.text()}`);
  return r.json();
}

export async function mastReport(ticId: number, sector: number, params: DetectParams = DEFAULT_PARAMS): Promise<Blob> {
  const r = await fetch(`${API_BASE}/api/mast/report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      tic_id: ticId, sector,
      detect_threshold: params.threshold, detect_min_snr: params.minSnr,
    }),
  });
  if (!r.ok) throw new Error(`MAST report failed: ${await r.text()}`);
  return r.blob();
}
// ─── ADD THESE to frontend/src/api.ts ────────────────────────────────────────
//
// 1. Add ExominerResult interface (export it so ExoMinerPanel.tsx can import it)
// 2. Add runExominer() async function
//
// Paste both blocks into api.ts (after the existing exports).
// ─────────────────────────────────────────────────────────────────────────────

// ---------- Types -------------------------------------------------------

export interface ExominerScalars {
  period_d: number;
  duration_h: number;
  depth_ppm: number;
  transit_count: number;
  odd_even_sigma: number;
  secondary_depth_sigma: number;
  centroid_shift_sigma: number;
  scatter_mad: number;
  crowdsap: number | null;
  sg_detrend_window_h: number;
}

export interface ExominerArrays {
  global_view: number[];
  local_view: number[];
  secondary_view: number[];
  odd_transit_view: number[];
  even_transit_view: number[];
  centroid_global_view: number[] | null;
  centroid_local_view: number[] | null;
}

export interface ExominerResult {
  scalars: ExominerScalars;
  arrays: ExominerArrays;
  plots: {
    global_view?: string;
    local_view?: string;
    secondary_view?: string;
    odd_even_view?: string;
    centroid_global_view?: string;
    centroid_local_view?: string;
    diagnostic_sigmas?: string;
  };
}

// ---------- API call ----------------------------------------------------

export interface ExominerRequest {
  /** TIC ID — used by the backend to identify the cached parsed file */
  tic_id?: number;
  sector?: number;
  /** BLS best-fit period in days */
  period: number;
  /** BLS epoch (t0) in BTJD */
  t0: number;
  /** BLS transit duration in days */
  duration: number;
  /** CROWDSAP from FITS header (dilution correction) */
  crowdsap?: number;
}

export async function runExominer(req: ExominerRequest): Promise<ExominerResult> {
  const r = await fetch(`${API_BASE}/api/exominer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`ExoMiner failed (${r.status}): ${text}`);
  }
  return r.json();
}

// ---------- Manual tiny-dip selector -----------------------------------

export interface ManualDip {
  t_start: number;
  t_end: number;
  depth: number;
  depth_pct: number;
  duration_hr: number;
  n_points: number;
  baseline: number;
  shape?: Record<string, any>;
  centroid?: Record<string, any>;
}

export interface ManualDipRequest {
  tic_id?: number;
  sector?: number;
  t_start: number;
  t_end: number;
  crowdsap?: number;
}

export async function analyzeManualDip(req: ManualDipRequest): Promise<ManualDip> {
  const r = await fetch(`${API_BASE}/api/manual_dip`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!r.ok) throw new Error(`Manual dip analysis failed (${r.status}): ${await r.text()}`);
  return r.json();
}

// ---------- Predicted Observables for Exoplanets (POE) ------------------

export interface ObservablesQuery {
  tic_id?: number;
  stellar_teff?: number;
  stellar_radius_sun?: number;
  stellar_mass_sun?: number;
  luminosity_lsun?: number;
  distance_pc?: number;
  orbital_period_d?: number;
  semi_major_axis_au?: number;
  rp_rjup?: number;
  rp_earth?: number;
  transit_depth_frac?: number;
  mp_mjup?: number;
  inclination_deg?: number;
  eccentricity?: number;
  vetting_verdict?: Record<string, any>;
}

export async function fetchObservables(query: ObservablesQuery) {
  const r = await fetch(`${API_BASE}/api/observables`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(query),
  });
  if (!r.ok) throw new Error(`Observables fetch failed (${r.status}): ${await r.text()}`);
  return r.json();
}

// ---------- Radial velocity -> absolute mass ----------------------------

export interface RVQuery {
  tic_id?: number;               // archive-first lookup
  orbital_period_d?: number;
  stellar_mass_sun?: number;
  inclination_deg?: number;
  eccentricity?: number;
  k_ms?: number;                 // direct semi-amplitude, OR ...
  rv_values_ms?: number[];       // ... a time series to reduce via min/max
  rv_reduce_method?: string;
}

export async function fetchRadialVelocity(query: RVQuery) {
  const r = await fetch(`${API_BASE}/api/rv`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(query),
  });
  if (!r.ok) throw new Error(`RV fetch failed (${r.status}): ${await r.text()}`);
  return r.json();
}
