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
