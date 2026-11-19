from worker.tesscut_aws import cache_name, ensure_tesscut


def test_cache_name_matches_nemesis_first_candidate():
    # nemesis_tess.pipeline._get_cached_fits tries f"{x:.6f}" first.
    assert cache_name(20, 3, 2, 158.3109660, 69.2707440, 11) == \
        "tess-s0020-3-2_158.310966_69.270744_11x11_astrocut.fits"
    assert cache_name(9, 1, 4, 5.0, -62.5, 11) == "tess-s0009-1-4_5.000000_-62.500000_11x11_astrocut.fits"


def test_skips_non_ffi_or_missing_coords(tmp_path):
    assert ensure_tesscut(1, 20, "2 minute", 1.0, 2.0, 11, tmp_path) is None
    assert ensure_tesscut(1, 20, "30 minute", None, 2.0, 11, tmp_path) is None
