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
}
