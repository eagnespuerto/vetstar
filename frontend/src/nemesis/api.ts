import { API_BASE, readableError } from "../api";

export interface NemesisStatus {
  enabled: boolean;
  online?: boolean;
  nemesis_version?: string | null;
  queue_depth?: number;
  detail?: string;
}

export interface NemesisJobRequest {
  tic: number;
  sector: number;
  cadence: "30 minute" | "10 minute" | "2 minute" | "20 second";
  minP?: number;
  maxP?: number;
  N_transits?: number;
  do_TLS_Search?: boolean;
}

export type JobState = "queued" | "running" | "done" | "error";

export interface NemesisJobStatus {
  job_id: string;
  state: JobState;
  stage: string;
  stage_label: string;
  position?: number | null;
  log_tail: string[];
  error?: string;
}

export interface LeoTest {
  key: string;
  title: string;
  group: "FA" | "FP";
  passed: boolean;
  meaning: string;
  detail: string;
}

export interface LeoResult {
  available: boolean;
  reason?: string;
  disposition?: "FA" | "FP" | "PC";
  tests?: LeoTest[];
}

export interface SearchParams {
  method: string;
  period_d: number | null;
  t0_btjd: number | null;
  duration_hr: number | null;
  depth_ppt: number | null;
  sde: number | null;
  radius_earth: number | null;
  n_transits: number | null;
}

export interface SearchResult {
  params: SearchParams;
  periodogram: { period?: number[]; sde?: number[] };
  fold: { phase?: number[]; flux?: number[]; bin_phase?: number[]; bin_flux?: number[] };
  leo: LeoResult;
}

export interface NemesisResult {
  light_curve: { time?: number[]; flux?: (number | null)[]; sap?: (number | null)[] };
  search: Record<string, SearchResult> | null;
  primary: string | null;
  /** Base64 PNGs drawn by the worker in the NEMESIS II paper style. */
  figures?: { fold?: string | null; periodogram?: string | null; light_curve?: string | null; odd_even?: string | null };
  star?: { tic_id: number; ra?: number | null; dec?: number | null; tmag?: number | null;
           teff?: number | null; radius?: number | null; mass?: number | null; logg?: number | null };
  meta: { tic: number; sector: number; cadence: string; nemesis_version: string; runtime_s: number;
          lite?: boolean; peak_rss_mb?: number | null };
}

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw await readableError(r, "NEMESIS request");
  return r.json();
}

export const nemesisStatus = () => fetch(`${API_BASE}/api/nemesis/status`).then((r) => j<NemesisStatus>(r));

export interface NemesisSectors {
  tic: number;
  sectors: { sector: number; cadence: NemesisJobRequest["cadence"] }[];
  unsupported: number[];
  max_supported: number;
}

/** Sectors NEMESIS can actually run for this star (full-frame-image coverage
 *  that is also in NEMESIS's sector table). */
export const nemesisSectors = (tic: number) =>
  fetch(`${API_BASE}/api/nemesis/sectors/${tic}`).then((r) => j<NemesisSectors>(r));

export const submitNemesis =(req: NemesisJobRequest) =>
  fetch(`${API_BASE}/api/nemesis/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  }).then((r) => j<{ job_id: string; position: number }>(r));

export const nemesisJob = (id: string) =>
  fetch(`${API_BASE}/api/nemesis/jobs/${id}`).then((r) => j<NemesisJobStatus>(r));

export const nemesisResult = (id: string) =>
  fetch(`${API_BASE}/api/nemesis/jobs/${id}/result`).then((r) => j<NemesisResult>(r));

export const nemesisFiles = (id: string) =>
  fetch(`${API_BASE}/api/nemesis/jobs/${id}/files`).then((r) => j<{ name: string; bytes: number }[]>(r));

/** Vetstar PDF report for a finished run (built on the site from the worker's result). */
export async function nemesisReportPdf(
  id: string, hciImage?: string | null, credit?: { author?: string; initials?: string },
): Promise<Uint8Array> {
  const r = await fetch(`${API_BASE}/api/nemesis/jobs/${id}/report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      hci_image: hciImage ?? null, include_ffi: true,
      author: credit?.author?.trim() || null, initials: credit?.initials?.trim() || null,
    }),
  });
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg = (await r.json()).detail ?? msg; } catch { /* not JSON */ }
    throw new Error(`PDF report failed: ${msg}`);
  }
  return new Uint8Array(await r.arrayBuffer());
}

export async function nemesisFileBytes(id: string, name: string): Promise<Uint8Array> {
  const r = await fetch(nemesisFileUrl(id, name));
  if (!r.ok) throw new Error(`${name}: ${r.status}`);
  return new Uint8Array(await r.arrayBuffer());
}

export const nemesisFileUrl =(id: string, name: string) =>
  `${API_BASE}/api/nemesis/jobs/${id}/files/${encodeURIComponent(name)}`;

export const nemesisHci = (body: {
  tic: number; period_d?: number | null; radius_earth?: number | null;
  duration_hr?: number | null; depth_ppt?: number | null; leo?: LeoResult;
}) =>
  fetch(`${API_BASE}/api/nemesis/hci`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => j<any>(r));
