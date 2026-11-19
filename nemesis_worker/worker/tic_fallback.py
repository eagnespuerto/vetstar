"""TIC lookups that survive a MAST outage.

NEMESIS asks MAST's catalog service for TIC rows (``Catalogs.query_object``
"TIC <id>" with a cone radius) and retries forever when MAST is down. This
module wraps astroquery's TIC calls in the job process:

1. Try MAST, with a time limit.
2. If that fails, answer from STScI's AWS copy of the TIC (HATS Parquet,
   ``s3://stpubdata/tess/public/hats/tic/tic``). It has MAST's exact 126
   TIC columns, so NEMESIS gets the same table shape.

The AWS copy is split by sky position, so a TIC ID first needs a position:
from ``tic_coords.txt`` (every star MAST or the fallback has returned, kept
in the cache folder), else from VizieR's TIC v8.2 (IV/39/tic82, at CDS).
Sector lists need no MAST: the worker gets them from tess-point locally.
"""
from __future__ import annotations

import concurrent.futures as cf
import csv
import os
import re
from pathlib import Path
from typing import Optional

import numpy as np

HATS = "stpubdata/tess/public/hats/tic/tic"
VIZIER_SERVERS = ("vizier.cds.unistra.fr", "vizier.cfa.harvard.edu", "vizier.iucaa.in")
MAST_TIMEOUT_S = float(os.environ.get("NEMESIS_MAST_TIMEOUT_S", "60"))
_pool = cf.ThreadPoolExecutor(max_workers=2)
_partitions: Optional[list[tuple[int, int]]] = None


# --------------------------------------------------------------------- coords list
def coords_file() -> Path:
    d = Path(os.environ.get("NEMESIS_CACHE_DIR") or "/tmp/nemesis/cache")
    d.mkdir(parents=True, exist_ok=True)
    return d / "tic_coords.txt"


def remember(rows) -> None:
    """Append TIC id, ra, dec of these rows to tic_coords.txt (a plain text list)."""
    try:
        ids, ras, decs = list(rows["ID"]), list(rows["ra"]), list(rows["dec"])
        known = load_coords()
        new = []
        for i, r, d in zip(ids, ras, decs):
            try:
                r, d = float(r), float(d)
            except (TypeError, ValueError):
                continue
            if str(i) not in known and np.isfinite(r) and np.isfinite(d):
                new.append((str(i), r, d))
    except Exception:
        return
    if not new:
        return
    path = coords_file()
    with open(path, "a", newline="") as f:
        if path.stat().st_size == 0:
            f.write("# TIC ID, RA (deg), Dec (deg): stars seen by this worker\n")
        w = csv.writer(f)
        for row in new:
            w.writerow([row[0], f"{row[1]:.7f}", f"{row[2]:.7f}"])


def load_coords() -> dict[str, tuple[float, float]]:
    out: dict[str, tuple[float, float]] = {}
    path = coords_file()
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        try:
            i, r, d = line.split(",")[:3]
            out[i.strip()] = (float(r), float(d))
        except ValueError:
            continue
    return out


def coords_for_tic(tic: int | str) -> Optional[tuple[float, float]]:
    """Position of a TIC star without MAST: the local list, then VizieR."""
    tic = str(int(tic))
    hit = load_coords().get(tic)
    if hit:
        return hit
    import requests
    for server in VIZIER_SERVERS:  # CDS, then mirrors (CDS is sometimes down)
        try:
            r = requests.get(f"https://{server}/viz-bin/asu-tsv", timeout=30, params={
                "-source": "IV/39/tic82", "TIC": tic, "-out": "TIC,RAJ2000,DEJ2000", "-out.max": "5"})
            r.raise_for_status()
            for line in r.text.splitlines():
                parts = line.split("\t")
                if len(parts) >= 3 and parts[0].strip() == tic:
                    ra, dec = float(parts[1]), float(parts[2])
                    with open(coords_file(), "a") as f:
                        f.write(f"{tic},{ra:.7f},{dec:.7f}" + chr(10))
                    return ra, dec
            print(f"[vetstar] TIC fallback: VizieR at {server} had no row for TIC {tic}", flush=True)
        except Exception as exc:
            print(f"[vetstar] TIC fallback: VizieR at {server} failed ({type(exc).__name__})", flush=True)
    return None


# --------------------------------------------------------------------- AWS cone
def _fs():
    import s3fs
    return s3fs.S3FileSystem(anon=True)


def _partition_list(fs) -> list[tuple[int, int]]:
    global _partitions
    if _partitions is None:
        with fs.open(f"{HATS}/partition_info.csv") as f:
            rows = list(csv.DictReader(line.decode() for line in f))
        _partitions = [(int(r["Norder"]), int(r["Npix"])) for r in rows]
    return _partitions


def _pix(order: int, ra, dec):
    from astropy import units as u
    from astropy_healpix import HEALPix
    return HEALPix(nside=2 ** order, order="nested").lonlat_to_healpix(np.atleast_1d(ra) * u.deg, np.atleast_1d(dec) * u.deg)


def cone(ra: float, dec: float, radius_deg: float):
    """TIC rows within radius_deg of (ra, dec), from the AWS copy, nearest first."""
    import pyarrow.compute as pc
    import pyarrow.dataset as ds
    from astropy import units as u
    from astropy.coordinates import SkyCoord
    from astropy_healpix import HEALPix

    fs = _fs()
    parts = _partition_list(fs)
    # Points on and inside the cone, to find every partition it touches.
    ang = np.linspace(0, 2 * np.pi, 16, endpoint=False)
    c0 = SkyCoord(ra * u.deg, dec * u.deg)
    pts = [c0] + [c0.directional_offset_by(a * u.rad, radius_deg * u.deg) for a in ang]
    pra = np.array([p.ra.deg for p in pts]); pdec = np.array([p.dec.deg for p in pts])
    files = set()
    for order in sorted({o for o, _ in parts}):
        have = {n for o, n in parts if o == order}
        for n in set(int(x) for x in _pix(order, pra, pdec)):
            if n in have:
                files.add(f"{HATS}/dataset/Norder={order}/Dir={(n // 10000) * 10000}/Npix={n}.parquet")
    # Order-29 index ranges covering the cone, so only matching row groups are read.
    o = 14
    hp = HEALPix(nside=2 ** o, order="nested")
    pix = hp.cone_search_lonlat(ra * u.deg, dec * u.deg, radius=(radius_deg * u.deg) + hp.pixel_resolution)
    shift = 2 * (29 - o)
    cond = None
    for p in np.unique(pix):
        lo, hi = int(p) << shift, (int(p) + 1) << shift
        c = (pc.field("_healpix_29") >= lo) & (pc.field("_healpix_29") < hi)
        cond = c if cond is None else (cond | c)
    tables = []
    for path in files:
        tables.append(ds.dataset(path, filesystem=fs, format="parquet").to_table(filter=cond))
    import pyarrow as pa
    tab = pa.concat_tables(tables).to_pandas() if tables else None
    if tab is None or tab.empty:
        return tab
    sep = SkyCoord(tab["ra"].values * u.deg, tab["dec"].values * u.deg).separation(c0)
    tab["dstArcSec"] = sep.arcsec
    tab = tab[sep.deg <= radius_deg].sort_values("dstArcSec").drop(columns=["_healpix_29"])
    tab["ID"] = tab["ID"].astype(str)          # MAST returns IDs as strings
    return tab.reset_index(drop=True)


# --------------------------------------------------------------------- the wrapper
def _radius_deg(radius) -> float:
    if radius is None:
        return 0.2
    try:
        from astropy import units as u
        return float(u.Quantity(radius).to(u.deg).value) if hasattr(radius, "unit") else float(radius)
    except Exception:
        return float(radius)


def _with_timeout(fn, *a, **k):
    return _pool.submit(fn, *a, **k).result(timeout=MAST_TIMEOUT_S)


def install() -> None:
    """Wrap astroquery's Catalogs TIC calls in this process."""
    from astropy.table import Table
    from astroquery.mast import Catalogs

    orig_object = Catalogs.query_object
    orig_criteria = Catalogs.query_criteria

    def query_object(objectname, radius=0.2, catalog="Hsc", *args, **kwargs):
        if str(catalog).upper() != "TIC":
            return orig_object(objectname, radius=radius, catalog=catalog, *args, **kwargs)
        try:
            res = _with_timeout(orig_object, objectname, radius=radius, catalog=catalog, *args, **kwargs)
            remember(res)
            return res
        except Exception as exc:
            print(f"[vetstar] MAST TIC query failed ({type(exc).__name__}); using the AWS TIC copy", flush=True)
        m = re.match(r"\s*TIC\s*(\d+)\s*$", str(objectname), re.I)
        pos = coords_for_tic(m.group(1)) if m else None
        if pos is None:
            from astropy.coordinates import SkyCoord
            c = SkyCoord(str(objectname), unit="deg")
            pos = (c.ra.deg, c.dec.deg)
        tab = cone(pos[0], pos[1], _radius_deg(radius))
        if tab is None or tab.empty:
            raise RuntimeError(f"No TIC rows near {objectname} in the AWS TIC copy")
        remember(tab)
        return Table.from_pandas(tab)

    def query_criteria(catalog="Tic", *args, **kwargs):
        if str(catalog).upper() != "TIC" or "ID" not in kwargs:
            return orig_criteria(catalog=catalog, *args, **kwargs)
        try:
            res = _with_timeout(orig_criteria, catalog=catalog, *args, **kwargs)
            remember(res)
            return res
        except Exception as exc:
            print(f"[vetstar] MAST TIC query failed ({type(exc).__name__}); using the AWS TIC copy", flush=True)
        tic = str(int(np.atleast_1d(kwargs["ID"])[0]))
        pos = coords_for_tic(tic)
        if pos is None:
            raise RuntimeError(f"TIC {tic}: no position (MAST and VizieR unavailable, not in tic_coords.txt)")
        tab = cone(pos[0], pos[1], 3 / 3600)
        tab = tab[tab["ID"] == tic] if tab is not None else tab
        if tab is None or tab.empty:
            raise RuntimeError(f"TIC {tic} not found in the AWS TIC copy")
        return Table.from_pandas(tab.reset_index(drop=True))

    Catalogs.query_object = query_object
    Catalogs.query_criteria = query_criteria
