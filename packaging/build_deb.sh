#!/usr/bin/env bash
# Package the one-folder PyInstaller build (dist/vetstar/) as a .deb for
# Debian/Ubuntu (amd64) or Raspberry Pi OS 64-bit (arm64).
#
#   VETSTAR_ONEDIR=1 python build_exe.py
#   packaging/build_deb.sh 2.0.0            # -> dist/vetstar_2.0.0_<arch>.deb
set -euo pipefail

VERSION="${1:?usage: build_deb.sh VERSION}"
VERSION="${VERSION#v}"
ARCH="$(dpkg --print-architecture)"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/dist/vetstar"
PKG="$ROOT/build/deb/vetstar_${VERSION}_${ARCH}"
OUT="$ROOT/dist/vetstar_${VERSION}_${ARCH}.deb"

[ -x "$SRC/vetstar" ] || { echo "missing $SRC/vetstar; run: VETSTAR_ONEDIR=1 python build_exe.py" >&2; exit 1; }

rm -rf "$PKG"
mkdir -p "$PKG/DEBIAN" "$PKG/opt" "$PKG/usr/bin" "$PKG/usr/share/applications"
cp -a "$SRC" "$PKG/opt/vetstar"
ln -s /opt/vetstar/vetstar "$PKG/usr/bin/vetstar"

cat > "$PKG/usr/share/applications/vetstar.desktop" <<DESK
[Desktop Entry]
Type=Application
Version=1.0
Name=Vetstar
GenericName=NEMESIS and OGLE GUI
Comment=Vet TESS transit and microlensing candidates; results ready for ExoFOP-TESS
Exec=/opt/vetstar/vetstar
Icon=applications-science
Terminal=true
Categories=Science;Astronomy;Education;
Keywords=astronomy;TESS;transit;microlensing;NEMESIS;OGLE;exoplanet;
DESK

SIZE_KB="$(du -sk "$PKG/opt" | cut -f1)"
cat > "$PKG/DEBIAN/control" <<CTRL
Package: vetstar
Version: $VERSION
Section: science
Priority: optional
Architecture: $ARCH
Installed-Size: $SIZE_KB
Depends: libc6 (>= 2.35), xdg-utils
Maintainer: eagnespuerto <eagnespuerto@users.noreply.github.com>
Homepage: https://github.com/eagnespuerto/vetstar
Description: Vetstar: NEMESIS and OGLE GUI
 A GUI front for the peer-reviewed NEMESIS transit pipeline and the OGLE-IV
 microlensing event selection, for TESS light curves. Runs a local web app in
 your browser and builds ExoFOP-TESS bulk-upload files. NEMESIS jobs are sent
 to the public Vetstar site; everything else runs on this computer.
CTRL

mkdir -p "$ROOT/dist"
dpkg-deb --root-owner-group --build "$PKG" "$OUT"
echo "built $OUT ($(du -h "$OUT" | cut -f1))"
