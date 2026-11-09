#!/usr/bin/env bats
#
# The mail pass runs hourly, and the morning run has to cover the whole night.
# So each run searches from where the last *finished* run started — never a
# fixed "last hour" — and a run that fails leaves the checkpoint alone, so the
# next one picks up what it missed.

MAIL_WINDOW=hermes/bin/mail-window

setup() {
  HERMES_HOME="$(mktemp -d)"
  export HERMES_HOME
  export MAIL_WINDOW_NOW=1758790000
}

teardown() {
  rm -rf "$HERMES_HOME"
}

@test "the first run looks back one day" {
  run "$MAIL_WINDOW" start
  [ "$status" -eq 0 ]
  [ "$output" = "after:$((1758790000 - 86400))" ]
}

@test "the next run starts where the finished one started" {
  "$MAIL_WINDOW" start
  "$MAIL_WINDOW" done
  MAIL_WINDOW_NOW=1758793600 run "$MAIL_WINDOW" start
  [ "$output" = "after:1758790000" ]
}

@test "a run that never finished leaves the checkpoint where it was" {
  "$MAIL_WINDOW" start
  "$MAIL_WINDOW" done
  MAIL_WINDOW_NOW=1758793600 "$MAIL_WINDOW" start
  # no done: the pass broke half-way
  MAIL_WINDOW_NOW=1758797200 run "$MAIL_WINDOW" start
  [ "$output" = "after:1758790000" ]
}

@test "a checkpoint older than a week is capped" {
  "$MAIL_WINDOW" start
  "$MAIL_WINDOW" done
  MAIL_WINDOW_NOW=$((1758790000 + 30 * 86400)) run "$MAIL_WINDOW" start
  [ "$output" = "after:$((1758790000 + 23 * 86400))" ]
}

@test "done without start fails and says why" {
  run "$MAIL_WINDOW" done
  [ "$status" -ne 0 ]
  [[ "$output" == *"start"* ]]
}

@test "done twice does not move the checkpoint again" {
  "$MAIL_WINDOW" start
  "$MAIL_WINDOW" done
  run "$MAIL_WINDOW" done
  [ "$status" -ne 0 ]
  MAIL_WINDOW_NOW=1758793600 run "$MAIL_WINDOW" start
  [ "$output" = "after:1758790000" ]
}

@test "a damaged state file starts over from one day back" {
  mkdir -p "$HERMES_HOME/gmail"
  echo 'not json' > "$HERMES_HOME/gmail/window.json"
  run "$MAIL_WINDOW" start
  [ "$status" -eq 0 ]
  [ "$output" = "after:$((1758790000 - 86400))" ]
}
