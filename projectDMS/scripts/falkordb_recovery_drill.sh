#!/usr/bin/env bash
# Destructive, disposable FalkorDB backup/recovery drill.
#
# Proves - or disproves - that the documented production backup captures
# enough persisted state to rebuild the graph on a fresh container. It reads
# the image, the Redis arguments and the volume mount straight out of
# docker-compose.prod.yml, so it fails again if the deployed persistence
# configuration and the backup target ever drift apart.
#
#   seed -> persist -> scripts/backup_volume.sh -> destroy container AND volume
#        -> scripts/production_restore_volumes.sh -> fresh container
#        -> compare semantic snapshots
#
# It never touches a live container, a live volume or a business graph: every
# resource it creates carries the run-owned "ra4drill_" prefix and is removed
# on exit. tar exiting 0 is not a pass; only a matching snapshot is.
#
# Usage: scripts/falkordb_recovery_drill.sh [--keep]
set -euo pipefail

ROOT_DIR=${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
COMPOSE_FILE="$ROOT_DIR/docker-compose.prod.yml"
KEEP=false
[[ "${1:-}" == "--keep" ]] && KEEP=true

RUN_ID="ra4drill_$(date '+%H%M%S')_$$"
SOURCE_CONTAINER="${RUN_ID}_source"
TARGET_CONTAINER="${RUN_ID}_target"
SOURCE_VOLUME="${RUN_ID}_source_vol"
TARGET_VOLUME="${RUN_ID}_target_vol"
EMPTY_VOLUME="${RUN_ID}_empty_vol"
GRAPH="${RUN_ID}_graph"
PASSWORD="ra4-drill-not-a-production-secret"
# The archive directory is bind-mounted into a container, so it has to be a
# path the Docker daemon can see. Override DRILL_WORK_DIR when the shell's
# temp directory is not one of them (Git Bash's /tmp, for instance).
WORK_DIR=$(mktemp -d "${DRILL_WORK_DIR:-${TMPDIR:-/tmp}}/ra4drill.XXXXXX")

failures=0
step() { echo; echo "=== $* ==="; }
pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*" >&2; failures=$((failures + 1)); }

cleanup() {
  if [[ "$KEEP" == "true" ]]; then
    echo "--keep: leaving $SOURCE_CONTAINER $TARGET_CONTAINER $SOURCE_VOLUME $TARGET_VOLUME $WORK_DIR"
    return
  fi
  docker rm -f "$SOURCE_CONTAINER" "$TARGET_CONTAINER" >/dev/null 2>&1 || true
  docker volume rm "$SOURCE_VOLUME" "$TARGET_VOLUME" "$EMPTY_VOLUME" >/dev/null 2>&1 || true
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

# --------------------------------------------------------------------------- #
# Deployment configuration, read rather than assumed                           #
# --------------------------------------------------------------------------- #

block=$(awk '/^  falkordb:$/{inside=1; next} inside && /^  [a-z]/{exit} inside{print}' "$COMPOSE_FILE")
if [[ -z "$block" ]]; then
  echo "no falkordb service in $COMPOSE_FILE" >&2
  exit 2
fi

IMAGE=$(printf '%s\n' "$block" | sed -n 's/^    image: *//p' | head -1)
MOUNT=$(printf '%s\n' "$block" | sed -n 's/^      - falkordb_data: *//p' | head -1)
REDIS_ARGS=$(printf '%s\n' "$block" \
  | awk '/^      REDIS_ARGS: >-$/{inside=1; next} inside && !/^        /{exit} inside{print}' \
  | sed 's/^ *//' | tr '\n' ' ' | sed 's/  */ /g; s/ *$//')
REDIS_ARGS=$(printf '%s' "$REDIS_ARGS" | sed "s/\${FALKORDB_PASSWORD[^}]*}/$PASSWORD/g")

if [[ -z "$IMAGE" || -z "$MOUNT" || -z "$REDIS_ARGS" ]]; then
  echo "could not read image/mount/REDIS_ARGS out of $COMPOSE_FILE" >&2
  exit 2
fi

echo "Image      : $IMAGE"
echo "Mount      : falkordb_data -> $MOUNT"
echo "REDIS_ARGS : $REDIS_ARGS"

start_falkor() {
  local name=$1 volume=$2
  docker run -d --name "$name" \
    -e FALKORDB_PASSWORD="$PASSWORD" \
    -e REDIS_ARGS="$REDIS_ARGS" \
    -v "${volume}:${MOUNT}" \
    "$IMAGE" >/dev/null
  local attempt
  for attempt in $(seq 1 30); do
    if docker exec "$name" redis-cli --no-auth-warning -a "$PASSWORD" PING 2>/dev/null | grep -q PONG; then
      return 0
    fi
    sleep 1
  done
  echo "FalkorDB $name never answered PING" >&2
  return 1
}

query() {
  local name=$1 cypher=$2
  docker exec "$name" redis-cli --no-auth-warning -a "$PASSWORD" \
    GRAPH.QUERY "$GRAPH" "$cypher" 2>/dev/null
}

# A snapshot is graph names, counts, every property of every node and edge, and
# the index schema - not "the container came up".
snapshot() {
  local name=$1
  echo "## graphs"
  docker exec "$name" redis-cli --no-auth-warning -a "$PASSWORD" GRAPH.LIST 2>/dev/null | sort
  echo "## nodes"
  query "$name" "MATCH (n:Letter) RETURN n.normCode, n.subject, n.createdAt ORDER BY n.normCode"
  echo "## edges"
  query "$name" "MATCH (a:Letter)-[r:CITES]->(b:Letter) RETURN a.normCode, r.owner_document_id, r.weight, b.normCode ORDER BY a.normCode, b.normCode"
  echo "## counts"
  query "$name" "MATCH (n) RETURN count(n)"
  query "$name" "MATCH ()-[r]->() RETURN count(r)"
  echo "## indexes"
  query "$name" "CALL db.indexes() YIELD label, properties RETURN label, properties"
}

# Execution timings and cache counters differ between runs and say nothing
# about recovered state.
normalise() { grep -vE '^(Cached execution|Query internal execution time)'; }

# --------------------------------------------------------------------------- #

step "1. Fresh source instance on production's image and configuration"
docker volume create "$SOURCE_VOLUME" >/dev/null
start_falkor "$SOURCE_CONTAINER" "$SOURCE_VOLUME"
CONFIGURED_DIR=$(docker exec "$SOURCE_CONTAINER" redis-cli --no-auth-warning -a "$PASSWORD" CONFIG GET dir 2>/dev/null | tail -1)
echo "Configured persistence dir: $CONFIGURED_DIR (the backed-up volume is mounted at $MOUNT)"
if [[ "$CONFIGURED_DIR" != "$MOUNT" ]]; then
  fail "FalkorDB persists to $CONFIGURED_DIR, which is outside the backed-up volume at $MOUNT"
else
  pass "FalkorDB persists inside the backed-up volume"
fi

step "2. Seed a deterministic disposable graph"
query "$SOURCE_CONTAINER" "CREATE
  (a:Letter {normCode:'ra4-let-001', subject:'alpha', createdAt:1700000001}),
  (b:Letter {normCode:'ra4-let-002', subject:'beta',  createdAt:1700000002}),
  (c:Letter {normCode:'ra4-let-003', subject:'gamma', createdAt:1700000003}),
  (a)-[:CITES {owner_document_id:'ra4-doc-1', weight:1}]->(b),
  (b)-[:CITES {owner_document_id:'ra4-doc-2', weight:2}]->(c),
  (a)-[:CITES {owner_document_id:'ra4-doc-1', weight:3}]->(c)" >/dev/null
query "$SOURCE_CONTAINER" "CREATE INDEX FOR (n:Letter) ON (n.normCode)" >/dev/null
snapshot "$SOURCE_CONTAINER" | normalise >"$WORK_DIR/before.txt"
echo "Seeded snapshot:"
sed 's/^/  /' "$WORK_DIR/before.txt"

step "3. Force persistence the way the backup procedure does"
docker exec "$SOURCE_CONTAINER" redis-cli --no-auth-warning -a "$PASSWORD" BGSAVE 2>/dev/null
for attempt in $(seq 1 30); do
  if docker exec "$SOURCE_CONTAINER" redis-cli --no-auth-warning -a "$PASSWORD" INFO persistence 2>/dev/null \
    | tr -d '\r' | grep -q '^rdb_bgsave_in_progress:0'; then
    break
  fi
  sleep 1
done
docker exec "$SOURCE_CONTAINER" redis-cli --no-auth-warning -a "$PASSWORD" BGREWRITEAOF 2>/dev/null || true
sleep 2

step "4. Run the production backup procedure against the volume"
ARCHIVE="$WORK_DIR/falkordb-data-drill.tar.gz"
BACKUP_START=$(date +%s)
if bash "$ROOT_DIR/scripts/backup_volume.sh" "$SOURCE_VOLUME" "$ARCHIVE" --profile redis-persistence; then
  pass "backup command completed"
else
  fail "backup command reported failure"
fi
BACKUP_SECONDS=$(( $(date +%s) - BACKUP_START ))
if [[ -f "$ARCHIVE" ]]; then
  echo "Archive contents:"
  tar -tzvf "$ARCHIVE" | sed 's/^/  /'
  echo "Archive sha256: $(sha256sum "$ARCHIVE" | cut -d' ' -f1)"
  echo "Archive bytes : $(wc -c <"$ARCHIVE")"
fi

step "5. Destroy the container AND the volume"
docker rm -f "$SOURCE_CONTAINER" >/dev/null
docker volume rm "$SOURCE_VOLUME" >/dev/null
echo "source container and volume removed"

step "6. Restore into a fresh volume with the production restore script"
RESTORE_START=$(date +%s)
docker volume create "$TARGET_VOLUME" >/dev/null
bash "$ROOT_DIR/scripts/production_restore_volumes.sh" --apply "$TARGET_VOLUME" "$ARCHIVE"

step "7. Recreate the container on the restored volume"
start_falkor "$TARGET_CONTAINER" "$TARGET_VOLUME"
RESTORE_SECONDS=$(( $(date +%s) - RESTORE_START ))
snapshot "$TARGET_CONTAINER" | normalise >"$WORK_DIR/after.txt"
echo "Recovered snapshot:"
sed 's/^/  /' "$WORK_DIR/after.txt"

step "8. Compare semantic snapshots"
if diff -u "$WORK_DIR/before.txt" "$WORK_DIR/after.txt"; then
  pass "recovered graph is semantically identical (names, nodes, edges, properties, counts, indexes)"
else
  fail "recovered graph does not match the seeded graph"
fi

step "9. Negative controls - a broken backup must fail visibly"
docker rm -f "$TARGET_CONTAINER" >/dev/null 2>&1 || true

if bash "$ROOT_DIR/scripts/production_restore_volumes.sh" --apply "$TARGET_VOLUME" "$WORK_DIR/does-not-exist.tar.gz" >/dev/null 2>&1; then
  fail "missing archive was accepted"
else
  pass "missing archive refused"
fi

cp "$ARCHIVE" "$WORK_DIR/corrupt.tar.gz"
printf 'X' | dd of="$WORK_DIR/corrupt.tar.gz" bs=1 seek=40 count=1 conv=notrunc >/dev/null 2>&1
if bash "$ROOT_DIR/scripts/production_restore_volumes.sh" --apply "$TARGET_VOLUME" "$WORK_DIR/corrupt.tar.gz" >/dev/null 2>&1; then
  fail "corrupted archive was accepted"
else
  pass "corrupted archive refused"
fi

docker volume create "$EMPTY_VOLUME" >/dev/null
if bash "$ROOT_DIR/scripts/backup_volume.sh" "$EMPTY_VOLUME" "$WORK_DIR/empty.tar.gz" --profile redis-persistence >/dev/null 2>&1; then
  fail "backing up a volume with no persisted graph state reported success"
else
  pass "volume with no persisted graph state refused"
fi

step "10. Measured local recovery time"
echo "Backup  : ${BACKUP_SECONDS}s"
echo "Restore : ${RESTORE_SECONDS}s (restore + container start + readiness)"
echo "Dataset : 3 nodes, 3 edges, 1 index (see the seeded snapshot above)"
echo "These are local, disposable-instance figures. A production RTO needs the"
echo "production corpus and an owner/ops target; do not carry these numbers over."

echo
if [[ "$failures" -eq 0 ]]; then
  echo "FALKORDB RECOVERY DRILL: PASS"
  exit 0
fi
echo "FALKORDB RECOVERY DRILL: FAIL ($failures check(s) failed)" >&2
exit 1
