# Sinusoidal-regression vetting + secondary-eclipse σ toggle

**Date:** 2026-06-01
**Status:** Design approved, pending implementation plan

## Goal

Two new user-facing knobs that improve dip detection on variable stars and let users tune false-positive sensitivity for eclipsing-binary checks:

1. **"High stellar variability" toggle** — when enabled, fit a sinusoid + first harmonic to the light curve and subtract it before BLS. Helps BLS find shallow dips on spotted rotators and other wave-like variables.
2. **"Secondary eclipse σ" slider** — replaces the hard-coded 3σ threshold inside `secondary_eclipse_search()` (`backend/app/pipeline.py:637`) with a per-request value in the range 1σ – 7σ (default 3σ).

Both toggles apply equally to single-sector and multi-sector vetting and are wired through the existing `DetectParams` payload.

## Non-goals

- Iterative prewhitening (fit-subtract-refit beyond first harmonic).
- Gaussian-process / spline detrending.
- Per-sector custom rotation periods in a multi-sector run (one optional period, applied per-sector).
- Tunable thresholds for any check other than secondary eclipse.

## UI

### Sinusoidal toggle

- **Location.** Next to the existing single/multi sector mode buttons in `frontend/src/App.tsx` — it is a vetting-mode decision, not a sensitivity tweak.
- **Control.** Checkbox labelled "High stellar variability (detrend before BLS)".
- **Expansion.** When checked, reveals a small optional number input "Expected rotation period (days)". Blank ⇒ use the Lomb-Scargle peak.
- **State.** Two new fields on `DetectParams`: `highVariability: boolean` and `rotationPeriod: number | null`.

### Secondary-eclipse σ slider

- **Location.** Inside the existing collapsible "Detection sensitivity" panel, below the existing threshold and min-SNR controls.
- **Control.** Range slider 1.0 – 7.0 step 0.1, default 3.0, plus a numeric readout.
- **State.** New `DetectParams.secondarySigma: number`.

### Glossary

Add `glossary.ts` entries: `high_stellar_variability`, `rotation_period_days`, `secondary_sigma`, `detrend_amplitude`.

## Backend — sinusoidal regression

### New module `backend/app/detrend.py`

Two functions:

```python
@dataclass
class SinusoidFit:
    period_days: float
    C: float
    A1: float; B1: float
    A2: float; B2: float
    amplitude_ppm: float              # sqrt(A1² + B1²), scaled to ppm of median flux
    harmonic_amplitude_ppm: float     # sqrt(A2² + B2²)
    rms_before: float
    rms_after: float

def fit_sinusoid(t: np.ndarray, f: np.ndarray, period_days: float) -> SinusoidFit: ...
def apply_detrend(f: np.ndarray, fit: SinusoidFit, t: np.ndarray) -> np.ndarray: ...
```

**Model.** Linear least-squares on the design matrix
`[1, sin(2πt/P), cos(2πt/P), sin(4πt/P), cos(4πt/P)]`
via `numpy.linalg.lstsq`. Six coefficients (`C, A₁, B₁, A₂, B₂`) with `P` fixed. No iteration.

**Skip condition.** If `amplitude_ppm < per_cadence_noise_ppm`, the fit is considered noise and detrending is skipped (`applied=False`, `reason="skipped_low_amplitude"`).

### Pipeline wiring (`backend/app/pipeline.py`)

After Lomb-Scargle, before `run_bls`:

```python
if params.high_variability:
    period = params.rotation_period_days or ls_peak_period
    fit = fit_sinusoid(t, f, period)
    if fit.amplitude_ppm >= noise_floor_ppm:
        f = apply_detrend(f, fit, t)
        detrend_meta = {"applied": True, "reason": "user_period" if params.rotation_period_days else "ls_peak", **fit.as_dict()}
    else:
        detrend_meta = {"applied": False, "reason": "skipped_low_amplitude", ...}
else:
    detrend_meta = {"applied": False, "reason": "disabled"}
```

The detrended `f` is then passed unchanged into `run_bls`, odd/even, and `secondary_eclipse_search`. `detrend_meta` is attached to the result.

### Multi-sector

Per-sector fit + per-sector residual, then concatenated for the multi-sector BLS pass. Stellar rotation phase is not preserved across multi-month gaps, so a global fit would be wrong. Each sector entry in the multi-sector result carries its own `detrend` block.

### Secondary-eclipse σ

- `secondary_eclipse_search()` at `pipeline.py:617` gains a `secondary_sigma: float = 3.0` parameter; the hard-coded `sigma > 3` at `pipeline.py:637` becomes `sigma > secondary_sigma`.
- `pipeline.py:813` already keys off `secondary.get("detected")`, so it picks up the new threshold without change.
- The odd/even `flag_eb` check at `pipeline.py:613` is **not** modified — odd/even is a separate concern from secondary eclipse.

### API surface (`backend/app/main.py`)

Add three optional fields to the request schemas for `/api/analyze`, `/api/mast/analyze`, `/api/mast/multisector`, `/api/mast/multisector/report`, and the report endpoints:

- `high_variability: bool = False`
- `rotation_period_days: float | None = None`
- `secondary_sigma: float = 3.0`  (server-side clamp to `[1.0, 7.0]`; out-of-range ⇒ 422)

`frontend/src/api.ts` serializes the three new fields alongside the existing `threshold` and `min_snr`.

## Result schema additions

Single-sector `VettingResult` and each per-sector entry in the multi-sector response:

```jsonc
{
  "detrend": {
    "applied": true,
    "reason": "user_period",          // "ls_peak" | "skipped_low_amplitude" | "disabled"
    "period_days": 7.34,
    "amplitude_ppm": 1820.0,
    "harmonic_amplitude_ppm": 410.0,
    "rms_reduction_pct": 38.2
  },
  "sensitivity": {
    "threshold": 0.997,
    "min_snr": 4.0,
    "secondary_sigma": 3.0             // echo back what was used
  }
}
```

## Plots & report

### New plot panel — "Stellar variability detrend"

Rendered in `make_plots` at `pipeline.py:879` and the report builder in `report.py`. Three stacked traces with shared x-axis:

1. Raw flux.
2. Fitted sinusoid (sin + 1st harmonic) overlay on the raw flux.
3. Residuals fed to BLS.

Only emitted when `detrend.applied == True`. Multi-sector: one mini-panel per sector.

### PDF text

One line: `"Detrended at P = X.XXX d (amplitude Y ppm, RMS reduced Z%)."` — or the skip reason when applicable.

### Site display & share button

The new plot is returned as base64 PNG in the JSON result (same convention as existing plots). The React UI renders the image inline and wraps it with the existing `<ShareToImgbbButton base64={...} title="detrend_TIC{id}_S{sector}" label="Stellar variability detrend" />` from `frontend/src/ShareButton.tsx`, identical to the pattern already used in `ExoMinerPanel` and `FfiCutoutPanel`. A small "Detrending applied" badge with period + amplitude appears above the plot.

## Testing

- **`backend/tests/test_detrend.py`** — unit:
  - Pure sinusoid input ⇒ recovered period/amplitude within 1%.
  - Sinusoid + injected box transit ⇒ BLS SDE on the residual exceeds SDE on the raw flux.
  - Flat light curve ⇒ `applied=False, reason="skipped_low_amplitude"`.
- **`backend/tests/test_secondary_sigma.py`** — unit:
  - Synthetic light curve with a known-σ secondary; verify `detected` flips at the slider boundary.
  - Out-of-range value ⇒ 422 from the API.
- **Integration** — existing pipeline test fixtures must produce byte-identical output when `high_variability=False, secondary_sigma=3.0` (i.e. the defaults preserve current behavior). Add one smoke test with `high_variability=True`.
- **Frontend** — render-test that the detrend panel and share button appear iff `detrend.applied` is true.

## Backwards compatibility

Defaults (`high_variability=False`, `secondary_sigma=3.0`) produce byte-identical pipeline output to today. No migration needed for stored results; old payloads simply lack the `detrend` and `sensitivity.secondary_sigma` keys, and the UI treats them as absent.
