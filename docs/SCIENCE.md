# Vetstar — Scientific & Technical Pipeline Documentation

This document walks through the *physics, statistics, and algorithms* behind
every stage of the Vetstar pipeline. It is the companion to the user-facing
[README](../README.md): the README tells you **what** the app does, this
document tells you **how** and **why**.

Each equation below is written inline as LaTeX and rendered natively by
GitHub's MathJax support; PNG fallbacks are kept under
`docs/legacy-pngs/` for viewers without MathJax (see
`docs/render_equations.ps1` to regenerate them). Symbols follow standard
astrophysics convention: `★` = host star, `p` = planet/companion,
`⊕` = Earth, `☉` = Sun.

---

## Table of contents

1. [Light-curve ingestion & detrending](#1-light-curve-ingestion--detrending)
   - 1.1 [Savitzky-Golay baseline removal](#11-savitzky-golay-baseline-removal)
   - 1.2 [Optional sinusoidal-regression detrend (high stellar variability)](#12-optional-sinusoidal-regression-detrend-high-stellar-variability)
   - 1.3 [Plot-only rolling-median flatten and time-binned overlay](#13-plot-only-rolling-median-flatten-and-time-binned-overlay)
2. [Adaptive dip detection](#2-adaptive-dip-detection)
3. [Periodograms — BLS & Lomb-Scargle](#3-periodograms--bls--lomb-scargle)
4. [Phase folding & transit-model fit](#4-phase-folding--transit-model-fit)
5. [Diagnostic tests](#5-diagnostic-tests)
   - 5.1 [Centroid offset test](#51-centroid-offset-test)
   - 5.2 [Odd / even depth comparison](#52-odd--even-depth-comparison)
   - 5.3 [Secondary-eclipse search](#53-secondary-eclipse-search)
   - 5.4 [CROWDSAP dilution correction](#54-crowdsap-dilution-correction)
6. [Transit geometry (TLCM)](#6-transit-geometry-tlcm)
7. [Predicted Observables for Exoplanets (POE)](#7-predicted-observables-for-exoplanets-poe)
8. [Radial-velocity reduction & absolute mass](#8-radial-velocity-reduction--absolute-mass)
9. [Habitability Chance Index (HCI)](#9-habitability-chance-index-hci)
10. [ExoMiner feature extraction](#10-exominer-feature-extraction)
11. [FFI cutout rendering](#11-ffi-cutout-rendering)
12. [Multi-sector aggregation](#12-multi-sector-aggregation)
13. [Time-system conventions](#13-time-system-conventions)
14. [References](#14-references)
15. [Microlensing pipeline — Module A: model-comparison classifier](#15-microlensing-pipeline--module-a-model-comparison-classifier)
    - 15.1 [Paczyński single-lens (PSPL) magnification](#151-paczyński-single-lens-pspl-magnification)
    - 15.2 [Davenport-2014 empirical flare template](#152-davenport-2014-empirical-flare-template)
    - 15.3 [Null model and window baseline normalisation](#153-null-model-and-window-baseline-normalisation)
    - 15.4 [BIC-based model selection and verdict rules](#154-bic-based-model-selection-and-verdict-rules)
    - 15.5 [Residual symmetry statistic](#155-residual-symmetry-statistic)
16. [Microlensing pipeline — Module B: TESS sector-overlap targeting](#16-microlensing-pipeline--module-b-tess-sector-overlap-targeting)
    - 16.1 [tess-point coordinate resolution](#161-tess-point-coordinate-resolution)
    - 16.2 [Static TESS sector-date table](#162-static-tess-sector-date-table)
    - 16.3 [Observability logic (wings margin)](#163-observability-logic-wings-margin)
    - 16.4 [Bulge / ecliptic blind-zone flag](#164-bulge--ecliptic-blind-zone-flag)
17. [Observable parameters and predicted planet quantities](#17-observable-parameters-and-predicted-planet-quantities)
    - 17.1 [Peak magnification, brightening, and FWHM](#171-peak-magnification-brightening-and-fwhm)
    - 17.2 [Einstein-crossing duration and blend fraction](#172-einstein-crossing-duration-and-blend-fraction)
    - 17.3 [Fiducial-lens physical scales (θ_E, r_E, v_rel)](#173-fiducial-lens-physical-scales-θ_e-r_e-v_rel)
    - 17.4 [Planet-detection sensitivity floor](#174-planet-detection-sensitivity-floor)
18. [TESS-specific microlensing caveats (Harris et al. 2026)](#18-tess-specific-microlensing-caveats-harris-et-al-2026)

---

## 1. Light-curve ingestion & detrending

Vetstar accepts SPOC FITS, TESS-SPOC FFI, QLP FFI, or legacy Kepler FITS.
The parser (`backend/app/parsers.py`) extracts the time series and quality
flags, then normalises the flux to a baseline of unity. For SPOC products
the **PDCSAP_FLUX** column is preferred (planetary-friendly systematics
removal); legacy Kepler PDCSAP_FLUX is treated identically.

### 1.1 Savitzky-Golay baseline removal

Detrending is done with a **Savitzky-Golay** filter sized adaptively to the
data cadence. The detrended flux

```
f_norm(t) = f_raw(t) / SG_window(f_raw, w)
```

leaves transit-depth signals undisturbed while removing slow stellar
variability. Sample-to-sample jumps larger than ~5× the median cadence are
flagged as data gaps (downlinks, momentum dumps, scattered-light excisions);
events cannot span a gap, which prevents a multi-day outage from registering
as one very wide "dip".

The robust scatter of the out-of-dip baseline is measured with the **median
absolute deviation** (MAD), scaled to a Gaussian-equivalent σ:

$$\sigma_{\mathrm{out}} \;=\; 1.4826 \cdot \mathrm{median}\!\left(\bigl|x_{i}-\mathrm{median}(x)\bigr|\right)$$

The 1.4826 factor is the standard MAD→σ conversion for a Gaussian. MAD is
preferred over the sample standard deviation because real light curves carry
outliers (cosmic rays, momentum-dump residuals) that inflate `std()` but
leave the median essentially untouched.

### 1.2 Optional sinusoidal-regression detrend (high stellar variability)

Spotted rotators, ellipsoidal binaries, and other wave-like variables carry
a stellar signal whose amplitude is often **much larger than a planetary
transit**. The Savitzky-Golay window above is sized for SPOC-style
systematics removal and intentionally leaves coherent oscillations of period
≳ 1 d intact, so on these targets BLS spends most of its power on the
rotation signal and misses shallow dips.

When the user opts in (the **High stellar variability** toggle in the UI;
`high_variability=true` on the API), Vetstar fits a sine plus its first
harmonic, at a fixed period `P`, by ordinary linear least squares:

```
f(t) ≈ C + A₁·sin(2π t / P) + B₁·cos(2π t / P)
         + A₂·sin(4π t / P) + B₂·cos(4π t / P)
```

The five free coefficients `(C, A₁, B₁, A₂, B₂)` are recovered by
`numpy.linalg.lstsq` against the 5-column design matrix
`[1, sin(ω t), cos(ω t), sin(2ω t), cos(2ω t)]` with `ω = 2π / P`. Fixing
`P` keeps the problem linear and closed-form — the alternative of treating
`P` as a free parameter would make the system nonlinear and overlap
strongly with BLS itself.

**Period selection.** If `rotation_period_days` is supplied, it is used
verbatim. Otherwise the **Lomb-Scargle top peak** from §3.2 is used. The
choice is recorded in the result as `detrend.reason ∈ {"user_period",
"ls_peak"}`.

**Amplitude.** The fitted fundamental amplitude (peak, not RMS) is
`A = √(A₁² + B₁²)`, reported in ppm of the median flux. The harmonic
amplitude `√(A₂² + B₂²)` is reported separately.

**Skip condition.** Fitting a sinusoid to pure white noise produces a
non-zero amplitude purely by chance. Vetstar therefore estimates the
per-cadence high-frequency noise floor

$$\sigma_{\mathrm{floor}} \;\approx\; \dfrac{1.4826 \cdot \mathrm{median}\!\bigl(|\Delta f|\bigr)}{\sqrt{2}}$$

using the point-to-point MAD on the *flux differences*,

```
σ_floor ≈ 1.4826 · median |Δf| / √2     (ppm × 1e6)
```

(the `/√2` factor follows from `Var(Δf) = 2 σ²` for white noise; this form
is the standard photometric noise estimator and, unlike `MAD(f − 1)`, is
**not** biased by the very low-frequency variability we are about to fit).
When the fitted fundamental amplitude falls below `σ_floor` the detrend is
declared a fit-to-noise and **skipped**; the original flux is fed to BLS
and `detrend.reason = "skipped_low_amplitude"`.

**Residuals.** When the fit is applied, the BLS input becomes

```
f_resid(t) = f(t) − [C + A₁·sin(ωt) + B₁·cos(ωt)
                       + A₂·sin(2ωt) + B₂·cos(2ωt)]   + 1.0
```

(the trailing `+ 1.0` re-centres around unity since `C` already absorbs the
mean of `f`). The diagnostic plot panel emits a 3-row figure — raw flux
with the fitted model overlaid, the model alone, and the residual that BLS
actually sees — so the user can verify that the rotation signal was
captured cleanly without distorting the in-transit cadences. The fit and
the RMS-reduction percentage `100 · (σ_before − σ_after) / σ_before` are
recorded in the JSON response and the PDF report.

**Multi-sector behaviour.** Each sector is detrended independently against
its own LS peak (or the same user-supplied period applied per-sector).
Rotation phase is **not** preserved across the multi-month gaps between
TESS sectors, so a single global fit would be wrong; running the
regression per-sector is correct and is what the multi-sector pipeline
does internally before its BLS pass.

**Defaults preserve pre-feature behaviour.** With `high_variability=false`
the entire block is bypassed and `f_resid ≡ f`; the regression test
`test_defaults_match_pre_change_pipeline_output` locks BLS period, SDE,
secondary-detected, and verdict category to their pre-change values.

### 1.3 Plot-only rolling-median flatten and time-binned overlay

The Savitzky-Golay (§1.1) and sinusoidal-regression (§1.2) detrends both
operate on the **flux fed into detection** — they change what BLS and the
adaptive event scan see. A noisy faint target can still look like a
shotgun-blast scatter plot in the diagnostic LC even after detection has
already flattened it for its own purposes. The full-LC diagnostic plot in
`make_plots()` therefore runs an additional **presentation-only**
flatten + bin pass that does not feed back into detection or the
periodograms:

**Rolling-median flatten.** For a sorted time series `t` with a window
half-width `w_½ = 0.5 d`, the trend at sample `i` is

```
trend(i) = median{ f(j) : t_i − w_½ ≤ t(j) ≤ t_i + w_½ }
```

evaluated by an O(n) two-pointer sweep over the sorted `t`. The displayed
flux is `f(i) / trend(i)`, then re-normalised so the global median is unity.
A 1-day window is long enough to leave a transit-shaped dip (typically
1–10 h) almost untouched — the median inside the window is dominated by
out-of-transit cadences — while still scrubbing the slow variability that
makes shallow dips invisible to the eye. Robustness to single-cadence
outliers comes for free (median, not mean). The frontend ships the same
helper in JS (`rollingMedianDetrend` in `ManualDipSelector.tsx`) so the
in-browser drag-to-select tool sees the same view without a round trip.

**Time binning.** When `plot_bin_minutes = b` is set, samples are grouped
into equal-width bins of `b` minutes in `t`. The per-bin mean and standard
error of the mean

```
f̄_k = (1/N_k) Σ_{i∈k} f_i,        SEM_k = √( Var_k / max(1, N_k) )
```

are overplotted as red points (Var_k from the second-moment running sum,
clipped to ≥ 0). Empty bins are dropped. Binning beats white per-cadence
noise down by **√N_k**, so on a 2-min SPOC LC a 30-min bin reduces scatter
by √15 ≈ 3.9× — typically enough to make a 0.1% transit visible to the eye.
The overlay is purely visual; events / BLS / SNR all still run on the raw
cadences.

**Defaults and toggles.** `plot_detrend` defaults to `True` and
`plot_bin_minutes` defaults to `30`. Both are exposed on `POST
/api/mast/analyze*` (and threaded through `run_full_vetting → make_plots`)
and on a toggle bar above the LC plot in the UI. Setting either to its
"off" value reverts to the legacy view (raw flux, no overlay). Detection
output, verdict, periodograms, and PDF tables are byte-identical regardless
of these knobs — only the rendered LC PNG differs.

---

## 2. Adaptive dip detection

The studio combines two thresholds and picks whichever is more sensitive:

$$T_{\mathrm{eff}} \;=\; \min\!\bigl(T_{\mathrm{abs}},\; 1 - k\,\sigma_{\mathrm{out}}\bigr)$$

with `T_abs` the user-set absolute floor (default 0.997 — flag dips deeper
than 0.3%) and `k` the user-set SNR floor (default 4σ). For a quiet star
where the MAD-σ is 0.0002, this yields an effective threshold of
0.9992 — far more sensitive than the fixed default — while for a noisy
star it simply falls back to `T_abs`.

Each candidate event must additionally clear an **integrated SNR** test:

$$\mathrm{SNR}_{\mathrm{int}} \;=\; \dfrac{\bar{\delta}\,\sqrt{N_{\mathrm{in}}}}{\sigma_{\mathrm{out}}}$$

where  `δ̄`  is the mean fractional depth of the event,  `N_in`  is the
number of in-transit samples, and  `σ_out`  is the out-of-dip MAD-σ. The
`√N_in` factor is what allows a shallow transit on a noisy star to qualify:
a 0.8 %-deep, 5-hour transit on a star with 0.5 % point scatter has a
per-point SNR near 1 but an integrated SNR around 10. This is the same
matched-filter logic used by the BLS in §3 and by ExoMiner's local-view
significance score.

Events whose interior is interrupted only by a handful of noisy points are
bridged so a single transit reports as one event rather than several
fragments.

---

## 3. Periodograms — BLS & Lomb-Scargle

### 3.1 Box Least Squares (BLS)

BLS (Kovács, Zucker & Mazeh 2002) is the matched-filter optimised for a
**boxcar** transit shape: a baseline flux dropping briefly by `δ`, then
returning. Vetstar uses `astropy.timeseries.BoxLeastSquares`.

The frequency-domain output is the **signal residue** SR; Vetstar's quoted
**Signal Detection Efficiency (SDE)** standardises the peak against the
periodogram's own baseline:

$$\mathrm{SDE} \;=\; \dfrac{\mathrm{SR}_{\mathrm{peak}} - \langle\mathrm{SR}\rangle}{\mathrm{std}(\mathrm{SR})}$$

We flag SDE > 6 as a confident period detection. Per-event significance is
recomputed using the integrated-SNR formula of §2 — BLS provides the
**period, depth, duration, epoch**, but Vetstar's adaptive detector decides
which individual events are real.

### 3.2 Lomb-Scargle

The classical (Press & Rybicki 1989) Lomb-Scargle power at angular
frequency ω is

$$P_{\mathrm{LS}}(\omega) \;=\; \dfrac{1}{2\sigma^{2}}\!\left[\dfrac{\left(\sum_{i}(x_i-\bar x)\cos\omega(t_i-\tau)\right)^{2}}{\sum_{i}\cos^{2}\omega(t_i-\tau)} + \dfrac{\left(\sum_{i}(x_i-\bar x)\sin\omega(t_i-\tau)\right)^{2}}{\sum_{i}\sin^{2}\omega(t_i-\tau)}\right]$$

with `τ` the standard time-offset that orthogonalises the sine and cosine
sums. Lomb-Scargle is sensitive to **sinusoidal variability** (rotation,
ellipsoidal variation, contact-binary modulation). A high LS peak at the
**same** period as the BLS peak — or, more often, at **half** the BLS
period — is a strong eclipsing-binary indicator.

---

## 4. Phase folding & transit-model fit

Phase folding maps time to a phase in [−½, ½):

$$\varphi(t) \;=\; \mathrm{mod}\!\left(\dfrac{t - t_{0}}{P},\;1\right) - \tfrac{1}{2}$$

Phase 0 is transit centre. The folded curve is binned to two grids:
**global** (2001 bins across the full phase) and **local** (201 bins
spanning ±2 transit durations). This matches the ExoMiner convention so the
same views can be fed downstream to the ExoMiner feature extractor (§10).

When a **SPOC Data Validation Time Series (DVT)** file is available,
Vetstar overlays the SPOC-fitted Mandel-Agol transit model (`MODEL_INIT`)
on the binned data (`LC_INIT`). The DVT file also supplies sharper values
for the geometric parameters `a/R★` (`ARAT`) and impact parameter `b`
(`IMPACT`), which feed the TLCM block of §6 directly.

---

## 5. Diagnostic tests

### 5.1 Centroid offset test

For SPOC 2-min products the FITS table carries the per-cadence centroid
columns `MOM_CENTR1`, `MOM_CENTR2`. Vetstar computes the in-transit centroid
mean and compares against the out-of-transit baseline:

$$n_\sigma^{(c)} \;=\; \dfrac{\sqrt{\Delta x^{2} + \Delta y^{2}}}{\sigma_{\mathrm{centroid}}}$$

A significant offset (default > 3σ) means the photometric centre of light
moved *off* the target during the dip — the canonical signature of a
**background eclipsing binary** diluted into the SPOC aperture. FFI
products (TESS-SPOC, QLP) do not carry centroid columns, so the test is
skipped with an amber banner.

### 5.2 Odd / even depth comparison

For an eclipsing binary near unit mass ratio, the primary and secondary
eclipses can be **similar in depth**, but a true planet's odd- and
even-numbered transits must agree. Vetstar measures the depth of every
detected event, partitions them by index parity, and computes:

$$n_\sigma \;=\; \dfrac{\bigl|\delta_{\mathrm{odd}} - \delta_{\mathrm{even}}\bigr|}{\sqrt{\sigma_{\mathrm{odd}}^{2} + \sigma_{\mathrm{even}}^{2}}}$$

A difference > 3σ flags the candidate as a probable EB. Each side of the
ratio carries the in-event MAD-σ propagated through the depth average.

### 5.3 Secondary-eclipse search

The phase-folded curve is searched at φ = 0.5 for a shallower secondary
dip. The required significance reuses the integrated-SNR formula: the
secondary depth `δ_sec` is compared against the out-of-eclipse scatter
`σ_oot / √N_in`, and the dip is **detected** when

```
δ_sec / (σ_oot / √N_in)  >  σ_thresh
```

`σ_thresh` is a user-tunable threshold (the **Secondary eclipse σ** slider
in the UI, `secondary_sigma` on the API) bounded to **1.0σ–7.0σ** by the
backend (HTTP 422 outside the range), defaulting to **3σ**. Lowering it
surfaces more EB candidates at the cost of more false positives on noisy
stars; raising it is stricter. A positive secondary at the BLS period
(and not at any plausible orbital harmonic) is recorded as a strong EB
indicator and feeds the verdict logic of the README.

### 5.4 CROWDSAP dilution correction

SPOC propagates a `CROWDSAP` keyword: the fraction of flux in the optimal
aperture that actually belongs to the target (the rest comes from blended
neighbours). Observed transit depths must be inflated to true source-frame
depths before any radius inference:

$$\delta_{\mathrm{true}} \;=\; \dfrac{\delta_{\mathrm{obs}}}{\mathrm{CROWDSAP}}$$

CROWDSAP < 1 always *underestimates* the true depth; a 0.5%-observed
transit on a CROWDSAP = 0.5 target is really a 1.0% transit on the source.
FFI products without CROWDSAP are flagged so the caller knows the
companion-radius estimate is a lower limit.

---

## 6. Transit geometry (TLCM)

The Transit Light Curve Model framework (Csizmadia 2020) gives a set of
**model-independent** quantities derivable from a single well-sampled
transit alone, without needing a catalogue stellar mass.

### 6.1 Radius ratio

From the dilution-corrected depth:

$$\delta \;=\; \left(\dfrac{R_{p}}{R_{\star}}\right)^{2}$$

so `k = Rp / R★ = √δ`. The corresponding companion radius is

$$R_{p} \;=\; k \cdot R_{\star} \;=\; \sqrt{\delta_{\mathrm{true}}}\,\cdot R_{\star}$$

For a grazing transit, the observed depth is a lower bound on `k²`; Vetstar
flags this and propagates the caveat to the verdict (§5 in the README).

### 6.2 Scaled semi-major axis from duration

Combining Kepler's third law with the chord-length geometry of a transit
(Csizmadia 2020 eq. 70) gives, for a known total duration `T₁₄`, impact
parameter `b`, and radius ratio `k`:

$$\dfrac{a}{R_{\star}} \;\approx\; \dfrac{P}{\pi\,T_{14}}\sqrt{(1+k)^{2} - b^{2}\bigl(1-\sin^{2}\!\left(\tfrac{\pi T_{14}}{P}\right)\bigr)}$$

When a DVT file is available, the SPOC-fitted `ARAT` and `IMPACT` replace
this duration-derived estimate; otherwise Vetstar assumes a central transit
(`b = 0`) and flags the assumption in the caveats.

### 6.3 Model-independent stellar density

The Seager & Mallén-Ornelas (2003) relation drops out:

$$\rho_{\star} \;=\; \dfrac{3\pi}{G\,P^{2}}\,\left(\dfrac{a}{R_{\star}}\right)^{3}$$

`ρ★` requires only `P` and `a/R★`. With `R★` from any source (ExoFOP, TIC
v8, Gaia DR3, or — in the fully-fallback case — the Pecaut & Mamajek (2013)
main-sequence interpolated against this density) Vetstar recovers a
stellar **mass** cross-check `M★ = (4/3)π R★³ ρ★`.

### 6.4 Photometric semi-major axis

Multiplying `a/R★` by the stellar radius gives a *photometric* `a`
independent of any catalogue mass. The alternative — Kepler's third law
from a catalogue stellar mass —

$$a^{3} \;=\; \dfrac{G\,M_{\star}\,P^{2}}{4\pi^{2}}$$

is also computed; agreement to within a few percent validates the solution.
A large discrepancy flags eccentricity, a grazing transit, dilution, or
bad stellar parameters (Csizmadia 2020 Appendix A).

---

## 7. Predicted Observables for Exoplanets (POE)

Vetstar implements the NASA Exoplanet Archive POE equations end-to-end.

### 7.1 Bolometric luminosity

$$L_{\star} \;=\; 4\pi R_{\star}^{2}\,\sigma_{\mathrm{SB}}\,T_{\mathrm{eff}}^{4}$$

with σ_SB the Stefan-Boltzmann constant. Vetstar reports `L★ / L☉`, which
also drives the habitable-zone calculation below.

### 7.2 Habitable-zone boundaries (Kopparapu)

The Kopparapu et al. (2013, 2014) HZ boundaries are quartic in
`T★ = T_eff − 5780 K`:

$$S_{\mathrm{eff}}(T_{\mathrm{eff}}) \;=\; S_{\mathrm{eff},\odot} + a\,T_{\star} + b\,T_{\star}^{2} + c\,T_{\star}^{3} + d\,T_{\star}^{4},\quad T_{\star} \equiv T_{\mathrm{eff}} - 5780\,\mathrm{K}$$

with coefficients `(S_eff,☉, a, b, c, d)` tabulated for each boundary
(recent Venus, runaway greenhouse, maximum greenhouse, early Mars).
The HZ distance in AU is then:

$$d_{\mathrm{HZ}}\,[\mathrm{AU}] \;=\; \sqrt{\dfrac{L_{\star}/L_{\odot}}{S_{\mathrm{eff}}}}$$

### 7.3 Instellation and equilibrium temperature

When no luminosity or distance is available, Vetstar falls back to the
**scaled-semi-major-axis** form of the instellation:

$$\dfrac{S}{S_{\oplus}} \;=\; \left(\dfrac{T_{\mathrm{eff}}}{T_{\odot}}\right)^{4}\!\left(\dfrac{215.03}{a/R_{\star}}\right)^{2}$$

(215.03 = 1 AU / R☉, ensuring dimensional consistency.) The equilibrium
temperature, assuming zero albedo and full redistribution, is:

$$T_{\mathrm{eq}} \;=\; 278.3\;\left(\dfrac{S}{S_{\oplus}}\right)^{1/4}\;\mathrm{K}$$

Both quantities feed the HCI habitable-zone sub-score (§9) when no
catalogue luminosity is available.

### 7.4 Radial-velocity semi-amplitude (forward model)

The Clubb (2008) closed form, used in POE:

$$K \;=\; \dfrac{203.255\;\mathrm{m\,s^{-1}}}{\sqrt{1-e^{2}}}\;\left(\dfrac{1\;\mathrm{day}}{P}\right)^{\!1/3}\!\left(\dfrac{M_{p}\sin i}{M_{\mathrm{Jup}}}\right)\!\left(\dfrac{M_{\odot}}{M_{\star}}\right)^{\!2/3}$$

For unknown planet mass `M_p`, Vetstar plugs in the Chen & Kipping (2017)
mass-radius relation (§8.2). The forward `K` feeds both the POE display
and the HCI density-aware planet-size sub-score.

---

## 8. Radial-velocity reduction & absolute mass

### 8.1 Mass function

A single-line spectroscopic binary obeys the mass function

$$f(m) \;=\; \dfrac{K^{3}\,P\,(1-e^{2})^{3/2}}{2\pi G} \;=\; \dfrac{(M_{p}\sin i)^{3}}{(M_{\star} + M_{p})^{2}}$$

The left-hand side is *fully observable* from a phased RV time series:
fit the radial-velocity semi-amplitude `K` (Vetstar uses the simple
min/max method on an uploaded RV series, or queries
`NASA Exoplanet Archive pscomppars` for `pl_rvamp`), combine with `P` and
`e`, and you have `f(m)`. The right-hand side then gives the absolute
companion mass `M_p` exactly (cubic root) once `M★` is known.

### 8.2 Mass-radius relations

Two complementary forms are reported so the dominant systematic — the
mass-radius scatter — is visible:

$$M_{p}/M_{\oplus} \;=\; \begin{cases} (R_{p}/R_{\oplus})^{3.58}, & R_{p} < 1.23\,R_{\oplus} \\ 1.436\,(R_{p}/R_{\oplus})^{1.70}, & 1.23 \le R_{p}/R_{\oplus} < 14.3 \\ \cdots & \mathrm{(Neptunian,\,Jovian\,branches)} \end{cases}$$

and a simpler piecewise power-law. The full HCI calculation runs both,
takes the resulting *range* of size-scores, and presents a banded score
(e.g. 78.5 with a 69.5-78.5 band). A measured mass from RV collapses the
band to a single value.

### 8.3 Bulk density classification

When `M_p` is available, the bulk density

$$\rho_{p} \;=\; \dfrac{3\,M_{p}}{4\pi R_{p}^{3}}$$

informs the HCI planet-size sub-score: ρ ≳ 3.3 g cm⁻³ is rocky-consistent
and confirms a solid surface; ρ ≲ 2.2 g cm⁻³ flags a volatile / H-He
envelope and downgrades the score regardless of radius.

---

## 9. Habitability Chance Index (HCI)

The HCI is a 0-100 weighted sum of six sub-scores, each in [0, 1]:

$$\mathrm{HCI} \;=\; 100 \cdot \sum_{i=1}^{6} w_{i}\,s_{i},\qquad \sum_{i=1}^{6} w_{i} = 1$$

with weights `wᵢ = (0.30, 0.25, 0.15, 0.15, 0.10, 0.05)` for
**size, habitable-zone, stellar type, TOI disposition, vetting flags,
multi-sector**. The weighting is the contribution declared in
`backend/app/habitability.py`. Each sub-score has a documented closed-form
mapping:

| Sub-score | Inputs | Mapping |
|-----------|--------|---------|
| Size      | `Rp` (and `ρp` if known) | piecewise from STEHM Fig 5: full credit ≥ 0.8 R⊕, marginal 0.7-0.8, falls to 0 by 0.5 R⊕; capped/downgraded by `ρp` band |
| HZ        | `a` (AU) or `S/S⊕`        | distance/flux compared to Kopparapu boundaries (§7.2); 1.0 within conservative HZ, taper to 0 outside optimistic HZ |
| Star      | `T_eff` (or density-typed) | 1.0 for FGK, taper for late-K / early-M, penalty for M-dwarfs |
| Disp      | ExoFOP TOI flag           | CP/KP = 1.0, PC/APC = 0.75, FP = 0.05, Unknown = 0.5 |
| Vetting   | Centroid / odd-even / secondary / companion-size pipeline outputs | each failed test deducts; passing all gives 1.0 |
| Sectors   | Number of independent detections | 1 → 0.4, 2 → 0.7, 3 → 0.9, ≥ 4 → 1.0 |

After the weighted sum, a **bulk-density modifier of ±10 percentage points**
is applied to the final HCI when `ρp` is available (§8.3):

- **Terrestrial density** (ρp ≳ 3.3 g cm⁻³, rocky-consistent) → **+10 pts**
- **Gas-giant density** (ρp ≲ 2.2 g cm⁻³, volatile / H-He envelope) → **−10 pts**
- Intermediate / unknown densities → no modifier

The modifier is applied after the weighted sum and before the EB/FP cap, and
the result is clamped to [0, 100]. This rewards a confirmed solid surface and
penalises a puffy envelope independently of the size sub-score's own density
band.

Confirmed EBs and FPs are **hard-capped at HCI = 12** regardless of the
weighted sum or density modifier — a safety bar so that, e.g., a 0.9 R⊕ object
in the HZ that the vetting tagged as a centroid-shift blend cannot mislead the
user.

When stellar parameters are missing, the chain of fallbacks documented in
the README (ExoFOP → TIC v8 → Gaia DR3 → Pecaut & Mamajek main sequence)
keeps the HCI computable directly from the light curve. Every fallback
that fires emits a `caveat` so the UI shows where the values came from.

### 9.1 HCI summary image — planet-system diagram

The PNG returned alongside the score (`hci_image`, embedded in the HCI page
of the PDF report) renders a compact top-down diagram of the system that
mirrors the ExoWorld tuner stage:

- The host star is drawn at the centre with a colour derived from `Teff`
  (M-dwarfs red, K orange, G yellow-white, F warm white, A/early bluish)
  and a radius scaled by `R★/R⊙`.
- The **Kopparapu / POE habitable zone** (§7.2) is a filled annulus from
  `inner_au` to `outer_au`, with dashed circles marking the recent-Venus
  and early-Mars edges.
- The orbit is a thin ring at `a/AU`. The planet is placed on it, coloured
  green when the orbit lies inside the HZ, red when interior to the inner
  edge (too hot), and slate-blue when exterior (too cold). A terminator
  shade darkens the anti-stellar hemisphere. When the true orbit would
  cause the planet disc to overlap the star disc visually (M-dwarf
  systems at ~0.05 AU, hot Jupiters), the planet is shifted outward by
  the minimum disc separation so it stays readable; this is a display
  guard only and does not change any numerical output.
- An overlay in the top-right prints the spectral type, `Teff` in K,
  semi-major axis in AU, and planet radius in `R⊕` — the same fields
  shown in ExoWorld so the two views are directly comparable.

The diagram lives in a sub-gridspec inside the existing header row, so
the overall figure size (9.0 × 7.4 in) is unchanged from prior releases
and PDF report layouts remain stable.

---

## 10. ExoMiner feature extraction

ExoMiner (Valizadegan et al. 2022, ApJ 926 120) is a deep-learning vetting
classifier with a fixed TFRecord input schema. Vetstar reproduces the full
schema so the same features can be inspected by eye (and, in future, fed
to a local ExoMiner network).

Phase-folded data are **median-binned** to fixed-length 1-D views:

$$\bar{f}_{j} \;=\; \mathrm{median}\!\bigl\lbrace\,f_{i} : \varphi_{i} \in [\varphi_{j}, \varphi_{j+1})\,\bigr\rbrace$$

Seven views are produced:

| View | Length | Span |
|------|--------|------|
| Global  | 2001 | full phase  |
| Local   | 201  | ± 2 × T₁₄    |
| Secondary | 201 | centred on φ = 0.5 |
| Odd-transit | 201 | local, odd-indexed events only |
| Even-transit | 201 | local, even-indexed events only |
| Centroid global | 2001 | folded centroid magnitude |
| Centroid local | 201 | folded centroid magnitude |

Scalar features mirror the ExoMiner paper: `tce_period, tce_duration,
tce_depth, tce_count, oe_sigma, sec_sigma, centroid_sigma, scatter_mad,
crowdsap, sg_window`. Each scalar is rendered with a σ-badge so an
abnormal value (e.g. odd-even σ > 3) is visible at a glance.

---

## 11. FFI cutout rendering

For every fetched target, Vetstar pulls a Full-Frame-Image cutout from
**MAST TESScut** centred on the TIC RA/Dec. The pixel array is rendered
with the same **asinh percentile stretch** that `astrocut` uses by default:

$$y \;=\; \dfrac{\sinh^{-1}\!\bigl((x-x_{\mathrm{lo}})/\beta\bigr)}{\sinh^{-1}\!\bigl((x_{\mathrm{hi}}-x_{\mathrm{lo}})/\beta\bigr)},\quad y \in [0,1]$$

where `x_lo`, `x_hi` are the (default) 0.5th and 99.5th percentiles of the
pixel-value distribution, and `β` is the asinh softening parameter. The
stretch compresses bright stars while keeping faint structure visible —
crucial for spotting a faint neighbour that could be the real eclipsing
source.

The target's pixel position is overlaid as a red **+**, the photometric
aperture (from the SPOC pipeline bitmask, when available) as a yellow
outline. The result is cached and embedded in the PDF report.

---

## 12. Multi-sector aggregation

Multi-sector mode runs §§1-10 independently on up to 5 sectors, then
performs a cross-sector consistency check on the **two deepest events per
sector**:

$$\bigl|T_{14}^{(s)} - T_{14}^{(s')}\bigr| \;\le\; 0.05\;\mathrm{h}$$

Events are grouped by duration (this ±0.05 h tolerance) and by period
(±2 % fractional tolerance, `PERIOD_MATCH_TOL_FRAC` in `pipeline.py`).
Up to **two distinct objects** are reported per target. An object is
flagged **confirmed** when it appears in ≥ 2 sectors with matching
duration *and* period. This separates a genuine repeating planet from a
second transit signal of different duration on the same star.

For each identified object the pipeline recomputes the **HCI** using the
real multi-sector detection counts, and re-extracts the full **ExoMiner**
view set from the sector showing that object's deepest event.

---

## 13. Time-system conventions

TESS times in BJD-2457000 ("BTJD") are converted to BJD for catalogue
cross-matching and ExoFOP submission:

$$\mathrm{BJD} \;=\; \mathrm{BTJD} \;+\; 2{,}457{,}000$$

This conversion is applied automatically to the fitted transit epoch when
filling the ExoFOP TOI parameter table and the BJD epoch helper in the UI.
The pipeline never silently mixes BTJD and BJD: the table labels make the
system explicit at every step.

Kepler legacy data are stored in BKJD = BJD − 2454833; the parser converts
to BJD before any downstream calculation.

---

## 14. References

- **STEHM** — Hill, Kane, Foley & Schaefer (2026), *Smaller Than Earth
  Habitability Model.* arXiv:2605.00170.
- **Kopparapu** — Kopparapu et al. (2013, 2014), ApJ 765 131 & ApJ 787 L29.
- **Seager & Mallén-Ornelas (2003)** — ApJ 585 1038, the
  density-from-transit relation.
- **Csizmadia (2020)** — MNRAS 496 4442 (TLCM). Equations 59, 70 and
  Appendix A are used directly.
- **Pecaut & Mamajek (2013)** — ApJS 208 9 (dwarf-sequence table).
- **Kovács, Zucker & Mazeh (2002)** — A&A 391 369 (BLS).
- **Press & Rybicki (1989)** — ApJ 338 277 (Lomb-Scargle).
- **Valizadegan et al. (2022)** — ApJ 926 120 (ExoMiner).
- **Chen & Kipping (2017)** — ApJ 834 17 (Forecaster mass-radius).
- **Mandel & Agol (2002)** — ApJ 580 L171 (analytic transit model used by
  the SPOC DV fit Vetstar overlays).
- **Clubb (2008)** — JAAVSO 36 75 (RV semi-amplitude closed form).
- **Tian et al. (2009)** — GRL 36 L02205 (CO₂ escape, referenced by STEHM).
- **Kite & Barnett (2020)** — PNAS 117 18264 (exoplanet secondary
  atmospheres, referenced by STEHM).
- **Paczyński (1986)** — ApJ 304 1 (single-lens gravitational microlensing
  magnification, the closed form used by Module A).
- **Davenport et al. (2014)** — ApJ 797 122 (empirical stellar flare
  light-curve template — polynomial rise + double-exponential decay).
- **Schwarz (1978)** — Annals of Statistics 6 461 (Bayesian Information
  Criterion, the model-selection score used to compare PSPL / flare / null).
- **Ricker et al. (2015)** — JATIS 1 014003 (TESS mission — orbit, sector
  cadence, camera layout).
- **Burke et al. (2020)** — RNAAS 4 176 (`tess-point` package — the
  coordinate → sector/camera/CCD resolver used by Module B).
- **Harris, Dragomir, Bachelet, Fausnaugh & Johnson (2026)** — ApJL 1005 L33
  ([DOI 10.3847/2041-8213/ae7a50](https://iopscience.iop.org/article/10.3847/2041-8213/ae7a50)) —
  TESS's first bound-planet microlensing detection (Gaia23bra). Their
  pyLIMA joint TESS + Gaia fit sets the standard for breaking the
  θ_E ↔ M_L degeneracy in TESS-only photometry. Their difference-image
  photometry methodology, single-sector limitations, and Gaia-alerts +
  Gaia G-band baseline strategy inform the caveats surfaced by the
  Vetstar Microlensing pipeline (see §18).

---

## 17. Observable parameters and predicted planet quantities

The classifier's response includes an `observables` block (derived from
the PSPL fit alone) and a `planet_predictions` block (fiducial-lens
physical scales plus a planetary-anomaly detection floor). Both are
rendered in the on-screen results panel and included in the PDF vetting
report at `POST /api/microlensing/report`. Backend:
`backend/app/microlensing.py::compute_observables` and
`compute_planet_predictions`.

### 17.1 Peak magnification, brightening, and FWHM

At closest approach $u = u_0$ the intrinsic PSPL magnification is

$$A_\mathrm{peak} = \frac{u_0^2 + 2}{u_0 \sqrt{u_0^2 + 4}}$$

With blending, the aperture measures a blended peak

$$A_\mathrm{obs,peak} = \frac{f_s \cdot A_\mathrm{peak} + f_b}{f_s + f_b}$$

and the peak brightening in magnitudes is

$$\Delta m = -2.5 \log_{10} A_\mathrm{obs,peak}$$

The magnification full-width-at-half-maximum in days is obtained by
inverting $A(u_\mathrm{half}) = (A_\mathrm{peak} + 1)/2$ for
$u_\mathrm{half} \ge u_0$ (Paczyński's A is monotone decreasing in $u$
for $u \ge u_0$), then

$$\mathrm{FWHM} = 2 \, t_E \sqrt{u_\mathrm{half}^2 - u_0^2}$$

The classifier solves this numerically by bisection on 80 iterations
(convergence to $\sim 10^{-24}$ relative precision).

### 17.2 Einstein-crossing duration and blend fraction

The **Einstein-crossing duration** is the total time the source spent
inside the Einstein ring ($u < 1$, magnification above the canonical
$3/\sqrt{5} \approx 1.342$ threshold at $u = 1$):

$$T_{u<1} = 2 \, t_E \sqrt{1 - u_0^2} \quad \text{when } u_0 < 1$$

For $u_0 > 1$ the source never enters the ring and the duration is 0.
The **source flux fraction** $g_\mathrm{s} = f_s / (f_s + f_b)$ and its
complement (blend fraction) measure how much of the aperture flux
comes from the lensed source vs. contaminating neighbours in the TESS
21″ pixel footprint — a large blend depresses the observed
$\Delta m$ relative to the intrinsic PSPL amplitude and is a major
systematic in crowded fields.

### 17.3 Fiducial-lens physical scales (θ_E, r_E, v_rel)

Under fiducial bulge-lens priors — $M_L = 0.3\,M_\odot$,
$D_L = 6\,\mathrm{kpc}$, $D_S = 8\,\mathrm{kpc}$ (Sumi et al. 2011
median values) — the relative parallax is
$\pi_\mathrm{rel} = 1\,\mathrm{AU}\,(1/D_L - 1/D_S)$, giving

$$\theta_E = \sqrt{\kappa \, M_L \, \pi_\mathrm{rel}}$$

with $\kappa = 8.144\,\mathrm{mas}/M_\odot$. The physical Einstein
radius at the lens is $r_E = \theta_E \cdot D_L$ (in AU when
$\theta_E$ is in mas and $D_L$ in kpc, by definition), and the
transverse relative velocity is $v_\mathrm{rel} = r_E / t_E$.

These scale as $\sqrt{M_L \pi_\mathrm{rel}}$, so a factor-of-2 shift in
either input moves them by $<30\%$ — order-of-magnitude reliable
without additional constraints.

### 17.4 Planet-detection sensitivity floor

A single-lens fit cannot detect a planet — that requires a **binary-lens
fit** with a resolved caustic-crossing anomaly (Han & Gould 1997). What
we CAN report is the sensitivity floor: the minimum planet-to-lens mass
ratio $q_\mathrm{min}$ whose caustic anomaly would be resolvable on the
current light curve given cadence. The planetary caustic anomaly
duration for a mass ratio $q$ is

$$\Delta t_\mathrm{anom} \approx 2 \, t_E \, \sqrt{q}$$

Requiring $\Delta t_\mathrm{anom} \ge 2 \times \Delta t_\mathrm{cadence}$
(so the anomaly spans at least two cadences and is unambiguous) gives

$$q_\mathrm{min} \approx \left( \frac{\Delta t_\mathrm{cadence}}{t_E} \right)^{2}$$

At the fiducial 1-hour effective cadence (TESS 2-min SPOC binned to
match ~10-min FFI cadence typical for microlensing follow-up) and a
$t_E = 5\,\mathrm{d}$ event, this yields $q_\mathrm{min} \sim 1.7
\times 10^{-5}$ — corresponding to a planet mass floor of $\sim 1.7\,
M_\oplus$ at the fiducial $M_L = 0.3\,M_\odot$. Longer events push the
floor lower ($t_E = 30\,\mathrm{d}$ gives $q_\mathrm{min} \sim
5\times 10^{-7}$).

This is a **theoretical floor** — real detection also needs adequate
SNR through the anomaly, a well-characterised baseline, and (for TESS)
long-baseline Gaia photometry to lock down the geometry (see §18).

---

## 18. TESS-specific microlensing caveats (Harris et al. 2026)

The Vetstar Microlensing pipeline's methodological caveats and best
practices follow the first bound-planet microlensing detection in TESS
data:

**Harris, M., Dragomir, D., Bachelet, E., Fausnaugh, M. & Johnson, S.
(2026), *TESS's First Bound Microlensing Planet — A Binary
Microlensing Event Revealing a Planetary Companion toward the
Galactic Plane*, ApJL 1005, L33.
[DOI:10.3847/2041-8213/ae7a50](https://iopscience.iop.org/article/10.3847/2041-8213/ae7a50)**

Their analysis of Gaia23bra combined TESS difference-image photometry
(PSF-matched image-subtraction kernels — the same procedure Fausnaugh
et al. 2023c developed for TESS transient photometry) with binned Gaia
G-band alerts, then fit both jointly using `pyLIMA` (Bachelet et al.
2017, 2024) with a uniform-source binary-lens (USBL) model.
The joint fit yields a K-dwarf host with
$M_L = 0.79^{+0.19}_{-0.17}\,M_\odot$ and a Jovian companion with
$M_P = 1.63^{+0.42}_{-0.38}\,M_\mathrm{Jup}$ at projected separation
$a_{\perp,\mathrm{min}} \approx 4.8\,\mathrm{AU}$.

### 18.1 Why TESS-only fits are not enough

Three findings from Harris et al. that inform how this pipeline
qualifies TESS-only results:

1. **Short single-sector baseline** — TESS observes each field for
   $\sim 27\,\mathrm{d}$, comparable to a typical bulge-lens $t_E$.
   The wings of the magnification profile often extend beyond the
   sector, leaving the baseline poorly constrained and the fit
   partly degenerate. The Coverage tool's `wings_in_window` flag
   requires $t_0 \pm 1\cdot t_E$ inside the sector as a partial
   mitigation, but sub-sector-baseline events remain systematically
   worse-fit than long-baseline surveys.
2. **21″ pixel scale** — the source is almost always blended with
   nearby stars, biasing $f_s / f_b$. Harris et al. resolved this
   with difference-image photometry against a reference stack; SPOC
   PDCSAP photometry does not attempt this. Vetstar's classifier
   fits blending as free parameters, but the result is
   under-constrained without external source identification (e.g.
   Gaia crossmatch).
3. **Single-band photometry** — TESS is a broad-band imager. The
   classical achromaticity test that distinguishes microlensing from
   variables cannot be run on TESS alone. The classifier surfaces
   this as an explicit `notes` entry on every fit.

### 18.2 Best-practice recommendation

For events that Vetstar's classifier flags as **microlensing** with
high confidence, follow-up should include:

- **Gaia baseline photometry** in the G band (Gaia Alerts pull, or
  Gaia DR3 epoch photometry when available) — this doubles or triples
  the baseline for typical bulge events, dramatically reducing the
  $t_E \leftrightarrow u_0$ degeneracy.
- **Difference-image photometry** on the TESS FFIs at the target's
  centroid — SPOC PDCSAP dilutes the microlensing signal by including
  the full pixel neighbourhood. Fausnaugh et al. (2023c) and Harris
  et al. (2026) describe the standard workflow.
- **Joint modelling** with `pyLIMA` (Bachelet et al. 2017) or an
  equivalent — a single-lens fit is a starting point, but a
  binary-lens (`USBL`) fit is required if you want to detect a
  planetary companion.

### 18.3 Joint TESS + Gaia fit (implemented)

The Vetstar Microlensing pipeline now ships this workflow directly. A
new `POST /api/microlensing/fit_joint` endpoint accepts TESS flux
arrays and Gaia G-band magnitude arrays, and fits a shared PSPL
geometry (single `t0`, `tE`, `u0`) with **per-band blending**
$(f_s^\mathrm{T}, f_b^\mathrm{T})$ and $(f_s^\mathrm{G}, f_b^\mathrm{G})$.

Gaia magnitudes are converted to relative flux via
$F_G / F_{G,\mathrm{base}} = 10^{-0.4 (G - G_\mathrm{base})}$ with
propagated errors $\sigma_F = F \cdot 0.4 \ln 10 \cdot \sigma_G$. The
Gaia baseline magnitude is estimated as the median of out-of-event
epochs (points more than $\pm 100$ d from `t0_guess`), with a
faint-tail-quantile fallback for events without out-of-event coverage.

Errors follow the Kruszyńska et al. (2022, A&A 662, A59) calibration
approximation

$$\sigma^2_\mathrm{calibrated} = (\alpha \cdot \sigma_\mathrm{reported})^2 + \sigma^2_\mathrm{sys}$$

with $\alpha = 1.5$ and $\sigma_\mathrm{sys} = 3\,\mathrm{mmag}$ — a
single-coefficient approximation to their per-magnitude table.
Substitute the full table (their Table 2) for research-grade fits.

Times share a single system: Gaia JD(TCB) is converted to BTJD by
subtracting 2 457 000. The TCB↔TDB offset (seconds) is neglected —
negligible against typical microlensing timescales of days to weeks.

Residuals from both bands are concatenated with per-cadence
weights $r_i = (f_i - F_\mathrm{PSPL}(t_i)) / \sigma_i$ and fitted with
`scipy.optimize.least_squares` (7 free parameters). The response
returns per-band $\chi^2$ split for goodness diagnostics plus a
combined BIC and the observables/planet-prediction blocks recomputed
from the joint fit's shared geometry.

The frontend Classifier exposes this via a **Gaia baseline** panel
(alert-ID input, cone search on the Gaia Alerts master index) and a
**Fit joint (TESS + Gaia)** action button. See §5 of Harris et al. 2026
for the reference implementation using `pyLIMA`; the Vetstar
implementation is scipy-based and single-lens only. Binary-lens
(planetary anomaly) joint fits remain future work.

---

*To regenerate the equation PNGs after editing any LaTeX, run

## 15. Microlensing pipeline — Module A: model-comparison classifier

Given a user-flagged positive excursion in a light curve (window
`[t_start, t_end]` with a peak guess `t0_guess`), the classifier fits three
competing forward models on the windowed flux and returns a verdict via
Bayesian Information Criterion. Backend: `backend/app/microlensing.py`.

### 15.1 Paczyński single-lens (PSPL) magnification

For a point-source point-lens (PSPL) geometry with impact parameter `u₀`
and Einstein-crossing timescale `tE`, the projected lens-source separation
in units of the Einstein radius as a function of time is

$$u(t) = \sqrt{u_0^2 + \left(\frac{t - t_0}{t_E}\right)^2}$$

and the resulting **magnification** is Paczyński's (1986) closed form

$$A(u) = \frac{u^2 + 2}{u \, \sqrt{u^2 + 4}}$$

Note that $A(u) \to 1$ as $u \to \infty$ (no lensing far from the peak)
and $A \to \infty$ as $u \to 0$ (caustic-approach divergence — real
events are moderated by finite-source effects the classifier ignores by
construction, keeping only the point-source point-lens fit).

With blending — a common necessity because the source and any
unresolved neighbours share the TESS aperture — the observed
normalised flux is

$$F_{\mathrm{obs}}(t) = f_s \cdot A(t) + f_b$$

with $f_s + f_b \approx 1$ under the pre-fit baseline normalisation
(§15.3). Both are left as free parameters so the fit can absorb residual
baseline offset; expect $f_s + f_b \approx 1$ when the fit is good.

**Five free parameters**: $t_0, t_E, u_0, f_s, f_b$. Initial guesses are
$t_0 = t_{0,\mathrm{guess}}$, $t_E = \frac{1}{4}(t_\mathrm{end} -
t_\mathrm{start})$, $u_0 = 0.3$, $f_s = 0.8$, $f_b = 0.2$; bounds keep
$t_E, u_0 > 0$ and $f_s, f_b \ge 0$. The fit uses
`scipy.optimize.least_squares` with residuals
$r_i = (f_i - F_\mathrm{obs}(t_i)) / \sigma_i$; parameter errors are
extracted from the Jacobian as `pcov = (JᵀJ)⁻¹ · σ²_resid` (the
`curve_fit` recipe) — linearised 1σ, expect them to be optimistic near
degenerate corners of the (`u_0`, `tE`, `f_s`) subspace.

> **On MulensModel.** The implementation uses the closed form above rather
> than `MulensModel.Model` because both return identical output for the
> point-source point-lens case — verified in
> `backend/tests/test_microlensing.py::test_pspl_matches_mulensmodel_reference`,
> which pins the closed form against `mm.Model.get_magnification` to
> $< 10^{-12}$ absolute agreement across three parameter regimes. The
> parity test uses `pytest.importorskip` so environments without
> MulensModel installed still get a clean test run. `MulensModel` remains
> in `requirements.txt` so future work (parallax, finite-source, binary
> lens) can drop in without a re-install; the runtime path stays
> single-file, dep-light, and CI-portable.

### 15.2 Davenport-2014 empirical flare template

Stellar flares are the dominant astrophysical impostor for a
short-duration PSPL peak on TESS cadence data. The classifier fits the
Davenport et al. (2014, ApJ 797 122) empirical template — derived from
Kepler short-cadence flare stacks — parameterised by peak time
$t_\mathrm{peak}$, amplitude $A_f$, and full-width-at-half-maximum
$\Delta t_{1/2}$ (FWHM).

Normalised time: $\tilde{t} = (t - t_\mathrm{peak}) / \Delta t_{1/2}$.

**Rise** ($-1 \le \tilde{t} \le 0$) — quartic polynomial:

$$T_\mathrm{rise}(\tilde{t}) = 1 + 1.941\,\tilde{t}
                                - 0.175\,\tilde{t}^2
                                - 2.246\,\tilde{t}^3
                                - 1.125\,\tilde{t}^4$$

**Decay** ($\tilde{t} > 0$) — double exponential:

$$T_\mathrm{decay}(\tilde{t}) = 0.6890 \, e^{-1.600\,\tilde{t}}
                                + 0.3030 \, e^{-0.2783\,\tilde{t}}$$

**Full flux**: $F(t) = 1 + A_f \cdot T(\tilde{t})$, with $T$ set to zero
outside $\tilde{t} \in [-1, \infty)$.

**Three free parameters**: $t_\mathrm{peak}, A_f, \Delta t_{1/2}$.
Amplitude guess is clipped to the fit bounds so the initial point stays
strictly interior — otherwise `least_squares` raises before iterating.

The template is **asymmetric by construction** — the sharp rise plus
slow double-exponential decay is the key structural signal separating
flares from the symmetric PSPL magnification profile (see §15.5).

### 15.3 Null model and window baseline normalisation

The null (no-signal) model is a constant baseline with a single free
parameter — the weighted mean of the windowed flux,

$$F_\mathrm{null} = \frac{\sum_i f_i / \sigma_i^2}{\sum_i 1 / \sigma_i^2}$$

with $\sigma_{F} = 1/\sqrt{\sum_i 1/\sigma_i^2}$. Closed form; no
optimiser required.

Before fitting, the windowed flux is normalised to a baseline of unity by
dividing by the **weighted mean of the lower quartile** of window fluxes
(so the peak dominates but does not bias the reference level). Errors
are scaled identically. Under this normalisation the null baseline
should sit near $1.0$; the pre-normalisation baseline is returned in the
response as `window.baseline_flux` so overlays can be de-normalised back
to physical units.

**Note on narrow windows.** If the user selects a window that is
mostly peak (e.g. only a few tE wide), the 25th-percentile baseline is
biased high and the PSPL fit will absorb the offset via $f_s < 1$. The
recovered $t_0, t_E, u_0$ remain accurate in practice because blending
is a degeneracy the fit is designed to handle.

### 15.4 BIC-based model selection and verdict rules

For each fit, given $\chi^2$ (sum of squared normalised residuals),
number of parameters $k$, and number of in-window data points $N$:

$$\chi^2_\nu = \chi^2 / (N - k)$$
$$\mathrm{BIC} = \chi^2 + k \ln N$$

BIC penalises added complexity (Schwarz 1978) — a lower BIC is a better
posterior-odds bet for the model given the data. Define

$$\Delta_{\mathrm{null-PSPL}} = \mathrm{BIC}_\mathrm{null} - \mathrm{BIC}_\mathrm{PSPL}$$
$$\Delta_{\mathrm{flare-PSPL}} = \mathrm{BIC}_\mathrm{flare} - \mathrm{BIC}_\mathrm{PSPL}$$

**Verdict rules** (per the spec):

- If $\mathrm{BIC}_\mathrm{PSPL}$ is lowest AND $\Delta_{\mathrm{null-PSPL}}
  > 10$ AND $|\Delta_{\mathrm{flare-PSPL}}| \ge 6$ → **microlensing**
  (PSPL strongly preferred over both the flat baseline and the flare
  template).
- If $|\Delta_{\mathrm{flare-PSPL}}| < 6$ → **ambiguous** (PSPL and flare
  BICs are close enough that the classifier declines to pick a winner).
- If $\mathrm{BIC}_\mathrm{flare}$ is lowest AND
  $\Delta_{\mathrm{flare-PSPL}}$ is negative (flare better) → **flare**.
- Otherwise (null lowest) → **null**.

**Confidence** is a smooth map of the winning model's margin over the
runner-up:

$$C = 1 - \exp\!\left( -\frac{\max(0,\, \Delta_\mathrm{margin})}{10} \right)$$

so $C \to 1$ for margins $\gg 10$ and stays low for close calls.

The response returns both raw BICs and $\Delta\mathrm{BIC}$ pairs so the
frontend can render the ranking without recomputing.

### 15.5 Residual symmetry statistic

A supplementary diagnostic: after the PSPL fit, fold the residuals
$r_i = f_i - F_\mathrm{PSPL}(t_i)$ about the fitted $t_0$. For each
distance $\Delta t > 0$, interpolate the residual at $t = t_0 - \Delta t$
(left wing) and $t = t_0 + \Delta t$ (mirrored right wing) on a common
30-point grid spanning the shared range. Report Pearson's correlation
coefficient between the two:

- $+1$ = perfectly symmetric wings (PSPL-like).
- $\sim 0$ = uncorrelated (typically a good fit — residuals are
  noise-dominated).
- $< 0$ = anti-symmetric (unusual — often indicates fit degeneracy
  rather than physics).

The score is a *diagnostic*, not a verdict driver; it complements the
BIC ranking rather than replacing it. A poor PSPL fit through a
strongly asymmetric flare event should leave residuals whose left/right
correlation is markedly different from a well-fit PSPL through PSPL
data — but the discriminating power is not high enough to invert an
already-close BIC decision. Returned as `symmetry_score` in the
response.

**Achromaticity** — the classical *bona fide* microlensing test (event
looks the same in every band because gravitational lensing is
wavelength-independent) — is *not testable from single-band TESS
photometry*. This limitation is surfaced in the response `notes`
array; the request schema leaves a hook for an optional second-band
array to be added later.

---

## 16. Microlensing pipeline — Module B: TESS sector-overlap targeting

Given a catalog of known / candidate microlensing events (from Gaia
alerts, OGLE, MOA, KMTNet — all publish RA/Dec/`t0`), Module B
determines which events are actually observable in TESS data, so Module A
can be run against a real target list rather than blind. Backend:
`backend/app/microlensing_coverage.py`.

### 16.1 tess-point coordinate resolution

For each event, coordinates are resolved to a list of
$(\mathrm{sector}, \mathrm{camera}, \mathrm{CCD})$ triples via the
`tess-point` package (Burke et al. 2020, RNAAS 4 176). `tess-point`
implements the TESS field-of-view model — camera boresight pointings +
CCD tessellation per sector — and returns every historical (and
scheduled) sector that saw the coordinate. If `tess-point` is
unavailable the event is flagged `no_tess_point: true` in the response
and coverage is treated as null; the endpoint still returns a
well-formed payload.

### 16.2 Static TESS sector-date table

Sector observation windows are sourced from **tess-point's bundled
`TESS_Spacecraft_Pointing_Data.midtimes` table** in
`backend/app/tess_sector_dates.py`. tess-point ships the per-sector
BJD midtimes for the full mission (currently sectors 1–121) — those
midtimes are the authoritative TESS calendar the tess-point
maintainers keep updated. From each midtime we form a window
$[T_\mathrm{mid} - 13.7\,\mathrm{d},\; T_\mathrm{mid} + 13.7\,\mathrm{d}]$,
matching the two-orbit sector length (two $\sim 13.7$-day orbits
with a $\sim 1$-day perigee downlink gap). These windows are
flagged `nominal: false` in the response.

If tess-point is unavailable the module falls back to an anchor-based
approximation (Sector 1 = BTJD $1325.29$ + $N \cdot 27.4$-day
cadence). Fallback windows are flagged `nominal: true`. The response
`notes` array reports which source powered the loaded calendar via
`tess_sector_dates.calendar_source()`.

Per-orbit precision (per-sector `t_min`/`t_max` from the actual SPOC
or QLP FITS products) is not encoded here; the $\pm 13.7$-day window
is the intended granularity for observability decisions and is
accurate to well under a day at each sector edge.

### 16.3 Observability logic (wings margin)

For each returned sector window $[T_\mathrm{start}, T_\mathrm{end}]$ and
each event with peak time $t_0$ and Einstein-crossing timescale $t_E$:

$$\text{t0\_in\_window} = (T_\mathrm{start} \le t_0 \le T_\mathrm{end})$$

For the stronger "wings in-window" test with margin $m$ (default $m = 1$,
tunable via the endpoint's `margin_te` query parameter):

$$\text{wings\_in\_window} = \bigl( (t_0 - m \cdot t_E) \ge T_\mathrm{start}\bigr)
                             \; \land \;
                             \bigl( (t_0 + m \cdot t_E) \le T_\mathrm{end}\bigr)$$

An event is marked **observable** if *any* returned sector satisfies
`t0_in_window` (the peak alone is inside a covered window); the
stronger `observable_with_wings` flag additionally requires the fit
window's wings to fit for shape characterisation.

### 16.4 Bulge / ecliptic blind-zone flag

The Galactic bulge — where microlensing event rates peak — lies near
ecliptic latitude $\beta \approx -5.5°$, right in TESS's *thinnest*
coverage zone (the four TESS cameras tile out to $\sim 96°$ from the
ecliptic pole but leave a thin equatorial strip near $|\beta| \lesssim 6°$
under-covered). To flag this per-event, Module B computes ecliptic
latitude from J2000 $(\alpha, \delta)$ via

$$\sin \beta = \sin \delta \cos \varepsilon
                - \cos \delta \sin \varepsilon \sin \alpha$$

with mean obliquity $\varepsilon = 23.4392911°$ (no precession
correction — accuracy is well inside the $\pm 6°$ threshold this drives).
Any event with $|\beta| < 6°$ is flagged `in_bulge_blind_zone: true` and
surfaced in the UI (amber-highlighted ecliptic latitude column, a
summary counter, and a mandatory banner explaining that classic bulge
events are expected to come back not observable — this is not a bug).

Realistic yield: events happening at mid/high ecliptic latitudes, or
bulge-adjacent fields in the specific sectors that dipped lowest.

---

*To regenerate the equation PNGs after editing any LaTeX, run
`docs/render_equations.ps1`. The script renders via the CodeCogs online
LaTeX→PNG service so no local LaTeX install is required.*
