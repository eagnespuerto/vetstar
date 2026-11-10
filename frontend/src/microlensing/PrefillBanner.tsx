import { CyclingLoader, COORD_LC_MSGS } from "../Loaders";
import type { ClassifierPrefill } from "./types";

export function PrefillBanner({
  prefill, onDismiss, lcLoaded, onAutoload, autoloadBusy,
}: {
  prefill: ClassifierPrefill;
  onDismiss?: () => void;
  lcLoaded: boolean;
  onAutoload?: () => void;
  autoloadBusy?: boolean;
}) {
  // Encode a MAST portal search link for the RA/Dec so the user can pull the
  // FITS light curve manually without leaving the app.
  const mastUrl =
    `https://mast.stsci.edu/portal/Mashup/Clients/Mast/Portal.html?searchQuery=` +
    encodeURIComponent(`${prefill.ra} ${prefill.dec}`);
  return (
    <div className="rounded-lg border-l-4 border-blue-500 bg-blue-50 p-3 flex items-start justify-between gap-3">
      <div className="text-sm text-blue-900">
        <p className="font-semibold">
          Handed off from coverage table: <span className="font-mono">{prefill.event_id}</span>
        </p>
        <p className="text-xs mt-1 leading-relaxed">
          RA <span className="font-mono">{prefill.ra.toFixed(4)}</span>°, Dec{" "}
          <span className="font-mono">{prefill.dec.toFixed(4)}</span>° · t₀ ={" "}
          <span className="font-mono">{prefill.t0.toFixed(3)}</span> BTJD · tE ={" "}
          <span className="font-mono">{prefill.tE.toFixed(2)}</span> d
          {prefill.sector != null && (
            <>
              {" "}· TESS S{String(prefill.sector).padStart(3, "0")}
              {prefill.camera != null && `/cam${prefill.camera}/ccd${prefill.ccd}`}
            </>
          )}
        </p>
        <p className="text-xs mt-1">
          {lcLoaded
            ? "Selection window pre-set around t₀ ± 2·tE. Fine-tune by dragging, then fit."
            : "Pull the TESS light curve for this target automatically, or upload one manually below."}
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {onAutoload && (
            <button
              onClick={onAutoload}
              disabled={autoloadBusy}
              className="px-3 py-1 text-xs font-semibold bg-blue-600 text-white rounded hover:bg-blue-700 disabled:bg-slate-300 min-w-[11rem]"
              title={`Resolve RA=${prefill.ra.toFixed(4)}, Dec=${prefill.dec.toFixed(4)} → nearest TIC and download the light curve from MAST`}
            >
              {autoloadBusy
                ? <CyclingLoader messages={COORD_LC_MSGS} className="text-[10px]" />
                : (lcLoaded ? "Re-fetch from MAST" : "Fetch TESS light curve")}
            </button>
          )}
          <a
            href={mastUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-blue-700 underline"
          >
            Open on MAST portal →
          </a>
        </div>
      </div>
      {onDismiss && (
        <button
          onClick={onDismiss}
          className="text-xs text-blue-700 hover:underline shrink-0"
          title="Clear the handoff"
        >
          dismiss
        </button>
      )}
    </div>
  );
}

// ============================================================================
// Data loader UI
// ============================================================================
