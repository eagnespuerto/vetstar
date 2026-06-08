"""
Vetting pipeline: BLS, Lomb-Scargle, centroid check, odd/even, secondary
eclipse search, shape analysis, verdict.

All functions are pure-ish — accept arrays + metadata, return dicts of
results.  Plotting helpers return base64-encoded PNGs so the API can ship
them straight to the frontend.
"""
from __future__ import annotations

import base64
import io
import math
from dataclasses import asdict, dataclass, field
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from astropy.timeseries import BoxLeastSquares, LombScargle
from scipy.ndimage import median_filter


# ----------------------------------------------------------------------
# External catalog cross-match (Gaia DR3 + SIMBAD)
# ----------------------------------------------------------------------
# Cone-search radius for matching a TIC position to a Gaia DR3 / SIMBAD
# entry. TESS pixels are ~21", so a few arcsec is plenty for an unambiguous
# match on the target star itself without dragging in nearby blends.
CROSSMATCH_RADIUS_ARCSEC = 5.0

# SIMBAD object-type prefixes that we treat as "known" classifications. The
# value is the verdict headline we substitute in when one is matched.
# Order matters: planet host beats binary beats variable, because a confirmed
# exoplanet host is the more specific (and more useful) label.
_SIMBAD_OTYPE_MAP = [
    # Exoplanet-related
    ("Pl",  "Known Planet"),         # confirmed exoplanet
    ("Pl?", "Known Planet Candidate"),
    # Eclipsing binaries
    ("EB*", "Known Eclipsing Binary"),
    ("Al*", "Known Eclipsing Binary (Algol)"),
    ("bL*", "Known Eclipsing Binary (β Lyr)"),
    ("WU*", "Known Eclipsing Binary (W UMa)"),
    # Other binaries
    ("SB*", "Known Spectroscopic Binary"),
    ("**",  "Known Multiple Star"),
    # Variables
    ("RR*", "Known RR Lyrae Variable"),
    ("Ce*", "Known Cepheid Variable"),
    ("dS*", "Known δ Scuti Variable"),
    ("Mi*", "Known Mira Variable"),
    ("V*",  "Known Variable Star"),
    ("CV*", "Known Cataclysmic Variable"),
]


def _gaia_nss_description(nss_flag) -> Optional[str]:
    """Translate a Gaia DR3 `non_single_star` integer into a human label.

    The DR3 field is a bitmask: 1=astrometric, 2=spectroscopic, 4=eclipsing.
    Combinations (e.g. 3 = astrometric+spectroscopic) are common. Returns
    None when the flag is 0 / missing (Gaia has no NSS solution).
    """
    try:
        flag = int(nss_flag)
    except (TypeError, ValueError):
        return None
    if flag <= 0:
        return None
    parts = []
    if flag & 1:
        parts.append("astrometric")
    if flag & 2:
        parts.append("spectroscopic")
    if flag & 4:
        parts.append("eclipsing")
    return ("Gaia DR3 NSS solution: " + " + ".join(parts)) if parts else None


def crossmatch_known_object(
    ra: Optional[float],
    dec: Optional[float],
    radius_arcsec: float = CROSSMATCH_RADIUS_ARCSEC,
) -> dict:
    """Cone-search Gaia DR3 and SIMBAD for a known object at (ra, dec).

    Returns ``{"available": False, ...}`` when coordinates are missing or
    every external query fails (offline, timeout, service down) — the
    caller should leave the pipeline verdict untouched in that case.

    A successful match returns:
        {
          "available": True,
          "matched":   True/False,
          "headline":  "Known Eclipsing Binary" | ...   (only if matched)
          "name":      "TIC 12345 / HD 6789"            (only if matched)
          "description": "Gaia DR3 NSS: eclipsing; SIMBAD otype EB*"
          "sources":   ["Gaia DR3", "SIMBAD"],
          "distance_arcsec": 0.7,
        }
    """
    if ra is None or dec is None or not np.isfinite(ra) or not np.isfinite(dec):
        return {"available": False, "reason": "no coordinates"}

    # Lazy imports so a missing/offline astroquery doesn't break unit tests
    # of the rest of the pipeline.
    try:
        from astropy import units as u
        from astropy.coordinates import SkyCoord
    except Exception as exc:  # pragma: no cover
        return {"available": False, "reason": f"astropy import failed: {exc}"}

    coord = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs")
    radius = radius_arcsec * u.arcsec

    sources_tried = []
    gaia_match = None
    simbad_match = None

    # ----- Gaia DR3 ----------------------------------------------------
    try:
        from astroquery.gaia import Gaia
        sources_tried.append("Gaia DR3")
        Gaia.ROW_LIMIT = 5
        # gaiadr3.gaia_source contains non_single_star; we also pull the
        # in_qso_candidates / in_galaxy_candidates flags so we can flag
        # non-stellar matches.
        adql = (
            "SELECT TOP 1 source_id, ra, dec, phot_g_mean_mag, "
            "non_single_star, "
            "DISTANCE(POINT('ICRS', ra, dec), "
            f"POINT('ICRS', {ra}, {dec})) * 3600 AS dist_arcsec "
            "FROM gaiadr3.gaia_source "
            f"WHERE 1=CONTAINS(POINT('ICRS', ra, dec), "
            f"CIRCLE('ICRS', {ra}, {dec}, {radius_arcsec / 3600.0})) "
            "ORDER BY dist_arcsec ASC"
        )
        job = Gaia.launch_job_async(adql)
        tbl = job.get_results()
        if len(tbl) > 0:
            row = tbl[0]
            nss_desc = _gaia_nss_description(row["non_single_star"])
            gaia_match = {
                "source_id": int(row["source_id"]),
                "dist_arcsec": float(row["dist_arcsec"]),
                "g_mag": float(row["phot_g_mean_mag"]) if row["phot_g_mean_mag"] is not None else None,
                "nss_flag": int(row["non_single_star"]) if row["non_single_star"] is not None else 0,
                "nss_description": nss_desc,
            }
    except Exception as exc:  # network/service issue: keep going
        gaia_match = {"error": str(exc)}

    # ----- SIMBAD ------------------------------------------------------
    try:
        from astroquery.simbad import Simbad
        sources_tried.append("SIMBAD")
        sb = Simbad()
        sb.TIMEOUT = 15
        sb.add_votable_fields("otype", "ids")
        tbl = sb.query_region(coord, radius=radius)
        if tbl is not None and len(tbl) > 0:
            row = tbl[0]
            main_id = str(row["MAIN_ID"]) if "MAIN_ID" in row.colnames else None
            otype = str(row["OTYPE"]) if "OTYPE" in row.colnames else ""
            ids = str(row["IDS"]) if "IDS" in row.colnames else ""
            simbad_match = {
                "main_id": main_id,
                "otype": otype,
                "ids": ids,
            }
    except Exception as exc:
        simbad_match = {"error": str(exc)}

    # ----- Decide whether this counts as a "known" classification -----
    headline = None
    description_bits = []
    name_bits = []

    if simbad_match and "otype" in simbad_match:
        otype = simbad_match["otype"]
        for prefix, label in _SIMBAD_OTYPE_MAP:
            if otype.startswith(prefix):
                headline = label
                description_bits.append(f"SIMBAD object type '{otype}'")
                break
        if simbad_match.get("main_id"):
            name_bits.append(simbad_match["main_id"])

    if gaia_match and gaia_match.get("nss_description"):
        # Gaia NSS solution always implies binarity. Only upgrade the
        # headline if SIMBAD didn't already give us something more
        # specific (e.g. confirmed planet host).
        if headline is None or headline.startswith("Known Variable"):
            headline = "Known Binary"
        description_bits.append(gaia_match["nss_description"])
        name_bits.append(f"Gaia DR3 {gaia_match['source_id']}")

    matched = headline is not None
    out = {
        "available": True,
        "matched": matched,
        "sources_tried": sources_tried,
        "gaia": gaia_match,
        "simbad": simbad_match,
    }
    if matched:
        out["headline"] = headline
        out["name"] = " / ".join(name_bits) if name_bits else None
        out["description"] = "; ".join(description_bits)
        # Closest-match distance (Gaia is sub-arcsec accurate; SIMBAD has
        # no distance in this minimal query, so prefer Gaia's).
        if gaia_match and "dist_arcsec" in gaia_match:
            out["distance_arcsec"] = gaia_match["dist_arcsec"]
    return out


# ----------------------------------------------------------------------
# Tolerances
# ----------------------------------------------------------------------
# How far two transit/eclipse durations may differ (in HOURS) and still be
# treated as "the same" event — across cycles within a sector, or across
# sectors in a multi-sector run. Mature vetting pipelines allow a small
# slop here so that noise-driven width differences are NOT mistaken for the
# unequal primary/secondary durations that betray an eclipsing binary.
# Tune this if real signals are being over-flagged as EBs.
DURATION_MATCH_TOL_H = 0.05  # hours

# Fractional tolerance for calling two periods "the same" across sectors.
PERIOD_MATCH_TOL_FRAC = 0.02  # 2%

# Companion-size thresholds used by the verdict. A radius just above the
# planetary cap is BORDERLINE — mature pipelines do not call an eclipsing
# binary on size alone; they need an eclipse signature (secondary / odd-even)
# or an RV mass. Only a radius well above the cap is treated as unambiguously
# stellar/brown-dwarf sized.
COMPANION_PLANET_CAP_RJUP = 2.2   # largest plausible (inflated) giant planet
COMPANION_EB_HARD_RJUP = 4.0      # above this: unambiguously not a planet


def durations_consistent(durations_h, tol_h: float = DURATION_MATCH_TOL_H) -> bool:
    """True if all finite durations (hours) lie within ``tol_h`` of their median.

    A single value (or none) is trivially consistent. This is the absolute
    test used when the durations being compared are already robust medians
    (e.g. odd-vs-even transit durations, or one representative event per
    sector) rather than raw, noise-broadened single-event widths.
    """
    arr = np.asarray(
        [d for d in durations_h if d is not None and np.isfinite(d)], dtype=float
    )
    if arr.size < 2:
        return True
    return bool(np.all(np.abs(arr - np.median(arr)) <= tol_h))


def periods_consistent(periods_d, tol_frac: float = PERIOD_MATCH_TOL_FRAC) -> bool:
    """True if all finite periods agree to within ``tol_frac`` of their median."""
    arr = np.asarray(
        [p for p in periods_d if p is not None and np.isfinite(p) and p > 0],
        dtype=float,
    )
    if arr.size < 2:
        return True
    med = np.median(arr)
    return bool(med > 0 and np.all(np.abs(arr - med) / med <= tol_frac))


# ----------------------------------------------------------------------
# Data containers
# ----------------------------------------------------------------------
@dataclass
class StarInfo:
    tic_id: Optional[int] = None
    tmag: Optional[float] = None
    teff: Optional[float] = None
    radius: Optional[float] = None        # R_sun
    logg: Optional[float] = None
    mass: Optional[float] = None          # M_sun (often derived)
    ra: Optional[float] = None
    dec: Optional[float] = None
    sector: Optional[int] = None
    camera: Optional[int] = None
    ccd: Optional[int] = None
    crowdsap: Optional[float] = None
    source: str = "unknown"               # "fits" or "exofop_json"


@dataclass
class VettingResult:
    star: StarInfo
    summary: dict = field(default_factory=dict)
    bls: dict = field(default_factory=dict)
    lomb_scargle: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    centroid: dict = field(default_factory=dict)
    odd_even: dict = field(default_factory=dict)
    secondary: dict = field(default_factory=dict)
    shape: dict = field(default_factory=dict)
    physics: dict = field(default_factory=dict)
    verdict: dict = field(default_factory=dict)
    known_object: dict = field(default_factory=dict)
    detrend: dict = field(default_factory=dict)        # NEW
    sensitivity: dict = field(default_factory=dict)    # NEW (echo applied thresholds)
    plots: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["star"] = asdict(self.star)
        return d


# ----------------------------------------------------------------------
# Plot helpers
# ----------------------------------------------------------------------
def _fig_to_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ----------------------------------------------------------------------
# Core pipeline
# ----------------------------------------------------------------------
def clean_lightcurve(
    t: np.ndarray,
    flux: np.ndarray,
    flux_err: np.ndarray,
    quality: Optional[np.ndarray] = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Standard quality-flag mask + finite filtering + normalisation."""
    mask = np.isfinite(t) & np.isfinite(flux) & (flux > 0)
    if quality is not None:
        mask &= quality == 0
    if flux_err is not None:
        mask &= np.isfinite(flux_err)
    t, flux = t[mask], flux[mask]
    fe = flux_err[mask] if flux_err is not None else np.full_like(flux, np.nanstd(flux))
    med = np.nanmedian(flux)
    return t, flux / med, fe / med


def run_lomb_scargle(t, f, fe, p_min=0.1, p_max=20.0) -> dict:
    ls = LombScargle(t, f, fe)
    freq, power = ls.autopower(
        minimum_frequency=1 / p_max,
        maximum_frequency=1 / p_min,
        samples_per_peak=15,
    )
    periods = 1 / freq
    # FAP for the top peak
    ip = int(np.argmax(power))
    try:
        fap = float(ls.false_alarm_probability(power[ip]))
    except Exception:
        fap = None
    # 5 well-separated peaks
    order = np.argsort(power)[::-1]
    tops = []
    for idx in order:
        p = float(periods[idx])
        if all(abs(p - tp["period"]) / tp["period"] > 0.05 for tp in tops):
            tops.append({"period": p, "power": float(power[idx])})
        if len(tops) >= 5:
            break
    return {
        "top_period": float(periods[ip]),
        "top_power": float(power[ip]),
        "false_alarm_prob": fap,
        "top_peaks": tops,
    }


# Default fractional half-width of the BLS search window around a known
# period, e.g. 0.02 means scan periods in [P*0.98, P*1.02].
BLS_KNOWN_PERIOD_TOL_FRAC = 0.02


def run_bls_constrained(
    t, f, fe,
    known_period_days: float,
    tol_frac: float = BLS_KNOWN_PERIOD_TOL_FRAC,
    n_periods: int = 4000,
    search_harmonics: bool = True,
) -> dict:
    """BLS over a narrow window around a known period plus P/2 and 2P harmonics.

    Returns the same dict shape as :func:`run_bls` plus three extra keys:
    ``constrained``, ``known_period_input_days``, and ``matched_harmonic``
    (one of ``"P"``, ``"P/2"``, ``"2P"``).
    """
    span = float(t.max() - t.min())
    p_max_blind = max(0.5, span * 0.7)

    sub_grids = [("P", known_period_days)]
    if search_harmonics:
        if known_period_days / 2.0 >= 0.5:
            sub_grids.append(("P/2", known_period_days / 2.0))
        if 2.0 * known_period_days <= p_max_blind:
            sub_grids.append(("2P", 2.0 * known_period_days))

    per_grid = max(200, n_periods // len(sub_grids))
    grid_periods = []
    grid_labels = []
    for label, p in sub_grids:
        lo = p * (1.0 - tol_frac)
        hi = p * (1.0 + tol_frac)
        ps = np.linspace(lo, hi, per_grid)
        grid_periods.append(ps)
        grid_labels.append((label, ps))

    periods = np.concatenate(grid_periods)
    durations = np.array([0.05, 0.1, 0.15, 0.2, 0.3])
    bls = BoxLeastSquares(t, f, fe)
    res = bls.power(periods, durations)
    ib = int(np.argmax(res.power))

    best_p = float(res.period[ib])
    matched = "P"
    for label, ps in grid_labels:
        if ps[0] <= best_p <= ps[-1]:
            matched = label
            break

    _std = float(np.std(res.power))
    sde = float((res.power[ib] - np.median(res.power)) / _std) if _std > 0 else 0.0
    return {
        "period": best_p,
        "t0": float(res.transit_time[ib]),
        "duration": float(res.duration[ib]),
        "depth": float(res.depth[ib]),
        "power": float(res.power[ib]),
        "sde": sde,
        "n_transits_in_window": int(
            np.floor((t.max() - res.transit_time[ib]) / res.period[ib])
            - np.ceil((t.min() - res.transit_time[ib]) / res.period[ib])
            + 1
        ),
        "_periodogram": {
            "periods": periods.tolist()[::20],
            "power": res.power.tolist()[::20],
        },
        "constrained": True,
        "known_period_input_days": float(known_period_days),
        "matched_harmonic": matched,
    }


def run_bls(
    t, f, fe, p_min=0.5, p_max=None, n_periods=20000
) -> dict:
    span = float(t.max() - t.min())
    if p_max is None:
        p_max = max(p_min * 2, span * 0.7)
    durations = np.array([0.05, 0.1, 0.15, 0.2, 0.3])
    bls = BoxLeastSquares(t, f, fe)
    periods = np.linspace(p_min, p_max, n_periods)
    res = bls.power(periods, durations)
    ib = int(np.argmax(res.power))
    _std = float(np.std(res.power))
    sde = float((res.power[ib] - np.median(res.power)) / _std) if _std > 0 else 0.0
    return {
        "period": float(res.period[ib]),
        "t0": float(res.transit_time[ib]),
        "duration": float(res.duration[ib]),
        "depth": float(res.depth[ib]),
        "power": float(res.power[ib]),
        "sde": sde,
        "n_transits_in_window": int(
            np.floor((t.max() - res.transit_time[ib]) / res.period[ib])
            - np.ceil((t.min() - res.transit_time[ib]) / res.period[ib])
            + 1
        ),
        "_periodogram": {
            "periods": periods.tolist()[::20],     # sub-sample for transport
            "power": res.power.tolist()[::20],
        },
    }


def detect_events(t, f, threshold=0.997, min_pts=10, min_snr=4.0, max_gap=5) -> list:
    """Direct event detection — finds discrete dips regardless of period.

    Uses a two-pass approach:
      Pass 1 — measure the star's photometric scatter from out-of-dip data.
      Pass 2 — flag contiguous stretches where smoothed flux drops below
               **the greater of** the user's absolute threshold AND an
               adaptive threshold = baseline − min_snr × scatter.

    The adaptive threshold is the key change: for a quiet star with
    scatter = 0.0002, a 3σ dip is only 0.06% deep — well above the old
    fixed 0.997 threshold. The adaptive approach catches it. For a noisy
    star the fixed threshold still acts as a floor.

    Each candidate event must also pass a per-event SNR check. That check
    uses the *integrated* significance of the dip, not a single point:

        snr = mean_in_transit_depth × sqrt(n_points) / scatter

    The sqrt(n) factor is essential. A real but shallow transit on a noisy
    star can have a per-point depth ≈ the scatter (point SNR ≈ 1) yet still
    be hugely significant once you average over the dozens of in-transit
    points (integrated SNR ≈ 10). The old check divided a single point's
    depth by the scatter, so it threw away every shallow-on-noisy transit —
    e.g. an 0.8%-deep, ~5 h transit on a star with 0.53% point scatter
    scored ~1.5 and was rejected despite being a clear, repeating signal.

    Contiguous in-dip stretches separated by only a few points (median-filter
    noise can briefly lift the smoothed flux back over the threshold mid-dip)
    are bridged via ``max_gap`` so one transit yields one event, not two.
    """
    fs = median_filter(f, size=21)
    baseline = float(np.nanmedian(fs))

    # Identify real time gaps so we don't (a) flag points whose smoothed flux
    # was dragged down by the median filter reaching across the gap, or
    # (b) merge two separate in-dip runs across the gap into one fake event.
    # A "gap" is any sample-to-sample dt much larger than the typical cadence.
    t_arr = np.asarray(t)
    dt = np.diff(t_arr)
    pos_dt = dt[dt > 0]
    if pos_dt.size:
        cadence = float(np.median(pos_dt))
        gap_threshold = max(5 * cadence, 0.2)  # days; ~5x cadence or 4.8 h
        # gap_after[i] is True if there is a large gap between sample i and i+1
        gap_after = np.concatenate([dt > gap_threshold, [False]])
    else:
        gap_after = np.zeros(len(t_arr), dtype=bool)
        cadence = 0.0

    # --- Pass 1: measure local scatter from non-dip data ---
    # Use a rough cut: anything within 2× the raw MAD of median is "baseline"
    raw_mad = float(1.4826 * np.nanmedian(np.abs(f - baseline)))
    rough_mask = np.abs(fs - baseline) < 3 * max(raw_mad, 1e-6)
    if rough_mask.sum() > 100:
        scatter = float(1.4826 * np.nanmedian(np.abs(f[rough_mask] - np.nanmedian(f[rough_mask]))))
    else:
        scatter = raw_mad
    if scatter <= 0:
        scatter = 1e-5

    # --- Pass 2: adaptive threshold ---
    # The threshold is the HIGHER (less strict) of:
    #   (a) the user-supplied absolute threshold (e.g. 0.997 = 0.3% below baseline)
    #   (b) baseline − min_snr × scatter (adapts to the star's actual noise)
    # This way quiet stars get a sensitive threshold automatically, while the
    # absolute threshold still caps things for noisy stars.
    adaptive_threshold = baseline - min_snr * scatter
    effective_threshold = max(threshold, adaptive_threshold)

    in_dip = fs < effective_threshold

    # Suppress samples adjacent to a large time gap: the size-21 median filter
    # can pull edge points well below baseline by mixing in unrelated flux from
    # the other side of the gap (or the gap edge itself), producing a spurious
    # "dip" that just traces the discontinuity.
    edge_pad = 10  # half the median filter window
    n = len(in_dip)
    gap_idx = np.where(gap_after)[0]
    for gi in gap_idx:
        lo = max(0, gi - edge_pad + 1)
        hi = min(n, gi + edge_pad + 1)
        in_dip[lo:hi] = False

    # Bridge short gaps: fill runs of <= max_gap "out" points that sit between
    # two "in" stretches, so median-filter noise doesn't split one transit.
    if max_gap > 0:
        i = 0
        while i < n:
            if not in_dip[i]:
                start = i
                while i < n and not in_dip[i]:
                    i += 1
                # gap is [start, i); bridge only if flanked on both sides and
                # the bridged region does not straddle a real time gap.
                if (
                    0 < start
                    and i < n
                    and (i - start) <= max_gap
                    and not gap_after[start - 1 : i].any()
                ):
                    in_dip[start:i] = True
            else:
                i += 1

    events = []
    i = 0
    while i < len(t):
        if in_dip[i]:
            start = i
            while i < len(t) and in_dip[i]:
                # Stop a run at a real time gap — a single event cannot span
                # a multi-hour data outage.
                if gap_after[i]:
                    i += 1
                    break
                i += 1
            end = i
            if end - start >= min_pts:
                seg = f[start:end]
                n_pts = end - start
                seg_min = float(fs[start:end].min())
                depth = float(baseline - seg_min)              # max depth (reporting)
                mean_depth = float(baseline - np.nanmean(seg))  # avg depth (significance)
                # Integrated SNR: average dip depth grows in significance with
                # sqrt(number of in-transit points). This is what lets a shallow
                # transit on a noisy star clear the bar.
                snr = mean_depth * np.sqrt(n_pts) / scatter if scatter > 0 else 0.0
                if snr >= min_snr and mean_depth > 0:
                    events.append(
                        {
                            "t_start": float(t[start]),
                            "t_end": float(t[end - 1]),
                            "duration_d": float(t[end - 1] - t[start]),
                            "min_flux": seg_min,
                            "depth": depth,
                            "depth_snr": float(snr),
                            "n_points": int(n_pts),
                        }
                    )
        else:
            i += 1

    return events


def centroid_check(
    t, mom_x, mom_y, t_start, t_end, pad=0.5
) -> dict:
    """Compare in-event vs out-of-event centroids."""
    in_mask = (t >= t_start) & (t <= t_end)
    oot_mask = ((t >= t_start - pad) & (t < t_start)) | (
        (t > t_end) & (t <= t_end + pad)
    )
    if in_mask.sum() < 5 or oot_mask.sum() < 5:
        return {"available": False}
    mx_oot, my_oot = np.median(mom_x[oot_mask]), np.median(mom_y[oot_mask])
    mx_in, my_in = np.median(mom_x[in_mask]), np.median(mom_y[in_mask])
    mx_std = max(np.std(mom_x[oot_mask]), 1e-6)
    my_std = max(np.std(mom_y[oot_mask]), 1e-6)
    return {
        "available": True,
        "shift_col_px": float(mx_in - mx_oot),
        "shift_row_px": float(my_in - my_oot),
        "shift_col_sigma": float((mx_in - mx_oot) / mx_std),
        "shift_row_sigma": float((my_in - my_oot) / my_std),
        "on_target": bool(
            abs((mx_in - mx_oot) / mx_std) < 3
            and abs((my_in - my_oot) / my_std) < 3
        ),
    }


def odd_even_check(t, f, period, t0, duration, odd_even_sigma: float = 3.0) -> dict:
    """Compare even- vs odd-numbered transits.  Big depth difference -> EB.

    ``odd_even_sigma`` is the user-tunable EB-flag threshold (default 3σ).
    """
    if period is None or period <= 0:
        return {"available": False, "reason": "no period"}
    # phase folded; cycle number
    cycle = np.round((t - t0) / period).astype(int)
    half = duration * 0.7
    in_tr = np.abs((t - t0 - cycle * period)) < half
    if in_tr.sum() < 5:
        return {"available": False, "reason": "no in-transit points"}
    odd = in_tr & (cycle % 2 == 1)
    even = in_tr & (cycle % 2 == 0)
    if odd.sum() < 3 or even.sum() < 3:
        return {
            "available": False,
            "reason": "insufficient odd or even transits",
            "n_odd": int(odd.sum()),
            "n_even": int(even.sum()),
        }
    d_odd = 1.0 - np.median(f[odd])
    d_even = 1.0 - np.median(f[even])
    err_odd = np.std(f[odd]) / np.sqrt(odd.sum())
    err_even = np.std(f[even]) / np.sqrt(even.sum())
    diff = d_odd - d_even
    diff_err = math.sqrt(err_odd**2 + err_even**2)
    sigma = abs(diff) / diff_err if diff_err > 0 else 0
    return {
        "available": True,
        "depth_odd": float(d_odd),
        "depth_even": float(d_even),
        "difference": float(diff),
        "sigma": float(sigma),
        "n_odd": int(odd.sum()),
        "n_even": int(even.sum()),
        "flag_eb": bool(sigma > odd_even_sigma),
        "threshold_sigma": float(odd_even_sigma),
    }


def secondary_eclipse_search(t, f, period, t0, duration, secondary_sigma: float = 3.0) -> dict:
    """Look at phase 0.5 for a secondary dip.

    ``secondary_sigma`` is the user-tunable detection threshold (default 3σ).
    """
    if period is None or period <= 0:
        return {"available": False, "reason": "no period"}
    phase = ((t - t0) / period) % 1.0
    in_sec = np.abs(phase - 0.5) < (duration / period / 1.5)
    if in_sec.sum() < 5:
        return {"available": False, "reason": "no phase-0.5 coverage"}
    f_sec = np.median(f[in_sec])
    oot = (phase > 0.2) & (phase < 0.4) | (phase > 0.6) & (phase < 0.8)
    if oot.sum() < 10:
        return {"available": False, "reason": "no oot baseline"}
    baseline = np.median(f[oot])
    depth = baseline - f_sec
    noise = np.std(f[oot]) / np.sqrt(in_sec.sum())
    sigma = depth / noise if noise > 0 else 0
    return {
        "available": True,
        "depth": float(depth),
        "sigma": float(sigma),
        "detected": bool(sigma > secondary_sigma),
        "threshold_sigma": float(secondary_sigma),
    }


def measure_shape(t, f, t_start, t_end) -> dict:
    """Estimate ingress/egress and flat-bottom durations from a single event."""
    pad = 0.3 * (t_end - t_start)
    m = (t > t_start - pad) & (t < t_end + pad)
    if m.sum() < 30:
        return {"available": False}
    tt, ff = t[m], f[m]
    fs = median_filter(ff, size=11)
    baseline = np.median(fs[(tt < t_start) | (tt > t_end)])
    minf = float(fs.min())
    half_depth = baseline - 0.5 * (baseline - minf)
    # Find first/last point that crosses half-depth
    cross = fs < half_depth
    if cross.sum() < 5:
        return {"available": False}
    idx = np.where(cross)[0]
    t14 = float(tt[idx[-1]] - tt[idx[0]])
    # Find flat-bottom (within 10% of min)
    flat = fs < (minf + 0.10 * (baseline - minf))
    if flat.sum() >= 3:
        idx2 = np.where(flat)[0]
        t23 = float(tt[idx2[-1]] - tt[idx2[0]])
    else:
        t23 = 0.0
    return {
        "available": True,
        "t14_d": t14,
        "t23_d": t23,
        "t14_hours": t14 * 24,
        "t23_hours": t23 * 24,
        "t23_over_t14": float(t23 / t14) if t14 > 0 else 0,
        "ingress_d": float((t14 - t23) / 2) if t14 > t23 else 0.0,
        "shape_class": (
            "U (flat-bottomed)"
            if t14 > 0 and t23 / t14 > 0.4
            else "V (grazing/pointed)"
            if t14 > 0
            else "unknown"
        ),
    }


def physics_interpretation(star: StarInfo, depth: float, t14_d: float) -> dict:
    """
    Compute companion radius and a sanity-check period for a CENTRAL transit.
    Apply CROWDSAP dilution correction if available.
    """
    if star.radius is None or depth is None:
        return {"available": False}

    obs_depth = depth
    true_depth = obs_depth / star.crowdsap if star.crowdsap else obs_depth
    # Cap to physical range
    true_depth = min(max(true_depth, 0.0), 0.99)
    ratio = math.sqrt(true_depth)
    R_sun_in_R_jup = 9.73
    R_comp_Rsun = ratio * star.radius
    R_comp_Rjup = R_comp_Rsun * R_sun_in_R_jup

    # Estimate mass if missing (rough): use radius+logg if available
    M_sun_est = None
    if star.logg is not None and star.radius is not None:
        # g = GM/R^2 -> M = g R^2 / G
        G = 6.674e-11
        Rsun_m = 6.96e8
        Msun_kg = 1.989e30
        g_cgs = 10 ** star.logg          # cm/s^2
        g_si = g_cgs / 100.0
        M = g_si * (star.radius * Rsun_m) ** 2 / G
        M_sun_est = M / Msun_kg

    # Central-transit period from T14 (rough)
    P_central_d = None
    if t14_d and t14_d > 0 and (star.mass or M_sun_est):
        Mstar_Msun = star.mass or M_sun_est
        G = 6.674e-11
        Msun = 1.989e30
        Rsun_m = 6.96e8
        day = 86400
        Mstar = Mstar_Msun * Msun
        Rstar = star.radius * Rsun_m
        T14_s = t14_d * day
        P_central_s = G * Mstar * math.pi * T14_s**3 / (4 * Rstar**3)
        P_central_d = float(P_central_s / day)

    # Categorize companion
    if R_comp_Rjup < 2.2:
        category = "Planet-sized"
    elif R_comp_Rjup < 7:
        category = "Brown dwarf / very-low-mass star sized"
    elif R_comp_Rjup < 20:
        category = "M-dwarf sized"
    else:
        category = "Stellar (G-K or earlier)"

    return {
        "available": True,
        "observed_depth": obs_depth,
        "dilution_corrected_depth": true_depth,
        "ratio_companion_over_star": ratio,
        "R_companion_Rsun": R_comp_Rsun,
        "R_companion_Rjup": R_comp_Rjup,
        "category": category,
        "is_planet_candidate": R_comp_Rjup < 2.2,
        "M_star_estimated_Msun": M_sun_est,
        "P_central_implied_d": P_central_d,
    }


def make_verdict(
    n_events: int,
    physics: dict,
    centroid: dict,
    odd_even: dict,
    secondary: dict,
    bls_sde: float,
    duration_consistent: Optional[bool] = None,
    duration_tol_h: float = DURATION_MATCH_TOL_H,
) -> dict:
    flags = []
    reasons = []
    confidence = 0.5

    # No detection at all
    if n_events == 0 and bls_sde < 7:
        return {
            "headline": "No significant transit/eclipse signal",
            "category": "no_signal",
            "confidence": 0.8,
            "flags": [],
            "reasons": ["No discrete dip events found and BLS SDE < 7."],
        }

    # Physics-based companion size. A radius just over the planetary cap is
    # BORDERLINE, not an automatic EB — only a radius well above the cap is a
    # hard EB indicator on size alone.
    companion_borderline = False
    if physics.get("available"):
        R = physics["R_companion_Rjup"]
        if R > COMPANION_EB_HARD_RJUP:
            flags.append("companion_too_large_for_planet")
            reasons.append(
                f"Implied companion radius {R:.1f} R_Jup ({physics['category']}) — "
                f"far above the planetary cap (~{COMPANION_PLANET_CAP_RJUP} R_Jup); "
                "stellar / brown-dwarf sized."
            )
        elif R > COMPANION_PLANET_CAP_RJUP:
            companion_borderline = True
            flags.append("companion_borderline_large")
            reasons.append(
                f"Implied companion radius {R:.1f} R_Jup is just above the "
                f"~{COMPANION_PLANET_CAP_RJUP} R_Jup planetary cap — borderline. "
                "Size alone is not treated as proof of an eclipsing binary; an "
                "eclipse signature (secondary or odd/even) or an RV mass is "
                "needed to confirm."
            )

    # Centroid offset = blend
    if centroid.get("available") and centroid.get("on_target") is False:
        flags.append("centroid_offset")
        reasons.append("In-transit centroid shifts >3σ — possible background blend.")
    elif centroid.get("available") and centroid.get("on_target"):
        reasons.append("Centroid is on-target — not a background blend.")

    # Odd/even
    if odd_even.get("available") and odd_even.get("flag_eb"):
        flags.append("odd_even_mismatch")
        reasons.append(
            f"Odd vs even transits differ at {odd_even['sigma']:.1f}σ — eclipsing-binary indicator."
        )

    # Secondary eclipse
    if secondary.get("available") and secondary.get("detected"):
        flags.append("secondary_eclipse_detected")
        reasons.append(
            f"Secondary eclipse detected at phase 0.5 ({secondary['sigma']:.1f}σ) — eclipsing-binary indicator."
        )

    # Transit-duration corroboration (±duration_tol_h). Consistent durations
    # across events support a single real transiting body; large variation is a
    # caution flag (blends / multiple signals) but is not, by itself, an EB call.
    if duration_consistent is True:
        reasons.append(
            f"Transit durations agree across events to within ±{duration_tol_h:.2f} h "
            "— consistent with a single repeating transit."
        )
    elif duration_consistent is False:
        flags.append("duration_inconsistent")
        reasons.append(
            f"Transit durations vary by more than ±{duration_tol_h:.2f} h between "
            "events — check for blends or more than one signal."
        )

    # Single-transit case
    if n_events == 1:
        reasons.append(
            "Only one in-sector event — period unconstrained; need follow-up or future sectors."
        )

    # Decide
    has_eclipse_indicator = (
        "secondary_eclipse_detected" in flags or "odd_even_mismatch" in flags
    )
    if "companion_too_large_for_planet" in flags or has_eclipse_indicator:
        category = "eclipsing_binary_candidate"
        headline = "Eclipsing binary candidate"
        confidence = 0.85
    elif "centroid_offset" in flags:
        category = "false_positive_blend"
        headline = "Likely background blend (false positive)"
        confidence = 0.75
    elif companion_borderline:
        # Borderline size, no corroborating eclipse signature -> large planet
        # candidate pending RV (matches mature-pipeline behaviour).
        category = "planet_candidate"
        headline = "Large planet candidate (RV needed to exclude brown dwarf)"
        confidence = 0.55
    elif physics.get("available") and physics.get("is_planet_candidate"):
        category = "planet_candidate"
        headline = "Planet candidate (further vetting required)"
        confidence = 0.60
    else:
        category = "ambiguous"
        headline = "Ambiguous signal — manual review needed"
        confidence = 0.40

    return {
        "headline": headline,
        "category": category,
        "confidence": confidence,
        "flags": flags,
        "reasons": reasons,
    }


# ----------------------------------------------------------------------
# Plot generation
# ----------------------------------------------------------------------
def make_plots(
    t, f, fe, mom_x, mom_y, events, primary_event,
    bls_periodogram, ls_periodogram,
    detrend_meta: Optional[dict] = None,
    f_raw: Optional[np.ndarray] = None,
) -> dict:
    """Generate diagnostic plots.

    ``events`` is the full list from detect_events (may be empty). ``primary_event``
    is the one chosen for centroid/shape analysis (typically the deepest).
    The full-LC plot shades EVERY event; the zoom plot shows up to 6 events
    in a grid, with the primary one highlighted.
    """
    plots = {}
    events = events or []

    # 0. Stellar variability detrend (only when actually applied).
    if (
        detrend_meta and detrend_meta.get("applied")
        and f_raw is not None and "fit" in detrend_meta
    ):
        from .detrend import _design_matrix  # internal but stable for plot use
        fit = detrend_meta["fit"]
        X = _design_matrix(t, fit["period_days"])
        coeffs = np.array([fit["C"], fit["A1"], fit["B1"], fit["A2"], fit["B2"]])
        model = X @ coeffs
        residual = f_raw - model + 1.0

        fig, axes = plt.subplots(3, 1, figsize=(10, 6.5), sharex=True)
        axes[0].plot(t, f_raw, "k.", ms=1, alpha=0.4)
        axes[0].plot(t, model, "C1-", lw=1.0, alpha=0.9, label="sin + 1st harmonic")
        axes[0].set_ylabel("Raw flux")
        axes[0].set_title(
            f"Stellar variability detrend — P = {fit['period_days']:.3f} d, "
            f"amplitude = {detrend_meta['amplitude_ppm']:.0f} ppm, "
            f"RMS reduced {detrend_meta['rms_reduction_pct']:.1f}%"
        )
        axes[0].legend(fontsize=8, loc="upper right")

        axes[1].plot(t, model, "C1-", lw=0.8)
        axes[1].set_ylabel("Fitted model")

        axes[2].plot(t, residual, "k.", ms=1, alpha=0.4)
        axes[2].axhline(1.0, color="gray", ls=":", alpha=0.5)
        axes[2].set_ylabel("Residual (BLS in)")
        axes[2].set_xlabel("Time (BTJD or similar)")

        for ax in axes:
            ax.ticklabel_format(axis="y", useOffset=False, style="plain")
            ax.yaxis.set_major_formatter(plt.ScalarFormatter(useOffset=False))

        fig.tight_layout()
        plots["detrend"] = _fig_to_b64(fig)

    # 1. Full LC with all events shaded.
    fig, ax = plt.subplots(figsize=(10, 3))
    ax.plot(t, f, "k.", ms=1, alpha=0.4)
    # Set y-limits explicitly so we can place labels just inside the top edge.
    ymin, ymax = float(np.nanpercentile(f, 0.3)), float(np.nanpercentile(f, 99.7))
    y_pad = 0.05 * (ymax - ymin)
    ax.set_ylim(ymin - y_pad, ymax + y_pad)
    # IMPORTANT: disable the "+1" offset notation — it's confusing for shallow dips
    ax.ticklabel_format(axis="y", useOffset=False, style="plain")
    ax.yaxis.set_major_formatter(plt.ScalarFormatter(useOffset=False))
    label_y = ymax + 0.3 * y_pad  # just below the top
    for i, ev in enumerate(events):
        is_primary = primary_event is not None and ev["t_start"] == primary_event["t_start"]
        ax.axvspan(
            ev["t_start"], ev["t_end"],
            color="red" if is_primary else "orange",
            alpha=0.30 if is_primary else 0.18,
        )
        mid = 0.5 * (ev["t_start"] + ev["t_end"])
        ax.text(
            mid, label_y,
            f"#{i+1}",
            ha="center", va="bottom", fontsize=8,
            color="darkred" if is_primary else "saddlebrown",
            fontweight="bold" if is_primary else "normal",
        )
    ax.set_xlabel("Time (BTJD or similar)")
    ax.set_ylabel("Normalised flux")
    title = "Detrended light curve"
    if events:
        title += f" — {len(events)} dip event{'s' if len(events) != 1 else ''} detected"
    ax.set_title(title)
    plots["lightcurve"] = _fig_to_b64(fig)

    # 2. Zoom: grid of up to 6 events. Primary highlighted.
    if events:
        n_show = min(len(events), 6)
        ncols = 1 if n_show == 1 else (2 if n_show <= 4 else 3)
        nrows = int(np.ceil(n_show / ncols))
        fig, axes = plt.subplots(
            nrows, ncols,
            figsize=(10 if ncols > 1 else 10, 3.0 * nrows),
            squeeze=False,
        )
        # Show DEEPEST events first.
        shown = sorted(events, key=lambda e: -e["depth"])[:n_show]
        for idx, ev in enumerate(shown):
            r, c = divmod(idx, ncols)
            ax = axes[r][c]
            pad = (ev["t_end"] - ev["t_start"]) * 1.5
            m = (t > ev["t_start"] - pad) & (t < ev["t_end"] + pad)
            if m.sum() <= 10:
                ax.text(0.5, 0.5, "(too few points)", ha="center", va="center",
                        transform=ax.transAxes)
                continue
            ax.errorbar(t[m], f[m], yerr=fe[m] if fe is not None else None,
                        fmt="k.", ms=2, alpha=0.5)
            ax.axhline(1.0, color="gray", ls=":", alpha=0.5)
            ax.axhline(ev["min_flux"], color="red", ls=":", alpha=0.6)
            is_primary = primary_event is not None and ev["t_start"] == primary_event["t_start"]
            border = "red" if is_primary else "gray"
            for spine in ax.spines.values():
                spine.set_edgecolor(border)
                spine.set_linewidth(1.5 if is_primary else 0.8)
            tag = " (primary)" if is_primary else ""
            snr_tag = f"  SNR={ev.get('depth_snr', 0):.1f}σ" if ev.get('depth_snr') else ""
            ax.set_title(
                f"Event at t≈{0.5*(ev['t_start']+ev['t_end']):.3f}{tag}\n"
                f"depth={ev['depth']*100:.2f}%, dur={ev['duration_d']*24:.1f}h{snr_tag}",
                fontsize=9,
            )
            ax.tick_params(labelsize=8)
        # Hide unused panels.
        for idx in range(n_show, nrows * ncols):
            r, c = divmod(idx, ncols)
            axes[r][c].set_visible(False)
        fig.supxlabel("Time", fontsize=9)
        fig.supylabel("Flux", fontsize=9)
        fig.tight_layout()
        plots["event_zoom"] = _fig_to_b64(fig)

    # 3. Centroid (anchored on the primary event only — that's where the
    # blend test is most meaningful)
    if primary_event is not None and mom_x is not None and mom_y is not None:
        pad = (primary_event["t_end"] - primary_event["t_start"]) * 2
        m = (t > primary_event["t_start"] - pad) & (t < primary_event["t_end"] + pad)
        if m.sum() > 20:
            fig, ax = plt.subplots(figsize=(10, 3))
            ax.plot(t[m], mom_x[m] - np.median(mom_x[m]), "b.", ms=2, alpha=0.5, label="col (x)")
            ax.plot(t[m], mom_y[m] - np.median(mom_y[m]), "g.", ms=2, alpha=0.5, label="row (y)")
            ax.axvspan(primary_event["t_start"], primary_event["t_end"], color="red", alpha=0.15)
            ax.set_xlabel("Time")
            ax.set_ylabel("Centroid offset (px)")
            ax.set_title("Centroid behaviour during primary event")
            ax.legend()
            plots["centroid"] = _fig_to_b64(fig)

    # 4. BLS periodogram
    if bls_periodogram:
        fig, ax = plt.subplots(figsize=(10, 3))
        ax.plot(bls_periodogram["periods"], bls_periodogram["power"], "k-", lw=0.6)
        ax.set_xlabel("Period (d)")
        ax.set_ylabel("BLS power")
        ax.set_title("Box Least Squares periodogram")
        plots["bls"] = _fig_to_b64(fig)

    # 5. LS
    if ls_periodogram and ls_periodogram.get("top_period"):
        fig, ax = plt.subplots(figsize=(6, 3))
        peaks = ls_periodogram.get("top_peaks", [])
        if peaks:
            ax.bar(
                [f"{p['period']:.2f} d" for p in peaks],
                [p["power"] for p in peaks],
                color="steelblue",
            )
            ax.set_ylabel("LS power")
            ax.set_title("Lomb-Scargle top peaks")
            ax.tick_params(axis="x", rotation=20)
            plots["lomb_scargle"] = _fig_to_b64(fig)

    return plots


# ----------------------------------------------------------------------
# Top-level driver
# ----------------------------------------------------------------------
def run_full_vetting(
    t: np.ndarray,
    flux: np.ndarray,
    flux_err: np.ndarray,
    quality: Optional[np.ndarray],
    mom_x: Optional[np.ndarray],
    mom_y: Optional[np.ndarray],
    star: StarInfo,
    detect_threshold: float = 0.997,
    detect_min_snr: float = 4.0,
    high_variability: bool = False,
    rotation_period_days: Optional[float] = None,
    known_period_days: Optional[float] = None,
    secondary_sigma: float = 3.0,
    odd_even_sigma: float = 3.0,
) -> VettingResult:
    from .detrend import apply_variability_detrend

    # Clean
    t_c, f_c, fe_c = clean_lightcurve(t, flux, flux_err, quality)
    if mom_x is not None and quality is not None:
        m = np.isfinite(t) & np.isfinite(flux) & (flux > 0) & (quality == 0)
        mom_x = mom_x[m]
        mom_y = mom_y[m]

    # Stats
    span = float(t_c.max() - t_c.min())

    # Lomb-Scargle (cap at half the baseline)
    ls = run_lomb_scargle(t_c, f_c, fe_c, p_min=0.1, p_max=min(20.0, span / 2))

    # --- Optional sinusoidal detrend before BLS ----------------------------
    detrend_meta: dict
    f_raw_for_plot = f_c.copy() if high_variability else None
    if high_variability:
        period_for_fit = rotation_period_days or ls.get("top_period")
        source = "user_period" if rotation_period_days else "ls_peak"
        # Per-cadence noise floor in ppm — anything below this is just scatter.
        # Use point-to-point differences so the floor reflects high-frequency
        # noise rather than the very variability we're trying to fit.
        df = np.diff(f_c)
        noise_floor_ppm = float(1.4826 * np.nanmedian(np.abs(df)) / np.sqrt(2.0)) * 1e6
        f_c, detrend_meta = apply_variability_detrend(
            t_c, f_c, period_days=period_for_fit,
            noise_floor_ppm=noise_floor_ppm, source=source,
        )
    else:
        detrend_meta = {
            "applied": False, "reason": "disabled",
            "period_days": None, "amplitude_ppm": None,
            "harmonic_amplitude_ppm": None, "rms_reduction_pct": None,
        }

    # BLS (runs on detrended residual when high_variability was enabled)
    if (known_period_days is not None
            and np.isfinite(known_period_days)
            and 0 < known_period_days <= 0.7 * span):
        bls = run_bls_constrained(t_c, f_c, fe_c, known_period_days=known_period_days)
    else:
        bls = run_bls(t_c, f_c, fe_c, p_min=0.5, p_max=span * 0.7)
        if known_period_days is not None:
            bls["constrained_fallback_reason"] = (
                "known_period_days outside valid range"
            )

    # Direct event detection (user-tunable sensitivity).
    events = detect_events(
        t_c, f_c,
        threshold=detect_threshold,
        min_pts=10,
        min_snr=detect_min_snr,
    )

    # If exactly one in-sector event, anchor centroid/shape on it.
    primary_event = events[0] if len(events) == 1 else None
    if len(events) > 1:
        primary_event = max(events, key=lambda e: e["depth"])

    # Centroid
    centroid = {"available": False}
    if primary_event and mom_x is not None and mom_y is not None and len(mom_x) == len(t_c):
        centroid = centroid_check(
            t_c, mom_x, mom_y, primary_event["t_start"], primary_event["t_end"]
        )

    # Shape
    shape = {"available": False}
    if primary_event:
        shape = measure_shape(t_c, f_c, primary_event["t_start"], primary_event["t_end"])

    # Odd/even and secondary (both threshold-tunable)
    odd_even = odd_even_check(
        t_c, f_c, bls["period"], bls["t0"], bls["duration"],
        odd_even_sigma=odd_even_sigma,
    )
    secondary = secondary_eclipse_search(
        t_c, f_c, bls["period"], bls["t0"], bls["duration"],
        secondary_sigma=secondary_sigma,
    )

    # Physics
    depth_for_physics = primary_event["depth"] if primary_event else bls.get("depth")
    t14_for_physics = shape.get("t14_d") if shape.get("available") else bls.get("duration")
    physics = physics_interpretation(star, depth_for_physics, t14_for_physics)

    # Verdict
    verdict = make_verdict(
        n_events=len(events),
        physics=physics,
        centroid=centroid,
        odd_even=odd_even,
        secondary=secondary,
        bls_sde=bls["sde"],
    )

    # External catalog cross-match (unchanged).
    known = crossmatch_known_object(star.ra, star.dec)
    if known.get("matched"):
        verdict["original_headline"] = verdict.get("headline")
        verdict["original_category"] = verdict.get("category")
        verdict["headline"] = known["headline"]
        verdict["category"] = "known_object"
        verdict["confidence"] = 0.99
        match_reason = (
            f"Catalog override: {known['headline']}"
            + (f" — {known['name']}" if known.get("name") else "")
            + (f" ({known['description']})" if known.get("description") else "")
            + (
                f" at {known['distance_arcsec']:.2f}\" from target"
                if known.get("distance_arcsec") is not None else ""
            )
            + "."
        )
        verdict.setdefault("reasons", []).insert(0, match_reason)
        verdict.setdefault("flags", []).append("known_object_override")

    # Plots — Task 7 will extend make_plots to accept detrend_meta + f_raw.
    # For now keep the existing call signature; Task 7 adds the new kwargs.
    plots = make_plots(
        t_c, f_c, fe_c, mom_x, mom_y, events, primary_event,
        bls.get("_periodogram"), ls,
        detrend_meta=detrend_meta,
        f_raw=f_raw_for_plot,
    )

    summary = {
        "n_points": int(len(t_c)),
        "time_span_d": span,
        "median_cadence_min": float(np.median(np.diff(t_c)) * 1440),
        "n_events_detected": len(events),
        "scatter_mad": float(1.4826 * np.nanmedian(np.abs(f_c - 1))),
    }

    bls.pop("_periodogram", None)

    return VettingResult(
        star=star,
        summary=summary,
        bls=bls,
        lomb_scargle=ls,
        events=events,
        centroid=centroid,
        odd_even=odd_even,
        secondary=secondary,
        shape=shape,
        physics=physics,
        verdict=verdict,
        known_object=known,
        detrend=detrend_meta,
        sensitivity={
            "threshold": float(detect_threshold),
            "min_snr": float(detect_min_snr),
            "secondary_sigma": float(secondary_sigma),
            "odd_even_sigma": float(odd_even_sigma),
        },
        plots=plots,
    )


# ----------------------------------------------------------------------
# Multi-sector analysis
# ----------------------------------------------------------------------

# Multi-sector runs are intentionally capped: from each of up to MAX_SECTORS
# sectors we take up to EVENTS_PER_SECTOR (2) of the deepest events, then group
# them into up to MAX_OBJECTS distinct objects by transit duration. Each
# object is confirmed by checking its members share the same duration and
# period across sectors. MAX_OBJECTS is the *default*; the caller can request
# more (up to MAX_OBJECTS_HARD_CAP) when the deepest dips are blends or false
# positives that mask the real TOI of interest.
MAX_SECTORS = 5
EVENTS_PER_SECTOR = 2
MAX_OBJECTS = 2
MAX_OBJECTS_HARD_CAP = MAX_SECTORS * EVENTS_PER_SECTOR  # 10 — every event its own bucket


def _cluster_events_into_objects(reps: list, tol_h: float, max_objects: int = MAX_OBJECTS) -> list:
    """Group representative events into <=max_objects clusters by duration.

    Events whose durations sit within ``tol_h`` of each other join the same
    object; a gap larger than ``tol_h`` starts a new object. If that yields
    more than ``max_objects`` clusters, the two closest are merged until the
    cap is met. Returns a list of lists of rep dicts.
    """
    if not reps:
        return []
    s = sorted(reps, key=lambda e: e["duration_h"])
    clusters = [[s[0]]]
    for prev, cur in zip(s, s[1:]):
        if cur["duration_h"] - prev["duration_h"] <= tol_h:
            clusters[-1].append(cur)
        else:
            clusters.append([cur])
    while len(clusters) > max_objects:
        gaps = [
            (clusters[i + 1][0]["duration_h"] - clusters[i][-1]["duration_h"], i)
            for i in range(len(clusters) - 1)
        ]
        _, i = min(gaps, key=lambda g: g[0])
        clusters[i].extend(clusters[i + 1])
        del clusters[i + 1]
    return clusters


def run_multisector_analysis(
    sector_results: list,
    period_d: float | None = None,
    t0: float | None = None,
    detect_threshold: float = 0.997,
    detect_min_snr: float = 4.0,
    high_variability: bool = False,
    secondary_sigma: float = 3.0,
    odd_even_sigma: float = 3.0,
    duration_tol_h: float = DURATION_MATCH_TOL_H,
    max_objects: int = MAX_OBJECTS,
    known_period_days: float | None = None,
) -> dict:
    """
    Given vetting results from multiple sectors, build:
      - A detection timeline: which sectors showed events
      - Up to EVENTS_PER_SECTOR representative (deepest) events per sector,
        across up to MAX_SECTORS sectors
      - Grouping of those events into up to ``max_objects`` distinct objects by
        transit duration, each confirmed to share the SAME duration (within
        ``duration_tol_h`` hours) and the SAME period across sectors
      - A consensus ephemeris (period refinement)

    Returns a plain dict — JSON-serialisable, ships to frontend.
    """
    if not sector_results:
        return {"error": "No sector results provided."}

    # Clamp max_objects to [1, MAX_OBJECTS_HARD_CAP]. Anything beyond the hard
    # cap (one bucket per representative event) is meaningless.
    try:
        max_objects = int(max_objects)
    except (TypeError, ValueError):
        max_objects = MAX_OBJECTS
    max_objects = max(1, min(max_objects, MAX_OBJECTS_HARD_CAP))

    # Cap to MAX_SECTORS, preferring sectors that show a dip and, among those,
    # the deepest detection (most diagnostic).
    ranked = sorted(
        sector_results,
        key=lambda sr: (
            len(sr[1].events) > 0,
            max((e["depth"] for e in sr[1].events), default=0.0),
        ),
        reverse=True,
    )
    sector_results = ranked[:MAX_SECTORS]

    timeline = []
    representative_events = []  # up to EVENTS_PER_SECTOR per sector with a dip

    for sec_num, res in sector_results:
        has_dip = len(res.events) > 0
        # Up to EVENTS_PER_SECTOR deepest events from this sector.
        top_evs = sorted(res.events, key=lambda e: e["depth"], reverse=True)[:EVENTS_PER_SECTOR]
        deepest_depth = top_evs[0]["depth"] if top_evs else 0.0
        timeline.append({
            "sector": sec_num,
            "n_events": len(res.events),
            "has_dip": has_dip,
            "deepest_depth_pct": round(deepest_depth * 100, 3),
            "bls_period_d": res.bls.get("period"),
            "bls_sde": res.bls.get("sde"),
            "verdict": res.verdict.get("category"),
        })
        for rep in top_evs:
            representative_events.append({
                "sector": sec_num,
                "t_center": round(0.5 * (rep["t_start"] + rep["t_end"]), 5),
                "duration_h": round(rep["duration_d"] * 24.0, 4),
                "depth_pct": round(rep["depth"] * 100, 4),
                "bls_period_d": res.bls.get("period"),
            })

    n_with_dip = sum(1 for x in timeline if x["has_dip"])
    n_total = len(timeline)

    # Period analysis: collect BLS peaks from all sectors
    period_estimates = [
        x["bls_period_d"] for x in timeline
        if x["bls_period_d"] and x["bls_sde"] and x["bls_sde"] > 6
    ]
    # When the user supplied a known period, every per-sector BLS was anchored
    # to it: include every finite peak so the refined median is meaningful even
    # when blind-search SDE would have been low.
    if known_period_days is not None and np.isfinite(known_period_days):
        constrained_estimates = [
            sr[1].bls.get("period")
            for sr in sector_results
            if sr[1].bls.get("constrained") and sr[1].bls.get("period")
        ]
        if len(constrained_estimates) >= len(period_estimates):
            period_estimates = constrained_estimates
    period_consensus = None
    refined_median_d = None
    refined_std_d = None
    if len(period_estimates) >= 2:
        p_arr = np.array(period_estimates)
        refined_median_d = float(np.median(p_arr))
        refined_std_d = float(np.std(p_arr))

    if known_period_days is not None and np.isfinite(known_period_days):
        per_sector_matches = [
            (x["sector"], (sr[1].bls.get("matched_harmonic")))
            for x, sr in zip(timeline, sector_results)
            if sr[1].bls.get("constrained")
        ]
        harmonics = {m for _, m in per_sector_matches if m}
        period_consensus = {
            "value_d": float(known_period_days),
            "source": "user known period (constrained BLS)",
            "harmonic_disagreement": len(harmonics) > 1,
            "per_sector_matches": per_sector_matches,
            "no_constrained_sectors": len(per_sector_matches) == 0,
        }
        if refined_median_d is not None:
            period_consensus["refined_median_d"] = refined_median_d
            period_consensus["refined_std_d"] = refined_std_d
    elif period_d:
        period_consensus = {"value_d": period_d, "source": "external (ExoFOP/user)"}
        if refined_median_d is not None:
            period_consensus["refined_median_d"] = refined_median_d
            period_consensus["refined_std_d"] = refined_std_d
    elif refined_median_d is not None:
        period_consensus = {
            "value_d": refined_median_d,
            "std_d": refined_std_d,
            "source": f"median of {len(period_estimates)} sector BLS peaks",
        }

    # --- Group events into up to ``max_objects`` distinct objects by duration -
    clusters = _cluster_events_into_objects(
        representative_events, duration_tol_h, max_objects=max_objects,
    )
    objects = []
    for oid, members in enumerate(clusters, start=1):
        durs = [m["duration_h"] for m in members]
        pers = [m["bls_period_d"] for m in members]
        sectors = sorted({m["sector"] for m in members})
        dur_ok = durations_consistent(durs, tol_h=duration_tol_h)
        if known_period_days is not None and np.isfinite(known_period_days):
            # With a user-supplied known period, every sector's BLS peak must
            # agree with that anchor (not just with each other) to within the
            # constrained-BLS tolerance.
            per_tol = BLS_KNOWN_PERIOD_TOL_FRAC
            finite_pers = [p for p in pers if p is not None and np.isfinite(p) and p > 0]
            per_ok = bool(finite_pers) and all(
                abs(p - known_period_days) / known_period_days <= per_tol
                for p in finite_pers
            )
        else:
            per_ok = periods_consistent(pers, tol_frac=PERIOD_MATCH_TOL_FRAC)
        spread = round(max(durs) - min(durs), 4) if durs else None
        # "Confirmed" requires the same object seen in >=2 sectors with matching
        # duration and period.
        confirmed = bool(len(sectors) >= 2 and dur_ok and per_ok)
        if len(sectors) < 2:
            note = (
                f"Seen in only {len(sectors)} sector — needs a second sector to "
                "cross-confirm duration and period."
            )
        elif confirmed:
            note = (
                f"Confirmed across {len(sectors)} sectors: matching duration "
                f"(spread {spread:.3f} h ≤ {duration_tol_h:.3f} h) and period."
            )
        else:
            bits = []
            if not dur_ok:
                bits.append(f"duration spread {spread:.3f} h > {duration_tol_h:.3f} h")
            if not per_ok:
                bits.append("periods disagree")
            note = "Inconsistent across sectors: " + "; ".join(bits) + "."
        objects.append({
            "object_id": oid,
            "n_events": len(members),
            "sectors": sectors,
            "duration_h_median": round(float(np.median(durs)), 4) if durs else None,
            "duration_spread_h": spread,
            "period_d_median": (
                round(float(np.median([p for p in pers if p])), 5)
                if any(pers) else None
            ),
            "depth_pct_median": round(float(np.median([m["depth_pct"] for m in members])), 4),
            "durations_consistent": dur_ok,
            "periods_consistent": per_ok,
            "confirmed_multisector": confirmed,
            "members": members,
            "note": note,
        })

    n_objects = len(objects)
    if n_objects == 0:
        objects_summary = "No repeating events to group into objects."
    elif n_objects == 1:
        objects_summary = "One object identified. " + objects[0]["note"]
    else:
        dur_list = ", ".join(
            f"{o['duration_h_median']:.2f} h" for o in objects if o["duration_h_median"]
        )
        objects_summary = (
            f"{n_objects} distinct objects identified by differing transit "
            f"durations ({dur_list})."
        )

    # Anchor t0 for any downstream folding.
    if t0 is None:
        for _, res in sector_results:
            if res.bls.get("t0"):
                t0 = res.bls["t0"]
                break

    # Detection timeline plot
    timeline_plot = _make_timeline_plot(timeline)

    analysis_settings = {
        "detect_threshold": float(detect_threshold),
        "detect_min_snr": float(detect_min_snr),
        "high_variability": bool(high_variability),
        "secondary_sigma": float(secondary_sigma),
        "odd_even_sigma": float(odd_even_sigma),
    }

    return {
        "n_sectors_observed": n_total,
        "n_sectors_with_detections": n_with_dip,
        "detection_rate": round(n_with_dip / n_total, 3) if n_total else 0,
        "max_sectors": MAX_SECTORS,
        "events_per_sector": EVENTS_PER_SECTOR,
        "max_objects": max_objects,
        "max_objects_default": MAX_OBJECTS,
        "max_objects_hard_cap": MAX_OBJECTS_HARD_CAP,
        "duration_tol_h": duration_tol_h,
        "timeline": timeline,
        "period_consensus": period_consensus,
        "n_objects_detected": n_objects,
        "objects": objects,
        "timeline_plot": timeline_plot,
        "settings": analysis_settings,
        "summary": (
            f"{n_with_dip}/{n_total} sectors show a dip event. "
            + (f"Consensus period ≈ {period_consensus['value_d']:.4f} d. "
               if period_consensus else "Period not well-constrained. ")
            + objects_summary
        ),
    }


def _make_timeline_plot(timeline: list) -> str:
    """Bar chart showing detection status and depth per sector."""
    if not timeline:
        return ""
    sectors = [str(x["sector"]) for x in timeline]
    depths  = [x["deepest_depth_pct"] for x in timeline]
    colors  = ["#ef4444" if x["has_dip"] else "#cbd5e1" for x in timeline]

    fig, ax = plt.subplots(figsize=(max(6, len(sectors) * 0.8), 3.5))
    bars = ax.bar(sectors, depths, color=colors, edgecolor="white", linewidth=0.8)
    ax.set_xlabel("TESS Sector")
    ax.set_ylabel("Deepest dip depth (%)")
    ax.set_title("Multi-sector detection timeline\n(red = dip detected, grey = no dip)")
    ax.axhline(0.3, ls=":", color="#94a3b8", alpha=0.6, label="0.3% threshold")
    ax.legend(fontsize=8)
    for bar, d in zip(bars, depths):
        if d > 0:
            ax.text(bar.get_x() + bar.get_width() / 2, d + 0.01 * max(depths, default=1),
                    f"{d:.2f}%", ha="center", va="bottom", fontsize=7, color="#374151")
    plt.tight_layout()

    buf = __import__("io").BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return __import__("base64").b64encode(buf.getvalue()).decode("ascii")
