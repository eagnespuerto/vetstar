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
  plots: Record<string, string>;
  mast?: MastInfo;
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
