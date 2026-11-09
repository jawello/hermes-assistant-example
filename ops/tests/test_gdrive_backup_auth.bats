#!/usr/bin/env bats

setup() {
  source ops/gdrive-backup-auth.sh
}

@test "the token is the JSON between rclone's paste markers" {
  out=$'Paste the following into your remote machine --->\n{"access_token":"a","refresh_token":"r"}\n<---End paste'
  run extract_token <<< "$out"
  [ "$status" -eq 0 ]
  [ "$output" = '{"access_token":"a","refresh_token":"r"}' ]
}

@test "no token in rclone's output is an error" {
  run extract_token <<< "Error: failed to get token"
  [ "$status" -ne 0 ]
}

@test "the client blob is URL-safe base64 without padding" {
  run client_blob "id" "secret"
  [ "$status" -eq 0 ]
  [[ "$output" != *"="* && "$output" != *"+"* && "$output" != *"/"* ]]
  decoded="$(printf '%s==' "$output" | tr -- '-_' '+/' | base64 -d 2>/dev/null || true)"
  [[ "$decoded" == *'"scope":"drive.file"'* ]]
}

@test "with a config blob rclone prints an encoded map; the token is inside" {
  # rclone authorize <fs> <blob> prints configmap.Simple.Encode():
  # URL-safe unpadded base64 of {"token": "<token JSON>"}.
  map='{"token":"{\"access_token\":\"a\",\"refresh_token\":\"r\"}"}'
  enc="$(printf '%s' "$map" | base64 -w0 | tr '+/' '-_' | tr -d '=')"
  out=$'Paste the following into your remote machine --->\n'"$enc"$'\n<---End paste'
  run extract_token <<< "$out"
  [ "$status" -eq 0 ]
  [ "$output" = '{"access_token":"a","refresh_token":"r"}' ]
}
