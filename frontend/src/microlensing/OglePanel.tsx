/**
 * Automatic OGLE-IV event search (Mróz et al. 2019, ApJS 244, 29) on the
 * loaded light curve. No window needed; each cut is shown with its OGLE value.
 */
import { useEffect, useState } from "react";
import { API_BASE } from "../api";
import type { LC } from "./types";
import ExofopBulkPanel, { IdentityFields, useExofopIdentity, type ExofopImage } from "../ExofopBulkPanel";
import { _buildRawFitsImages } from "./ExportToolbar";

export interface OgleExportMeta {
  tic_id: number | null;
  sector: number | null;
  provider: string | null;
}

interface Cut { name: string; value: number | null; passed: boolean | null; rule: string; ogle: number | null; why: string | null }
interface OgleReport {
  verdict: "ogle_event" | "fails_ogle_cuts" | "no_event";
  summary: string;
  passed: boolean;
  /** Set when slow stellar variability was removed before the (unchanged) OGLE search. */
  cleaning?: { note: string; window_d: number } | null;
  cuts: Cut[];
  fit: null | { t0: number; u0: number; tE: number; f_s: number; F_s: number; F_b: number; chi2_dof: number; tE_longer_than_data: boolean };
  bumps: { t_start: number; t_end: number; t_peak: number; chi2_3plus: number }[];
  /** Matplotlib PNG (base64): analysed light curve, bumps, event window, PSPL fit, residuals. */
  figure?: string;
  event_window?: { t_start: number; t_end: number };
  paper: { citation: string; ads: string; builds_on: string[] };
}

const LABELS: Record<string, string> = {
  chi2_3plus_min: "Bump strength (χ²₃₊)", chi2_out_max: "Flat outside the event", n_DIA: "Seen on difference images",
  n_bump: "Only one brightening", amplitude_mag_min: "Brightening amplitude", chi2_fit_max: "PSPL fit quality",
  chi2_fit_tE_max: "Fit quality near the peak", t0_in_data: "Peak inside the data", u0_max: "Impact parameter u₀",
  tE_max_d: "Einstein time tE", blend_floor: "Blend flux", fs_min: "Source fraction f_s",
};

const fmt = (v: number | null) => (v == null ? "—" : Math.abs(v) >= 100 ? v.toFixed(0) : v.toPrecision(3));

export function OglePanel({ lc, meta, ffiPng }: { lc: LC; meta: OgleExportMeta; ffiPng: string | null }) {
  const [rep, setRep] = useState<OgleReport | null>(null);
  const identity = useExofopIdentity();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { setRep(null); setErr(null); }, [lc]);

  const run = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await fetch(`${API_BASE}/api/microlensing/ogle_search`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ time: lc.time, flux: lc.flux, flux_err: lc.flux_err }),
      });
      if (!r.ok) throw new Error((await r.json()).detail ?? `HTTP ${r.status}`);
      setRep(await r.json());
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };

  const zipImages: ExofopImage[] = rep ? [
    ...(rep.figure ? [{ key: "ogle-gui-search", label: `OGLE GUI event search: light curve${rep.fit ? ", PSPL fit and residuals" : ""}`,
      b64: rep.figure, code: "L" }] : []),
    ...(ffiPng ? [{ key: "ffi-gaia-cutout", label: "TESScut FFI + Gaia DR3 overlay", b64: ffiPng, code: "O" }] : []),
  ] : [];

  const tone = rep?.verdict === "ogle_event" ? "bg-jade-100 text-jade-800 border-jade-300"
    : rep?.verdict === "no_event" ? "bg-ink-100 text-ink-700 border-ink-200" : "bg-apricot-100 text-apricot-800 border-apricot-300";

  return (
    <section className="card p-5 space-y-3">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h3 className="text-lg font-bold">Step 1: automatic OGLE event search</h3>
          <p className="text-sm text-muted max-w-2xl">
            Scans the whole light curve with the OGLE-IV event selection, the same cuts OGLE used to pick 8,000
            bulge events. No need to mark a window.
          </p>
        </div>
        <button className="btn-accent" onClick={run} disabled={busy}>{busy ? "Searching…" : "Run OGLE search"}</button>
      </div>
      {err && <p role="alert" className="text-sm text-coral-700">{err}</p>}
      {rep && (
        <div className="space-y-3">
          <div className={`rounded-lg border p-3 ${tone}`}>
            <div className="font-display text-lg">
              {rep.verdict === "ogle_event" ? "✓ Microlensing-like event" : rep.verdict === "no_event" ? "No event found" : "! Event fails OGLE cuts"}
            </div>
            <p className="text-sm">{rep.summary}</p>
            {rep.cleaning && <p className="text-xs mt-1 opacity-80">Light curve cleaned first: {rep.cleaning.note}</p>}
          </div>
          {rep.figure && (
            <figure className="space-y-1">
              <img src={`data:image/png;base64,${rep.figure}`} className="w-full rounded border border-line bg-white"
                   alt="Light curve analysed by the OGLE search, with strong bumps, the event window, the PSPL fit and residuals" />
              <figcaption className="text-xs text-muted">
                Black: each cadence of the light curve OGLE analysed{rep.cleaning ? " (slow variability removed)" : ""}.
                Red points: 30-minute bins. ▼ marks brightenings strong enough to count (χ²₃₊ ≥ 32).
                {rep.event_window ? " Shaded: the event window." : ""}
                {rep.fit ? " Orange line: the PSPL fit. Lower panel: data minus fit." : ""}
              </figcaption>
            </figure>
          )}
          {rep.fit && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-sm">
              {[["t₀ (BTJD)", rep.fit.t0], ["tE (days)", rep.fit.tE], ["u₀", rep.fit.u0], ["χ²/dof", rep.fit.chi2_dof]].map(([k, v]) => (
                <div key={k as string} className="rounded-md border border-line bg-surface-muted p-2">
                  <div className="text-xs text-muted">{k}</div><div className="font-mono">{fmt(v as number)}</div>
                </div>
              ))}
            </div>
          )}
          {rep.fit?.tE_longer_than_data && (
            <p className="text-xs text-apricot-700">tE is longer than the light curve, so it is poorly constrained by one sector.</p>
          )}
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="text-left text-xs text-muted">
                <th className="py-1 pr-3">OGLE cut</th><th className="pr-3">Value</th><th className="pr-3">Rule</th><th>Result</th>
              </tr></thead>
              <tbody>
                {rep.cuts.map((c) => (
                  <tr key={c.name} className="border-t border-line align-top" title={c.why ?? ""}>
                    <td className="py-1.5 pr-3">{LABELS[c.name] ?? c.name}</td>
                    <td className="pr-3 font-mono">{fmt(c.value)}</td>
                    <td className="pr-3 text-xs text-muted">{c.rule}</td>
                    <td>
                      <span className={`chip ${c.passed == null ? "bg-ink-100 text-ink-600" : c.passed ? "bg-jade-100 text-jade-800" : "bg-coral-100 text-coral-800"}`}>
                        {c.passed == null ? "n/a" : c.passed ? "✓ pass" : "✕ fail"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex justify-between items-center flex-wrap gap-2">
            <p className="text-xs text-muted">
              Method: <a href={rep.paper.ads} target="_blank" rel="noreferrer">{rep.paper.citation}</a>. Hover a row for how it was adapted to TESS.
            </p>

          </div>
        </div>
      )}
      {rep && meta.tic_id != null && (
        <div className="space-y-2 border-t border-line pt-3">
          <h4 className="font-semibold">Save for ExoFOP</h4>
          <div className="flex flex-wrap gap-2 items-end text-xs">
            <IdentityFields identity={identity} />
          </div>
          <ExofopBulkPanel identity={identity} tagDescription="Vetstar-OGLE-GUI"
            ticId={meta.tic_id} sector={meta.sector ?? undefined} images={zipImages}
            title="⬇ Build ExoFOP-TESS ZIP: OGLE plot and FFI cutout" />
          {meta.sector != null && (
            <ExofopBulkPanel identity={identity} tagDescription="Vetstar-OGLE-GUI" defaultCounter="002"
              ticId={meta.tic_id} sector={meta.sector}
              images={_buildRawFitsImages(meta.tic_id, meta.sector, meta.provider)}
              title="⬇ Build ExoFOP-TESS ZIP of the raw light-curve FITS" />
          )}
        </div>
      )}
    </section>
  );
}
