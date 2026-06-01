export interface StarInfo {
  tic_id: number | null;
  tmag: number | null;
  teff: number | null;
  radius: number | null;
  logg: number | null;
  mass: number | null;
  ra: number | null;
  dec: number | null;
  sector: number | null;
  camera: number | null;
  ccd: number | null;
  crowdsap: number | null;
  source: string;
}

export interface Event {
  t_start: number;
  t_end: number;
  duration_d: number;
  min_flux: number;
  depth: number;
  n_points: number;
}

export interface Verdict {
  headline: string;
  category: string;
  confidence: number;
  flags: string[];
  reasons: string[];
}

export interface MastInfo {
  filename: string;
  obs_id: string;
  matched_observations: number;
  author?: string;
  exptime?: number;
  fallback?: boolean;
  tried?: string[];
}

/** One TCE (Threshold Crossing Event) from a TESS SPOC DVT file. */
export interface DvtTce {
  tce_index: number;
  period_d?: number | null;
  duration_h?: number | null;
  epoch_btjd?: number | null;
  depth_ppm?: number | null;
  depth_frac?: number | null;
  /** SPOC DV-fitted impact parameter b (replaces b=0 assumption). */
  impact_b?: number | null;
  /** SPOC DV-fitted a/R★ from the ARAT header keyword. */
  a_over_rs?: number | null;
  inclination_deg?: number | null;
  rprs?: number | null;
  snr?: number | null;
  n_transits?: number | null;
  /** Base64 PNG: phase-folded data + SPOC transit model overlay. */
  phase_fold_plot?: string;
}

/** Parsed DVT summary returned by /api/mast/analyze. */
export interface DvtResult {
  available: boolean;
  star: Record<string, number | null>;
  tce: DvtTce;
  n_tces: number;
}

export interface DetrendBlock {
  applied: boolean;
  reason: "user_period" | "ls_peak" | "skipped_low_amplitude" | "disabled";
  period_days: number | null;
  amplitude_ppm: number | null;
  harmonic_amplitude_ppm: number | null;
  rms_reduction_pct: number | null;
}

export interface SensitivityEcho {
  threshold: number;
  min_snr: number;
  secondary_sigma: number;
}

export interface VettingResult {
  star: StarInfo;
  summary: Record<string, number>;
  bls: Record<string, any>;
  lomb_scargle: Record<string, any>;
  events: Event[];
  centroid: Record<string, any>;
  odd_even: Record<string, any>;
  secondary: Record<string, any>;
  shape: Record<string, any>;
  physics: Record<string, any>;
  verdict: Verdict;
  detrend?: DetrendBlock;
  sensitivity?: SensitivityEcho;
  plots: Record<string, string>;
  mast?: MastInfo;
  /** SPOC DVT fitted parameters (only present for MAST-fetched results). */
  dvt?: DvtResult | null;
  /** Downsampled cleaned light curve for the manual dip selector. */
  lightcurve?: { t: number[]; f: number[] };
}

export interface ObservablesResult {
  inputs: Record<string, any>;
  luminosity_lsun: number | null;
  habitable_zone: {
    inner_au?: number; center_au?: number; outer_au?: number; width_au?: number;
    inner_mas?: number; center_mas?: number; outer_mas?: number; width_mas?: number;
  };
  orbit: { semi_major_axis_au: number | null; orbital_period_d: number | null; derivation: string };
  insolation_searth: number | null;
  planet: {
    rp_rjup: number | null; rp_earth: number | null;
    mp_mjup: number | null; mp_earth: number | null; mass_source: string;
  };
  radial_velocity: { K_ms?: number; inclination_deg?: number; eccentricity?: number };
  astrometric: { theta_uas?: number };
  transit: { depth_pct?: number; capped?: boolean };
  max_projected_separation_arcsec: number | null;
  caveats: string[];
  exofop_source?: string | null;
}
