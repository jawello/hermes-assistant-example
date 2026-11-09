#!/usr/bin/env bash
# Daily backups of Hermes state (memory, sessions, skills, config) and the
# other stateful services, straight from the docker volumes. Restore:
# docs/backup-restore.md.
# Sourceable: defines functions at top level; runs main() only when executed.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/personal-assistant}"
DRY_RUN="${DRY_RUN:-0}"
RETENTION_DAYS=7
MIN_DUMP_BYTES=100
DATE="$(date +%F)"

# Off-site (Google Drive) config comes from .env if it has been materialised.
if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$REPO_ROOT/.env"
  set +a
fi

# volume | archive name | required (1) or skipped when absent (0).
# Not listed, because git, sops or the service itself recreates them:
# vaultwarden_tls (vault-cert-init), gmail_state (empty), the /shared volume
# (janitor-pruned scratch), hindsight_data (model caches; the graph is in
# hindsight_db_data).
VOLUMES=(
  "personal-assistant_hermes_state       hermes-state          1"
  "personal-assistant_vaultwarden_data   vaultwarden-data      1"
  "personal-assistant_chromium_profile   chromium-profile      1"
  "personal-assistant_hindsight_db_data  hindsight-db          1"
  # telegram-mcp state: the allowlist of chats the bot may write to (grown
  # at runtime by allow_chat) and the voice transcript cache. The session
  # itself is in sops, not here.
  "personal-assistant_telegram_state     telegram-state        1"
)
# GNU tar, not busybox: it tells "a file changed or vanished while being read"
# (exit 1, archive complete; hermes' SQLite -shm files do it) from a real
# failure (exit 2).
TAR_IMAGE=debian:bookworm-slim
RCLONE_IMAGE=rclone/rclone:1.69.1
FAILED=()

# Per-archive tar excludes. Chromium's caches are ~97% of its profile and
# refill on their own; logins live in Cookies and Local Storage.
tar_excludes() {
  case "$1" in
    chromium-profile)
      printf '%s\n' "--exclude=Cache" "--exclude=Code Cache" \
        "--exclude=GPUCache" "--exclude=Service Worker" ;;
  esac
}

archive_volume() {
  local volume="$1" out="$2" name="$3" rc=0 excludes=()
  mapfile -t excludes < <(tar_excludes "$name")
  docker run --rm -v "$volume":/src:ro "$TAR_IMAGE" \
    tar --ignore-failed-read --warning=no-file-changed \
        --warning=no-file-removed --warning=no-file-ignored \
        "${excludes[@]}" -czf - -C /src . > "$out" || rc=$?
  if (( rc > 1 )) || ! validate_gz "$out" "$MIN_DUMP_BYTES"; then
    echo "backup: tar of $volume exited $rc" >&2
    # Off-site would mirror a broken file; yesterday's archive stays.
    rm -f "$out"
    return 1
  fi
}

dump_all() {
  mkdir -p "$BACKUP_DIR"
  local entry volume name required
  for entry in "${VOLUMES[@]}"; do
    read -r volume name required <<< "$entry"
    if ! docker volume inspect "$volume" >/dev/null 2>&1; then
      if [[ "$required" == "1" ]]; then
        echo "backup: required volume $volume not found" >&2
        FAILED+=("$volume")
      else
        echo "$volume volume not found, skipping" >&2
      fi
      continue
    fi
    archive_volume "$volume" "$BACKUP_DIR/${name}-${DATE}.tar.gz" "$name" \
      || FAILED+=("$volume")
  done
}

validate_gz() {
  local file="$1" min="$2" size entries
  if [[ ! -f "$file" ]]; then echo "validate: missing $file" >&2; return 1; fi
  if ! gzip -t "$file" 2>/dev/null; then echo "validate: corrupt gzip $file" >&2; return 1; fi
  # An empty source volume tars to a valid-but-tiny tar.gz whose only entry is
  # the root directory ("." or "./"). That is a legitimate state, not
  # corruption, so don't fail the whole run on the size floor for it. Anything
  # else that is small — truncated dumps, non-tar junk (which lists as zero
  # entries) — still falls through to the floor check below.
  if entries=$(tar -tzf "$file" 2>/dev/null); then
    local trimmed
    trimmed="$(printf '%s' "$entries" | tr -d '[:space:]')"
    if [[ "$trimmed" == "." || "$trimmed" == "./" ]]; then
      echo "validate: $file archives an empty volume, skipping size floor" >&2
      return 0
    fi
  fi
  size=$(stat -c%s "$file")
  if (( size < min )); then echo "validate: $file too small ($size < $min bytes)" >&2; return 1; fi
  return 0
}

prune_local() {
  find "$BACKUP_DIR" -name '*.gz' -mtime +"$RETENTION_DAYS" -delete
}

# The off-site is Google Drive through an rclone crypt remote: Drive holds
# only encrypted files with encrypted names. The OAuth client is gmail-mcp's;
# the token (scope drive.file: rclone sees only what it wrote) and the crypt
# password come from sops, set by ops/gdrive-backup-auth.sh.
offsite_configured() {
  [[ -n "${GMAIL_CLIENT_ID:-}" && -n "${GMAIL_CLIENT_SECRET:-}" \
     && -n "${BACKUP_GDRIVE_TOKEN_B64:-}" && -n "${BACKUP_CRYPT_PASSWORD:-}" \
     && -n "${BACKUP_CRYPT_SALT:-}" ]]
}

# rclone_offsite <ro|rw> <rclone args...>: rclone with $BACKUP_DIR at /data
# and the remote `offsite:`. Restore: rclone_offsite rw copy offsite: /data
rclone_offsite() {
  local mode="$1"; shift
  # Secrets reach the container by name (-e VAR), never on the command line.
  (
    export RCLONE_CONFIG_GDRIVE_TYPE=drive
    export RCLONE_CONFIG_GDRIVE_SCOPE=drive.file
    export RCLONE_CONFIG_GDRIVE_CLIENT_ID="$GMAIL_CLIENT_ID"
    export RCLONE_CONFIG_GDRIVE_CLIENT_SECRET="$GMAIL_CLIENT_SECRET"
    RCLONE_CONFIG_GDRIVE_TOKEN="$(printf '%s' "$BACKUP_GDRIVE_TOKEN_B64" | base64 -d)"
    export RCLONE_CONFIG_GDRIVE_TOKEN
    export RCLONE_CONFIG_OFFSITE_TYPE=crypt
    export RCLONE_CONFIG_OFFSITE_REMOTE=gdrive:personal-assistant-backups
    export RCLONE_CONFIG_OFFSITE_PASSWORD="$BACKUP_CRYPT_PASSWORD"
    export RCLONE_CONFIG_OFFSITE_PASSWORD2="$BACKUP_CRYPT_SALT"
    docker run --rm \
      -e RCLONE_CONFIG_GDRIVE_TYPE -e RCLONE_CONFIG_GDRIVE_SCOPE \
      -e RCLONE_CONFIG_GDRIVE_CLIENT_ID -e RCLONE_CONFIG_GDRIVE_CLIENT_SECRET \
      -e RCLONE_CONFIG_GDRIVE_TOKEN -e RCLONE_CONFIG_OFFSITE_TYPE \
      -e RCLONE_CONFIG_OFFSITE_REMOTE -e RCLONE_CONFIG_OFFSITE_PASSWORD \
      -e RCLONE_CONFIG_OFFSITE_PASSWORD2 \
      -v "$BACKUP_DIR":/data:"$mode" "$RCLONE_IMAGE" "$@"
  )
}

offsite_sync() {
  if ! offsite_configured; then
    echo "off-site: Google Drive not configured, skipping" >&2
    return 0
  fi
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "off-site: DRY_RUN, skipping sync" >&2
    return 0
  fi
  local rc=0
  rclone_offsite ro sync /data offsite: --drive-use-trash=false \
    --stats-one-line -v || rc=$?
  if (( rc != 0 )); then
    echo "off-site: rclone sync exited $rc" >&2
  fi
  return "$rc"
}

main() {
  set -uo pipefail
  echo "== backup $(date -Is)"
  # One bad volume must not cost the others their backup: archive all, sync
  # what there is, then fail the run.
  dump_all
  prune_local
  offsite_sync || FAILED+=("off-site sync")
  if (( ${#FAILED[@]} )); then
    echo "backup: FAILED: ${FAILED[*]}" >&2
    return 1
  fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
