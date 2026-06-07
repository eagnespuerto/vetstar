# Known-Period BLS Constraint — Design

**Date:** 2026-06-07
**Status:** Approved (brainstorm)
**Applies to:** single-sector (`run_full_vetting`) and multi-sector vetting paths

## Motivation

When a user is vetting a known TOI, they often already have a period from
the TOI catalog or a prior analysis. Vetstar currently runs BLS as a blind
sweep from `0.5 d` to `0.7 × span` over 20,000 trial periods. For a known
target this wastes signal: the best peak can be polluted by aliases or
noise spikes elsewhere in the period range, and the recovered `period`,
`t0`, `duration`, and `depth` are noisier than necessary. Those quantities
feed directly into the downstream observables — odd/even check, secondary
eclipse search, transit-shape measurement, and the physics interpretation —
so a sharper BLS fit produces more accurate TOI observables.

## Scope

Add a single optional input, `known_period_days`, to both vetting entry
points. When supplied, BLS scans a narrow window around that period plus
its `P/2` and `2P` harmonics instead of the blind sweep. When omitted,
behavior is unchanged.

Out of scope: changing any downstream observable, changing the multi-sector
reconciliation logic, changing the existing `rotation_period_days` input
(which feeds the sinusoidal detrend and is a separate concept).

## Backend design

### New function in `backend/app/pipeline.py`

```python
BLS_KNOWN_PERIOD_TOL_FRAC = 0.02  # ±2% default window

def run_bls_constrained(
    t, f, fe,
    known_period_d: float,
    tol_frac: float = BLS_KNOWN_PERIOD_TOL_FRAC,
    n_periods: int = 4000,
    search_harmonics: bool = True,
) -> dict:
    """BLS over a narrow window around a known period (+ P/2, 2P harmonics)."""
```

Behavior:

1. Build up to three sub-grids:
   - `[P·(1−tol), P·(1+tol)]`
   - `[P/2·(1−tol), P/2·(1+tol)]` (only if `search_harmonics` and `P/2 > 0.5 d`)
   - `[2P·(1−tol), 2P·(1+tol)]` (only if `search_harmonics` and `2P < 0.7·span`)
2. Each sub-grid gets `n_periods // n_sub_grids` trial periods, with a
   floor of 200 per sub-grid to guard against degenerate tight-tolerance
   cases.
3. Concatenate the sub-grids and run a single
   `BoxLeastSquares(t, f, fe).power(periods, durations)` call using the
   existing `durations = [0.05, 0.1, 0.15, 0.2, 0.3]`.
4. Return the same dict shape as `run_bls()` (period, t0, duration, depth,
   power, sde, n_transits_in_window, _periodogram) plus:
   - `"constrained": True`
   - `"known_period_input_d": float`
   - `"matched_harmonic": "P" | "P/2" | "2P"` — based on which sub-grid
     contained the winning period.

The `_periodogram` payload uses the concatenated grid so the existing
frontend plot still renders (it will appear "zoomed" relative to the blind
sweep).

### `run_full_vetting` change

Add a new keyword argument:

```python
def run_full_vetting(
    ...,
    known_period_days: Optional[float] = None,
    ...,
) -> VettingResult:
```

If `known_period_days` is finite and > 0, call `run_bls_constrained`
instead of `run_bls`. All downstream code (`odd_even_check`,
`secondary_eclipse_search`, `measure_shape`, physics, verdict) consumes
the same `bls` dict shape and is unchanged.

### Multi-sector path

Thread `known_period_days` through the multi-sector orchestrator into
each per-sector `run_full_vetting` call. The cross-sector
`periods_consistent` check still runs; with a constrained search, every
sector should converge on the same period (within the existing
`PERIOD_MATCH_TOL_FRAC`), which is the desired outcome.

### Defensive fallback

If the pipeline receives `known_period_days` that is non-finite, ≤ 0, or
> `0.7 × span`, it falls back to the blind `run_bls` and records
`bls["constrained_fallback_reason"]`. The API layer is the primary
validator; this is belt-and-suspenders.

## API design

`backend/app/main.py`:

- Add `known_period_days: float | None = None` to the single-sector vetting
  request model.
- Add the same field to the multi-sector request model.
- Validation: if supplied, must be finite and in `(0, 1000]`; otherwise
  reject with HTTP 422 and a message naming the field.
- Pass through to the corresponding pipeline call.

No DB or migration changes. No new dependencies.

## Frontend design

`frontend/src/App.tsx`, `frontend/src/api.ts`, `frontend/src/types.ts`:

- Add a number input in the advanced/sensitivity controls section,
  labeled **"Known period (days, optional)"** with placeholder `e.g. 3.14`.
- Helper text directly below:

  > If set, BLS searches a ±2% window around this period (and its P/2 and
  > 2P harmonics) instead of a blind sweep. Leave blank for unconstrained
  > search.

- Empty input → omit the field from the request body (don't send `0` or
  `null`). Request type: `known_period_days?: number`.
- When the response has `bls.constrained === true`, render a small badge
  on the BLS panel: **"Constrained search (±2% around {known_period_input_d} d,
  matched {matched_harmonic})"**.

`frontend/src/glossary.ts`: add a "Constrained BLS" entry that mirrors the
helper text.

## Testing

New file `backend/tests/test_pipeline_known_period.py`:

1. Synthetic transit at P = 3.1 d injected into a noisy lightcurve. Run
   `run_bls_constrained(known_period_d=3.1)`. Assert recovered period is
   within tol, and `sde` is greater than the SDE from `run_bls()` on the
   same data.
2. Same synthetic data, but call with `known_period_d=6.2` (the 2× alias).
   Assert `matched_harmonic == "P/2"` and recovered period ≈ 3.1.
3. Same synthetic data, but call with `known_period_d=10.0` (wrong).
   Assert the function returns without error and the resulting `sde` is
   low (documenting the "trust the user" behavior; the verdict layer
   handles the low-SDE case downstream).
4. Output dict has all keys that `run_bls()` produces, plus the new ones.

Extend an existing `run_full_vetting` test to pass `known_period_days` and
assert `result.bls["constrained"] is True` and the new keys are present.

## Error handling

| Input | Behavior |
|-------|----------|
| Field omitted / `null` | Unchanged (blind BLS) |
| Negative, NaN, 0, or > 1000 | API 422 |
| Valid but > `0.7 × span` | Pipeline fallback to blind BLS, reason recorded |
| Tolerance so tight that a sub-grid would have < 200 points | Sub-grid clamped to 200 points |

## Non-goals

- No change to `rotation_period_days` or sinusoidal detrend behavior.
- No change to the multi-sector period reconciliation algorithm.
- No "lock period" mode (rejected during brainstorm in favor of a narrow
  search that still validates the input).
- No user-tunable tolerance in this iteration; the ±2% default is fixed.
  Power-user tolerance control can be a follow-up if requested.
