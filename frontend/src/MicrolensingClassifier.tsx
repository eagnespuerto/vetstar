import { useEffect, useState } from "react";
import { OglePanel } from "./microlensing/OglePanel";
import { fetchLightcurveByCoords, fetchLightcurveByTic, type LcByCoordsResponse } from "./api";
import { fetchMicrolensingFfi, type FfiCutoutResponse } from "./api";
import type { ClassifierPrefill, LC } from "./microlensing/types";
import { parseCsv, parseJson, makeDemoPspl, makeDemoFlare } from "./microlensing/lightCurve";
import { PrefillBanner } from "./microlensing/PrefillBanner";
import { DataLoader } from "./microlensing/DataLoader";
import { FfiCutoutSection } from "./microlensing/GaiaPanels";

export type { ClassifierPrefill } from "./microlensing/types";

interface ClassifierProps {
  prefill?: ClassifierPrefill | null;
  onDismissPrefill?: () => void;
}

export default function MicrolensingClassifier({ prefill, onDismissPrefill }: ClassifierProps = {}) {
  const [lc, setLc] = useState<LC | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);

  const onFile = async (file: File) => {
    setLoadErr(null);
    try {
      const text = await file.text();
      const lower = file.name.toLowerCase();
      const parsed = lower.endsWith(".json") ? parseJson(text) : parseCsv(text);
      if (parsed.time.length < 12)
        throw new Error(`Need at least 12 data points; got ${parsed.time.length}.`);
      setLc({ ...parsed, label: `${parsed.label}: ${file.name}` });
    } catch (e: any) {
      setLoadErr(e.message || String(e));
    }
  };

  const loadDemo = (which: "pspl" | "flare") => {
    setLoadErr(null);
    setLc(which === "pspl" ? makeDemoPspl() : makeDemoFlare());
  };

  // TIC+sector fetch (parallels the transit tab's MAST mode).
  const fetchFromTic = async (ticId: number, sector: number | null) => {
    setLoadErr(null);
    try {
      const r = await fetchLightcurveByTic(ticId, sector);
      const bits = [
        `TIC ${r.tic_id}`,
        `S${String(r.sector).padStart(3, "0")}`,
        r.provider,
        `${r.n_cadences} pts`,
      ].filter(Boolean).join(" · ");
      setLc({
        time: r.time,
        flux: r.flux,
        flux_err: r.flux_err,
        label: `MAST fetch — ${bits}`,
      });
      // Piggyback on lastAutoload so the ExoFOP exporter picks up TIC/sector/provider.
      setLastAutoload({
        time: r.time, flux: r.flux, flux_err: r.flux_err,
        tic_id: r.tic_id, sector: r.sector,
        provider: r.provider, filename: r.filename, n_cadences: r.n_cadences,
        // These four have no meaning for a TIC-keyed fetch; supply neutral values
        // that satisfy the LcByCoordsResponse shape without misrepresenting the source.
        resolved_ra: 0, resolved_dec: 0, separation_arcsec: 0, tmag: null,
      });
    } catch (e: any) {
      setLoadErr(e.message || String(e));
    }
  };

  // FFI cutout + Gaia overlay (auto-fetched when target coords + sector known).
  const [ffiData, setFfiData] = useState<FfiCutoutResponse | null>(null);
  const [ffiBusy, setFfiBusy] = useState(false);
  const [ffiErr, setFfiErr] = useState<string | null>(null);


  const [autoloadBusy, setAutoloadBusy] = useState(false);
  // Kept around after the LC loads so the ExoFOP exporter can name the
  // package (TIC + sector + provider) even if the prefill was dismissed.
  const [lastAutoload, setLastAutoload] = useState<LcByCoordsResponse | null>(null);
  const autoloadFromCoords = async () => {
    if (!prefill) return;
    setAutoloadBusy(true); setLoadErr(null);
    try {
      const r = await fetchLightcurveByCoords({
        ra: prefill.ra,
        dec: prefill.dec,
        sector: prefill.sector,
      });
      const providerBits = [
        `TIC ${r.tic_id}`,
        `S${String(r.sector).padStart(3, "0")}`,
        r.provider,
        `${r.n_cadences} pts`,
        `Δ ${r.separation_arcsec.toFixed(1)}"`,
      ].filter(Boolean).join(" · ");
      setLc({
        time: r.time,
        flux: r.flux,
        flux_err: r.flux_err,
        label: `MAST autoload (${prefill.event_id}) — ${providerBits}`,
      });
      setLastAutoload(r);
    } catch (e: any) {
      setLoadErr(e.message || String(e));
    } finally {
      setAutoloadBusy(false);
    }
  };

  // Auto-fetch FFI cutout when we have coords + sector.
  const ffiFetchKey = `${lastAutoload?.resolved_ra ?? prefill?.ra ?? ""}` +
    `|${lastAutoload?.resolved_dec ?? prefill?.dec ?? ""}` +
    `|${lastAutoload?.sector ?? prefill?.sector ?? ""}` +
    `|${lastAutoload?.tic_id ?? ""}`;
  useEffect(() => {
    const ra = lastAutoload?.resolved_ra ?? prefill?.ra;
    const dec = lastAutoload?.resolved_dec ?? prefill?.dec;
    const sector = lastAutoload?.sector ?? prefill?.sector ?? null;
    const tic_id = lastAutoload?.tic_id ?? null;
    if (ra == null || dec == null) return;
    setFfiBusy(true); setFfiErr(null); setFfiData(null);
    let cancelled = false;
    fetchMicrolensingFfi({ ra, dec, sector, tic_id })
      .then((r) => { if (!cancelled) setFfiData(r); })
      .catch((e) => { if (!cancelled) setFfiErr(e.message || String(e)); })
      .finally(() => { if (!cancelled) setFfiBusy(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ffiFetchKey]);

  return (
    <section className="space-y-5">
      {prefill && (
        <PrefillBanner
          prefill={prefill}
          onDismiss={onDismissPrefill}
          lcLoaded={!!lc}
          onAutoload={autoloadFromCoords}
          autoloadBusy={autoloadBusy}
        />
      )}

      <DataLoader
        onFile={onFile}
        onDemo={loadDemo}
        onFetchTic={fetchFromTic}
        loadErr={loadErr}
        lcLabel={lc?.label}
        lcPoints={lc?.time.length ?? 0}
      />

      {lc && (
        <OglePanel
          lc={lc}
          ffiPng={ffiData?.image ?? null}
          meta={{
            tic_id: lastAutoload?.tic_id ?? null,
            sector: lastAutoload?.sector ?? prefill?.sector ?? null,
            provider: lastAutoload?.provider ?? null,
          }}
        />
      )}

      {(ffiData || ffiBusy || ffiErr) && (
        <FfiCutoutSection ffiData={ffiData} busy={ffiBusy} err={ffiErr} />
      )}

    </section>
  );
}

// ============================================================================
// Prefill banner — surfaces the target handed off from the Coverage table
// ============================================================================
