#!/usr/bin/env bash
set -euo pipefail

find_default_src() {
  local latest
  latest="$(find "$HOME/.local/chrome-for-testing" -maxdepth 2 -type d -name chrome-linux64 2>/dev/null | sort -V | tail -n 1 || true)"
  if [[ -z "$latest" ]]; then
    echo "Could not find a local Chrome for Testing install under $HOME/.local/chrome-for-testing" >&2
    exit 1
  fi
  echo "$latest"
}

SRC="${1:-$(find_default_src)}"
DEST_ROOT="/opt/faltoobot/chrome-for-testing"
DEST="$DEST_ROOT/chrome-linux64"

if [[ ! -d "$SRC" ]]; then
  echo "Source Chrome for Testing dir not found: $SRC" >&2
  exit 1
fi

sudo mkdir -p "$DEST_ROOT"
sudo rm -rf "$DEST"
sudo cp -a "$SRC" "$DEST"
sudo chmod -R a+rX "$DEST_ROOT"

echo "Shared Chrome for Testing prepared at: $DEST"
echo "Binary: $DEST/chrome"
