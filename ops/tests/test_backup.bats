#!/usr/bin/env bats

# A fake `docker` on PATH stands in for the daemon:
#   docker volume inspect NAME  -> fails when NAME is in $FAKE_MISSING
#   docker run ... -v NAME:/src:ro ... -> a real tar.gz of a small dir on
#     stdout; exits with $FAKE_RC_<NAME> (non-alnum -> _) when that is set.
setup() {
  BACKUP_DIR="$(mktemp -d)"
  FAKE_BIN="$(mktemp -d)"
  export BACKUP_DIR FAKE_BIN
  export PATH="$FAKE_BIN:$PATH"
  cat > "$FAKE_BIN/docker" <<'EOF'
#!/usr/bin/env bash
if [[ "$1 $2" == "volume inspect" ]]; then
  for m in ${FAKE_MISSING:-}; do [[ "$m" == "$3" ]] && exit 1; done
  exit 0
fi
if [[ "$1" == "run" && " $* " == *" rclone/rclone:"* ]]; then
  echo "$*" >> "$BACKUP_DIR/.rclone-runs"
  echo "token=${RCLONE_CONFIG_GDRIVE_TOKEN:-} pass=${RCLONE_CONFIG_OFFSITE_PASSWORD:-}" \
    >> "$BACKUP_DIR/.rclone-env"
  exit "${FAKE_RCLONE_RC:-0}"
fi
if [[ "$1" == "run" ]]; then
  echo "$*" >> "$BACKUP_DIR/.tar-runs"
  vol=""
  for a in "$@"; do [[ "$a" == *:/src:ro ]] && vol="${a%%:/src:ro}"; done
  echo "$vol" >> "$BACKUP_DIR/.docker-runs"
  src="$(mktemp -d)"
  head -c 4096 /dev/urandom > "$src/data"
  tar -czf - -C "$src" .
  rm -rf "$src"
  var="FAKE_RC_${vol//[^a-zA-Z0-9]/_}"
  exit "${!var:-0}"
fi
exit 0
EOF
  chmod +x "$FAKE_BIN/docker"
  unset GMAIL_CLIENT_ID GMAIL_CLIENT_SECRET BACKUP_GDRIVE_TOKEN_B64 \
        BACKUP_CRYPT_PASSWORD BACKUP_CRYPT_SALT
}

configure_gdrive() {
  export GMAIL_CLIENT_ID=cid GMAIL_CLIENT_SECRET=csecret
  export BACKUP_GDRIVE_TOKEN_B64="$(printf '{"refresh_token":"rt-SECRET"}' | base64 -w0)"
  export BACKUP_CRYPT_PASSWORD=obscured-SECRET BACKUP_CRYPT_SALT=obscured-salt
}

teardown() {
  rm -rf "$BACKUP_DIR" "$FAKE_BIN"
}

@test "produces one archive per volume" {
  run bash ops/backup.sh
  [ "$status" -eq 0 ]
  for name in hermes-state vaultwarden-data chromium-profile hindsight-db \
              telegram-state; do
    ls "$BACKUP_DIR/$name"-*.tar.gz
  done
}


@test "a missing personal-assistant volume fails the run" {
  export FAKE_MISSING="personal-assistant_telegram_state"
  run bash ops/backup.sh
  [ "$status" -ne 0 ]
  [[ "$output" == *"personal-assistant_telegram_state"* ]]
}

@test "files that change while tar reads them do not fail the run" {
  # GNU tar exits 1 when a file changed or vanished during the read (hermes
  # SQLite -shm files); the archive itself is complete.
  export FAKE_RC_personal_assistant_hermes_state=1
  run bash ops/backup.sh
  [ "$status" -eq 0 ]
  ls "$BACKUP_DIR"/hermes-state-*.tar.gz
}

@test "a failed volume does not stop the others" {
  export FAKE_RC_personal_assistant_hermes_state=2
  run bash ops/backup.sh
  [ "$status" -ne 0 ]
  [[ "$output" == *"personal-assistant_hermes_state"* ]]
  ls "$BACKUP_DIR"/vaultwarden-data-*.tar.gz
}

@test "log starts with a timestamp" {
  run bash ops/backup.sh
  [[ "${lines[0]}" =~ ^==\ backup\ [0-9]{4}-[0-9]{2}-[0-9]{2}T ]]
}

@test "rotates backups older than 7 days, keeps newer" {
  touch -d '8 days ago' "$BACKUP_DIR/hermes-state-old.tar.gz"
  touch -d '6 days ago' "$BACKUP_DIR/hermes-state-recent.tar.gz"
  run bash ops/backup.sh
  [ "$status" -eq 0 ]
  [ ! -f "$BACKUP_DIR/hermes-state-old.tar.gz" ]
  [ -f "$BACKUP_DIR/hermes-state-recent.tar.gz" ]
}

@test "validate_gz accepts a real gzip above the floor" {
  f="$BACKUP_DIR/good.gz"
  head -c 4096 /dev/urandom | gzip > "$f"
  source ops/backup.sh
  run validate_gz "$f" 100
  [ "$status" -eq 0 ]
}

@test "validate_gz rejects a corrupt gzip" {
  f="$BACKUP_DIR/bad.gz"
  echo "not gzip at all" > "$f"
  source ops/backup.sh
  run validate_gz "$f" 100
  [ "$status" -ne 0 ]
}

@test "validate_gz rejects an undersized non-empty archive" {
  # A valid gzip that is NOT an empty-volume archive but still falls under the
  # floor must be rejected (guards against a truncated/partial dump).
  f="$BACKUP_DIR/tiny.gz"
  printf 'x' | gzip > "$f"
  source ops/backup.sh
  run validate_gz "$f" 100
  [ "$status" -ne 0 ]
}

@test "validate_gz accepts a tiny archive of a genuinely empty volume" {
  # An empty source volume tars to a valid-but-tiny gzip. It must NOT fail the
  # run on the size floor, otherwise a second, valid archive never gets synced.
  src="$(mktemp -d)"
  f="$BACKUP_DIR/empty-volume.tar.gz"
  tar -czf "$f" -C "$src" .
  rm -rf "$src"
  source ops/backup.sh
  # Force the floor high so the skip-empty guard is what lets it pass.
  run validate_gz "$f" 100000
  [ "$status" -eq 0 ]
}

@test "the Chromium archive leaves the browser caches out" {
  run bash ops/backup.sh
  [ "$status" -eq 0 ]
  line="$(grep personal-assistant_chromium_profile "$BACKUP_DIR/.tar-runs")"
  [[ "$line" == *"--exclude=Cache"* ]]
  [[ "$line" == *"--exclude=Code Cache"* ]]
  [[ "$line" == *"--exclude=Service Worker"* ]]
  ! grep personal-assistant_hermes_state "$BACKUP_DIR/.tar-runs" | grep -q -- --exclude=
}

@test "off-site sync skipped when Google Drive is not configured" {
  run bash ops/backup.sh
  [ "$status" -eq 0 ]
  [[ "$output" == *"off-site: Google Drive not configured, skipping"* ]]
  [ ! -e "$BACKUP_DIR/.rclone-runs" ]
}

@test "off-site sync skipped in dry-run even when configured" {
  configure_gdrive
  source ops/backup.sh
  export DRY_RUN=1
  run offsite_sync
  [ "$status" -eq 0 ]
  [ ! -e "$BACKUP_DIR/.rclone-runs" ]
}

@test "off-site mirrors the backup dir to the encrypted Drive remote" {
  configure_gdrive
  run bash ops/backup.sh
  [ "$status" -eq 0 ]
  line="$(cat "$BACKUP_DIR/.rclone-runs")"
  [[ "$line" == *"-v $BACKUP_DIR:/data:ro"* ]]
  [[ "$line" == *"sync /data offsite:"* ]]
  [[ "$line" == *"--drive-use-trash=false"* ]]
  # the container gets the decoded token and the crypt password ...
  grep -q 'token={"refresh_token":"rt-SECRET"} pass=obscured-SECRET' "$BACKUP_DIR/.rclone-env"
  # ... by name only: no secret on the docker command line
  [[ "$line" != *rt-SECRET* && "$line" != *obscured-SECRET* && "$line" != *csecret* ]]
}

@test "a failed off-site sync fails the run, archives stay" {
  configure_gdrive
  export FAKE_RCLONE_RC=1
  run bash ops/backup.sh
  [ "$status" -ne 0 ]
  [[ "$output" == *"off-site"* ]]
  ls "$BACKUP_DIR"/hermes-state-*.tar.gz
}

@test "a broken archive is not left behind for off-site to mirror" {
  export FAKE_RC_personal_assistant_hermes_state=2
  run bash ops/backup.sh
  [ ! -e "$BACKUP_DIR"/hermes-state-*.tar.gz ]
}
