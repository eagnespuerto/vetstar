import { useState } from "react";
import { multisectorReport } from "../api";
import { CyclingLoader } from "../Loaders";
import { PDF_LOADING_MSGS } from "./loadingMessages";
import { PlotsSection } from "./PlotsSection";
import { triggerDownload } from "./ui";

export const MS_EXOMINER_VIEWS: Array<[string, string]> = [
  ["global_view", "Global view"],
  ["local_view", "Local view"],
  ["secondary_view", "Secondary view"],
  ["odd_even_view", "Odd vs even"],
  ["centroid_global_view", "Centroid global"],
  ["centroid_local_view", "Centroid local"],
  ["diagnostic_sigmas", "Diagnostic σ"],
];

export function MultisectorPanel({ data }: { data: any }) {
  const [expanded, setExpanded] = useState(true);
  const [pdfBusy, setPdfBusy] = useState(false);
  const [pdfErr, setPdfErr] = useState<string | null>(null);
  if (!data) return null;

  const ticId: number | null = data.star?.tic_id ?? null;
  const mast = data.mast || {};
  const sectorsUsed: number[] = mast.sectors_used || [];
  const errors: { sector: number; error: string }[] = mast.errors || [];
  const comparison: any[] = mast.comparison || [];
  const repSector: number | null = mast.representative_sector ?? null;
  const verdict = data.verdict || {};
  const bls = data.bls || {};

  const downloadPdf = async () => {
    if (!ticId) return;
    setPdfBusy(true);
    setPdfErr(null);
    try {
      const blob = await multisectorReport(ticId, undefined, sectorsUsed);
      triggerDownload(blob, `vetting_TIC${ticId}_multisector.pdf`);
    } catch (e: any) {
      setPdfErr(e?.message || String(e));
    } finally {
      setPdfBusy(false);
    }
  };

  const sectorChips = sectorsUsed
    .map((s) => `S${String(s).padStart(3, "0")}`)
    .join(", ");

  return (
    <div className="mt-4 border-t pt-4 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <button
          onClick={() => setExpanded(!expanded)}
          className="text-left font-semibold text-slate-700 flex items-center gap-1 text-sm"
        >
          <span>{expanded ? "▲" : "▼"}</span>
          🔭 Multi-sector analysis — {sectorsUsed.length} sector
          {sectorsUsed.length === 1 ? "" : "s"} compared
          {verdict.headline ? ` · ${verdict.headline}` : ""}
        </button>
        <button
          onClick={downloadPdf}
          disabled={pdfBusy || !ticId}
          className="shrink-0 text-xs px-3 py-1.5 bg-slate-700 text-white rounded hover:bg-slate-800 disabled:bg-slate-300 transition"
        >
          {pdfBusy ? "Building…" : "Download multi-sector PDF"}
        </button>
      </div>
      {pdfBusy && (
        <div className="rounded bg-emerald-50 border border-emerald-200 p-3 text-emerald-900 text-sm">
          <CyclingLoader messages={PDF_LOADING_MSGS} />
          <span className="block mt-1 text-xs text-emerald-700">
            Building the multi-sector PDF — this can take a minute.
          </span>
        </div>
      )}
      {pdfErr && <p className="text-xs text-red-600">PDF error: {pdfErr}</p>}

      {expanded && (
        <div className="space-y-3">
          <div className="rounded bg-slate-50 border border-slate-200 p-3 text-xs text-slate-700 space-y-1">
            <p>
              <strong>Sectors compared:</strong>{" "}
              <span className="font-mono">{sectorChips || "none"}</span>{" "}
              <span className="text-slate-400">
                ({mast.sectors_succeeded ?? sectorsUsed.length}/
                {mast.sectors_attempted ?? sectorsUsed.length} succeeded)
              </span>
            </p>
            <p>
              Each sector ran through the standard single-sector pipeline
              back-to-back; only the per-sector summary survives between runs.
              The sector with the highest BLS SDE
              {repSector != null && (
                <>
                  {" "}
                  (<span className="font-mono">S{String(repSector).padStart(3, "0")}</span>)
                </>
              )}{" "}
              is kept as the representative result and its plots, HCI summary,
              and ExoMiner views are shown below. HCI is rebuilt with the
              real multi-sector detection counts (
              <strong>{mast.n_sectors_with_detections ?? 0}</strong>/
              <strong>{mast.n_sectors_observed ?? sectorsUsed.length}</strong>
              ).
            </p>
            {bls.period != null && (
              <p>
                <strong>Representative BLS:</strong> P ={" "}
                <span className="font-mono">{Number(bls.period).toFixed(5)} d</span>
                {bls.sde != null && (
                  <>
                    {" "}· SDE ={" "}
                    <span className="font-mono">{Number(bls.sde).toFixed(2)}</span>
                  </>
                )}
                {bls.depth != null && (
                  <>
                    {" "}· depth ={" "}
                    <span className="font-mono">
                      {(Number(bls.depth) * 100).toFixed(3)}%
                    </span>
                  </>
                )}
              </p>
            )}
          </div>

          {/* Per-sector comparison table */}
          {comparison.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-slate-700 mb-1">
                Per-sector comparison
              </p>
              <table className="w-full text-xs">
                <thead className="border-b text-slate-500 text-left">
                  <tr>
                    <th className="py-1">Sector</th>
                    <th>Events</th>
                    <th>Verdict</th>
                    <th>BLS period (d)</th>
                    <th>Depth (%)</th>
                    <th>SDE</th>
                    <th>Period agrees with</th>
                  </tr>
                </thead>
                <tbody>
                  {comparison.map((s: any) => {
                    const isRep = s.sector === repSector;
                    const aligned: number[] = s.period_aligned_with || [];
                    return (
                      <tr key={s.sector} className={`border-b ${isRep ? "bg-emerald-50" : ""}`}>
                        <td className="py-0.5 font-mono">
                          S{String(s.sector).padStart(3, "0")}
                          {isRep && <span className="ml-1 text-emerald-700">★</span>}
                        </td>
                        <td className="text-center">{s.n_events}</td>
                        <td>
                          <span
                            className={`px-1 rounded ${
                              s.category === "planet_candidate"
                                ? "bg-emerald-100 text-emerald-800"
                                : s.category === "eclipsing_binary_candidate"
                                ? "bg-amber-100 text-amber-800"
                                : "bg-slate-100 text-slate-600"
                            }`}
                          >
                            {s.verdict ?? s.category ?? "—"}
                          </span>
                        </td>
                        <td className="text-center font-mono">
                          {s.bls_period_d?.toFixed?.(4) ?? "—"}
                        </td>
                        <td className="text-center font-mono">
                          {s.bls_depth != null
                            ? (Number(s.bls_depth) * 100).toFixed(3)
                            : "—"}
                        </td>
                        <td className="text-center font-mono">
                          {s.bls_sde?.toFixed?.(2) ?? "—"}
                        </td>
                        <td className="text-center font-mono">
                          {aligned.length > 0
                            ? aligned
                                .map((sn: number) => `S${String(sn).padStart(3, "0")}`)
                                .join(", ")
                            : "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <p className="text-xs text-slate-400 mt-1">
                ★ representative sector — chosen by verdict, then by BLS
                period agreement with other sectors (rounded to the nearest
                integer day), then by SDE. Full plots, HCI, and ExoMiner
                below are drawn from this run.
              </p>
            </div>
          )}

          {/* Representative-sector plots */}
          {data.plots && (
            <PlotsSection
              plots={data.plots}
              ticId={ticId}
              sector={repSector}
            />
          )}

          {/* Fetch errors */}
          {errors.length > 0 && (
            <div className="text-xs text-slate-500 bg-slate-50 p-2 rounded">
              {errors.length} sector(s) could not be fetched:{" "}
              <span className="font-mono">
                {errors.map((e) => `S${String(e.sector).padStart(3, "0")}`).join(", ")}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
