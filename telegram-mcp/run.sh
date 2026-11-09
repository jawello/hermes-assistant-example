#!/bin/sh
# Starts the Telegram MCP server, and parks instead of crash-looping.
#
# A revoked or duplicated Telegram session makes the server exit at start.
# Restarted in a loop, the container would stay unhealthy, and the deploy's
# health gate would then refuse every later deploy of the whole stack. So a
# failed start marks the container parked (the healthcheck accepts that),
# says why in the log, and tries again on a timer. hermes meanwhile has no
# telegram tools, so the morning pass reports the breakage under Сломалось.
# A clean exit (a stop) ends the wrapper.
PARK_FILE="${TELEGRAM_MCP_PARK_FILE:-/tmp/telegram-mcp-parked}"
RETRY_SECONDS="${TELEGRAM_MCP_RETRY_SECONDS:-600}"

while :; do
  rm -f "$PARK_FILE"
  python main.py && exit 0
  status=$?
  touch "$PARK_FILE"
  echo "telegram-mcp: server exited with $status; parked, retrying in ${RETRY_SECONDS}s" >&2
  sleep "$RETRY_SECONDS"
done
