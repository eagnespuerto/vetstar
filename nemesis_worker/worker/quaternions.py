"""Fetch TESS engineering quaternions from STScI's public AWS mirror.

NEMESIS's ``get_qdata`` scrapes the directory listing at
https://archive.stsci.edu/missions/tess/engineering/ with the default
python-requests client, which MAST answers with 403 Forbidden. The same files
are on the ``stpubdata`` S3 bucket, which is meant for scripted access. We
download the sector's file there and save it under the name ``get_qdata``
checks first (``sector_NN_quaternion.fits``), so NEMESIS uses the cached copy
and never touches the MAST listing.

Each file is 250-470 MB, so we keep only the most recent few sectors.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

BUCKET = "https://stpubdata.s3.amazonaws.com"
PREFIX = "tess/public/engineering/"
USER_AGENT = "vetstar-nemesis-worker (+https://github.com/eagnespuerto/vetstar)"


def local_name(sector: int) -> str:
    """The file name NEMESIS's get_qdata looks for in quat_savepath."""
    return f"sector_{sector:02d}_quaternion.fits"


def find_key(sector: int, keys: list[str]) -> str | None:
    """Pick the quaternion key for a sector from a bucket listing."""
    pat = re.compile(rf"tess\d+_sector{sector:02d}-quat\.fits$")
    hits = sorted(k for k in keys if pat.search(k))
    return hits[-1] if hits else None  # latest reprocessing, if more than one


def list_keys(session) -> list[str]:
    keys: list[str] = []
    token = None
    while True:
        params = {"list-type": "2", "prefix": PREFIX + "tess"}
        if token:
            params["continuation-token"] = token
        r = session.get(f"{BUCKET}/", params=params, timeout=30)
        r.raise_for_status()
        keys += re.findall(r"<Key>([^<]+)</Key>", r.text)
        m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", r.text)
        if not m:
            return keys
        token = m.group(1)


def prune(quat_dir: Path, keep: int) -> None:
    """Delete all but the `keep` most recently used quaternion files."""
    files = sorted(quat_dir.glob("sector_*_quaternion.fits"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[keep:]:
        try:
            p.unlink()
        except OSError:
            pass


def ensure_quaternions(sector: int, quat_dir: str | Path, keep: int | None = None) -> Path | None:
    """Make sure the sector's quaternion file is in quat_dir. Fails soft.

    Returns the local path, or None if the mirror did not have it (NEMESIS
    then reports the quaternion features as not computed, as before).
    """
    import requests

    quat_dir = Path(quat_dir)
    quat_dir.mkdir(parents=True, exist_ok=True)
    keep = keep if keep is not None else int(os.environ.get("NEMESIS_QUAT_KEEP", "4"))
    dest = quat_dir / local_name(sector)
    if dest.is_file() and dest.stat().st_size > 0:
        os.utime(dest)  # mark as recently used for prune()
        print(f"[vetstar] quaternions S{sector}: cached", flush=True)
        return dest
    try:
        with requests.Session() as s:
            s.headers["User-Agent"] = USER_AGENT
            key = find_key(sector, list_keys(s))
            if not key:
                print(f"[vetstar] quaternions S{sector}: not on the AWS mirror", flush=True)
                return None
            print(f"[vetstar] quaternions S{sector}: downloading {key.rsplit('/', 1)[-1]} from the AWS mirror", flush=True)
            part = dest.with_suffix(".part")
            with s.get(f"{BUCKET}/{key}", stream=True, timeout=300) as r:
                r.raise_for_status()
                with open(part, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
            part.replace(dest)
    except Exception as exc:
        print(f"[vetstar] quaternions S{sector}: mirror download failed: {exc}", flush=True)
        return None
    prune(quat_dir, keep)
    return dest
