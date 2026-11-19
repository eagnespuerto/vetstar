from worker import tic_fallback as tf


def test_coords_list_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("NEMESIS_CACHE_DIR", str(tmp_path))
    tf.remember({"ID": ["1", "2"], "ra": [10.5, 20.25], "dec": [-5.0, 60.125]})
    tf.remember({"ID": ["1"], "ra": [10.5], "dec": [-5.0]})  # no duplicates
    assert tf.load_coords() == {"1": (10.5, -5.0), "2": (20.25, 60.125)}
    assert tf.coords_for_tic(2) == (20.25, 60.125)  # from the list, no network
    text = (tmp_path / "tic_coords.txt").read_text()
    assert text.startswith("#") and text.count("\n") == 3


def test_radius_parsing():
    import astropy.units as u
    assert tf._radius_deg(5 * u.arcsec) == (5 * u.arcsec).to(u.deg).value
    assert tf._radius_deg(0.03) == 0.03
