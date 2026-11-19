/**
 * Temporary pause of the TESScut FFI + Gaia DR3 overlay while the Gaia ESA
 * Archive is down for Gaia DR4. Set GAIA_OVERLAY_PAUSED to false once DR4
 * is out (and the overlay has been checked against DR4).
 */
export const GAIA_OVERLAY_PAUSED = true;

const NOTICE_URL = "https://www.cosmos.esa.int/web/gaia/news";

export function GaiaPauseNotice() {
  return (
    <div className="rounded-lg border border-apricot-300 bg-apricot-100 text-apricot-800 p-3 text-sm" role="note">
      <b>TESScut FFI + Gaia DR3 overlay is paused for now.</b> The Gaia ESA Archive is down for Gaia DR4 from
      25 November 2026, 12:00 CET, and reopens with Gaia DR4 on 2 December 2026, 12:00 CET (
      <a href={NOTICE_URL} target="_blank" rel="noopener noreferrer">ESA Gaia news, 8 October 2026</a>
      ). The overlay comes back after the DR4 release. The OGLE search and the ExoFOP ZIPs work as usual, without
      the cutout.
    </div>
  );
}
