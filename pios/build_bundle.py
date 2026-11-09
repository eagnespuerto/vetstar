#!/usr/bin/env python3
"""Build the VetStar Pi release bundle.

Packs the Pi front end (pios/) together with the shared science package
(vetstar_core/) into one tarball that Pi users download from a GitHub
release:

    vetstar-pi/
        install.sh  fetch.sh  requirements-pi.txt  README.md  VERSION
        systemd/  examples/
        vetstar_pi/      Tkinter GUI + CLI
        vetstar_core/    shared with the vetstar site

Usage:
    python pios/build_bundle.py                    # -> dist/vetstar-pi.tar.gz
    python pios/build_bundle.py --version v1.1.0
    python pios/build_bundle.py --stage DIR        # unpacked tree only (fetch.sh)

Standard library only, Python 3.9+, so fetch.sh can run it on a Pi.
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import tarfile
import tempfile

PIOS = pathlib.Path(__file__).resolve().parent
ROOT = PIOS.parent
TOP = "vetstar-pi"

# What goes in the bundle, relative to pios/ (or the repo root for core).
PIOS_ITEMS = ["install.sh", "fetch.sh", "requirements-pi.txt", "README.md",
              "systemd", "examples", "vetstar_pi"]
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")


def stage(dest: pathlib.Path, version: str) -> pathlib.Path:
    """Copy the bundle tree into ``dest/vetstar-pi`` and return that path."""
    out = dest / TOP
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for item in PIOS_ITEMS:
        src = PIOS / item
        if src.is_dir():
            shutil.copytree(src, out / item, ignore=SKIP)
        else:
            shutil.copy2(src, out / item)
    shutil.copytree(ROOT / "vetstar_core", out / "vetstar_core", ignore=SKIP)
    (out / "VERSION").write_text(version + "\n", encoding="utf-8")
    return out


def _executable(info: tarfile.TarInfo) -> tarfile.TarInfo:
    # Built on Windows too, where file modes don't carry the exec bit.
    if info.name.endswith(".sh"):
        info.mode = 0o755
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--version", default="dev", help="version label written to VERSION")
    ap.add_argument("--out", default=str(ROOT / "dist" / "vetstar-pi.tar.gz"))
    ap.add_argument("--stage", metavar="DIR",
                    help="write the unpacked tree to DIR/vetstar-pi and stop")
    args = ap.parse_args()

    if args.stage:
        out = stage(pathlib.Path(args.stage), args.version)
        print(f"Staged {out}")
        return 0

    target = pathlib.Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tree = stage(pathlib.Path(tmp), args.version)
        with tarfile.open(target, "w:gz") as tar:
            tar.add(tree, arcname=TOP, filter=_executable)
    print(f"Wrote {target} ({target.stat().st_size / 1024:.0f} KB, version {args.version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
