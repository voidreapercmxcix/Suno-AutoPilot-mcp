#!/usr/bin/env bash
# Suno Autopilot Launcher
# Starts the debug Chrome session and verifies the venv is ready.
# After this runs, just open LM Studio and prompt as normal.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv/bin/python"
PORT=9222

# Check venv exists
if [ ! -f "$VENV" ]; then
  notify-send -i dialog-error "Suno Autopilot" "venv not found.\nRun: cd $SCRIPT_DIR && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

# Start debug Chrome if not already running
if curl -s "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
  notify-send -i audio-x-generic "Suno Autopilot" "Chrome already running on port ${PORT}.\nReady — open LM Studio and go."
else
  notify-send -i audio-x-generic "Suno Autopilot" "Starting Suno Chrome session...\nLog in if prompted, then open LM Studio."
  exec google-chrome \
    --remote-debugging-port="${PORT}" \
    --user-data-dir="$HOME/.config/chrome-suno-autopilot" \
    --no-first-run \
    --no-default-browser-check \
    "https://suno.com/create"
fi
