#!/usr/bin/env bash
# Connects ops/backup.sh to Google Drive, once, or again when the token dies.
# Run on the laptop (browser + sops age key), from anywhere in the repo:
#   ops/gdrive-backup-auth.sh
# It opens Google's consent for gmail-mcp's OAuth client with scope
# drive.file, and writes into secrets.sops.yaml:
#   BACKUP_GDRIVE_TOKEN_B64  the rclone token, base64 (.env is sourced by bash)
#   BACKUP_CRYPT_PASSWORD, BACKUP_CRYPT_SALT  rclone-obscured crypt keys,
#     created on the first run only: new ones would lock out the archives
#     already on Drive.
# Prints no secret. Commit and push secrets.sops.yaml afterwards.
# Sourceable: defines functions at top level; runs main() only when executed.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS="$REPO_ROOT/secrets.sops.yaml"
RCLONE_IMAGE=rclone/rclone:1.69.1 # keep in step with ops/backup.sh

# rclone authorize takes the remote config as URL-safe unpadded base64 JSON.
client_blob() {
  printf '{"client_id":"%s","client_secret":"%s","scope":"drive.file"}' "$1" "$2" \
    | base64 -w0 | tr '+/' '-_' | tr -d '='
}

# The token is between rclone's "--->" and "<---End paste": plain JSON, or,
# when authorize got a config blob (our case), an encoded config map
# (URL-safe unpadded base64 JSON) whose "token" holds that JSON.
extract_token() {
  local pasted token
  pasted="$(sed -n '/--->$/,/^<---End paste/p' | sed '1d;$d')"
  if [[ "$pasted" == "{"* ]]; then
    token="$pasted"
  else
    token="$(python3 -c '
import base64, json, sys
s = sys.argv[1]
print(json.loads(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)))["token"])
' "$pasted" 2>/dev/null)" || token=""
  fi
  [[ "$token" == "{"* ]] || { echo "no token in rclone output" >&2; return 1; }
  printf '%s\n' "$token"
}

secret() { sops -d --extract "[\"env\"][\"$1\"]" "$SECRETS"; }
has_secret() { secret "$1" >/dev/null 2>&1; }
set_secret() { sops set "$SECRETS" "[\"env\"][\"$1\"]" "\"$2\""; }

new_obscured_key() {
  head -c 32 /dev/urandom | base64 -w0 \
    | docker run --rm -i "$RCLONE_IMAGE" obscure -
}

main() {
  set -euo pipefail
  local blob out token
  blob="$(client_blob "$(secret GMAIL_CLIENT_ID)" "$(secret GMAIL_CLIENT_SECRET)")"
  echo "Open the 127.0.0.1:53682 link below and allow access to Drive." >&2
  # stdout (the token) is captured; rclone's log, with the link, goes to the
  # terminal.
  out="$(docker run --rm --net=host "$RCLONE_IMAGE" \
    authorize drive "$blob" --auth-no-open-browser)"
  token="$(extract_token <<< "$out")"
  set_secret BACKUP_GDRIVE_TOKEN_B64 "$(printf '%s' "$token" | base64 -w0)"
  echo "token saved" >&2
  if has_secret BACKUP_CRYPT_PASSWORD; then
    echo "crypt keys already set, kept" >&2
  else
    set_secret BACKUP_CRYPT_PASSWORD "$(new_obscured_key)"
    set_secret BACKUP_CRYPT_SALT "$(new_obscured_key)"
    echo "crypt keys created" >&2
  fi
  echo "done: commit and push secrets.sops.yaml" >&2
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
