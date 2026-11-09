#!/usr/bin/env bats
#
# telegram-mcp's start wrapper. A revoked or duplicated Telegram session makes
# the server exit at start; restarting it in a loop would leave the container
# unhealthy and the deploy health gate would refuse every later deploy. The
# wrapper parks instead: it marks the container parked (the healthcheck
# accepts that), logs why, and retries on a timer.

RUN=telegram-mcp/run.sh

setup() {
  STUBS="$(mktemp -d)"
  export TELEGRAM_MCP_PARK_FILE="$STUBS/parked"
  export TELEGRAM_MCP_RETRY_SECONDS=1
  export CALLS="$STUBS/calls"
}

teardown() {
  rm -rf "$STUBS"
}

stub_python() {
  cat > "$STUBS/python" <<STUB
#!/bin/sh
echo run >> "\$CALLS"
exit $1
STUB
  chmod +x "$STUBS/python"
}

@test "a server that fails at start parks the container and retries" {
  stub_python 1
  PATH="$STUBS:$PATH" timeout 3 "$RUN" 2>"$STUBS/err" || true
  [ -f "$TELEGRAM_MCP_PARK_FILE" ]
  [ "$(wc -l < "$CALLS")" -ge 2 ]
  grep -q 'parked' "$STUBS/err"
}

@test "a server that stops cleanly ends the wrapper" {
  stub_python 0
  PATH="$STUBS:$PATH" run timeout 3 "$RUN"
  [ "$status" -eq 0 ]
  [ "$(wc -l < "$CALLS")" -eq 1 ]
}

@test "each start clears the parked mark" {
  stub_python 0
  touch "$TELEGRAM_MCP_PARK_FILE"
  PATH="$STUBS:$PATH" run timeout 3 "$RUN"
  [ ! -e "$TELEGRAM_MCP_PARK_FILE" ]
}

@test "the healthcheck accepts a parked container and the image starts through the wrapper" {
  block=$(sed -n '/^  telegram-mcp:/,/^  [a-z]/p' docker-compose.yml)
  grep -q 'telegram-mcp-parked' <<<"$block"
  grep -qE '^\s+init: true$' <<<"$block"
  grep -qE '^CMD \["/app/run.sh"\]$' telegram-mcp/Dockerfile
}
