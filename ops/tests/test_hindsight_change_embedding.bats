#!/usr/bin/env bats
#
# ops/hindsight-change-embedding.sh stops Hindsight, dumps the database, computes
# vectors locally and swaps the column, vectors and indexes in one transaction.
# TARGET is a stub that logs every remote command and every SQL script it gets;
# the local `docker run` is a stub that writes the vectors.

SCRIPT=ops/hindsight-change-embedding.sh

setup() {
  export LOG="$BATS_TEST_TMPDIR/remote.log"
  export SQL_LOG="$BATS_TEST_TMPDIR/sql.log"
  export WORK="$BATS_TEST_TMPDIR/work"
  STUB_BIN="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$STUB_BIN" "$WORK"
  : > "$LOG"
  : > "$SQL_LOG"

  cat > "$STUB_BIN/target" <<'STUB'
#!/usr/bin/env bash
# Stands in for `ssh host`. The command is the only argument.
printf '%s\n' "$1" >> "$LOG"
case "$1" in
  *pg_dump*)
    [ -n "${FAIL_DUMP:-}" ] && exit 1
    printf 'PGDMP fake dump' ;;
  *psql*)
    sql=$(cat)
    printf '%s\n---\n' "$sql" >> "$SQL_LOG"
    case "$sql" in
      *json_build_object*) printf '{"id":"a"}\n{"id":"b"}\n' ;;
      *COMMIT*) [ -n "${FAIL_APPLY:-}" ] && exit 3 ;;
      *) : ;;
    esac ;;
esac
exit 0
STUB

  cat > "$STUB_BIN/docker" <<'STUB'
#!/usr/bin/env bash
# Only `docker run` for the local computation reaches this stub.
[ -n "${FAIL_COMPUTE:-}" ] && exit 1
work=$(printf '%s\n' "$@" | sed -n 's|^\(.*\):/work$|\1|p')
if [ -n "${SHORT_VECTORS:-}" ]; then
  printf 'a\t[0.1,0.2,0.3]\n' > "$work/vectors.tsv"
else
  printf 'a\t[0.1,0.2,0.3]\nb\t[0.4,0.5,0.6]\n' > "$work/vectors.tsv"
fi
STUB

  chmod +x "$STUB_BIN/target" "$STUB_BIN/docker"
  export PATH="$STUB_BIN:$PATH"
  export TARGET=target
}

@test "refuses to run without a model and a work directory" {
  run bash "$SCRIPT"
  [ "$status" -ne 0 ]
  [ ! -s "$LOG" ]
}

@test "stops Hindsight before the dump and leaves it stopped after success" {
  run bash "$SCRIPT" some/model "$WORK"
  [ "$status" -eq 0 ]
  stop=$(grep -n 'docker stop' "$LOG" | head -1 | cut -d: -f1)
  dump=$(grep -n 'pg_dump' "$LOG" | head -1 | cut -d: -f1)
  [ "$stop" -lt "$dump" ]
  ! grep -q 'docker start' "$LOG"
}

@test "keeps the dump and removes the rows and vectors" {
  run bash "$SCRIPT" some/model "$WORK"
  [ "$status" -eq 0 ]
  ls "$WORK"/hindsight-before-*.dump
  [ "$(find "$WORK" -name 'rows.jsonl' -o -name 'vectors.tsv' | wc -l)" -eq 0 ]
}

@test "the transaction uses the dimension of the computed vectors" {
  run bash "$SCRIPT" some/model "$WORK"
  [ "$status" -eq 0 ]
  grep -q 'ALTER TABLE memory_units ALTER COLUMN embedding TYPE vector(3) USING NULL' "$SQL_LOG"
  grep -q 'ALTER TABLE mental_models ALTER COLUMN embedding TYPE vector(3) USING NULL' "$SQL_LOG"
  grep -q 'embedding vector(3)' "$SQL_LOG"
}

@test "vectors reach the database inside the transaction, before the commit" {
  run bash "$SCRIPT" some/model "$WORK"
  [ "$status" -eq 0 ]
  copy=$(grep -n '^COPY new_vectors FROM STDIN' "$SQL_LOG" | cut -d: -f1)
  data=$(grep -n '^a	\[0.1,0.2,0.3\]' "$SQL_LOG" | cut -d: -f1)
  commit=$(grep -n '^COMMIT;' "$SQL_LOG" | cut -d: -f1)
  begin=$(grep -n '^BEGIN;' "$SQL_LOG" | cut -d: -f1)
  [ "$begin" -lt "$copy" ]
  [ "$copy" -lt "$data" ]
  [ "$data" -lt "$commit" ]
}

@test "the transaction refuses units left without a vector and mental models with data" {
  run bash "$SCRIPT" some/model "$WORK"
  [ "$status" -eq 0 ]
  grep -q 'FROM memory_units WHERE embedding IS NULL' "$SQL_LOG"
  grep -q 'FROM mental_models WHERE embedding IS NOT NULL' "$SQL_LOG"
}

@test "a failed dump starts Hindsight again and changes nothing" {
  FAIL_DUMP=1 run bash "$SCRIPT" some/model "$WORK"
  [ "$status" -ne 0 ]
  grep -q 'docker start' "$LOG"
  ! grep -q 'ALTER TABLE' "$SQL_LOG"
}

@test "a failed computation starts Hindsight again and changes nothing" {
  FAIL_COMPUTE=1 run bash "$SCRIPT" some/model "$WORK"
  [ "$status" -ne 0 ]
  grep -q 'docker start' "$LOG"
  ! grep -q 'ALTER TABLE' "$SQL_LOG"
}

@test "fewer vectors than rows starts Hindsight again and changes nothing" {
  SHORT_VECTORS=1 run bash "$SCRIPT" some/model "$WORK"
  [ "$status" -ne 0 ]
  grep -q 'docker start' "$LOG"
  ! grep -q 'ALTER TABLE' "$SQL_LOG"
}

@test "a failed transaction starts Hindsight again" {
  FAIL_APPLY=1 run bash "$SCRIPT" some/model "$WORK"
  [ "$status" -ne 0 ]
  grep -q 'docker start' "$LOG"
}
