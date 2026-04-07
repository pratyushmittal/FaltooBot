#!/usr/bin/env bash
set -euo pipefail

REMOTE_USER="${REMOTE_USER:-remote}"
PORT="${PORT:-9223}"
DISPLAY_NUM="${DISPLAY_NUM:-:0}"
URL="${URL:-about:blank}"
CHROME_BIN="${CHROME_BIN:-/opt/faltoobot/chrome-for-testing/chrome-linux64/chrome}"
REMOTE_HOME="$(getent passwd "$REMOTE_USER" | cut -d: -f6)"
if [[ -z "$REMOTE_HOME" ]]; then
  echo "Could not resolve home for user: $REMOTE_USER" >&2
  exit 1
fi
UID_NUM="$(id -u "$REMOTE_USER")"
XAUTH="${XAUTH:-$REMOTE_HOME/.Xauthority}"
RUNTIME_DIR="/run/user/$UID_NUM"
PROFILE_DIR="$REMOTE_HOME/.cache/faltoobot/cdp-visible-profile"

if [[ ! -x "$CHROME_BIN" ]]; then
  echo "Chrome binary not executable: $CHROME_BIN" >&2
  echo "Run ./prepare_shared_chrome_for_testing.sh first, or set CHROME_BIN." >&2
  exit 1
fi

sudo -u "$REMOTE_USER" mkdir -p "$PROFILE_DIR"

nohup sudo -u "$REMOTE_USER" env \
  DISPLAY="$DISPLAY_NUM" \
  XAUTHORITY="$XAUTH" \
  XDG_RUNTIME_DIR="$RUNTIME_DIR" \
  DBUS_SESSION_BUS_ADDRESS="unix:path=$RUNTIME_DIR/bus" \
  "$CHROME_BIN" \
  --remote-debugging-port="$PORT" \
  --user-data-dir="$PROFILE_DIR" \
  --no-first-run \
  --no-default-browser-check \
  --password-store=basic \
  --disable-gpu \
  --disable-dev-shm-usage \
  --new-window \
  "$URL" >/tmp/faltoobot-visible-cdp.log 2>&1 &

sleep 2

echo "Visible CDP Chrome launch requested"
echo "Remote user: $REMOTE_USER"
echo "Display: $DISPLAY_NUM"
echo "CDP: http://127.0.0.1:$PORT/json/version"
echo "Profile: $PROFILE_DIR"
echo "Log: /tmp/faltoobot-visible-cdp.log"
