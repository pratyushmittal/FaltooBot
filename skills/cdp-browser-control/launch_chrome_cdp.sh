#!/usr/bin/env bash
set -euo pipefail

PORT=9222
BROWSER="chrome-testing"
URL="about:blank"
USER_DATA_DIR="${HOME}/.cache/faltoobot/cdp-profile"
HEADLESS=0
EXTRA_ARGS=()

usage() {
  cat <<USAGE
Usage:
  $(basename "$0") [options]

Options:
  --browser <chrome-testing|google-chrome|chromium|custom>
  --chrome-bin <path>         Explicit browser binary path
  --port <port>               CDP port (default: 9222)
  --url <url>                 Initial URL (default: about:blank)
  --user-data-dir <path>      Non-default profile dir (recommended)
  --headless                  Launch headless
  --arg <flag>                Extra browser arg (repeatable)
  -h, --help                  Show help

Examples:
  $(basename "$0") --browser chrome-testing --port 9222 --user-data-dir ~/.cache/faltoobot/cdp-profile
  $(basename "$0") --browser google-chrome --url https://news.ycombinator.com --arg --start-maximized
USAGE
}

CHROME_BIN="${CHROME_BIN:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --browser)
      BROWSER="$2"; shift 2 ;;
    --chrome-bin)
      CHROME_BIN="$2"; shift 2 ;;
    --port)
      PORT="$2"; shift 2 ;;
    --url)
      URL="$2"; shift 2 ;;
    --user-data-dir)
      USER_DATA_DIR="$2"; shift 2 ;;
    --headless)
      HEADLESS=1; shift ;;
    --arg)
      EXTRA_ARGS+=("$2"); shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown arg: $1" >&2
      usage >&2
      exit 1 ;;
  esac
done

resolve_bin() {
  if [[ -n "$CHROME_BIN" ]]; then
    echo "$CHROME_BIN"
    return 0
  fi

  case "$BROWSER" in
    chrome-testing)
      for candidate in \
        "$HOME/.local/bin/chrome-testing" \
        "$HOME/.cache/chrome-for-testing/chrome-linux64/chrome" \
        "$(command -v chrome-testing 2>/dev/null || true)"
      do
        [[ -n "$candidate" && -x "$candidate" ]] && { echo "$candidate"; return 0; }
      done
      ;;
    google-chrome)
      for candidate in \
        "$HOME/.local/bin/google-chrome-local" \
        "$(command -v google-chrome 2>/dev/null || true)" \
        "$(command -v google-chrome-stable 2>/dev/null || true)"
      do
        [[ -n "$candidate" && -x "$candidate" ]] && { echo "$candidate"; return 0; }
      done
      ;;
    chromium)
      for candidate in \
        "$(command -v chromium 2>/dev/null || true)" \
        "$(command -v chromium-browser 2>/dev/null || true)"
      do
        [[ -n "$candidate" && -x "$candidate" ]] && { echo "$candidate"; return 0; }
      done
      ;;
    custom)
      ;;
    *)
      echo "Unsupported browser choice: $BROWSER" >&2
      exit 1 ;;
  esac

  echo "Could not find a browser binary for: $BROWSER" >&2
  exit 1
}

BIN="$(resolve_bin)"
mkdir -p "$USER_DATA_DIR"

ARGS=(
  "--remote-debugging-port=${PORT}"
  "--user-data-dir=${USER_DATA_DIR}"
  "--no-first-run"
  "--no-default-browser-check"
  "--password-store=basic"
  "--disable-gpu"
  "--disable-dev-shm-usage"
)

if [[ "$HEADLESS" == "1" ]]; then
  ARGS+=("--headless=new" "--disable-gpu")
fi

if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  ARGS+=("${EXTRA_ARGS[@]}")
fi

printf 'Launching: %s\n' "$BIN"
printf 'CDP endpoint hint: http://127.0.0.1:%s/json/version\n' "$PORT"
printf 'User data dir: %s\n' "$USER_DATA_DIR"

nohup "$BIN" "${ARGS[@]}" "$URL" >/tmp/faltoobot-cdp-browser.log 2>&1 &
PID=$!
sleep 2
printf 'PID: %s\n' "$PID"
printf 'Log: /tmp/faltoobot-cdp-browser.log\n'
