#!/usr/bin/env bash
# VetStar Pi installer for Raspberry Pi OS (Bookworm / Bullseye).
#
# Installs system packages, creates a venv under ~/.local/share/vetstar-pi,
# pulls piwheels-prebuilt wheels for numpy/scipy/astropy/matplotlib/reportlab,
# and writes a desktop launcher + a `vetstar-pi` shim in ~/.local/bin.
#
# Runs on 1 GB RAM devices (Pi Zero 2 W, Pi 3B, Pi 4/1GB, Pi 5). Tested with
# system Python 3.11 (Bookworm) and 3.9 (Bullseye).
#
# Usage:
#   bash install.sh
#
# Uninstall: remove ~/.local/share/vetstar-pi, ~/.local/bin/vetstar-pi,
#            and ~/.local/share/applications/vetstar-pi.desktop.

set -euo pipefail

APP_HOME="${HOME}/.local/share/vetstar-pi"
BIN_DIR="${HOME}/.local/bin"
DESKTOP_DIR="${HOME}/.local/share/applications"

# ---- Repo detection -------------------------------------------------------
# When run from the extracted tarball, this script is in vetstar/pios/
# alongside vetstar_pi/. Resolve that path.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -d "${here}/vetstar_pi" ]]; then
  echo "install.sh: expected vetstar_pi/ next to this script; got ${here}" >&2
  exit 2
fi

echo "==> Installing OS packages (python3 venv + tk + BLAS libs for numpy)…"
if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip python3-tk \
    libopenblas0 libatlas3-base libjpeg62-turbo zlib1g \
    libfreetype6
else
  echo "install.sh: apt-get not found — this installer targets Raspberry Pi OS." >&2
  exit 3
fi

echo "==> Creating virtualenv at ${APP_HOME}…"
mkdir -p "${APP_HOME}"
python3 -m venv "${APP_HOME}/venv"
# shellcheck source=/dev/null
source "${APP_HOME}/venv/bin/activate"
pip install --upgrade pip

echo "==> Installing Python deps from piwheels (prebuilt ARM wheels)…"
pip install \
  --index-url https://www.piwheels.org/simple \
  --extra-index-url https://pypi.org/simple \
  -r "${here}/requirements-pi.txt"

echo "==> Copying vetstar_pi package into ${APP_HOME}/venv…"
cp -r "${here}/vetstar_pi" "${APP_HOME}/venv/lib/python3"*/site-packages/ 2>/dev/null || \
  cp -r "${here}/vetstar_pi" "${APP_HOME}/venv/lib/site-packages/"

echo "==> Installing launcher script to ${BIN_DIR}/vetstar-pi…"
mkdir -p "${BIN_DIR}"
cat > "${BIN_DIR}/vetstar-pi" <<EOF
#!/usr/bin/env bash
exec "${APP_HOME}/venv/bin/python" -m vetstar_pi "\$@"
EOF
chmod +x "${BIN_DIR}/vetstar-pi"

echo "==> Installing desktop launcher (Science menu)…"
mkdir -p "${DESKTOP_DIR}"
install -m 0644 "${here}/systemd/vetstar-pi.desktop" "${DESKTOP_DIR}/vetstar-pi.desktop"

# Rewrite the Exec/TryExec lines so the launcher points at this install.
sed -i "s|@EXEC@|${BIN_DIR}/vetstar-pi|g" "${DESKTOP_DIR}/vetstar-pi.desktop"

# Refresh the applications menu so the entry shows up immediately without a
# logout. update-desktop-database is the freedesktop-standard trigger; on
# LXDE-pi (Pi OS default) lxpanelctl also nudges the panel-embedded menu.
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "${DESKTOP_DIR}" 2>/dev/null || true
fi
if command -v lxpanelctl >/dev/null 2>&1; then
  lxpanelctl restart 2>/dev/null || true
fi

echo
echo "VetStar Pi installed."
echo "  GUI:  vetstar-pi          (or Menu → Science → VetStar Pi)"
echo "  CLI:  vetstar-pi transit  input.fits"
echo "        vetstar-pi microlens  data.csv --t-start 100 --t-end 120 --t0-guess 110"
echo
echo "Make sure ${BIN_DIR} is on your PATH. On stock Pi OS it already is."
