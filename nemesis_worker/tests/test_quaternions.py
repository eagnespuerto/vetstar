import os
import time

from worker.quaternions import find_key, local_name, prune
from worker.run_job import collect_raw_fits

KEYS = [
    "tess/public/engineering/tess2021146082523_sector38-emi.fits",
    "tess/public/engineering/tess2021146082523_sector38-eng.fits",
    "tess/public/engineering/tess2021146082523_sector38-quat.fits",
    "tess/public/engineering/tess2018331094053_sector01-quat.fits",
    "tess/public/engineering/tess2025001000000_sector100-quat.fits",
]


def test_find_key_picks_the_sector_quat_file():
    assert find_key(38, KEYS).endswith("_sector38-quat.fits")
    assert find_key(1, KEYS).endswith("_sector01-quat.fits")
    assert find_key(100, KEYS).endswith("_sector100-quat.fits")
    assert find_key(2, KEYS) is None


def test_local_name_matches_nemesis_get_qdata():
    # nemesis_tess.pipeline.get_qdata caches as sector_{sector:02d}_quaternion.fits
    assert local_name(3) == "sector_03_quaternion.fits"
    assert local_name(38) == "sector_38_quaternion.fits"


def test_prune_keeps_newest(tmp_path):
    for i, s in enumerate((1, 2, 3)):
        p = tmp_path / local_name(s)
        p.write_bytes(b"x")
        os.utime(p, (time.time() + i, time.time() + i))
    prune(tmp_path, keep=2)
    assert sorted(p.name for p in tmp_path.iterdir()) == [local_name(2), local_name(3)]


def test_collect_raw_fits_moves_new_cutouts_and_copies_tpfs(tmp_path):
    cache, out = tmp_path / "cache", tmp_path / "out"
    (cache / "TESSCut").mkdir(parents=True)
    (cache / "TESS").mkdir()
    out.mkdir()
    old = cache / "TESSCut" / "tess-s0001-1-1_1_2_11x11_astrocut.fits"
    old.write_bytes(b"old")
    os.utime(old, (1, 1))
    new = cache / "TESSCut" / "tess-s0038-3-2_177_-62_11x11_astrocut.fits"
    new.write_bytes(b"new")
    tpf = cache / "TESS" / "tess2021118034608-s0038-0000000322418709-0208-s_tp.fits"
    tpf.write_bytes(b"tpf")
    other = cache / "TESS" / "tess2021118034608-s0038-0000000000000001-0208-s_tp.fits"
    other.write_bytes(b"x")

    got = collect_raw_fits(cache, out, 322418709, since=time.time() - 60)

    assert sorted(got) == sorted([new.name, tpf.name])
    assert not new.exists() and old.exists()  # cutout moved, stale one untouched
    assert tpf.exists()                        # TPF copied, cache kept
    assert sorted(p.name for p in (out / "fits").iterdir()) == sorted([new.name, tpf.name])
