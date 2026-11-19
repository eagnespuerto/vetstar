
// Prefill payload from the Coverage table's "Analyze in Module A" action.
// The LC itself isn't auto-fetched — that would need a full MAST coord→TIC
// resolver — but we pre-select the window around t0 and surface the target
// metadata so the user knows what they're looking at.
export interface ClassifierPrefill {
  source: "coverage";
  event_id: string;
  ra: number;
  dec: number;
  t0: number;
  tE: number;
  sector: number | null;
  camera: number | null;
  ccd: number | null;
  observable: boolean;
}

// ============================================================================
// Data types + loaders
// ============================================================================

export type LC = { time: number[]; flux: number[]; flux_err: number[]; label: string };

export type ModelKey = "pspl" | "flare" | "null";
