#!/usr/bin/env bash
# Every check this repo has: compose config, shellcheck, bats and each Python
# test suite, with dummy values only. CI runs it; so can you, from the repo
# root: `bash scripts/check.sh`. Needs docker, uv, shellcheck and bats.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# Dummies so compose interpolation succeeds; no real account is touched.
for var in ANTHROPIC_API_KEY GOOGLE_API_KEY TELEGRAM_BOT_TOKEN TICKTICK_API_TOKEN \
           GMAIL_CLIENT_ID GMAIL_CLIENT_SECRET GMAIL_REFRESH_TOKEN VAULTWARDEN_ADMIN_TOKEN \
           BW_MASTER_PASSWORD HINDSIGHT_LLM_API_KEY HINDSIGHT_DB_PASSWORD ZENMONEY_TOKEN \
           GROQ_API_KEY TELEGRAM_API_ID TELEGRAM_API_HASH TELEGRAM_SESSION_STRING \
           TELEGRAM_EXPECTED_USERNAME; do
  export "$var=${!var:-x}"
done
export TELEGRAM_ALLOWED_USERS="${TELEGRAM_ALLOWED_USERS:-1}"
export PYTHONDONTWRITEBYTECODE=1

step() { printf '\n== %s\n' "$*"; }

step "docker compose config"
docker compose config --quiet

step "shellcheck"
shellcheck ops/*.sh hermes/entrypoint.sh hindsight/entrypoint.sh telegram-mcp/run.sh scripts/check.sh

step "bats"
bats ops/tests/

for pkg in login-broker zenmoney-cli gmail-mcp alice-bridge; do
  step "$pkg tests"
  uv run --quiet --project "$pkg" --extra test --with pytest python -m pytest "$pkg" -q -p no:cacheprovider
done

step "hermes patch and plugin tests"
uv run --quiet --no-project --with pytest python -m pytest hermes/patches hermes/plugins/tests -q -p no:cacheprovider

step "hindsight patch and tool tests"
uv run --quiet --no-project --with pytest --with numpy python -m pytest hindsight/patches hindsight/tools -q -p no:cacheprovider

step "telegram-mcp patch, send gate and history search tests"
uv run --quiet --no-project --with pytest --with pydantic \
  python -m pytest telegram-mcp/patches telegram-mcp/send_gate telegram-mcp/history_search -q -p no:cacheprovider

step "hadolint"
for f in */Dockerfile; do
  echo "-- $f"
  docker run --rm -i hadolint/hadolint:v2.15.1 hadolint --failure-threshold error - < "$f"
done

step "all checks passed"
