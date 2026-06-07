# Known-Period BLS Constraint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow users to supply a known orbital period to constrain BLS to a narrow window (±2% default) around that period and its P/2 and 2P harmonics, then reuse that period through the multi-sector reconciliation algorithm.

**Architecture:** Add `run_bls_constrained()` in `backend/app/pipeline.py`, hook it into `run_full_vetting` via a new `known_period_days` kwarg, and thread the same value through `run_multisector_analysis` for tightened cross-sector consistency and harmonic-disagreement detection. Expose via FastAPI request models and a single optional frontend input.

**Tech Stack:** Python 3 / FastAPI / astropy `BoxLeastSquares` (backend), React + TypeScript + Vite (frontend), pytest.

**Spec:** `docs/superpowers/specs/2026-06-07-known-period-bls-constraint-design.md`

**Branch:** `known-period-bls-constraint` (already created, tracking origin)

---

## File Map

- **Modify** `backend/app/pipeline.py` — add module constant, `run_bls_constrained()`, hook into `run_full_vetting`, extend `run_multisector_analysis` for reconciliation changes.
- **Create** `backend/tests/test_pipeline_known_period.py` — unit tests for constrained BLS + `run_full_vetting` integration.
- **Create** `backend/tests/test_multisector_known_period.py` — multi-sector reconciliation tests.
- **Modify** `backend/app/main.py` — add `known_period_days` to single- and multi-sector request models, validate, pass through.
- **Modify** `frontend/src/types.ts` — add `known_period_days?: number` to request type and `constrained` / `known_period_input_d` / `matched_harmonic` to BLS response type.
- **Modify** `frontend/src/api.ts` — include field in request payload when non-empty.
- **Modify** `frontend/src/App.tsx` — add input, helper text, response badge.
- **Modify** `frontend/src/glossary.ts` — add "Constrained BLS" entry.

---

### Task 1: Backend — constrained BLS and `run_full_vetting` integration

**Files:**
- Modify: `backend/app/pipeline.py` (add constant near top of module; add `run_bls_constrained` near existing `run_bls` at ~line 377; add kwarg + branch in `run_full_vetting` at ~line 1081 and ~line 1123)
- Create: `backend/tests/test_pipeline_known_period.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_pipeline_known_period.py`:

```python
"""Tests for known-period constrained BLS."""
import numpy as np
import pytest

from backend.app.pipeline import (
    BLS_KNOWN_PERIOD_TOL_FRAC,
    run_bls,
    run_bls_constrained,
    run_full_vetting,
    StarInfo,
)


def _inject_box_transit(rng, period_d=3.1, t0=1.0, depth=0.01, duration_d=0.1,
                       span_d=27.0, cadence_min=10.0, noise=0.0005):
    """Inject a clean box transit at the given ephemeris."""
    n = int(span_d * 1440.0 / cadence_min)
    t = np.linspace(0.0, span_d, n)
    f = np.ones_like(t) + rng.normal(0.0, noise, size=n)
    phase = ((t - t0) / period_d) % 1.0
    phase = np.where(phase > 0.5, phase - 1.0, phase)
    f[np.abs(phase) * period_d < duration_d / 2.0] -= depth
    fe = np.full_like(t, noise)
    return t, f, fe


def test_constrained_recovers_period_with_higher_sde():
    rng = np.random.default_rng(0)
    t, f, fe = _inject_box_transit(rng, period_d=3.1)
    blind = run_bls(t, f, fe, p_min=0.5, p_max=10.0, n_periods=2000)
    constrained = run_bls_constrained(t, f, fe, known_period_d=3.1)
    assert constrained["constrained"] is True
    assert constrained["known_period_input_d"] == pytest.approx(3.1)
    assert constrained["matched_harmonic"] == "P"
    assert abs(constrained["period"] - 3.1) / 3.1 <= BLS_KNOWN_PERIOD_TOL_FRAC
    assert constrained["sde"] >= blind["sde"]


def test_constrained_handles_two_x_alias():
    rng = np.random.default_rng(1)
    t, f, fe = _inject_box_transit(rng, period_d=3.1)
    result = run_bls_constrained(t, f, fe, known_period_d=6.2)
    assert result["matched_harmonic"] == "P/2"
    assert abs(result["period"] - 3.1) / 3.1 <= BLS_KNOWN_PERIOD_TOL_FRAC


def test_constrained_with_wrong_period_returns_low_sde():
    rng = np.random.default_rng(2)
    t, f, fe = _inject_box_transit(rng, period_d=3.1)
    result = run_bls_constrained(t, f, fe, known_period_d=10.0)
    # Function must not error; SDE will be low since no transit at 10 d.
    assert result["constrained"] is True
    assert np.isfinite(result["sde"])


def test_constrained_output_shape_matches_run_bls_plus_new_keys():
    rng = np.random.default_rng(3)
    t, f, fe = _inject_box_transit(rng, period_d=3.1)
    blind = run_bls(t, f, fe, p_min=0.5, p_max=10.0, n_periods=2000)
    constrained = run_bls_constrained(t, f, fe, known_period_d=3.1)
    for key in ("period", "t0", "duration", "depth", "power", "sde",
                "n_transits_in_window", "_periodogram"):
        assert key in constrained, f"missing {key}"
        assert key in blind
    for key in ("constrained", "known_period_input_d", "matched_harmonic"):
        assert key in constrained


def test_run_full_vetting_propagates_known_period():
    rng = np.random.default_rng(4)
    t, f, fe = _inject_box_transit(rng, period_d=3.1)
    star = StarInfo(tic_id=0, ra=0.0, dec=0.0, tmag=10.0,
                    teff_k=5800.0, radius_rsun=1.0, mass_msun=1.0)
    result = run_full_vetting(
        t, f, fe, quality=None, mom_x=None, mom_y=None,
        star=star, known_period_days=3.1,
    )
    assert result.bls.get("constrained") is True
    assert result.bls.get("known_period_input_d") == pytest.approx(3.1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_pipeline_known_period.py -v`
Expected: FAIL — `ImportError: cannot import name 'BLS_KNOWN_PERIOD_TOL_FRAC'` and `run_bls_constrained`.

- [ ] **Step 3: Add constant and `run_bls_constrained` to `backend/app/pipeline.py`**

Locate the existing `run_bls` function (around line 377) and add **directly above it**:

```python
# Default fractional half-width of the BLS search window around a known
# period, e.g. 0.02 means scan periods in [P*0.98, P*1.02].
BLS_KNOWN_PERIOD_TOL_FRAC = 0.02


def run_bls_constrained(
    t, f, fe,
    known_period_d: float,
    tol_frac: float = BLS_KNOWN_PERIOD_TOL_FRAC,
    n_periods: int = 4000,
    search_harmonics: bool = True,
) -> dict:
    """BLS over a narrow window around a known period plus P/2 and 2P harmonics.

    Returns the same dict shape as :func:`run_bls` plus three extra keys:
    ``constrained``, ``known_period_input_d``, and ``matched_harmonic``
    (one of ``"P"``, ``"P/2"``, ``"2P"``).
    """
    span = float(t.max() - t.min())
    p_max_blind = max(0.5, span * 0.7)

    sub_grids = [("P", known_period_d)]
    if search_harmonics:
        if known_period_d / 2.0 >= 0.5:
            sub_grids.append(("P/2", known_period_d / 2.0))
        if 2.0 * known_period_d <= p_max_blind:
            sub_grids.append(("2P", 2.0 * known_period_d))

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

    # Determine which sub-grid the winning period belongs to.
    best_p = float(res.period[ib])
    matched = "P"
    for label, ps in grid_labels:
        if ps[0] <= best_p <= ps[-1]:
            matched = label
            break

    sde = float((res.power[ib] - np.median(res.power)) / np.std(res.power))
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
        "known_period_input_d": float(known_period_d),
        "matched_harmonic": matched,
    }
```

- [ ] **Step 4: Add `known_period_days` kwarg to `run_full_vetting`**

In `backend/app/pipeline.py`, find the `run_full_vetting` signature (around line 1070). Add a new kwarg **after** `rotation_period_days`:

```python
    rotation_period_days: Optional[float] = None,
    known_period_days: Optional[float] = None,
    secondary_sigma: float = 3.0,
```

Find the existing call site (around line 1123):

```python
    bls = run_bls(t_c, f_c, fe_c, p_min=0.5, p_max=span * 0.7)
```

Replace with:

```python
    if (known_period_days is not None
            and np.isfinite(known_period_days)
            and 0 < known_period_days <= 0.7 * span):
        bls = run_bls_constrained(t_c, f_c, fe_c, known_period_d=known_period_days)
    else:
        bls = run_bls(t_c, f_c, fe_c, p_min=0.5, p_max=span * 0.7)
        if known_period_days is not None:
            bls["constrained_fallback_reason"] = (
                "known_period_days outside valid range"
            )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest backend/tests/test_pipeline_known_period.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /c/Users/agnes/OneDrive/Documents/claudecode/vetstar
git add backend/app/pipeline.py backend/tests/test_pipeline_known_period.py
git commit -m "feat: constrained BLS around a known period

Add run_bls_constrained() that scans ±2% windows around a user-supplied
period plus its P/2 and 2P harmonics. Wire it into run_full_vetting via
a new known_period_days kwarg with defensive fallback."
```

---

### Task 2: Backend — multi-sector reconciliation integration

**Files:**
- Modify: `backend/app/pipeline.py` — `run_multisector_analysis` (~line 1281) and supporting consensus block (~line 1348)
- Modify: `backend/app/pipeline.py` — caller(s) of per-sector vetting in the multi-sector path
- Create: `backend/tests/test_multisector_known_period.py`

- [ ] **Step 1: Locate the multi-sector caller**

Run: `grep -n "run_full_vetting\|run_multisector_analysis" backend/app/pipeline.py backend/app/main.py`

Note where `run_full_vetting` is called from the multi-sector path (likely in `main.py` or a helper in `pipeline.py`). The new `known_period_days` will be threaded through that call site in Step 4.

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_multisector_known_period.py`:

```python
"""Multi-sector reconciliation with a known period."""
import numpy as np
import pytest

from backend.app.pipeline import (
    BLS_KNOWN_PERIOD_TOL_FRAC,
    run_full_vetting,
    run_multisector_analysis,
    StarInfo,
)
from backend.tests.test_pipeline_known_period import _inject_box_transit


def _vet_sector(seed, period_d, known):
    rng = np.random.default_rng(seed)
    t, f, fe = _inject_box_transit(rng, period_d=period_d)
    star = StarInfo(tic_id=0, ra=0.0, dec=0.0, tmag=10.0,
                    teff_k=5800.0, radius_rsun=1.0, mass_msun=1.0)
    return run_full_vetting(
        t, f, fe, quality=None, mom_x=None, mom_y=None,
        star=star, known_period_days=known,
    )


def test_consensus_uses_user_known_period_label():
    sector_results = [(s, _vet_sector(s, 3.1, 3.1)) for s in (1, 2, 3)]
    out = run_multisector_analysis(sector_results, period_d=3.1,
                                   known_period_days=3.1)
    pc = out["period_consensus"]
    assert pc["source"] == "user known period (constrained BLS)"
    assert pc["value_d"] == pytest.approx(3.1)
    assert pc.get("harmonic_disagreement") is False
    assert "refined_median_d" in pc
    assert abs(pc["refined_median_d"] - 3.1) / 3.1 <= BLS_KNOWN_PERIOD_TOL_FRAC


def test_harmonic_disagreement_flagged():
    # Two sectors at true 3.1 d, one sector forced onto the 2P sub-grid
    # by passing only the 2P signal. We simulate this by giving sector 3 a
    # bls dict whose matched_harmonic is "2P".
    sr1 = _vet_sector(10, 3.1, 3.1)
    sr2 = _vet_sector(11, 3.1, 3.1)
    sr3 = _vet_sector(12, 3.1, 3.1)
    sr3.bls["matched_harmonic"] = "2P"
    out = run_multisector_analysis(
        [(1, sr1), (2, sr2), (3, sr3)],
        period_d=3.1, known_period_days=3.1,
    )
    pc = out["period_consensus"]
    assert pc["harmonic_disagreement"] is True
    assert "per_sector_matches" in pc


def test_periods_consistent_uses_tightened_tolerance_when_constrained():
    sr1 = _vet_sector(20, 3.1, 3.1)
    sr2 = _vet_sector(21, 3.1, 3.1)
    # Force a drift well outside ±2% but inside the default 5% tolerance.
    sr2.bls["period"] = 3.1 * 1.03
    out = run_multisector_analysis(
        [(1, sr1), (2, sr2)],
        period_d=3.1, known_period_days=3.1,
    )
    objs = out.get("objects", [])
    if objs:
        assert objs[0]["periods_consistent"] is False
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest backend/tests/test_multisector_known_period.py -v`
Expected: FAIL — `run_multisector_analysis` doesn't accept `known_period_days`.

- [ ] **Step 4: Extend `run_multisector_analysis`**

In `backend/app/pipeline.py`, modify the signature (around line 1281):

```python
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
    known_period_days: float | None = None,
) -> dict:
```

Replace the existing `period_consensus` block (around lines 1353–1362) with:

```python
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
```

Find the per-object `periods_consistent` call (around line 1372):

```python
        per_ok = periods_consistent(pers)
```

Replace with:

```python
        per_tol = (BLS_KNOWN_PERIOD_TOL_FRAC
                   if known_period_days is not None and np.isfinite(known_period_days)
                   else PERIOD_MATCH_TOL_FRAC)
        per_ok = periods_consistent(pers, tol_frac=per_tol)
```

- [ ] **Step 5: Thread `known_period_days` at the multi-sector call site**

Find the caller of `run_multisector_analysis` (use Step 1's grep output). Update the call to pass `known_period_days=known_period_days` (assuming the caller already has the value from the request — it will after Task 3).

If the caller is in `backend/app/main.py`, also update its inner per-sector loop to pass `known_period_days=req.known_period_days` to `run_full_vetting`. Task 3 wires the request field; this step just ensures both calls receive it.

If `known_period_days` is not yet in scope at the caller, leave a clear comment:

```python
# known_period_days flows in from the request model added in Task 3.
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest backend/tests/test_multisector_known_period.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/pipeline.py backend/tests/test_multisector_known_period.py
git commit -m "feat: integrate known period into multi-sector reconciliation

Thread known_period_days into run_multisector_analysis, label consensus
as 'user known period (constrained BLS)', detect harmonic disagreement
across sectors, tighten per-object periods_consistent tolerance, and
always report refined median for user comparison."
```

---

### Task 3: API — request models, validation, pass-through

**Files:**
- Modify: `backend/app/main.py` — single-sector and multi-sector request models; vetting endpoints

- [ ] **Step 1: Locate the request models**

Run: `grep -n "class.*Request\|class.*Body\|known_period\|rotation_period_days" backend/app/main.py`

Identify both the single-sector and multi-sector request model classes (they likely already carry `rotation_period_days` and similar optional floats).

- [ ] **Step 2: Add field and validator to both request models**

For each request model (single- and multi-sector), add:

```python
    known_period_days: Optional[float] = Field(
        default=None,
        description=(
            "Optional. If set, BLS searches a ±2% window around this "
            "period (and its P/2 and 2P harmonics) instead of a blind "
            "sweep. Must be in (0, 1000] days."
        ),
    )

    @field_validator("known_period_days")
    @classmethod
    def _validate_known_period_days(cls, v):
        if v is None:
            return v
        if not math.isfinite(v) or v <= 0 or v > 1000:
            raise ValueError(
                "known_period_days must be a finite number in (0, 1000]."
            )
        return v
```

Add the imports at the top of `main.py` if missing:

```python
import math
from pydantic import Field, field_validator
```

(If the project uses Pydantic v1, replace `field_validator` with `validator` and `@classmethod`/`cls` accordingly to match existing validators in the file.)

- [ ] **Step 3: Pass through to pipeline calls**

In the single-sector vetting endpoint, find the `run_full_vetting(...)` call and add:

```python
        known_period_days=req.known_period_days,
```

In the multi-sector endpoint, find both:

- the per-sector `run_full_vetting(...)` call → add `known_period_days=req.known_period_days,`
- the `run_multisector_analysis(...)` call → add `known_period_days=req.known_period_days,`

- [ ] **Step 4: Smoke-test the API**

Run: `pytest backend/tests/ -k "main or api or endpoint" -v`
(If no existing API tests, run the full backend suite as a smoke check.)

Run: `pytest backend/tests/ -v`
Expected: all tests PASS, including those added in Tasks 1 and 2.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py
git commit -m "feat: accept known_period_days in vetting request models

Add validated optional field to single- and multi-sector request models
and thread it into the pipeline."
```

---

### Task 4: Frontend — input, types, response badge, glossary

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/glossary.ts`

- [ ] **Step 1: Update types**

In `frontend/src/types.ts`, locate the vetting request type (it has the existing optional fields like `rotation_period_days`) and add:

```ts
  known_period_days?: number;
```

Locate the BLS response type and add:

```ts
  constrained?: boolean;
  known_period_input_d?: number;
  matched_harmonic?: "P" | "P/2" | "2P";
  constrained_fallback_reason?: string;
```

If the multi-sector response type has a `period_consensus` shape, extend it with:

```ts
  harmonic_disagreement?: boolean;
  per_sector_matches?: [number, "P" | "P/2" | "2P" | null][];
  refined_median_d?: number;
  refined_std_d?: number;
```

- [ ] **Step 2: Update request builder**

In `frontend/src/api.ts`, in the request-building function for vetting, add (next to the existing optional-field handling):

```ts
  if (
    typeof opts.known_period_days === "number" &&
    Number.isFinite(opts.known_period_days) &&
    opts.known_period_days > 0
  ) {
    body.known_period_days = opts.known_period_days;
  }
```

Apply the same to the multi-sector request builder if it's a separate function.

- [ ] **Step 3: Add UI input and badge**

In `frontend/src/App.tsx`, locate the sensitivity / advanced controls block (it contains the existing `rotation_period_days` input). Add the new control directly below it:

```tsx
<label className="block mt-3">
  <span className="text-sm font-medium">
    Known period (days, optional)
  </span>
  <input
    type="number"
    step="any"
    min="0"
    placeholder="e.g. 3.14"
    value={knownPeriod ?? ""}
    onChange={(e) =>
      setKnownPeriod(e.target.value === "" ? undefined : Number(e.target.value))
    }
    className="mt-1 block w-full rounded border px-2 py-1"
  />
  <p className="mt-1 text-xs text-gray-600">
    If set, BLS searches a ±2% window around this period (and its P/2 and 2P
    harmonics) instead of a blind sweep. Leave blank for unconstrained search.
  </p>
</label>
```

Add the matching state hook near the other sensitivity-control hooks:

```tsx
const [knownPeriod, setKnownPeriod] = useState<number | undefined>(undefined);
```

Include it in the request payload:

```tsx
known_period_days: knownPeriod,
```

In the BLS results panel render block, add a badge that appears only when constrained:

```tsx
{result.bls?.constrained && (
  <span className="ml-2 inline-block rounded bg-blue-100 px-2 py-0.5 text-xs text-blue-800">
    Constrained search (±2% around {result.bls.known_period_input_d?.toFixed(4)} d
    , matched {result.bls.matched_harmonic})
  </span>
)}
```

For the multi-sector view, where `period_consensus` is rendered, add (only when `harmonic_disagreement` is true):

```tsx
{multi.period_consensus?.harmonic_disagreement && (
  <p className="mt-1 text-xs text-amber-700">
    Harmonic disagreement across sectors —
    {" "}
    {multi.period_consensus.per_sector_matches
      ?.map(([s, h]) => `S${s}:${h ?? "?"}`)
      .join(", ")}
    . Your input period may be off by a factor of two on some sectors.
  </p>
)}
```

- [ ] **Step 4: Glossary entry**

In `frontend/src/glossary.ts`, add an entry that follows the existing object shape:

```ts
  "Constrained BLS": {
    short: "BLS run over a narrow window around a user-supplied period.",
    long:
      "When you supply a known period, Vetstar restricts the Box Least " +
      "Squares search to a ±2% window around that period and its P/2 and " +
      "2P harmonics, instead of the blind sweep from 0.5 d to 0.7 × span. " +
      "This sharpens the recovered period, t0, duration, and depth — " +
      "which feeds cleaner inputs into the odd/even, secondary, and " +
      "transit-shape checks.",
  },
```

(Match the property names used by surrounding entries — adjust `short`/`long` to whatever keys the file already uses.)

- [ ] **Step 5: Type-check and build**

Run: `cd frontend && npm run build`
Expected: build succeeds with no TypeScript errors.

- [ ] **Step 6: Commit and push**

```bash
git add frontend/src/types.ts frontend/src/api.ts frontend/src/App.tsx frontend/src/glossary.ts
git commit -m "feat: known-period BLS UI — input, badge, glossary

Add optional 'Known period (days)' input with helper text noting the
±2% default tolerance, surface a constrained-search badge on the BLS
panel, warn on cross-sector harmonic disagreement, and add a glossary
entry."
git push
```

---

## Self-Review Result

- **Spec coverage:** Backend constrained BLS (Task 1), pipeline integration (Task 1), multi-sector reconciliation incl. harmonic disagreement + tightened tolerance + refined median (Task 2), API field + validation (Task 3), frontend input + helper text + badge + glossary (Task 4). All spec sections are mapped.
- **Placeholder scan:** No TBDs. Each step shows the actual code or the actual command. Where existing-codebase shapes (`api.ts` request builder, `glossary.ts` entry shape, Pydantic version) might differ from the assumed pattern, the plan notes "match existing pattern" with a concrete example to mirror.
- **Type consistency:** `BLS_KNOWN_PERIOD_TOL_FRAC` is defined in Task 1 and reused by name in Task 2 and the spec. The new BLS keys (`constrained`, `known_period_input_d`, `matched_harmonic`) appear identically in Tasks 1, 2, 3, and 4. `known_period_days` is the request-field / kwarg name across all four tasks.
- **Scope:** Single coherent feature, no decomposition needed.
