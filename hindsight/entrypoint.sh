#!/usr/bin/env bash
# Runs the Hindsight API in the foreground as PID 1, as the non-root user `hs`.
#
# Uses an EXTERNAL PostgreSQL (pgvector) via HINDSIGHT_API_DATABASE_URL rather
# than the bundled pg0 embedded database — pg0 was flaky under a docker volume
# ("PostgreSQL started: None" → migrations had no DB URL). The pgvector image is
# a standard, robust Postgres that owns its own data volume.
#
# The embeddings/reranker/tiktoken models download on first boot and cache under
# /home/hs (volume), so later boots are fast. Needs egress (Portal extractor +
# first-boot model downloads).
set -euo pipefail

: "${HINDSIGHT_API_LLM_API_KEY:?HINDSIGHT_API_LLM_API_KEY is required}"
: "${HINDSIGHT_API_DATABASE_URL:?HINDSIGHT_API_DATABASE_URL is required}"
export HINDSIGHT_API_LLM_PROVIDER="${HINDSIGHT_API_LLM_PROVIDER:-openai}"
export HINDSIGHT_API_LLM_MODEL="${HINDSIGHT_API_LLM_MODEL:-openai/gpt-6-luna}"
export HINDSIGHT_API_LLM_BASE_URL="${HINDSIGHT_API_LLM_BASE_URL:-https://inference-api.nousresearch.com/v1}"
export HINDSIGHT_API_HOST="${HINDSIGHT_API_HOST:-0.0.0.0}"
export HINDSIGHT_API_PORT="${HINDSIGHT_API_PORT:-8888}"

exec python -m hindsight_api.main --port "${HINDSIGHT_API_PORT}"
