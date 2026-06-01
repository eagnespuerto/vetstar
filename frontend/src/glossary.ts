/**
 * Glossary of technical terms used in Vetstar.
 *
 * Each entry maps a term (or data-field key) to a plain-English definition
 * aimed at hobbyist astronomers and citizen scientists (e.g. TESS Planet
 * Hunters participants). Keep definitions short (1–3 sentences), jargon-free
 * where possible, and link to the relevant concept rather than the math.
 */

export const GLOSSARY: Record<string, string> = {
  // --- Pipeline concepts ---
  "BLS":
    "Box Least Squares — a period-search algorithm that looks for repeating box-shaped dips in brightness, which is how transits appear in a light curve. A strong BLS peak means the data contain a periodic dip pattern.",
  "Lomb-Scargle":
    "A period-search method that looks for smooth, sinusoidal brightness variations (like starspots rotating in and out of view). Unlike BLS, it doesn't assume box-shaped dips.",
  "centroid":
    "The measured centre of the star's light on the detector. If the centroid shifts during a dip, the dimming probably comes from a nearby background star (a \"blend\") rather than the target star itself.",
  "odd/even":
    "Compares the depth of odd-numbered transits to even-numbered ones. If they differ significantly, the signal is likely an eclipsing binary (two stars orbiting each other) with unequal eclipse depths, not a planet.",
  "secondary eclipse":
    "A dip that occurs halfway between the main dips (at orbital phase 0.5). Planets don't produce detectable secondary eclipses in TESS data, so seeing one is a strong sign of an eclipsing binary.",
  "transit shape":
    "The profile of the dip. A flat-bottomed \"U\" shape means the smaller object fully crosses the larger one (a full transit). A pointed \"V\" shape means it only grazes the edge — common in eclipsing binaries.",
  "verdict":
    "The pipeline's automated classification based on all the tests above: planet candidate, eclipsing binary candidate, background blend, ambiguous, or no signal detected.",
  "CROWDSAP":
    "Contamination ratio — the fraction of light in the TESS aperture that comes from the target star (vs. nearby stars). A value of 0.85 means 85% of the light is from the target and 15% is from neighbours. Lower values mean more contamination, which dilutes the true transit depth.",
  "PDCSAP flux":
    "Pre-search Data Conditioning Simple Aperture Photometry — the TESS pipeline's best estimate of the star's brightness over time, with instrumental trends and systematics removed. This is the light curve the vetting pipeline analyses.",

  // --- Data-field keys (shown in KV tables) ---
  "period":
    "The time between consecutive transits (in days). A planet's orbital period.",
  "t0":
    "The reference time of the first detected transit (in BTJD — Barycentric TESS Julian Date). Used to predict when future transits will occur.",
  "duration":
    "How long the transit lasts (in days). Depends on the planet's orbital speed and how centrally it crosses the star.",
  "depth":
    "How much the star dims during transit, as a fraction of its normal brightness. A depth of 0.01 means the star gets 1% dimmer. Deeper = larger companion relative to the star.",
  "sde":
    "Signal Detection Efficiency — measures how strongly the BLS periodogram peak stands out above the noise. Higher SDE = more confident detection. SDE > 7 is generally considered a solid detection.",
  "power":
    "The strength of a periodogram peak. Higher power means the data more strongly favour that period.",
  "top_period":
    "The period with the strongest Lomb-Scargle power — the most likely rotation or variability period.",
  "top_power":
    "The Lomb-Scargle power at the best-fit period.",
  "false_alarm_prob":
    "False alarm probability — the chance that the strongest Lomb-Scargle peak is just noise rather than a real signal. Lower is better (e.g. 1e-10 means the signal is almost certainly real).",
  "n_transits_in_window":
    "How many transits of this period fit within the observed time span. More transits = more confidence the signal is real.",
  "n_events_detected":
    "Number of discrete brightness dips found by the event detector (independent of BLS period search).",

  // --- Centroid fields ---
  "shift_col_px":
    "How far the centroid moved horizontally (in pixels) during the dip compared to outside the dip.",
  "shift_row_px":
    "How far the centroid moved vertically (in pixels) during the dip.",
  "shift_col_sigma":
    "Centroid column shift measured in standard deviations (sigma). If > 3σ, the shift is statistically significant — the dip may come from a background star.",
  "shift_row_sigma":
    "Centroid row shift in sigma. Same interpretation as column shift.",
  "on_target":
    "Whether the centroid stayed on the target star during the dip (true = good, the dip is from this star; false = possible background blend).",

  // --- Shape fields ---
  "t14_d":
    "Total transit duration from first contact to last contact (in days). Also called T14.",
  "t14_hours":
    "Total transit duration in hours.",
  "t23_d":
    "Duration of the flat bottom of the transit (in days). Also called T23 — the time the planet is fully on the stellar disk.",
  "t23_hours":
    "Flat-bottom duration in hours.",
  "t23_over_t14":
    "Ratio of flat-bottom time to total transit time. Close to 1 = very central transit; close to 0 = grazing.",
  "ingress_d":
    "Time for the planet to move from first touching the stellar disk to fully on it (in days).",
  "shape_class":
    "Whether the transit is U-shaped (flat-bottomed, full transit) or V-shaped (grazing or pointed). U-shapes are more consistent with a true planetary transit.",

  // --- Odd/even fields ---
  "depth_odd":
    "Transit depth measured from odd-numbered transits only.",
  "depth_even":
    "Transit depth measured from even-numbered transits only.",
  "difference":
    "The difference between odd and even transit depths. A large difference suggests an eclipsing binary.",
  "sigma":
    "How statistically significant a measurement is, in units of standard deviation. 3σ = 99.7% confident it's real.",
  "flag_eb":
    "Whether this test flags the signal as a likely eclipsing binary (true = EB suspected).",

  // --- Physics fields ---
  "observed_depth":
    "The transit depth as measured directly from the light curve, before correcting for contamination from nearby stars.",
  "dilution_corrected_depth":
    "Transit depth after correcting for CROWDSAP contamination. This is the true depth on the target star and is always deeper than or equal to the observed depth.",
  "ratio_companion_over_star":
    "The radius of the transiting object divided by the radius of the star. Derived from the square root of the transit depth.",
  "R_companion_Rsun":
    "Estimated radius of the transiting companion in solar radii (R☉).",
  "R_companion_Rjup":
    "Estimated radius of the transiting companion in Jupiter radii (R_Jup). Planets are < ~2.2 R_Jup; anything larger is likely a star.",
  "category":
    "In the Physical Interpretation section: classification based on companion size (planet-sized < 2.2 R_Jup, brown dwarf, M-dwarf, or stellar). In the Verdict section: top-level signal class (planet_candidate, eclipsing_binary, background_blend, ambiguous, or no_signal).",
  "is_planet_candidate":
    "Whether the implied companion radius is consistent with a planet (< 2.2 R_Jup).",
  "M_star_estimated_Msun":
    "Estimated mass of the host star in solar masses, derived from its radius and surface gravity.",
  "P_central_implied_d":
    "The orbital period that would be implied if this were a perfectly central transit with the observed duration. Useful as a sanity check.",

  // --- Star fields ---
  "tic_id":
    "TESS Input Catalog identifier — the unique number TESS uses for every star in its catalog.",
  "tmag":
    "TESS magnitude — how bright the star appears to the TESS cameras. Lower = brighter.",
  "teff":
    "Effective temperature of the star's surface (in Kelvin). The Sun is ~5778 K. Hotter stars are bluer; cooler stars are redder.",
  "radius":
    "Radius of the star in solar radii (R☉). The Sun = 1.0.",
  "logg":
    "Surface gravity of the star (log base-10 of gravity in cm/s²). The Sun is ~4.44. Lower values mean a puffier, more evolved star (subgiant or giant).",
  "mass":
    "Mass of the star in solar masses (M☉). The Sun = 1.0.",
  "ra":
    "Right Ascension — the star's east-west position on the sky (in degrees). Like longitude on Earth.",
  "dec":
    "Declination — the star's north-south position on the sky (in degrees). Like latitude on Earth.",
  "sector":
    "Which TESS observing sector this data comes from. TESS divides the sky into sectors and observes each for ~27 days.",
  "camera":
    "Which of TESS's four cameras observed this star (1–4).",
  "ccd":
    "Which CCD detector on the camera captured this star (1–4).",
  "crowdsap":
    "Same as CROWDSAP — fraction of aperture flux from the target star. See CROWDSAP above.",
  "source":
    "Where the stellar parameters came from (e.g. 'fits' = from the FITS file header, 'exofop' = from ExoFOP-TESS).",

  // --- Summary fields ---
  "n_points":
    "Total number of data points in the cleaned light curve (after removing bad-quality flagged cadences and NaN values).",
  "time_span_d":
    "Total time span of the observations in days.",
  "median_cadence_min":
    "The typical time between consecutive measurements (in minutes). TESS 2-minute cadence data has ~2.0 min.",
  "scatter_mad":
    "Photometric scatter of the light curve, measured as the Median Absolute Deviation (MAD). Lower = quieter star = easier to detect shallow transits.",

  // --- Detection sensitivity ---
  "detect_threshold":
    "The absolute brightness threshold below which the pipeline considers a point to be in a dip. Default 0.997 means \"flag anything dimmer than 99.7% of baseline.\" For quiet stars, the adaptive threshold (based on SNR) is usually more important.",
  "detect_min_snr":
    "Minimum signal-to-noise ratio for a dip to be flagged as a real event. A 4σ threshold means the dip must be 4× deeper than the star's natural brightness scatter.",
  "depth_snr":
    "Signal-to-noise ratio of a detected dip event. Higher SNR = more confident the dip is real and not just noise.",

  // --- HCI ---
  "HCI":
    "Habitability Chance Index — a 0–100 score estimating how likely a planet candidate is to be able to retain an atmosphere and potentially support liquid water, based on the STEHM model (Hill et al. 2026).",
  "STEHM":
    "Smaller Than Earth Habitability Model — a theoretical model that determines how small a rocky planet can be and still maintain a CO₂ atmosphere over billions of years. The key finding: planets ≥ 0.8 Earth radii can retain atmospheres around Sun-like stars.",
  "habitable zone":
    "The region around a star where a rocky planet with sufficient atmosphere could have liquid water on its surface. Not too hot (water boils), not too cold (water freezes).",
  "conservative HZ":
    "The narrower, more certain habitable zone defined by the runaway greenhouse limit (inner edge) and maximum greenhouse limit (outer edge). For the Sun: roughly 0.95–1.68 AU.",
  "optimistic HZ":
    "The wider habitable zone that includes the recent Venus limit (inner) and early Mars limit (outer). For the Sun: roughly 0.75–1.77 AU.",

  // --- TOI / ExoFOP ---
  "TOI":
    "TESS Object of Interest — a star flagged by the TESS pipeline as potentially hosting a transiting planet. Each TOI gets a number (e.g. TOI-700) and is listed on ExoFOP for community vetting.",
  "ExoFOP":
    "Exoplanet Follow-up Observing Program — NASA's community portal where TESS planet candidates are listed, vetted, and dispositioned by astronomers worldwide.",
  "disposition":
    "The current classification of a TOI: PC (planet candidate), CP (confirmed planet), KP (known planet), FP (false positive), or FA (false alarm).",
  "PC":
    "Planet Candidate — a TOI that has passed initial pipeline vetting but hasn't been independently confirmed yet.",
  "CP":
    "Confirmed Planet — a TOI that has been independently verified as a real planet (e.g. via radial velocity measurements).",
  "FP":
    "False Positive — a TOI that turned out not to be a planet (usually an eclipsing binary or instrumental artefact).",

  // --- Misc ---
  "BTJD":
    "Barycentric TESS Julian Date — the time system used in TESS data, corrected for the light travel time to the solar system's centre of mass. Essentially \"when did this photon actually leave the star.\"",
  "eclipsing binary":
    "Two stars orbiting each other, where one periodically passes in front of the other as seen from Earth. Their eclipses can mimic planetary transits but are usually deeper and show odd/even depth differences or secondary eclipses.",
  "background blend":
    "When a faint eclipsing binary near the target star falls within the same TESS pixel, its eclipses get mixed into the target's light curve, creating a false transit signal. The centroid test catches these.",

  // --- Generic test-result fields (appear in most KV tables) ---
  "available":
    "Whether this test could be run on the data. False usually means the input was missing something the test needs (e.g. no centroid moments, too few transits) — see the accompanying 'reason'.",
  "reason":
    "A short explanation of why a test was not run, or extra context about its result.",
  "detected":
    "Whether the test found a significant signal (true = yes). For the secondary-eclipse search, true means a dip was found near phase 0.5, which is a red flag for an eclipsing binary.",
  "n_odd":
    "Number of odd-numbered transits used in the odd/even depth comparison.",
  "n_even":
    "Number of even-numbered transits used in the odd/even depth comparison.",

  // --- Verdict object ---
  "headline":
    "One-line summary of what the pipeline thinks this signal is.",
  "confidence":
    "How sure the pipeline is in its verdict, from 0 to 1. Higher = more confident.",
  "flags":
    "Short tags describing which automated tests fired (e.g. 'centroid_shift', 'secondary_detected', 'odd_even_diff').",
  "reasons":
    "Plain-English bullet points explaining why the pipeline reached this verdict.",

  // --- Star / ExoFOP extras ---
  "distance":
    "Distance to the star in parsecs (1 pc ≈ 3.26 light-years). Usually from Gaia parallax.",
  "toi_number":
    "The TESS Object of Interest number assigned by the TESS pipeline (e.g. 700.01).",
  "period_d":
    "Orbital period in days.",
  "depth_ppm":
    "Transit depth in parts per million (ppm). 10,000 ppm = 1% dimming.",
  "duration_hr":
    "Transit duration in hours.",
  "radius_earth":
    "Companion radius in Earth radii (R⊕). Earth = 1.0, Neptune ≈ 3.9, Jupiter ≈ 11.2.",
  "semi_major_axis_au":
    "Average orbital distance in astronomical units (AU). 1 AU = Earth–Sun distance.",
  "sectors":
    "List of TESS sectors in which this target was observed.",

  // --- Light-curve event fields ---
  "t_start":
    "Time (BTJD) when a detected dip event begins.",
  "t_end":
    "Time (BTJD) when a detected dip event ends.",
  "duration_d":
    "Duration of an individual dip event in days.",
  "min_flux":
    "Minimum (faintest) brightness reached during a dip event, as a fraction of the baseline.",

  // --- Multi-sector consensus ---
  "object_id":
    "Internal identifier the pipeline assigns to a candidate that appears consistently across multiple sectors.",
  "n_sectors_observed":
    "How many TESS sectors of data were searched for this target.",
  "n_sectors_with_detections":
    "Number of sectors in which at least one dip event was detected.",
  "detection_rate":
    "Fraction of sectors with at least one detection (n_sectors_with_detections / n_sectors_observed).",
  "durations_consistent":
    "Whether the transit duration is consistent across sectors. True = supports a real periodic signal.",
  "periods_consistent":
    "Whether the BLS period is consistent across sectors. True = supports a real periodic signal.",
  "confirmed_multisector":
    "True if the same candidate was detected with consistent duration and period in multiple TESS sectors.",
  "bls_period_d":
    "BLS-derived orbital period for this sector, in days.",
  "bls_sde":
    "BLS Signal Detection Efficiency for this sector (see 'sde').",
  "deepest_depth_pct":
    "Depth of the deepest dip event in this sector, expressed as a percentage.",
  "depth_pct":
    "Dip depth as a percentage of baseline brightness.",
  "depth_pct_median":
    "Median dip depth across sectors, as a percentage.",
  "duration_h":
    "Dip duration in hours.",
  "duration_h_median":
    "Median dip duration across sectors, in hours.",
  "duration_spread_h":
    "Range (max – min) of dip durations across sectors, in hours. Small spread = consistent signal.",
  "t_center":
    "Time (BTJD) at the midpoint of a dip event.",
  "n_events":
    "Number of dip events detected.",
  "has_dip":
    "Whether at least one dip event was detected in this sector.",
  "value_d":
    "A best-estimate value, in days (e.g. consensus period across sectors).",
  "std_d":
    "Standard deviation of that value across sectors, in days. Smaller = more consistent.",

  // --- TOI dispositions (extras) ---
  "KP":
    "Known Planet — a previously confirmed planet rediscovered by TESS.",
  "FA":
    "False Alarm — a TOI that was a statistical fluctuation rather than a real astrophysical signal.",
  "APC":
    "Ambiguous Planet Candidate — passes some vetting tests but fails or is inconclusive on others.",

  // --- Habitability extras ---
  "AU":
    "Astronomical Unit — the average Earth–Sun distance (about 150 million km). Used to describe orbital sizes.",
  "S_eff":
    "Stellar flux received at the planet, relative to Earth (Earth = 1.0). Values near 1 are most Earth-like.",
  "equilibrium_temperature":
    "The temperature a planet would have if it absorbed all incoming starlight and re-radiated it uniformly (in Kelvin). Earth's is ~255 K; the surface is warmer due to greenhouse gases.",
  "high_stellar_variability":
    "When ticked, the pipeline fits a sine wave plus first harmonic to the light curve at the user-supplied rotation period (or the Lomb-Scargle peak if blank) and subtracts it before running BLS. Useful for spotted rotators and other wave-like variables where the rotation signal would otherwise mask shallow planetary dips.",
  "rotation_period_days":
    "Optional period (in days) of the dominant stellar rotation signal to subtract before BLS. Leave blank to let the pipeline use its Lomb-Scargle top peak instead.",
  "secondary_sigma":
    "Detection threshold for the secondary-eclipse search at orbital phase 0.5. A candidate is flagged when the dip at phase 0.5 exceeds this many σ above the out-of-transit scatter. Lower values flag more eclipsing-binary candidates (more false positives); higher values are stricter.",
  "detrend_amplitude":
    "Peak amplitude of the fitted sinusoid (square-root of A1² + B1²), expressed in parts per million of the median flux. Quantifies how much rotational variability the detrender removed.",
};

/**
 * Look up a term in the glossary. Tries exact match first, then
 * case-insensitive, then checks if the key appears as a substring.
 */
export function lookupTerm(key: string): string | undefined {
  if (GLOSSARY[key]) return GLOSSARY[key];
  const lower = key.toLowerCase();
  for (const [k, v] of Object.entries(GLOSSARY)) {
    if (k.toLowerCase() === lower) return v;
  }
  return undefined;
}
