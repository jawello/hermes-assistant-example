#!/usr/bin/env bats
#
# `remember` is what a cron-run agent uses instead of the memory tool, which
# hermes disables for scheduled jobs. A silent failure here means a fact read
# from the mailbox is lost with nothing to show for it, so these tests care
# most about two things: the JSON survives awkward content, and a failure is
# loud and free of the fact itself.

REMEMBER=hermes/bin/remember

setup() {
  HERMES_HOME="$(mktemp -d)"
  export HERMES_HOME
  mkdir -p "$HERMES_HOME/hindsight"
  cat > "$HERMES_HOME/hindsight/config.json" <<JSON
{"mode": "local_external", "api_url": "http://hindsight:8888", "bank_id": "hermes"}
JSON
}

teardown() {
  rm -rf "$HERMES_HOME"
}

@test "dry run emits a valid retain body carrying the fact" {
  run "$REMEMBER" --dry-run "Счёт за электричество 4200 руб"
  [ "$status" -eq 0 ]
  echo "$output" | python3 -c '
import json, sys
body = json.load(sys.stdin)
item = body["items"][0]
assert item["content"] == "Счёт за электричество 4200 руб", item
assert body["async"] is False, body
assert item["timestamp"], item
'
}

@test "the fact can arrive on stdin" {
  run bash -c "echo 'from stdin' | $REMEMBER --dry-run"
  [ "$status" -eq 0 ]
  echo "$output" | grep -q '"content": "from stdin"'
}

@test "quotes, newlines and unicode survive the encoding" {
  # The agent composes these calls, and a fact will sooner or later contain a
  # quote or a line break. A hand-rolled JSON string would break here.
  fact='He said "pay by 25th"
second line — and a dash'
  run "$REMEMBER" --dry-run "$fact"
  [ "$status" -eq 0 ]
  echo "$output" | python3 -c '
import json, sys
content = json.load(sys.stdin)["items"][0]["content"]
assert chr(10) in content, "newline lost"
assert chr(34) + "pay by 25th" + chr(34) in content, content
assert "—" in content, "unicode lost"
'
}

@test "context and tags reach the item" {
  run "$REMEMBER" --dry-run --context "gmail digest" --tag gmail --tag bills "a fact"
  [ "$status" -eq 0 ]
  echo "$output" | python3 -c '
import json, sys
item = json.load(sys.stdin)["items"][0]
assert item["context"] == "gmail digest", item
assert item["tags"] == ["gmail", "bills"], item
'
}

@test "an unreachable memory service fails loudly" {
  # Port 1 refuses immediately. Exit 0 here would mean the agent reports a
  # fact as saved when it is not.
  run "$REMEMBER" --api-url http://127.0.0.1:1 --bank hermes --timeout 5 "a fact"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "could not reach memory"
}

@test "a failure never echoes the fact back" {
  run "$REMEMBER" --api-url http://127.0.0.1:1 --bank hermes --timeout 5 "medical result for the user"
  [ "$status" -eq 1 ]
  ! echo "$output" | grep -q "medical result"
}

@test "an empty fact is refused rather than stored" {
  run bash -c "printf '   \n' | $REMEMBER --dry-run"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q "nothing to remember"
}

@test "a missing config with no overrides names the file it looked in" {
  rm -f "$HERMES_HOME/hindsight/config.json"
  run "$REMEMBER" "a fact"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q "cannot tell where memory lives"
}

@test "a document id makes a repeated fact replace itself instead of piling up" {
  # hindsight replaces a document whose id it already holds. The Telegram
  # pass re-reads its window after a broken run; the same message must land
  # on the same document rather than store the fact twice.
  run "$REMEMBER" --dry-run --document-id "tg-123-456-1" "Саша ждёт ответа"
  [ "$status" -eq 0 ]
  echo "$output" | python3 -c '
import json, sys
item = json.load(sys.stdin)["items"][0]
assert item["document_id"] == "tg-123-456-1", item
'
}

@test "without a document id none is sent" {
  run "$REMEMBER" --dry-run "fact"
  [ "$status" -eq 0 ]
  [ -z "$(echo "$output" | grep document_id)" ]
}
