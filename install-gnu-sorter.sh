#!/usr/bin/env bash
# Funnel — one-shot installer.
#
# Clones the repository and runs install.sh for the current user (no root
# required). Just download and run this single script:
#
#   curl -fsSL https://raw.githubusercontent.com/notmicrosoft2000-cmd/gnu-sorter/main/install-gnu-sorter.sh | bash
#
# To enable background watching when you sign in, add --service:
#
#   curl -fsSL https://raw.githubusercontent.com/notmicrosoft2000-cmd/gnu-sorter/main/install-gnu-sorter.sh | bash -s -- --service
#
# Requirements: bash, git. The app installs into ~/.local/share/gnu-sorter/app
# and the CLI into ~/.local/bin/gnu-sorter.

set -euo pipefail

REPO_URL="${GNU_SORTER_REPO_URL:-https://github.com/notmicrosoft2000-cmd/gnu-sorter.git}"

if ! command -v git >/dev/null 2>&1; then
  echo "[setup] error: git is required but was not found." >&2
  echo "[setup] install it first, e.g.  sudo apt install git  (Debian/Ubuntu)" >&2
  exit 1
fi

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

echo "[setup] Downloading Funnel..."
git clone --quiet --depth 1 "$REPO_URL" "$WORK_DIR/gnu-sorter"

echo "[setup] Running install.sh (user-local)..."
bash "$WORK_DIR/gnu-sorter/install.sh" "$@"