#!/usr/bin/env bash
# Suno Autopilot — launch the debug Chrome the local model drives.
#
# Owns the browser yourself (remote debugging on 9222) so it stays open across
# model turns. Uses a DEDICATED profile so your Suno login persists here and
# never touches your everyday Chrome.
#
# Usage:  bash start-suno-autopilot.sh   (or use the desktop shortcut)
# First run: log into suno.com in the window that opens, then leave it open.

PORT=9222
PROFILE="$HOME/.config/chrome-suno-autopilot"

# Reuse an existing debug Chrome on this port instead of launching a second one.
if curl -s "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
  echo "Debug Chrome already running on port ${PORT}. Reusing it."
  exit 0
fi

echo "Launching Suno Autopilot Chrome (remote debugging on ${PORT}) ..."
exec google-chrome \
  --remote-debugging-port="${PORT}" \
  --user-data-dir="${PROFILE}" \
  --no-first-run \
  --no-default-browser-check \
  "https://suno.com/create"
