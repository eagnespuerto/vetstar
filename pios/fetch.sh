#!/usr/bin/env bash
# One-shot fetcher for Raspberry Pi OS users.
#
# Downloads *only* the pios/ directory from the eagnespuerto/vetstar repo,
# without pulling the full repo (which drags in the React frontend and the
# Docker build machinery). Runs install.sh afterwards.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/eagnespuerto/vetstar/HEAD/pios/fetch.sh | bash
#
# Env vars:
#   VETSTAR_REF   git ref to fetch (default: HEAD)
#   VETSTAR_DIR   destination (default: ~/.local/share/vetstar-pi/src)
#   NO_INSTALL    set to 1 to just download without running install.sh

set -euo pipefail

REF="${VETSTAR_REF:-HEAD}"
DEST="${VETSTAR_DIR:-${HOME}/.local/share/vetstar-pi/src}"
REPO="eagnespuerto/vetstar"

echo "==> Fetching pios/ from ${REPO}@${REF} to ${DEST}"
mkdir -p "${DEST}"

# Use git sparse-checkout so only pios/ ends up on disk. Falls back to a
# tarball extract if the git version doesn't support cone-mode sparse
# checkout (Raspberry Pi OS Bullseye ships git ≥ 2.30, which does).
if command -v git >/dev/null 2>&1; then
  tmp="$(mktemp -d)"
  trap 'rm -rf "${tmp}"' EXIT
  git -C "${tmp}" init -q
  git -C "${tmp}" remote add origin "https://github.com/${REPO}.git"
  git -C "${tmp}" config core.sparseCheckout true
  git -C "${tmp}" sparse-checkout set --cone pios
  git -C "${tmp}" fetch --depth 1 origin "${REF}"
  git -C "${tmp}" checkout FETCH_HEAD
  rm -rf "${DEST}"
  mv "${tmp}/pios" "${DEST}"
else
  echo "==> git not found; falling back to tarball fetch"
  curl -fsSL "https://codeload.github.com/${REPO}/tar.gz/${REF}" \
    | tar -xz --strip-components=1 -C "${DEST}" --wildcards '*/pios/*'
  # tarball leaves a nested pios/ dir — flatten it.
  if [[ -d "${DEST}/pios" ]]; then
    mv "${DEST}/pios/"* "${DEST}/"
    rmdir "${DEST}/pios"
  fi
fi

chmod +x "${DEST}/install.sh" "${DEST}/fetch.sh" 2>/dev/null || true

if [[ "${NO_INSTALL:-0}" == "1" ]]; then
  echo "==> Fetched. Run: bash ${DEST}/install.sh"
else
  echo "==> Running installer…"
  bash "${DEST}/install.sh"
fi
