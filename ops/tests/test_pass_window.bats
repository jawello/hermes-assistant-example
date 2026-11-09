#!/usr/bin/env bats
#
# One checkpoint helper for every scheduled reading pass. Each source keeps
# its own checkpoint; gmail's output is the Gmail query term the mail jobs
# already use, telegram's is a timestamp and a date for list_messages.

PASS_WINDOW=hermes/bin/pass-window

setup() {
  HERMES_HOME="$(mktemp -d)"
  export HERMES_HOME
  export PASS_WINDOW_NOW=1758790000  # 2025-09-25T08:46:40Z
}

teardown() {
  rm -rf "$HERMES_HOME"
}

@test "telegram's first run looks back one day, as a UTC time and date" {
  run "$PASS_WINDOW" telegram start
  [ "$status" -eq 0 ]
  [ "$output" = "since=2025-09-24T08:46:40Z date=2025-09-24" ]
}

@test "telegram's next run starts where the finished one started" {
  "$PASS_WINDOW" telegram start
  "$PASS_WINDOW" telegram done
  PASS_WINDOW_NOW=1758876400 run "$PASS_WINDOW" telegram start
  [ "$output" = "since=2025-09-25T08:46:40Z date=2025-09-25" ]
}

@test "a telegram run that never finished leaves the checkpoint where it was" {
  "$PASS_WINDOW" telegram start
  "$PASS_WINDOW" telegram done
  PASS_WINDOW_NOW=1758876400 "$PASS_WINDOW" telegram start
  # no done: the pass broke half-way
  PASS_WINDOW_NOW=1758962800 run "$PASS_WINDOW" telegram start
  [ "$output" = "since=2025-09-25T08:46:40Z date=2025-09-25" ]
}

@test "gmail and telegram keep separate checkpoints" {
  "$PASS_WINDOW" telegram start
  "$PASS_WINDOW" telegram done
  [ -f "$HERMES_HOME/telegram/window.json" ]
  [ ! -e "$HERMES_HOME/gmail/window.json" ]
  run "$PASS_WINDOW" gmail start
  [ "$output" = "after:$((1758790000 - 86400))" ]
}

@test "mail-window is pass-window for gmail" {
  [ "$(readlink hermes/bin/mail-window)" = "pass-window" ]
  run hermes/bin/mail-window start
  [ "$status" -eq 0 ]
  [ "$output" = "after:$((1758790000 - 86400))" ]
}

@test "an unknown source fails with the usage line" {
  run "$PASS_WINDOW" slack start
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage: pass-window gmail|telegram start|done"* ]]
}
