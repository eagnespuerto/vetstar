"""Figures render from synthetic NEMESIS-shaped data."""
import base64

import numpy as np
import pandas as pd

from worker.figures import all_figures


def _data():
    rng = np.random.default_rng(1)
    t = np.arange(1842.0, 1868.0, 1 / 48)
    P, t0, depth, dur = 3.446, 1845.514, 0.77e-3, 1.66 / 24
    ph = ((t - t0) / P + 0.5) % 1 - 0.5
    model = np.where(np.abs(ph * P) < dur / 2, 1 - depth, 1.0)
    f = model + rng.normal(0, 4e-4, len(t))
    lc = pd.DataFrame({"Time": t, "Detrended Flux": f})
    ps = pd.DataFrame({"TLS Periods": np.linspace(0.5, 13, 3000),
                       "TLS SDE": rng.random(3000) * 3})
    mdl = pd.DataFrame({"Time": t, "Model": model})
    params = {"method": "TLS", "period_d": P, "t0_btjd": t0, "duration_hr": 1.66,
              "depth_ppt": 0.77, "radius_earth": 1.9}
    return lc, (None, None, None, ps, mdl, None), params


def test_all_figures_render(tmp_path):
    lc, tr, params = _data()
    figs = all_figures(lc, tr, "TLS", params, 103633434, 20, "30 minute")
    assert set(figs) == {"fold", "periodogram", "light_curve", "odd_even"}
    for name, b64 in figs.items():
        assert b64, name
        png = base64.b64decode(b64)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        (tmp_path / f"{name}.png").write_bytes(png)


def test_missing_period_is_soft():
    lc, tr, params = _data()
    figs = all_figures(lc, tr, "TLS", {"method": "TLS"}, 1, 1, "30 minute")
    assert figs["fold"] is None and figs["light_curve"]
