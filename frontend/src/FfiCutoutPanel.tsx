/**
 * FfiCutoutPanel.tsx
 *
 * Shows a TESS Full-Frame-Image (FFI) cutout of the target, so the user can
 * see the patch of sky the light curve came from — a quick visual check for
 * a bright neighbour or background eclipsing binary blended into the aperture.
 *
 * Mirrors the ExoMiner panel pattern: auto-runs once on mount, collapsible,
 * fails soft with a friendly message. The cutout is fetched from MAST's
 * TESScut service on the backend and cached per (TIC, sector).
 *
 * Usage (after vetting finishes, e.g. in App.tsx):
 *   <FfiCutoutPanel result={result} />
 */
import { useEffect, useRef, useState } from "react";
import { runFfiCutout, type FfiCutoutResult } from "./api";
import type { VettingResult } from "./types";
import { ShareToImgbbButton } from "./ShareButton";

export default function FfiCutoutPanel({ result }: { result: VettingResult }) {
  const [data, setData] = useState<FfiCutoutResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(true);
  const ranRef = useRef(false);

  useEffect(() => {
    if (ranRef.current) return;
    ranRef.current = true;
    doRun();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function doRun() {
    const { ra, dec, sector, tic_id } = result.star;
    if (ra == null || dec == null) {
      setError("No coordinates available for this target — cannot fetch an FFI cutout.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await runFfiCutout({
        ra,
        dec,
        sector: sector ?? undefined,
        tic_id: tic_id ?? undefined,
      });
      setData(res);
    } catch (e: any) {
      setError(e?.message ?? "FFI cutout request failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="max-w-6xl mx-auto px-6 mb-6">
      <section className="mt-4 rounded-lg border border-slate-200 bg-white shadow-sm overflow-hidden">
        <header className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
        <div>
          <h2 className="text-sm font-semibold text-slate-700">
            Target field — TESS FFI cutout
          </h2>
          <p className="text-xs text-slate-400">
            The patch of sky the photometry came from (TESScut)
          </p>
        </div>
        <div className="flex items-center gap-2">
          {!loading && (
            <button
              onClick={doRun}
              className="text-xs text-slate-500 hover:text-slate-800 underline"
            >
              Re-run
            </button>
          )}
          <button
            onClick={() => setExpanded((x) => !x)}
            className="text-xs text-slate-500 hover:text-slate-800"
            aria-label={expanded ? "Collapse" : "Expand"}
          >
            {expanded ? "▾" : "▸"}
          </button>
        </div>
      </header>

      {expanded && (
        <div className="p-4">
          {loading && (
            <div className="text-sm text-slate-500 py-6 text-center">
              Fetching FFI cutout from MAST…
            </div>
          )}

          {!loading && error && (
            <div className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded p-3">
              {error}
            </div>
          )}

          {!loading && !error && data && !data.available && (
            <div className="text-sm text-slate-500 bg-slate-50 border border-slate-200 rounded p-3">
              {data.reason ?? "No FFI cutout available for this target."}
            </div>
          )}

          {!loading && !error && data?.available && data.image && (
            <figure className="space-y-2">
              <div className="flex items-center justify-between">
                <figcaption className="text-xs text-slate-500">
                  Target marked with a red <span className="text-red-500 font-bold">+</span>
                  {" · "}aperture outlined in yellow where available
                  {data.size_px ? ` · ${data.size_px}×${data.size_px} px` : ""}
                  {data.n_frames ? ` · median of ${data.n_frames} frames` : ""}
                </figcaption>
                <ShareToImgbbButton
                  base64={data.image}
                  title={`FFI cutout TIC ${result.star.tic_id ?? ""}`}
                  label="FFI cutout"
                />
              </div>
              <img
                src={`data:image/png;base64,${data.image}`}
                alt="TESS FFI cutout of the target"
                className="mx-auto block h-auto w-full max-w-[320px] rounded border shadow-sm object-contain"
                loading="lazy"
              />
            </figure>
          )}
        </div>
      )}
      </section>
    </div>
  );
}
