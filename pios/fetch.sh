#!/usr/bin/env bash
# One-shot fetcher for Raspberry Pi OS users.
#
# Downloads the VetStar Pi release bundle (the Pi front end plus the shared
# vetstar_core science package) from GitHub Releases, then runs install.sh.
# No React frontend or Docker machinery is downloaded.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/eagnespuerto/vetstar/HEAD/pios/fetch.sh | bash
#
# Env vars:
#   VETSTAR_VERSION  release tag to install, e.g. v1.1.0 (default: latest)
#   VETSTAR_REF      install from a git branch/tag/commit instead of a
#                    release (for testing unreleased changes)
#   VETSTAR_DIR      destination (default: ~/.local/share/vetstar-pi/src)
#   NO_INSTALL       set to 1 to just download without running install.sh

set -euo pipefail

VERSION="${VETSTAR_VERSION:-latest}"
REF="${VETSTAR_REF:-}"
DEST="${VETSTAR_DIR:-${HOME}/.local/share/vetstar-pi/src}"
REPO="eagnespuerto/vetstar"
ASSET="vetstar-pi.tar.gz"

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
mkdir -p "$(dirname "${DEST}")"

install_tree() {          # $1 = unpacked vetstar-pi/ directory
  rm -rf "${DEST}"
  mv "$1" "${DEST}"
}

fetch_release() {
  local url
  if [[ "${VERSION}" == "latest" ]]; then
    url="https://github.com/${REPO}/releases/latest/download/${ASSET}"
  else
    url="https://github.com/${REPO}/releases/download/${VERSION}/${ASSET}"
  fi
  echo "==> Downloading ${url}"
  curl -fsSL "${url}" -o "${tmp}/${ASSET}" || return 1
  tar -xzf "${tmp}/${ASSET}" -C "${tmp}"
  install_tree "${tmp}/vetstar-pi"
}

# Source install: fetch only pios/ and vetstar_core/ at REF, then lay them
# out the same way the release bundle does.
fetch_source() {
  echo "==> Fetching pios/ and vetstar_core/ from ${REPO}@${REF}"
  local src="${tmp}/src"
  mkdir -p "${src}"
  if command -v git >/dev/null 2>&1; then
    git -C "${src}" init -q
    git -C "${src}" remote add origin "https://github.com/${REPO}.git"
    git -C "${src}" sparse-checkout set --cone pios vetstar_core
    git -C "${src}" fetch -q --depth 1 origin "${REF}"
    git -C "${src}" checkout -q FETCH_HEAD
  else
    echo "==> git not found; falling back to tarball fetch"
    curl -fsSL "https://codeload.github.com/${REPO}/tar.gz/${REF}" \
      | tar -xz --strip-components=1 -C "${src}" --wildcards '*/pios/*' '*/vetstar_core/*'
  fi
  python3 "${src}/pios/build_bundle.py" --stage "${tmp}/staged" --version "${REF}"
  install_tree "${tmp}/staged/vetstar-pi"
}

if [[ -n "${REF}" ]]; then
  fetch_source
elif ! fetch_release; then
  echo "==> No release bundle found for '${VERSION}'; installing from the default branch instead."
  REF="HEAD"
  fetch_source
fi

chmod +x "${DEST}/install.sh" "${DEST}/fetch.sh" 2>/dev/null || true
echo "==> VetStar Pi $(cat "${DEST}/VERSION" 2>/dev/null || echo '?') is in ${DEST}"

if [[ "${NO_INSTALL:-0}" == "1" ]]; then
  echo "==> Fetched. Run: bash ${DEST}/install.sh"
else
  echo "==> Running installer…"
  bash "${DEST}/install.sh"
fi
