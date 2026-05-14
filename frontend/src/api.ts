export const API_BASE = import.meta.env.VITE_API_URL || "";

export async function analyze(file: File) {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(`${API_BASE}/api/analyze`, { method: "POST", body: fd });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`Analyze failed (${r.status}): ${text}`);
  }
  return r.json();
}

export async function downloadReport(file: File): Promise<Blob> {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(`${API_BASE}/api/report`, { method: "POST", body: fd });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`Report failed (${r.status}): ${text}`);
  }
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
  // Backward-compat: old response was list of numbers.
  if (Array.isArray(data.sectors) && data.sectors.length > 0 && typeof data.sectors[0] === "number") {
    return (data.sectors as number[]).map((s) => ({ sector: s, providers: [] }));
  }
  return data.sectors as SectorInfo[];
}

export async function mastAnalyze(ticId: number, sector: number) {
  const r = await fetch(`${API_BASE}/api/mast/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tic_id: ticId, sector }),
  });
  if (!r.ok) throw new Error(`MAST analyze failed (${r.status}): ${await r.text()}`);
  return r.json();
}

export async function mastReport(ticId: number, sector: number): Promise<Blob> {
  const r = await fetch(`${API_BASE}/api/mast/report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tic_id: ticId, sector }),
  });
  if (!r.ok) throw new Error(`MAST report failed: ${await r.text()}`);
  return r.blob();
}
