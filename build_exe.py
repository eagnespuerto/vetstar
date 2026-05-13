#!/usr/bin/env python3
"""
Build a single-file executable of TESS Vetting Studio.

Run this on the OS you want to target:
    Windows  → produces dist/tess-vetting-studio.exe
    macOS    → produces dist/tess-vetting-studio    (Mach-O)
    Linux    → produces dist/tess-vetting-studio    (ELF)

PyInstaller cannot cross-compile. To get a Windows .exe you must run this
on a Windows machine (or in a Windows VM / GitHub Actions runner).

Usage:
    python build_exe.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import pathlib


ROOT = pathlib.Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
DIST = FRONTEND / "dist"
BACKEND_REQ = ROOT / "backend" / "requirements.txt"


def run(cmd, cwd=None):
    print(f"\n$ {' '.join(str(c) for c in cmd)}", flush=True)
    subprocess.check_call(cmd, cwd=cwd)


def main():
    # 1. Backend deps must be installed in the *current* interpreter,
    #    because PyInstaller bundles whatever it finds there.
    run([sys.executable, "-m", "pip", "install", "-r", str(BACKEND_REQ)])
    run([sys.executable, "-m", "pip", "install", "pyinstaller>=6.0"])

    # 2. Build the frontend if not present.
    if not (DIST / "index.html").is_file():
        if not shutil.which("npm"):
            sys.exit(
                "npm not found. Install Node.js >= 18 and rerun, or run "
                "`npm install && npm run build` in ./frontend yourself first."
            )
        run(["npm", "install", "--no-audit", "--no-fund"], cwd=FRONTEND)
        run(["npm", "run", "build"], cwd=FRONTEND)
    else:
        print(f"Frontend bundle already present at {DIST}")

    # 3. Clean previous PyInstaller outputs.
    for d in ("build", "dist", "__pycache__"):
        p = ROOT / d
        if p.exists():
            print(f"Removing {p}")
            shutil.rmtree(p, ignore_errors=True)

    # 4. Run PyInstaller.
    run([sys.executable, "-m", "PyInstaller", "build_exe.spec",
         "--clean", "--noconfirm"])

    # 5. Report.
    output_dir = ROOT / "dist"
    print("\nBuilt binaries:")
    for p in sorted(output_dir.iterdir()) if output_dir.is_dir() else []:
        size_mb = p.stat().st_size / 1024 / 1024
        print(f"  {p.name}  ({size_mb:.1f} MB)")
    print(
        "\nDouble-click the binary (or run it from a terminal) to launch the app.\n"
        "It will start a local web server at http://127.0.0.1:8000 and open\n"
        "your default browser to the UI."
    )


if __name__ == "__main__":
    main()
