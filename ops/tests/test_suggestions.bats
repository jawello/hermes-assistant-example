#!/usr/bin/env bats
#
# The ledger is the only thing connecting the morning job to the user's reply in
# chat, so these tests care about three things: ids are stable and numbered per
# day, a status only moves forward, and a damaged line is never silently lost.

SUGGESTIONS=hermes/bin/suggestions

setup() {
  HERMES_HOME="$(mktemp -d)"
  export HERMES_HOME
  export SUGGESTIONS_TODAY=2026-09-21
  LEDGER="$HERMES_HOME/suggestions/ledger.jsonl"
}

teardown() {
  if [ -n "${STUB_PID:-}" ]; then kill "$STUB_PID" 2>/dev/null || true; fi
  rm -f "${PORT_FILE:-}"
  rm -rf "$HERMES_HOME"
}

@test "add numbers suggestions within the day and stores them pending" {
  run "$SUGGESTIONS" add --title "Сравнить тарифы" --why "тариф истекает 15.10" --source "письмо от Example Insurance, 18.09" --due 2026-10-10
  [ "$status" -eq 0 ]
  [ "$output" = "$(printf '1\ts-2026-09-21-1')" ]
  run "$SUGGESTIONS" add --title "Второе" --why "w" --source "s"
  [ "$output" = "$(printf '2\ts-2026-09-21-2')" ]
  python3 - "$LEDGER" <<'PY'
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8")]
assert [r["id"] for r in rows] == ["s-2026-09-21-1", "s-2026-09-21-2"], rows
first = rows[0]
assert first == {"id": "s-2026-09-21-1", "date": "2026-09-21", "title": "Сравнить тарифы",
                 "why": "тариф истекает 15.10", "source": "письмо от Example Insurance, 18.09",
                 "due": "2026-10-10", "status": "pending"}, first
assert rows[1]["due"] is None, rows[1]
PY
}

@test "numbering restarts on a new day" {
  "$SUGGESTIONS" add --title a --why w --source s
  # `VAR=x run ...` would not reach the command: run is a bats function.
  run env SUGGESTIONS_TODAY=2026-09-22 "$SUGGESTIONS" add --title b --why w --source s
  [ "$output" = "$(printf '1\ts-2026-09-22-1')" ]
}

@test "add refuses an empty field and a malformed due date" {
  run "$SUGGESTIONS" add --title "  " --why w --source s
  [ "$status" -ne 0 ]
  run "$SUGGESTIONS" add --title t --why w --source s --due 10.10.2026
  [ "$status" -ne 0 ]
  [ ! -s "$LEDGER" ]
}

@test "list --pending shows only pending entries" {
  "$SUGGESTIONS" add --title keep --why w --source s
  "$SUGGESTIONS" add --title gone --why w --source s
  "$SUGGESTIONS" set-status s-2026-09-21-2 rejected
  run "$SUGGESTIONS" list --pending
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "s-2026-09-21-1	pending	keep"
  ! echo "$output" | grep -q gone
}

@test "list --date filters to one batch" {
  "$SUGGESTIONS" add --title old --why w --source s
  SUGGESTIONS_TODAY=2026-09-22 "$SUGGESTIONS" add --title new --why w --source s
  run env SUGGESTIONS_TODAY=2026-09-22 "$SUGGESTIONS" list --date 2026-09-22
  echo "$output" | grep -q new
  ! echo "$output" | grep -q old
}

@test "set-status moves a pending entry and refuses to move it again" {
  "$SUGGESTIONS" add --title t --why w --source s
  run "$SUGGESTIONS" set-status s-2026-09-21-1 accepted
  [ "$status" -eq 0 ]
  run "$SUGGESTIONS" set-status s-2026-09-21-1 rejected
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "already accepted"
}

@test "set-status rejects an unknown id and an unknown status" {
  "$SUGGESTIONS" add --title t --why w --source s
  run "$SUGGESTIONS" set-status s-2099-01-01-1 accepted
  [ "$status" -eq 1 ]
  run "$SUGGESTIONS" set-status s-2026-09-21-1 maybe
  [ "$status" -ne 0 ]
}

@test "set-status done records a suggestion acted on without a task" {
  "$SUGGESTIONS" add --title t --why w --source s
  run "$SUGGESTIONS" set-status s-2026-09-21-1 done
  [ "$status" -eq 0 ]
  run "$SUGGESTIONS" list
  echo "$output" | grep -q "s-2026-09-21-1	done	t"
  run "$SUGGESTIONS" set-status s-2026-09-21-1 accepted
  [ "$status" -eq 1 ]
}

@test "message renders today's pending batch with a hint built from its numbers" {
  "$SUGGESTIONS" add --title "Сравнить тарифы" --why "тариф истекает 15.10" --source "письмо от Example Insurance, 18.09"
  "$SUGGESTIONS" add --title "Второе" --why "w2" --source "s2"
  run "$SUGGESTIONS" message
  [ "$status" -eq 0 ]
  expected="$(cat <<'TXT'
Предложения на сегодня:
1. Сравнить тарифы
   тариф истекает 15.10 — письмо от Example Insurance, 18.09
2. Второе
   w2 — s2
Ответь «да 1, 2», «нет», «сделано 2» или «1 — на пятницу».
TXT
)"
  [ "$output" = "$expected" ]
}

@test "message names the first and last number of a batch of three" {
  for t in a b c; do "$SUGGESTIONS" add --title "$t" --why w --source s; done
  run "$SUGGESTIONS" message
  [ "$(echo "$output" | tail -1)" = "Ответь «да 1, 3», «нет», «сделано 3» или «1 — на пятницу»." ]
}

@test "message for a single suggestion needs no numbers" {
  "$SUGGESTIONS" add --title a --why w --source s
  run "$SUGGESTIONS" message
  [ "$(echo "$output" | head -1)" = "Предложение на сегодня:" ]
  [ "$(echo "$output" | tail -1)" = "Ответь «да», «нет», «сделано» или «на пятницу»." ]
}

@test "message leaves out older batches and settled entries, and fails when nothing is left" {
  SUGGESTIONS_TODAY=2026-09-20 "$SUGGESTIONS" add --title yesterday --why w --source s
  "$SUGGESTIONS" add --title settled --why w --source s
  "$SUGGESTIONS" add --title kept --why w --source s
  "$SUGGESTIONS" set-status s-2026-09-21-1 rejected
  run "$SUGGESTIONS" message
  [ "$status" -eq 0 ]
  [ -z "$(echo "$output" | grep -e yesterday -e settled)" ]
  echo "$output" | grep -q "^2\. kept$"
  "$SUGGESTIONS" set-status s-2026-09-21-2 done
  run "$SUGGESTIONS" message
  [ "$status" -eq 1 ]
}

@test "pending entries older than 7 days expire, newer ones stay" {
  SUGGESTIONS_TODAY=2026-09-13 "$SUGGESTIONS" add --title stale --why w --source s
  SUGGESTIONS_TODAY=2026-09-14 "$SUGGESTIONS" add --title fresh --why w --source s
  run "$SUGGESTIONS" list
  echo "$output" | grep -q "s-2026-09-13-1	expired	stale"
  echo "$output" | grep -q "s-2026-09-14-1	pending	fresh"
}

@test "a malformed line is reported and survives a rewrite" {
  mkdir -p "$(dirname "$LEDGER")"
  printf '%s\n' 'not json at all' > "$LEDGER"
  run "$SUGGESTIONS" add --title t --why w --source s
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "line 1 is not a suggestion"
  grep -qx 'not json at all' "$LEDGER"
  grep -q '"s-2026-09-21-1"' "$LEDGER"
}

# A stand-in for Hindsight's recall endpoint: answers every POST with the same
# two facts, one retained recently and one long ago. Set STUB_BODY before
# calling to serve a different (e.g. malformed) response body instead.
start_memory_stub() {
  PORT_FILE="$(mktemp)"
  python3 - "$PORT_FILE" <<'PY' 3>&- &
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        override = os.environ.get("STUB_BODY")
        if override:
            body = override.encode()
        else:
            body = json.dumps({"results": [
                {"text": "Тариф истекает 15.10", "occurred_start": "2026-10-15T00:00:00", "mentioned_at": "2026-09-19T06:00:00+00:00"},
                {"text": "Старый факт", "occurred_start": None, "mentioned_at": "2026-08-01T06:00:00+00:00"},
            ]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
srv = HTTPServer(("127.0.0.1", 0), H)
open(sys.argv[1], "w").write(str(srv.server_port))
srv.serve_forever()
PY
  STUB_PID=$!
  for _ in $(seq 50); do [ -s "$PORT_FILE" ] && break; sleep 0.1; done
  STUB_URL="http://127.0.0.1:$(cat "$PORT_FILE")"
}

@test "context prints the ledger and both memory sections" {
  "$SUGGESTIONS" add --title "Сравнить тарифы" --why w --source s
  start_memory_stub
  run "$SUGGESTIONS" context --api-url "$STUB_URL" --bank hermes --timeout 5
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "^## Ledger"
  echo "$output" | grep -q "Сравнить тарифы"
  echo "$output" | grep -q "^## Memory: dated items"
  echo "$output" | grep -q "2026-10-15.*Тариф истекает 15.10"
  echo "$output" | grep -q "^## Memory: last 7 days"
}

@test "the last-7-days section leaves out facts retained earlier" {
  start_memory_stub
  run "$SUGGESTIONS" context --api-url "$STUB_URL" --bank hermes --timeout 5
  recent="$(echo "$output" | sed -n '/^## Memory: last 7 days/,$p')"
  echo "$recent" | grep -q "Тариф истекает"
  ! echo "$recent" | grep -q "Старый факт"
}

@test "context fails loudly when memory is unreachable, ledger still printed" {
  "$SUGGESTIONS" add --title "Сравнить тарифы" --why w --source s
  run "$SUGGESTIONS" context --api-url http://127.0.0.1:1 --bank hermes --timeout 5
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "Сравнить тарифы"
  echo "$output" | grep -q "could not read memory"
}

@test "context finds memory through the provider config" {
  mkdir -p "$HERMES_HOME/hindsight"
  start_memory_stub
  printf '{"api_url": "%s", "bank_id": "hermes"}' "$STUB_URL" > "$HERMES_HOME/hindsight/config.json"
  run "$SUGGESTIONS" context --timeout 5
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "Тариф истекает"
}

@test "context's ledger keeps a rejected entry past 30 days but drops an accepted one" {
  SUGGESTIONS_TODAY=2026-08-01 "$SUGGESTIONS" add --title "Old rejected" --why w --source s
  SUGGESTIONS_TODAY=2026-08-01 "$SUGGESTIONS" add --title "Old accepted" --why w --source s
  SUGGESTIONS_TODAY=2026-08-01 "$SUGGESTIONS" set-status s-2026-08-01-1 rejected
  SUGGESTIONS_TODAY=2026-08-01 "$SUGGESTIONS" set-status s-2026-08-01-2 accepted
  start_memory_stub
  run "$SUGGESTIONS" context --api-url "$STUB_URL" --bank hermes --timeout 5
  [ "$status" -eq 0 ]
  ledger_section="$(echo "$output" | sed -n '/^## Ledger/,/^$/p')"
  echo "$ledger_section" | grep -q "Old rejected"
  ! echo "$ledger_section" | grep -q "Old accepted"
}

@test "context reports a malformed recall response cleanly, no traceback" {
  "$SUGGESTIONS" add --title "Сравнить тарифы" --why w --source s
  export STUB_BODY='{"results": "not-a-list"}'
  start_memory_stub
  run "$SUGGESTIONS" context --api-url "$STUB_URL" --bank hermes --timeout 5
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "could not read memory"
  ! echo "$output" | grep -q "Traceback"
}

@test "context reports cleanly when a recall result item is not an object" {
  "$SUGGESTIONS" add --title "Сравнить тарифы" --why w --source s
  export STUB_BODY='{"results": [1, 2, 3]}'
  start_memory_stub
  run "$SUGGESTIONS" context --api-url "$STUB_URL" --bank hermes --timeout 5
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "could not read memory"
  ! echo "$output" | grep -q "Traceback"
}

@test "a ledger line missing date is reported and kept, other commands still work" {
  mkdir -p "$(dirname "$LEDGER")"
  printf '%s\n' '{"id": "s-2026-09-01-1", "status": "pending", "title": "x", "why": "w", "source": "s", "due": null}' > "$LEDGER"
  run "$SUGGESTIONS" list
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "line 1 is not a suggestion"
  run "$SUGGESTIONS" add --title t --why w --source s
  [ "$status" -eq 0 ]
  grep -q '"s-2026-09-01-1"' "$LEDGER"
  grep -q '"s-2026-09-21-1"' "$LEDGER"
}
