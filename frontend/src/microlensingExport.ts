// Client-side helpers for the microlensing classifier's export/share actions.
// Two responsibilities:
//   1. svgToPng — rasterise an inline SVG element to a base64 PNG string
//      (so ShareToImgbbButton can upload it).
//   2. buildExofopPackage — assemble the fit results into a ZIP suitable
//      for attaching to an ExoFOP follow-up notes upload (or general
//      archiving). ExoFOP doesn't ship a canonical microlensing schema, so
//      the package is deliberately human-readable: a Markdown summary, a
//      one-row CSV with the headline params, the full JSON response, and
//      the windowed light-curve arrays.

import { buildZip, type ZipEntry } from "./zip";
import type { MicrolensingFitResponse } from "./api";

// ----------------------------------------------------------------------------
// SVG → base64 PNG
// ----------------------------------------------------------------------------

/** Rasterise an SVG element to base64 PNG (no `data:image/png;base64,` prefix,
 *  matching what ShareToImgbbButton expects). Scale factor applied to the
 *  SVG's viewBox pixel dimensions — 2 gives a crisp retina-ish PNG. */
export async function svgToPng(
  svgEl: SVGSVGElement,
  scale = 2
): Promise<string> {
  // Serialise the live DOM node — inline styles are already Tailwind-flat
  // (colours + widths on attributes) so no CSS injection needed.
  const clone = svgEl.cloneNode(true) as SVGSVGElement;
  // Ensure xmlns present — required for standalone parseability.
  if (!clone.getAttribute("xmlns")) {
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  }
  const vb = clone.getAttribute("viewBox");
  let w = 800, h = 260;
  if (vb) {
    const parts = vb.split(/\s+/).map((v) => parseFloat(v));
    if (parts.length === 4 && parts.every(Number.isFinite)) {
      w = parts[2]; h = parts[3];
    }
  }
  // Pin explicit width/height so the browser's SVG-as-image path uses them.
  clone.setAttribute("width", String(w));
  clone.setAttribute("height", String(h));

  const xml = new XMLSerializer().serializeToString(clone);
  const svg64 = btoa(unescape(encodeURIComponent(xml)));
  const dataUrl = `data:image/svg+xml;base64,${svg64}`;

  return new Promise<string>((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(w * scale);
      canvas.height = Math.round(h * scale);
      const ctx = canvas.getContext("2d");
      if (!ctx) return reject(new Error("2D canvas context unavailable"));
      // Paint a white background so ImgBB/PDFs don't render a see-through PNG.
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      // toDataURL yields "data:image/png;base64,AAAA…"; drop the prefix.
      const url = canvas.toDataURL("image/png");
      const prefix = "data:image/png;base64,";
      resolve(url.startsWith(prefix) ? url.slice(prefix.length) : url);
    };
    img.onerror = () => reject(new Error("SVG failed to load into image element"));
    img.src = dataUrl;
  });
}

// ----------------------------------------------------------------------------
// ExoFOP export package (ZIP)
// ----------------------------------------------------------------------------

export interface ExportMetadata {
  event_id?: string | null;
  tic_id?: number | null;
  ra?: number | null;
  dec?: number | null;
  sector?: number | null;
  provider?: string | null;
}

const ENC = new TextEncoder();

/** Build a store-only ZIP with the fit results in ExoFOP-friendly formats. */
export async function buildExofopPackage(
  result: MicrolensingFitResponse,
  meta: ExportMetadata,
  pngBase64?: string | null
): Promise<{ blob: Blob; filename: string }> {
  const pspl = result.models.pspl.params;
  const psplErr = result.models.pspl.param_err;
  const flare = result.models.flare.params;
  const nul = result.models.null.params;

  const idBits = [
    meta.event_id,
    meta.tic_id != null ? `TIC${meta.tic_id}` : null,
    meta.sector != null ? `S${String(meta.sector).padStart(3, "0")}` : null,
  ].filter(Boolean);
  const stem = (idBits.length ? idBits.join("_") : "microlensing_event")
    .replace(/[^a-zA-Z0-9_-]/g, "_");
  const filename = `${stem}_exofop.zip`;

  // ---- summary.csv — one headline row per event -------------------------
  const summaryHeader = [
    "event_id", "tic_id", "ra_deg", "dec_deg", "sector", "provider",
    "verdict", "confidence",
    "n_points", "baseline_flux",
    "t0_btjd", "t0_err",
    "tE_days", "tE_err",
    "u0", "u0_err",
    "f_s", "f_s_err",
    "f_b", "f_b_err",
    "bic_pspl", "bic_flare", "bic_null",
    "delta_bic_null_minus_pspl", "delta_bic_flare_minus_pspl",
    "symmetry_score",
    "flare_t_peak", "flare_amplitude", "flare_fwhm",
    "null_baseline",
  ].join(",");
  const row = [
    meta.event_id ?? "",
    meta.tic_id ?? "",
    meta.ra ?? "",
    meta.dec ?? "",
    meta.sector ?? "",
    meta.provider ?? "",
    result.verdict,
    result.confidence.toFixed(4),
    result.window.n_points,
    result.window.baseline_flux.toExponential(6),
    _num(pspl.t0), _num(psplErr.t0),
    _num(pspl.tE), _num(psplErr.tE),
    _num(pspl.u0), _num(psplErr.u0),
    _num(pspl.f_s), _num(psplErr.f_s),
    _num(pspl.f_b), _num(psplErr.f_b),
    _num(result.models.pspl.bic),
    _num(result.models.flare.bic),
    _num(result.models.null.bic),
    _num(result.delta_bic.null_minus_pspl),
    _num(result.delta_bic.flare_minus_pspl),
    _num(result.symmetry_score),
    _num(flare.t_peak), _num(flare.amplitude), _num(flare.fwhm),
    _num(nul.baseline),
  ].join(",");
  const summaryCsv = `${summaryHeader}\n${row}\n`;

  // ---- lightcurve_windowed.csv — the actual data fed to the fit --------
  const lcHeader = "time,flux_norm,flux_err_norm,pspl_model,flare_model,null_model";
  const lcRows: string[] = [];
  for (let i = 0; i < result.time_windowed.length; i++) {
    lcRows.push(
      [
        result.time_windowed[i].toFixed(8),
        result.flux_normalized[i].toFixed(8),
        result.flux_err_normalized[i].toExponential(6),
        _lcCell(result.models.pspl.model_flux, i),
        _lcCell(result.models.flare.model_flux, i),
        _lcCell(result.models.null.model_flux, i),
      ].join(",")
    );
  }
  const lcCsv = `${lcHeader}\n${lcRows.join("\n")}\n`;

  // ---- notes.md — human-readable summary suitable for ExoFOP notes -----
  const now = new Date().toISOString();
  const notes = _buildNotes(result, meta, now);

  // ---- fit_full.json — the raw response, for reproducibility -----------
  const fullJson = JSON.stringify(
    { generated: now, metadata: meta, result },
    null,
    2
  );

  const entries: ZipEntry[] = [
    { name: "summary.csv", data: ENC.encode(summaryCsv) },
    { name: "lightcurve_windowed.csv", data: ENC.encode(lcCsv) },
    { name: "notes.md", data: ENC.encode(notes) },
    { name: "fit_full.json", data: ENC.encode(fullJson) },
  ];
  if (pngBase64) {
    entries.push({ name: "plot.png", data: _base64ToBytes(pngBase64) });
  }
  return { blob: buildZip(entries), filename };
}

// ----------------------------------------------------------------------------
// Small helpers
// ----------------------------------------------------------------------------

function _num(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "";
  const abs = Math.abs(v);
  if (abs !== 0 && (abs < 1e-4 || abs >= 1e6)) return v.toExponential(6);
  return v.toPrecision(8);
}

function _lcCell(arr: number[] | null | undefined, i: number): string {
  if (!arr || i >= arr.length) return "";
  const v = arr[i];
  if (!Number.isFinite(v)) return "";
  return v.toFixed(8);
}

function _base64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function _buildNotes(
  result: MicrolensingFitResponse,
  meta: ExportMetadata,
  generatedAt: string
): string {
  const pspl = result.models.pspl.params;
  const psplErr = result.models.pspl.param_err;
  const lines: string[] = [];
  lines.push(`# Vetstar microlensing fit — ${meta.event_id ?? "event"}`);
  lines.push("");
  lines.push(`Generated: ${generatedAt}`);
  if (meta.tic_id != null) lines.push(`TIC: ${meta.tic_id}`);
  if (meta.ra != null && meta.dec != null)
    lines.push(`Coords: RA=${meta.ra.toFixed(5)}°, Dec=${meta.dec.toFixed(5)}°`);
  if (meta.sector != null)
    lines.push(`Sector: S${String(meta.sector).padStart(3, "0")}` +
               (meta.provider ? ` via ${meta.provider}` : ""));
  lines.push("");
  lines.push(`## Verdict`);
  lines.push("");
  lines.push(`**${result.verdict.toUpperCase()}** at ${(result.confidence * 100).toFixed(0)}% confidence`);
  lines.push("");
  lines.push(`Window: BTJD ${result.window.t_start.toFixed(3)} → ${result.window.t_end.toFixed(3)} (${result.window.n_points} points)`);
  lines.push("");
  lines.push(`## PSPL best-fit parameters`);
  lines.push("");
  lines.push("| Parameter | Value | 1σ error |");
  lines.push("|---|---|---|");
  for (const k of ["t0", "tE", "u0", "f_s", "f_b"]) {
    const v = pspl[k];
    const e = psplErr[k];
    lines.push(
      `| ${k} | ${_num(v)} | ${e != null && Number.isFinite(e) ? _num(e) : "—"} |`
    );
  }
  lines.push("");
  lines.push(`## Model comparison (BIC — lower is better)`);
  lines.push("");
  lines.push("| Model | BIC | ΔBIC vs PSPL |");
  lines.push("|---|---|---|");
  lines.push(`| PSPL  | ${_num(result.models.pspl.bic)} | 0 |`);
  lines.push(`| Flare | ${_num(result.models.flare.bic)} | ${_num(result.delta_bic.flare_minus_pspl)} |`);
  lines.push(`| Null  | ${_num(result.models.null.bic)} | ${_num(result.delta_bic.null_minus_pspl)} |`);
  lines.push("");
  lines.push(`Symmetry score (PSPL residuals): **${_num(result.symmetry_score)}** (+1 = symmetric, ~0 = uncorrelated, <0 = anti-symmetric)`);
  lines.push("");
  if (result.notes.length) {
    lines.push(`## Diagnostic notes`);
    lines.push("");
    for (const n of result.notes) lines.push(`- ${n}`);
    lines.push("");
  }
  lines.push(`---`);
  lines.push(`*Package contents: summary.csv (headline params), lightcurve_windowed.csv (data + model curves), fit_full.json (raw response), notes.md (this file)` +
             `, plot.png (if attached).*`);
  return lines.join("\n");
}
