import { useState } from "react";
import type { MicrolensingFitResponse } from "../api";
import { ShareToImgbbButton } from "../ShareButton";
import ExofopBulkPanel, { type ExofopImage } from "../ExofopBulkPanel";
import { buildExofopPackage, svgToPng } from "../microlensingExport";
import { fetchMicrolensingReport } from "../api";
import { CyclingLoader, PDF_MSGS } from "../Loaders";

export function ExportToolbar({
  result, svgRef, ffiPng, exportMetadata,
}: {
  result: MicrolensingFitResponse;
  svgRef: React.RefObject<SVGSVGElement | null>;
  ffiPng: string | null;
  exportMetadata: {
    event_id: string | null;
    tic_id: number | null;
    ra: number | null;
    dec: number | null;
    sector: number | null;
    provider: string | null;
  };
}) {
  const [pngB64, setPngB64] = useState<string | null>(null);
  const [renderErr, setRenderErr] = useState<string | null>(null);
  const [rendering, setRendering] = useState(false);
  const [zipBusy, setZipBusy] = useState(false);
  const [zipErr, setZipErr] = useState<string | null>(null);
  const [pdfBusy, setPdfBusy] = useState(false);
  const [pdfErr, setPdfErr] = useState<string | null>(null);

  // Rasterise the current SVG on demand (lazily — first click of Share).
  const renderPng = async (): Promise<string | null> => {
    if (pngB64) return pngB64;
    if (!svgRef.current) {
      setRenderErr("Plot not ready yet — try again after the light curve renders.");
      return null;
    }
    setRendering(true); setRenderErr(null);
    try {
      const b64 = await svgToPng(svgRef.current, 2);
      setPngB64(b64);
      return b64;
    } catch (e: any) {
      setRenderErr(e.message || String(e));
      return null;
    } finally {
      setRendering(false);
    }
  };

  const downloadPdf = async () => {
    setPdfBusy(true); setPdfErr(null);
    try {
      let png: string | null = pngB64;
      if (!png && svgRef.current) {
        try { png = await svgToPng(svgRef.current, 2); setPngB64(png); }
        catch { png = null; }
      }
      const blob = await fetchMicrolensingReport(result, exportMetadata, png, ffiPng);
      const url = URL.createObjectURL(blob);
      const stem = ([
        exportMetadata.event_id,
        exportMetadata.tic_id != null ? `TIC${exportMetadata.tic_id}` : null,
        exportMetadata.sector != null ? `S${String(exportMetadata.sector).padStart(3, "0")}` : null,
      ].filter(Boolean).join("_") || "microlensing_event")
        .replace(/[^a-zA-Z0-9_-]/g, "_");
      const a = document.createElement("a");
      a.href = url; a.download = `${stem}_report.pdf`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      setPdfErr(e.message || String(e));
    } finally {
      setPdfBusy(false);
    }
  };

  const downloadZip = async () => {
    setZipBusy(true); setZipErr(null);
    try {
      // Try to attach the plot PNG if we can — helps ExoFOP notes readers
      // see the fit at a glance. Non-fatal if the render fails.
      let png: string | null = pngB64;
      if (!png && svgRef.current) {
        try { png = await svgToPng(svgRef.current, 2); setPngB64(png); }
        catch { png = null; }
      }
      const { blob, filename } = await buildExofopPackage(result, exportMetadata, png);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = filename;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      setZipErr(e.message || String(e));
    } finally {
      setZipBusy(false);
    }
  };

  const shareLabel = exportMetadata.event_id
    ? `microlensing ${exportMetadata.event_id}`
    : "microlensing fit";
  const shareTitle = [
    "vetstar_microlensing",
    exportMetadata.event_id,
    exportMetadata.tic_id != null ? `TIC${exportMetadata.tic_id}` : null,
    exportMetadata.sector != null ? `S${String(exportMetadata.sector).padStart(3, "0")}` : null,
  ].filter(Boolean).join("_");

  return (
    <section className="rounded border border-slate-200 bg-slate-50 p-3 flex flex-wrap items-center gap-3 text-xs">
      <span className="font-semibold text-slate-700">Export &amp; share:</span>

      {/* ImgBB share — rasterise SVG on first click, then hand off to
          ShareToImgbbButton once we have the base64 in state. */}
      {!pngB64 ? (
        <button
          onClick={renderPng}
          disabled={rendering}
          className="px-2 py-1 rounded bg-slate-100 hover:bg-purple-100 text-slate-700 hover:text-purple-700 flex items-center gap-1 transition"
          title="Rasterise the plot and upload to ImgBB for a shareable link"
        >
          {rendering ? "rendering…" : "📤 Share plot"}
        </button>
      ) : (
        <ShareToImgbbButton base64={pngB64} title={shareTitle} label={shareLabel} />
      )}

      {/* Vetting-report PDF */}
      <button
        onClick={downloadPdf}
        disabled={pdfBusy}
        className="px-2 py-1 rounded bg-sky-700 hover:bg-sky-800 text-white font-medium disabled:bg-slate-300 min-w-[9rem]"
        title="Generate a PDF vetting report — verdict, observables, planet predictions, ExoFOP rows, FFI + Gaia overlay, and the plot"
      >
        {pdfBusy
          ? <CyclingLoader messages={PDF_MSGS} className="text-[10px]" />
          : "📄 Download PDF report"}
      </button>

      {/* Analysis bundle — plot + LC data + full JSON + notes (not the same
          format as ExoFOP-TESS bulk upload; that's the separate panel below). */}
      <button
        onClick={downloadZip}
        disabled={zipBusy}
        className="px-2 py-1 rounded bg-emerald-600 hover:bg-emerald-700 text-white font-medium disabled:bg-slate-300 min-w-[9rem]"
        title="ZIP with summary.csv, lightcurve_windowed.csv, fit_full.json, notes.md, and the plot PNG — for local archiving and reproducibility"
      >
        {zipBusy ? "packaging…" : "⬇ Analysis bundle"}
      </button>

      {renderErr && <span className="text-red-700">plot render: {renderErr}</span>}
      {zipErr && <span className="text-red-700">export: {zipErr}</span>}
      {pdfErr && <span className="text-red-700">PDF: {pdfErr}</span>}

      {/* Real ExoFOP-TESS bulk-upload panel — same convention as the transit
          tab uses. Requires a TIC ID for the filename scheme. */}
      {exportMetadata.tic_id != null && (
        <div className="w-full mt-2">
          <ExofopBulkPanel
            ticId={exportMetadata.tic_id}
            sector={exportMetadata.sector ?? undefined}
            images={_buildExofopImages(pngB64, ffiPng)}
            title="⬇ Build ExoFOP-TESS bulk-upload ZIP (transit-tab convention)"
            caption={
              <p className="text-slate-500 mt-2">
                Bundles the microlensing fit plot + FFI/Gaia cutout as
                <code> TIC{`{id}`}O-xxYYYYMMDD.slug.png</code> inside a
                <code> xxYYYYMMDD-nnn.zip</code> with a matching
                <code> .txt</code> descriptor — the same
                <a
                  className="text-blue-600 hover:underline mx-1"
                  href="https://exofop.ipac.caltech.edu/tess/script_upload_help.php"
                  target="_blank" rel="noopener noreferrer"
                >
                  ExoFOP-TESS bulk file upload
                </a>
                convention the Transit tab uses. Initials + data tag are
                remembered between runs.
              </p>
            }
          />
        </div>
      )}
    </section>
  );
}

/** Assemble the ExofopBulkPanel image list — classifier plot first (if
 *  rasterised), FFI+Gaia cutout second (if fetched). Both are optional. */
export function _buildExofopImages(plotPng: string | null, ffiPng: string | null): ExofopImage[] {
  const out: ExofopImage[] = [];
  if (plotPng) {
    out.push({ key: "microlensing-lc-fit", label: "Microlensing PSPL/flare/null overlay",
                b64: plotPng, code: "O" });
  }
  if (ffiPng) {
    out.push({ key: "ffi-gaia-cutout", label: "TESScut FFI + Gaia DR3 overlay",
                b64: ffiPng, code: "O" });
  }
  return out;
}

// ============================================================================
// Observables + planet-prediction panels
// ============================================================================
