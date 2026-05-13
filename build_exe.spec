# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for TESS Vetting Studio.

Usage:
    pyinstaller build_exe.spec --clean --noconfirm

Output:
    dist/tess-vetting-studio          (one binary, no installer needed)
    dist/tess-vetting-studio.exe      (on Windows)

Notes on bundled deps that need extra care:
  - astropy: ships data files (CDS_units, IERS leap-second tables, etc.)
  - matplotlib: needs mpl-data
  - scipy: a few hidden imports for special functions / lazy submodules
  - astroquery: configuration + cache directories handled at runtime
  - reportlab: ships its own data files
  - fastapi/starlette/pydantic: well-supported, no special handling
"""
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
    copy_metadata,
)

import os

# ---------------------------------------------------------------------- 
# Data files: include the entire built frontend and any package data.
# ---------------------------------------------------------------------- 
datas = [
    ("frontend/dist", "frontend/dist"),
    ("backend/app", "backend/app"),
]

# Astropy ships physical-constants tables, time scale data, etc.
datas += collect_data_files("astropy")
# Astroquery ships config templates and HTML/XML helpers.
datas += collect_data_files("astroquery")
# Matplotlib ships fonts, color tables, and rcParams under mpl-data.
datas += collect_data_files("matplotlib")
# ReportLab ships its own font and standard-library data.
datas += collect_data_files("reportlab")
# Scipy ships some compiled .pyd helpers that aren't auto-picked up.
datas += collect_data_files("scipy", include_py_files=False)

# Package metadata (some libs use importlib.metadata at import time)
for pkg in ("astropy", "astroquery", "matplotlib", "scipy", "numpy",
            "fastapi", "starlette", "pydantic", "reportlab", "uvicorn"):
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass


# ---------------------------------------------------------------------- 
# Hidden imports: modules imported via string/lazy mechanisms.
# ---------------------------------------------------------------------- 
hiddenimports = []
hiddenimports += collect_submodules("astropy")
hiddenimports += collect_submodules("astroquery")
hiddenimports += collect_submodules("scipy")
hiddenimports += collect_submodules("matplotlib")
hiddenimports += collect_submodules("fastapi")
hiddenimports += collect_submodules("starlette")
hiddenimports += collect_submodules("pydantic")
hiddenimports += collect_submodules("reportlab")

# Uvicorn loops & lifecycle helpers
hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "h11",
    "anyio",
    "anyio._backends._asyncio",
]

# FastAPI / Starlette / Pydantic v2 lazy imports
hiddenimports += [
    "email_validator",
    "python_multipart",
    "multipart",
    "pydantic_core",
    "pydantic.deprecated",
    "pydantic.deprecated.decorator",
]

# Our own modules
hiddenimports += [
    "app",
    "app.main",
    "app.pipeline",
    "app.parsers",
    "app.report",
    "app.mast_fetch",
]


# ---------------------------------------------------------------------- 
# Build steps
# ---------------------------------------------------------------------- 
block_cipher = None

a = Analysis(
    ["app.py"],
    pathex=["backend"],     # so `import app.main` resolves
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Trim the bloat — these are big and we don't use them.
        "tkinter",
        "PyQt5", "PyQt6", "PySide2", "PySide6",
        "IPython", "jupyter", "notebook",
        "pytest", "sphinx",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# --onefile mode: produces a single self-contained binary.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="tess-vetting-studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                # UPX often confuses antivirus on Windows
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,             # keep a console window so users see logs
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
