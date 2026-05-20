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
