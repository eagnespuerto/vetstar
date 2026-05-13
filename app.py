#!/usr/bin/env python3
"""
TESS Vetting Studio — single-file launcher and PyInstaller entry point.

Run modes:
    python app.py                  # dev: auto-build frontend if needed, then serve
    python app.py --port 9000      # custom port
    python app.py --skip-build     # don't rebuild frontend
    python app.py --api-only       # API only (no SPA)
    python app.py --no-browser     # don't auto-open the browser
    ./tess-vetting-studio          # frozen exe: everything bundled, just runs
"""
from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import threading
import time
import webbrowser


# When PyInstaller freezes the app, sys.frozen is True and sys._MEIPASS
# points at the unpacked-on-launch temp directory containing data files.
IS_FROZEN = getattr(sys, "frozen", False)

if IS_FROZEN:
    ROOT = pathlib.Path(sys._MEIPASS)  # type: ignore[attr-defined]
else:
    ROOT = pathlib.Path(__file__).resolve().parent

BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
DIST = FRONTEND / "dist"


def _run(cmd, cwd=None, check=True):
    print(f"\n$ {' '.join(str(c) for c in cmd)}", flush=True)
    return subprocess.run(cmd, cwd=cwd, check=check)


def ensure_python_deps():
    if IS_FROZEN:
        return
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
        import astropy  # noqa: F401
        import scipy    # noqa: F401
        import matplotlib  # noqa: F401
        import reportlab  # noqa: F401
        import astroquery  # noqa: F401
        return
    except ImportError:
        pass
    print("Installing backend Python dependencies...")
    _run([sys.executable, "-m", "pip", "install", "-r", str(BACKEND / "requirements.txt")])


def ensure_frontend_built(skip_build: bool):
    if IS_FROZEN or skip_build:
        return DIST.is_dir()
    if DIST.is_dir() and (DIST / "index.html").is_file():
        return True

    if not shutil.which("npm"):
        print(
            "WARNING: npm not found in PATH; cannot build the frontend.",
            file=sys.stderr,
        )
        return False

    if not (FRONTEND / "node_modules").is_dir():
        _run(["npm", "install", "--no-audit", "--no-fund"], cwd=FRONTEND)
    _run(["npm", "run", "build"], cwd=FRONTEND)
    return DIST.is_dir()


def open_browser_later(url: str, delay: float = 1.5):
    def _open():
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:
            pass
    threading.Thread(target=_open, daemon=True).start()


def main():
    ap = argparse.ArgumentParser(description="TESS Vetting Studio launcher")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true")
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--api-only", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    ensure_python_deps()

    if not args.api_only:
        ok = ensure_frontend_built(args.skip_build)
        if ok:
            os.environ["FRONTEND_DIST"] = str(DIST)

    if not IS_FROZEN:
        sys.path.insert(0, str(BACKEND))

    import uvicorn
    # Import the FastAPI app object directly. When frozen, the package was
    # collected by PyInstaller and is importable from the bundled archive.
    # When running from source, we just inserted backend/ into sys.path above.
    if IS_FROZEN:
        # PyInstaller put backend/app on the import path via Analysis(pathex=)
        sys.path.insert(0, str(ROOT / "backend"))
    from app.main import app as fastapi_app

    url = f"http://{args.host}:{args.port}/"
    print(f"\n🔭 TESS Vetting Studio starting at  {url}")
    print(f"   Health check:                    {url}api/health")
    print(f"   Interactive API docs:            {url}docs")
    print(f"   Press Ctrl-C to quit.\n")

    if IS_FROZEN and not args.no_browser:
        open_browser_later(url)

    try:
        uvicorn.run(
            fastapi_app,                            # pass the object, not a string
            host=args.host,
            port=args.port,
            reload=args.reload and not IS_FROZEN,
        )
    except KeyboardInterrupt:
        print("\nShutting down.")


if __name__ == "__main__":
    main()
