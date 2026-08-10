export const API_BASE = import.meta.env.VITE_API_URL || "";

/** Direct download URL for the raw SPOC light-curve FITS of a (TIC, sector). */
export function fitsDownloadUrl(ticId: number, sector: number): string {
  return `${API_BASE}/api/mast/download/${ticId}/${sector}`;
}

export interface DetectParams {
  threshold: number;             // 0.91..0.9999
  minSnr: number;                // 0.5..32
  highVariability: boolean;      // toggle for sinusoid+harmonic detrend
  rotationPeriod: number | null; // optional manual rotation period (days)
  secondarySigma: number;        // 0.5..20, default 3
  oddEvenSigma: number;          // 0.5..20, default 3 — EB-flag threshold
  knownPeriod?: number | null;   // optional known period (days) — constrains BLS to ±2% around P, P/2, 2P
  // Plot-only knobs (don't affect detection). Backend re-renders the LC PNG with these.
  plotDetrend?: boolean;             // 1-day rolling-median flatten on the LC plot. Default true.
  plotBinMinutes?: number | null;    // bin overlay in minutes; null = no overlay. Default 30.
}

const DEFAULT_PARAMS: DetectParams = {
  threshold: 0.997,
  minSnr: 4.0,
  highVariability: false,
  rotationPeriod: null,
  secondarySigma: 3.0,
  oddEvenSigma: 3.0,
  knownPeriod: null,
  plotDetrend: true,
  plotBinMinutes: 30,
};

function hasKnownPeriod(p: DetectParams): p is DetectParams & { knownPeriod: number } {
  return (
    typeof p.knownPeriod === "number" &&
    Number.isFinite(p.knownPeriod) &&
    p.knownPeriod > 0
  );
}

function qs(params: DetectParams = DEFAULT_PARAMS): string {
  const base = `?detect_threshold=${params.threshold}&detect_min_snr=${params.minSnr}`;
  const tail =
    `&high_variability=${params.highVariability}` +
    (params.rotationPeriod !== null ? `&rotation_period_days=${params.rotationPeriod}` : "") +
    `&secondary_sigma=${params.secondarySigma}` +
    `&odd_even_sigma=${params.oddEvenSigma}` +
    (hasKnownPeriod(params) ? `&known_period_days=${params.knownPeriod}` : "");
  return base + tail;
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

function plotKwargs(p: DetectParams): Record<string, any> {
  // Only emit keys when set so older backends that ignore unknown fields
  // keep working unchanged.
  const out: Record<string, any> = {};
  if (typeof p.plotDetrend === "boolean") out.plot_detrend = p.plotDetrend;
  if (p.plotBinMinutes === null || typeof p.plotBinMinutes === "number") {
    out.plot_bin_minutes = p.plotBinMinutes;
  }
  return out;
}

export async function mastAnalyze(ticId: number, sector: number, params: DetectParams = DEFAULT_PARAMS) {
  const r = await fetch(`${API_BASE}/api/mast/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      tic_id: ticId, sector,
      detect_threshold: params.threshold,
      detect_min_snr: params.minSnr,
      high_variability: params.highVariability,
      rotation_period_days: params.rotationPeriod,
      secondary_sigma: params.secondarySigma,
      odd_even_sigma: params.oddEvenSigma,
      ...(hasKnownPeriod(params) ? { known_period_days: params.knownPeriod } : {}),
      ...plotKwargs(params),
    }),
  });
  if (!r.ok) throw new Error(`MAST analyze failed (${r.status}): ${await r.text()}`);
  return r.json();
}

// ---------- Progress-tracked variant of mastAnalyze ---------------------
//
// Calls /api/mast/analyze_async, then subscribes to the SSE stream and
// invokes ``onProgress(stage, percent, message)`` for each event. Resolves
// with the final result dict (same shape as mastAnalyze) or rejects with
// the backend error message.
//
// Falls back to a one-shot mastAnalyze() call if the kickoff POST fails
// with anything other than a successful 200 — keeps older backends working.
// ----------------------------------------------------------------------

export interface ProgressEvent {
  stage: string;
  percent: number;
  message: string;
}

export async function mastAnalyzeWithProgress(
  ticId: number,
  sector: number,
  params: DetectParams = DEFAULT_PARAMS,
  onProgress?: (e: ProgressEvent) => void,
): Promise<any> {
  const body = JSON.stringify({
    tic_id: ticId, sector,
    detect_threshold: params.threshold,
    detect_min_snr: params.minSnr,
    high_variability: params.highVariability,
    rotation_period_days: params.rotationPeriod,
    secondary_sigma: params.secondarySigma,
    odd_even_sigma: params.oddEvenSigma,
    ...(hasKnownPeriod(params) ? { known_period_days: params.knownPeriod } : {}),
    ...plotKwargs(params),
  });

  // Kick off the job. If the async endpoint is missing (older deployment),
  // fall back to the blocking endpoint so the user still gets a result.
  const kickoff = await fetch(`${API_BASE}/api/mast/analyze_async`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  });
  if (!kickoff.ok) {
    if (kickoff.status === 404) {
      return mastAnalyze(ticId, sector, params);
    }
    throw new Error(`MAST analyze kickoff failed (${kickoff.status}): ${await kickoff.text()}`);
  }
  const { job_id } = await kickoff.json();

  return new Promise<any>((resolve, reject) => {
    const url = `${API_BASE}/api/jobs/${job_id}/stream`;
    const es = new EventSource(url);
    es.onmessage = (msg) => {
      let data: any;
      try { data = JSON.parse(msg.data); } catch { return; }
      if (data.type === "progress") {
        onProgress?.({ stage: data.stage, percent: data.percent, message: data.message });
      } else if (data.type === "done") {
        onProgress?.({ stage: "done", percent: 100, message: "complete" });
        es.close();
        resolve(data.result);
      } else if (data.type === "error") {
        es.close();
        reject(new Error(data.message || "analysis failed"));
      }
    };
    es.onerror = () => {
      es.close();
      reject(new Error("Progress stream disconnected"));
    };
  });
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
    planet_mass_earth: number;
    use_archive_rv: boolean;
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

export async function fetchMultisector(
  ticId: number,
  params: DetectParams = { threshold: 0.997, minSnr: 4.0, highVariability: false, rotationPeriod: null, secondarySigma: 3.0, oddEvenSigma: 3.0, knownPeriod: null },
  sectors?: number[],
  maxObjects?: number,
) {
  const r = await fetch(`${API_BASE}/api/mast/multisector`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      tic_id: ticId,
      detect_threshold: params.threshold,
      detect_min_snr: params.minSnr,
      high_variability: params.highVariability,
      rotation_period_days: params.rotationPeriod,
      secondary_sigma: params.secondarySigma,
      odd_even_sigma: params.oddEvenSigma,
      ...(hasKnownPeriod(params) ? { known_period_days: params.knownPeriod } : {}),
      ...(sectors && sectors.length ? { sectors } : {}),
    }),
  });
  if (!r.ok) throw new Error(`Multi-sector fetch failed (${r.status}): ${await r.text()}`);
  return r.json();
}

export async function multisectorReport(
  ticId: number,
  params: DetectParams = { threshold: 0.997, minSnr: 4.0, highVariability: false, rotationPeriod: null, secondarySigma: 3.0, oddEvenSigma: 3.0, knownPeriod: null },
  sectors?: number[],
  maxObjects?: number,
): Promise<Blob> {
  const r = await fetch(`${API_BASE}/api/mast/multisector/report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      tic_id: ticId,
      detect_threshold: params.threshold,
      detect_min_snr: params.minSnr,
      high_variability: params.highVariability,
      rotation_period_days: params.rotationPeriod,
      secondary_sigma: params.secondarySigma,
      odd_even_sigma: params.oddEvenSigma,
      ...(hasKnownPeriod(params) ? { known_period_days: params.knownPeriod } : {}),
      ...(sectors && sectors.length ? { sectors } : {}),
    }),
  });
  if (!r.ok) throw new Error(`Multi-sector report failed (${r.status}): ${await r.text()}`);
  return r.blob();
}

export async function mastReport(ticId: number, sector: number, params: DetectParams = DEFAULT_PARAMS): Promise<Blob> {
  const r = await fetch(`${API_BASE}/api/mast/report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      tic_id: ticId, sector,
      detect_threshold: params.threshold,
      detect_min_snr: params.minSnr,
      high_variability: params.highVariability,
      rotation_period_days: params.rotationPeriod,
      secondary_sigma: params.secondarySigma,
      odd_even_sigma: params.oddEvenSigma,
      ...(hasKnownPeriod(params) ? { known_period_days: params.knownPeriod } : {}),
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

// ---------- FFI cutout --------------------------------------------------

export interface FfiCutoutResult {
  available: boolean;
  reason?: string;
  image?: string; // base64 PNG (no data-URI prefix)
  size_px?: number;
  n_frames?: number;
  sector?: number | null;
}

export interface FfiCutoutRequest {
  ra: number;
  dec: number;
  sector?: number;
  tic_id?: number;
  size_px?: number;
}

export async function runFfiCutout(req: FfiCutoutRequest): Promise<FfiCutoutResult> {
  const r = await fetch(`${API_BASE}/api/ffi_cutout`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`FFI cutout failed (${r.status}): ${text}`);
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
  mr_relation?: string;
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

// ----------------------------------------------------------------------------
// Microlensing (Module A)
// ----------------------------------------------------------------------------

export interface MicrolensingFitRequest {
  time: number[];
  flux: number[];
  flux_err: number[];
  window: { t_start: number; t_end: number };
  t0_guess: number;
}

export interface MicrolensingModelFit {
  params: Record<string, number>;
  param_err: Record<string, number | null>;
  chi2: number | null;
  chi2_red: number | null;
  bic: number | null;
  n_params: number;
  n_points: number;
  success: boolean;
  model_flux: number[] | null;
}

export interface MicrolensingObservables {
  t0_btjd: number;
  t0_btjd_err: number | null;
  t0_bjd: number;
  einstein_timescale_d: number;
  einstein_timescale_err_d: number | null;
  impact_parameter_u0: number;
  impact_parameter_err: number | null;
  peak_magnification: number;
  peak_magnification_observed: number;
  peak_brightening_mag: number | null;
  einstein_crossing_duration_d: number;
  magnification_fwhm_d: number | null;
  source_flux_fraction: number | null;
  blend_flux_fraction: number | null;
  f_s: number;
  f_b: number;
  mu_rel_mas_per_yr_fiducial: number | null;
  mu_rel_note: string;
}

export interface MicrolensingPlanetPredictions {
  assumption: string;
  fiducial_lens_mass_solar: number;
  fiducial_lens_distance_kpc: number;
  fiducial_source_distance_kpc: number;
  theta_E_mas_fiducial: number | null;
  einstein_radius_au_fiducial: number | null;
  v_rel_km_s_fiducial: number | null;
  closest_approach_au_fiducial: number | null;
  planet_sensitivity_note: string;
  planet_q_min_detectable: number | null;
  planet_mass_floor_m_earth_fiducial: number | null;
  planet_mass_floor_m_jupiter_fiducial: number | null;
}

export interface MicrolensingFitResponse {
  verdict: "microlensing" | "flare" | "null" | "ambiguous";
  confidence: number;
  window: { t_start: number; t_end: number; baseline_flux: number; n_points: number };
  time_windowed: number[];
  flux_normalized: number[];
  flux_err_normalized: number[];
  models: {
    pspl: MicrolensingModelFit;
    flare: MicrolensingModelFit;
    null: MicrolensingModelFit;
  };
  delta_bic: {
    null_minus_pspl: number | null;
    flare_minus_pspl: number | null;
    null_minus_flare: number | null;
  };
  symmetry_score: number | null;
  observables: MicrolensingObservables | null;
  planet_predictions: MicrolensingPlanetPredictions | null;
  notes: string[];
}

export async function fitMicrolensing(
  req: MicrolensingFitRequest
): Promise<MicrolensingFitResponse> {
  const r = await fetch(`${API_BASE}/api/microlensing/fit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!r.ok) throw new Error(`Microlensing fit failed (${r.status}): ${await r.text()}`);
  return r.json();
}

/** Ship a fit result + optional metadata + optional plot PNG to the backend
 *  and receive back a rendered PDF vetting report as a Blob. */
export async function fetchMicrolensingReport(
  result: MicrolensingFitResponse,
  metadata?: Record<string, unknown> | null,
  plotPngBase64?: string | null,
): Promise<Blob> {
  const r = await fetch(`${API_BASE}/api/microlensing/report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      result,
      metadata: metadata ?? null,
      plot_png_base64: plotPngBase64 ?? null,
    }),
  });
  if (!r.ok) throw new Error(`Microlensing report failed (${r.status}): ${await r.text()}`);
  return r.blob();
}

// ----------------------------------------------------------------------------
// Microlensing (Module B) — TESS sector coverage of a catalog
// ----------------------------------------------------------------------------

export interface CoverageSector {
  sector: number;
  camera: number;
  ccd: number;
  window: { start_btjd: number; end_btjd: number; nominal: boolean } | null;
  t0_in_window: boolean;
  wings_in_window: boolean;
}

export interface CoverageEvent {
  event_id: string;
  ra: number;
  dec: number;
  t0: number;
  tE: number;
  ecliptic_latitude_deg: number;
  in_bulge_blind_zone: boolean;
  sectors: CoverageSector[];
  observable: boolean;
  observable_with_wings: boolean;
  no_tess_point: boolean;
}

export interface CoverageResponse {
  events: CoverageEvent[];
  summary: {
    n_total: number;
    n_observable: number;
    n_observable_with_wings: number;
    n_in_bulge_blind_zone: number;
    n_no_tess_point: number;
    margin_te_used: number;
  };
  notes: string[];
}

export async function uploadCoverageCsv(
  file: File,
  marginTe = 1.0
): Promise<CoverageResponse> {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(`${API_BASE}/api/microlensing/coverage?margin_te=${marginTe}`, {
    method: "POST",
    body: fd,
  });
  if (!r.ok) throw new Error(`Coverage upload failed (${r.status}): ${await r.text()}`);
  return r.json();
}

// ----------------------------------------------------------------------------
// RA/Dec → TIC → TESS light-curve autoload (Module B → Module A handoff)
// ----------------------------------------------------------------------------

export interface LcByCoordsRequest {
  ra: number;
  dec: number;
  sector?: number | null;
  radius_arcsec?: number;
}

export interface LcByCoordsResponse {
  time: number[];
  flux: number[];
  flux_err: number[];
  tic_id: number;
  sector: number;
  resolved_ra: number;
  resolved_dec: number;
  separation_arcsec: number;
  tmag: number | null;
  provider: string | null;
  filename: string | null;
  n_cadences: number;
}

export async function fetchLightcurveByCoords(
  req: LcByCoordsRequest
): Promise<LcByCoordsResponse> {
  const r = await fetch(`${API_BASE}/api/microlensing/lightcurve_by_coords`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!r.ok) throw new Error(`Coord LC fetch failed (${r.status}): ${await r.text()}`);
  return r.json();
}

export interface LcByTicResponse {
  time: number[];
  flux: number[];
  flux_err: number[];
  tic_id: number;
  sector: number;
  provider: string | null;
  filename: string | null;
  n_cadences: number;
}

export async function fetchLightcurveByTic(
  tic_id: number,
  sector?: number | null
): Promise<LcByTicResponse> {
  const r = await fetch(`${API_BASE}/api/microlensing/lightcurve_by_tic`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tic_id, sector: sector ?? null }),
  });
  if (!r.ok) throw new Error(`TIC LC fetch failed (${r.status}): ${await r.text()}`);
  return r.json();
}
