"""Make NEMESIS's TESScut cutout from STScI's public AWS cubes.

NEMESIS asks MAST's TESScut service for an FFI cutout, and that service is
sometimes slow or briefly down (a job once hung at "[lksearch] trying
radius" for many minutes). The full-frame-image cubes TESScut cuts from are
also on the public ``stpubdata`` S3 bucket, and ``astrocut`` (the library
TESScut itself uses) can cut straight from them, reading only the pixels it
needs.

NEMESIS looks in its cache before it searches MAST (``_get_cached_fits``):
``<cache>/TESSCut/tess-sSSSS-C-D_<ra:.6f>_<dec:.6f>_<N>x<N>_astrocut.fits``
with the TIC coordinates. So we cut the stamp from AWS, save it under that
name, and NEMESIS uses it without calling TESScut. Anything that fails here
is logged and NEMESIS falls back to MAST as before.
"""
from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

CUBE_URI = "s3://stpubdata/tess/public/mast/tess-s{sector:04d}-{camera}-{ccd}-cube.fits"
FFI_CADENCES = {"30 minute", "10 minute", "200 second"}


def cache_name(sector: int, camera: int, ccd: int, ra: float, dec: float, size: int) -> str:
    """The file name NEMESIS's cache-first lookup tries first."""
    return f"tess-s{int(sector):04d}-{int(camera)}-{int(ccd)}_{ra:.6f}_{dec:.6f}_{int(size)}x{int(size)}_astrocut.fits"


def camera_ccd(tic: int, ra: float, dec: float, sector: int) -> Optional[tuple[int, int]]:
    """(camera, ccd) that saw these coordinates in this sector, via tess-point."""
    from tess_stars2px import tess_stars2px_function_entry as t2p

    out = t2p(int(tic), float(ra), float(dec), trySector=int(sector))
    sectors, cams, ccds = out[3], out[4], out[5]
    for s, c, d in zip(sectors, cams, ccds):
        if int(s) == int(sector) and int(c) > 0:
            return int(c), int(d)
    return None


def ensure_tesscut(tic: int, sector: int, cadence: str, ra: Optional[float], dec: Optional[float],
                   size: int, cache_dir: str | Path) -> Optional[Path]:
    """Put the AWS-made cutout where NEMESIS looks first. Fails soft (returns None)."""
    if cadence not in FFI_CADENCES or ra is None or dec is None:
        return None
    try:
        cc = camera_ccd(tic, ra, dec, sector)
        if cc is None:
            print(f"[vetstar] TESScut from AWS: S{sector} does not cover this star", flush=True)
            return None
        camera, ccd = cc
        dest_dir = Path(cache_dir) / "TESSCut"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / cache_name(sector, camera, ccd, ra, dec, size)
        if dest.is_file() and dest.stat().st_size > 0:
            print(f"[vetstar] TESScut from AWS: cached {dest.name}", flush=True)
            return dest

        import astrocut
        from astropy.coordinates import SkyCoord

        t0 = time.time()
        uri = CUBE_URI.format(sector=int(sector), camera=camera, ccd=ccd)
        print(f"[vetstar] TESScut from AWS: cutting {size}x{size} px from {uri.rsplit('/', 1)[-1]}", flush=True)
        with tempfile.TemporaryDirectory() as tmp:
            out = astrocut.cube_cut(uri, SkyCoord(ra, dec, unit="deg"), int(size), output_path=tmp, verbose=False)
            shutil.move(str(out), dest)
        print(f"[vetstar] TESScut from AWS: saved {dest.name} in {time.time() - t0:.0f} s", flush=True)
        return dest
    except Exception as exc:
        print(f"[vetstar] TESScut from AWS failed ({type(exc).__name__}: {exc}); NEMESIS will ask MAST", flush=True)
        return None
