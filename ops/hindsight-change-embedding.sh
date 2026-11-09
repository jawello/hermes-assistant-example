#!/usr/bin/env bash
# Change Hindsight's local embedding model on a bank that already has data.
#
# Upstream refuses a new dimension on a non-empty table, so the column, its vectors
# and its indexes change in one transaction while Hindsight is stopped. The vectors
# are computed on this machine, because a small server has no memory to spare.
#
# Usage: ops/hindsight-change-embedding.sh <model> <work-dir>
#
# On success Hindsight stays stopped: deploying the matching
# HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL starts it with the new model. On any failure
# before the commit the database is unchanged and Hindsight is started again.
# To roll back, run this with the previous model and revert the setting.
set -euo pipefail

usage="usage: hindsight-change-embedding.sh <model> <work-dir>"
model=${1:?$usage}
work=${2:?$usage}
target=${TARGET:-ssh deploy@your-server}
app=${APP_CONTAINER:-personal-assistant-hindsight-1}
db=${DB_CONTAINER:-personal-assistant-hindsight-db-1}
image=${IMAGE:-assistant/hindsight:latest}
tool=$(cd "$(dirname "$0")/../hindsight/tools" && pwd)/reembed.py

psql="docker exec -i $db sh -c 'psql -U \${POSTGRES_USER:-hindsight} -d \${POSTGRES_DB:-hindsight} -v ON_ERROR_STOP=1 -q -At'"
pg_dump="docker exec $db sh -c 'pg_dump -U \${POSTGRES_USER:-hindsight} -d \${POSTGRES_DB:-hindsight} -Fc'"

on_target() {
  # shellcheck disable=SC2086  # TARGET is a command with its arguments.
  $target "$1"
}

umask 077
mkdir -p "$work"
tmp=$(mktemp -d "$work/tmp.XXXXXX")
applied=no

finish() {
  rm -rf "$tmp"
  if [ "$applied" != yes ]; then
    echo "Stopped before the commit, the database is unchanged. Starting Hindsight again." >&2
    on_target "docker start $app" >/dev/null || echo "Could not start $app." >&2
  fi
}
trap finish EXIT

echo "Stopping Hindsight"
on_target "docker stop -t 30 $app" >/dev/null

dump_file="$work/hindsight-before-$(date +%F-%H%M).dump"
echo "Dumping to $dump_file"
on_target "$pg_dump" >"$dump_file"
[ "$(head -c 5 "$dump_file")" = PGDMP ]

echo "Exporting rows"
on_target "$psql" >"$tmp/rows.jsonl" <<'SQL'
SET TIME ZONE 'UTC';
SELECT json_build_object(
         'id', m.id, 'text', m.text, 'fact_type', m.fact_type,
         'occurred_start', m.occurred_start, 'occurred_end', m.occurred_end, 'mentioned_at', m.mentioned_at,
         'entities', coalesce(array_agg(e.canonical_name ORDER BY e.canonical_name) FILTER (WHERE e.id IS NOT NULL), '{}'))
FROM memory_units m
LEFT JOIN unit_entities ue ON ue.unit_id = m.id
LEFT JOIN entities e ON e.id = ue.entity_id
GROUP BY m.id;
SQL

echo "Computing vectors with $model"
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$tmp:/work" -v "$tool:/tmp/reembed.py:ro" \
  -e HOME=/work -e HF_HOME=/work/hf \
  -e HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL="$model" \
  -e HINDSIGHT_API_DATABASE_URL=postgresql://unused/unused -e HINDSIGHT_API_LLM_API_KEY=none \
  --entrypoint python "$image" /tmp/reembed.py /work/rows.jsonl /work/vectors.tsv

rows=$(wc -l <"$tmp/rows.jsonl")
vectors=$(wc -l <"$tmp/vectors.tsv")
if [ "$rows" -eq 0 ] || [ "$rows" -ne "$vectors" ]; then
  echo "Got $vectors vectors for $rows rows." >&2
  exit 1
fi
dim=$(head -1 "$tmp/vectors.tsv" | awk -F, '{print NF}')

echo "Applying $vectors vectors of dimension $dim"
{
  cat <<SQL
BEGIN;
SET LOCAL lock_timeout = '30s';
DO \$\$ BEGIN
  IF EXISTS (SELECT 1 FROM mental_models WHERE embedding IS NOT NULL) THEN
    RAISE EXCEPTION 'mental_models has vectors, and this script does not recompute them';
  END IF;
END \$\$;
CREATE TEMP TABLE vector_indexes ON COMMIT DROP AS
  SELECT indexname, indexdef FROM pg_indexes
  WHERE schemaname = 'public' AND tablename IN ('memory_units', 'mental_models')
    AND indexdef LIKE '%USING hnsw (embedding%';
DO \$\$ DECLARE item record; BEGIN
  FOR item IN SELECT indexname FROM vector_indexes LOOP
    EXECUTE format('DROP INDEX public.%I', item.indexname);
  END LOOP;
END \$\$;
ALTER TABLE memory_units ALTER COLUMN embedding TYPE vector($dim) USING NULL;
ALTER TABLE mental_models ALTER COLUMN embedding TYPE vector($dim) USING NULL;
CREATE TEMP TABLE new_vectors (id uuid PRIMARY KEY, embedding vector($dim)) ON COMMIT DROP;
COPY new_vectors FROM STDIN;
SQL
  cat "$tmp/vectors.tsv"
  cat <<SQL
\\.
UPDATE memory_units m SET embedding = n.embedding FROM new_vectors n WHERE n.id = m.id;
DO \$\$ BEGIN
  IF EXISTS (SELECT 1 FROM memory_units WHERE embedding IS NULL) THEN
    RAISE EXCEPTION 'some units were left without a vector';
  END IF;
END \$\$;
DO \$\$ DECLARE item record; BEGIN
  FOR item IN SELECT indexdef FROM vector_indexes LOOP
    EXECUTE item.indexdef;
  END LOOP;
END \$\$;
COMMIT;
SQL
} | on_target "$psql"
applied=yes

on_target "$psql" <<'SQL'
VACUUM ANALYZE memory_units;
SELECT 'units', count(*), 'with vector', count(embedding), 'dimension', min(vector_dims(embedding)), max(vector_dims(embedding)) FROM memory_units;
SELECT 'vector indexes', count(*) FROM pg_indexes WHERE indexdef LIKE '%USING hnsw (embedding%';
SQL

echo "Done. Hindsight is stopped: deploy HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL=$model to start it."
