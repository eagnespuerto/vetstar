import { useEffect, useState } from "react";

/** Spinning gear icon + rotating status message — the same visual pattern the
 *  Transit tab uses so long-running work never looks like it stalled. */
export function CyclingLoader({
  messages,
  intervalMs = 2200,
  className = "",
}: {
  messages: string[];
  intervalMs?: number;
  className?: string;
}) {
  const [i, setI] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setI((n) => (n + 1) % messages.length), intervalMs);
    return () => clearInterval(id);
  }, [messages, intervalMs]);
  return (
    <span className={`inline-flex items-center ${className}`}>
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 640 640"
        className="w-3.5 h-3.5 mr-2 shrink-0 block animate-spin"
        fill="currentColor"
        aria-hidden="true"
      >
        <path d="M272 112C272 85.5 293.5 64 320 64C346.5 64 368 85.5 368 112C368 138.5 346.5 160 320 160C293.5 160 272 138.5 272 112zM272 528C272 501.5 293.5 480 320 480C346.5 480 368 501.5 368 528C368 554.5 346.5 576 320 576C293.5 576 272 554.5 272 528zM112 272C138.5 272 160 293.5 160 320C160 346.5 138.5 368 112 368C85.5 368 64 346.5 64 320C64 293.5 85.5 272 112 272zM480 320C480 293.5 501.5 272 528 272C554.5 272 576 293.5 576 320C576 346.5 554.5 368 528 368C501.5 368 480 346.5 480 320zM139 433.1C157.8 414.3 188.1 414.3 206.9 433.1C225.7 451.9 225.7 482.2 206.9 501C188.1 519.8 157.8 519.8 139 501C120.2 482.2 120.2 451.9 139 433.1zM139 139C157.8 120.2 188.1 120.2 206.9 139C225.7 157.8 225.7 188.1 206.9 206.9C188.1 225.7 157.8 225.7 139 206.9C120.2 188.1 120.2 157.8 139 139zM501 433.1C519.8 451.9 519.8 482.2 501 501C482.2 519.8 451.9 519.8 433.1 501C414.3 482.2 414.3 451.9 433.1 433.1C451.9 414.3 482.2 414.3 501 433.1z" />
      </svg>
      {messages[i]}
    </span>
  );
}

// Microlensing-flavoured message packs. Kept alongside the Transit tab's
// packs so both pipelines share the same rotating-status personality.

export const MICROLENSING_FIT_MSGS = [
  "Solving Paczyński's magnification…",
  "Weighing the flare against the lens…",
  "Bisecting the magnification profile…",
  "Chasing the peak across five tE…",
  "Reconciling blending and baseline…",
  "Convincing scipy to converge…",
  "Comparing BICs like a bouncer with a clipboard…",
  "Folding residuals about t0…",
];

export const MICROLENSING_JOINT_MSGS = [
  "Handshaking TESS with Gaia…",
  "Sharing t0 across two bands…",
  "Reconciling five years of Gaia with 27 days of TESS…",
  "Breaking the tE ↔ u0 degeneracy the hard way…",
  "Chasing the K-dwarf lens through the plane…",
  "Squinting at both light curves at once…",
];

export const GAIA_FETCH_MSGS = [
  "Hitching a ride on gsaweb.ast.cam.ac.uk…",
  "Coaxing the CSV out of Gaia Alerts…",
  "Inflating errors the Kruszyńska way…",
  "Filtering 99.999 sentinel rows…",
];

export const GAIA_SEARCH_MSGS = [
  "Sweeping the Alerts master index…",
  "Haversine-ing around the target…",
  "Ranking hits by separation…",
];

export const TIC_LC_MSGS = [
  "Asking MAST politely for a SPOC LC…",
  "Walking sectors newest-first…",
  "Dodging DVT-only sectors…",
  "Downloading FITS from Baltimore…",
  "Extracting PDCSAP flux column…",
];

export const COORD_LC_MSGS = [
  "Resolving RA/Dec to the nearest TIC…",
  "Querying MAST Catalogs…",
  "Then walking sectors newest-first…",
  "Downloading the FITS from Baltimore…",
];

export const COVERAGE_MSGS = [
  "Sending coords through tess-point…",
  "Cross-checking against the sector calendar…",
  "Flagging bulge blind-zone entries…",
];

export const PDF_MSGS = [
  "Asking an alien for the write-up…",
  "Rendering plots into PDF goodness…",
  "Stamping the report with cosmic credentials…",
  "Binding the report with hyperlinks…",
];

export const FFI_CUTOUT_MSGS = [
  "Bribing TESScut for a stamp…",
  "Median-stacking the FFI cadences…",
  "Cross-matching Gaia sources within the FOV…",
  "Painting neighbour stars onto pixels…",
];
