"""Download TESS files from STScI's public AWS mirror instead of MAST.

MAST's own docs point scripted bulk users to the ``stpubdata`` S3 bucket,
and archive.stsci.edu now answers some scripted clients with 403. The
mirror holds the same files. We map a MAST product ``dataURI`` to its S3
key; anything we cannot map, or that is missing on the mirror, falls back
to the normal MAST download.

    mast:TESS/product/tess...-s0020-0000000103633434-0165-s_lc.fits
      -> tess/public/tid/s0020/0000/0001/0363/3434/<same file>
    mast:HLSP/qlp/s0020/0000/0001/0363/3434/<file>   (also tess-spoc)
      -> mast/hlsp/qlp/s0020/0000/0001/0363/3434/<file>
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

BUCKET_URL = "https://stpubdata.s3.amazonaws.com"
USER_AGENT = "vetstar (+https://github.com/eagnespuerto/vetstar)"
CACHE_DIR = Path(os.environ.get("VETSTAR_AWS_CACHE", "mastDownload/aws"))

_SPOC = re.compile(r"^mast:TESS/product/(tess\d+-s(\d{4})(?:-s\d{4})?-(\d{16})-[^/]+)$")
_HLSP = re.compile(r"^mast:HLSP/((?:qlp|tess-spoc)/[^ ]+)$", re.I)


def s3_key(data_uri: str) -> Optional[str]:
    """S3 key for a MAST dataURI, or None if we don't know the layout."""
    m = _SPOC.match(data_uri or "")
    if m:
        fname, sector, tic16 = m.groups()
        parts = "/".join(tic16[i:i + 4] for i in range(0, 16, 4))
        return f"tess/public/tid/s{sector}/{parts}/{fname}"
    m = _HLSP.match(data_uri or "")
    if m:
        return f"mast/hlsp/{m.group(1)}"
    return None


def download(data_uri: str, timeout: float = 120) -> Optional[str]:
    """Fetch one file from the mirror into CACHE_DIR. Returns the local path,
    or None (caller then uses MAST). Reuses a cached copy."""
    key = s3_key(data_uri)
    if not key:
        return None
    dest = CACHE_DIR / key.rsplit("/", 1)[-1]
    if dest.is_file() and dest.stat().st_size > 0:
        return str(dest)
    import requests

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        with requests.get(f"{BUCKET_URL}/{key}", stream=True, timeout=timeout,
                          headers={"User-Agent": USER_AGENT}) as r:
            if r.status_code == 404:
                log.info("AWS mirror: %s not there, using MAST", key)
                return None
            r.raise_for_status()
            with open(part, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
        part.replace(dest)
        log.info("AWS mirror: downloaded %s", key)
        return str(dest)
    except Exception as exc:
        log.warning("AWS mirror download failed for %s (%s), using MAST", key, exc)
        return None
