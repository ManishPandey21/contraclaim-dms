#!/usr/bin/env bash
# Destructive, disposable drill for the FalkorDB RESCUE path.
#
# `scripts/falkordb_recovery_drill.sh` proves the *volume* backup restores. That
# is not the path production is on: production FalkorDB persists into the image
# WORKDIR `/FalkorDB`, inside the container's writable layer, so the volume
# archive is 89 bytes of empty directory and the rescue archive taken by hand at
# the start of every maintenance window is the actual backup.
#
# That hand-taken archive is what R-A8O got wrong: 14.7 MB, verified,
# checksummed, and rooted at `FalkorDB/` - so restoring it into a volume mounted
# at `/data` would have produced `/data/FalkorDB/dump.rdb`, where the engine
# does not look (R-A8P F4). Nothing in the repository exercised that path, so
# nothing caught it.
#
#   container persisting to its writable layer   (the production shape)
#     -> scripts/falkordb_rescue_archive.sh
#     -> archive-root inspection
#     -> scripts/production_restore_volumes.sh into a CLEAN disposable volume
#     -> fresh FalkorDB started on it with --dir /data
#     -> graph inventory parity
#     -> negative controls: the R-A8O shape and the 89-byte archive
#
# It never touches a live container, a live volume or a business graph: every
# resource carries the run-owned "ra8qrescue_" prefix and is removed on exit.
# tar exiting 0 is not a pass; only a matching graph inventory is.
#
# Usage: scripts/falkordb_rescue_restore_drill.sh [--keep]
set -euo pipefail

ROOT_DIR=${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
COMPOSE_FILE="$ROOT_DIR/docker-compose.prod.yml"
# shellcheck source=scripts/lib/docker_paths.sh
. "$ROOT_DIR/scripts/lib/docker_paths.sh"

KEEP=false
[[ "${1:-}" == "--keep" ]] && KEEP=true

RUN_ID="ra8qrescue_$(date '+%H%M%S')_$$"
SOURCE_CONTAINER="${RUN_ID}_source"
TARGET_CONTAINER="${RUN_ID}_target"
TARGET_VOLUME="${RUN_ID}_target_vol"
PASSWORD="ra8q-rescue-drill-not-a-production-secret"
# Bind-mounted into a container, so it has to be a path the Docker daemon can
# see. Override DRILL_WORK_DIR when the shell's temp directory is not one.
WORK_DIR=$(mktemp -d "${DRILL_WORK_DIR:-${TMPDIR:-/tmp}}/ra8qrescue.XXXXXX")

failures=0
step() { echo; echo "=== $* ==="; }
pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*" >&2; failures=$((failures + 1)); }

cleanup() {
  if [[ "$KEEP" == "true" ]]; then
    echo "--keep: leaving $SOURCE_CONTAINER $TARGET_CONTAINER $TARGET_VOLUME $WORK_DIR"
    return
  fi
  docker rm -f "$SOURCE_CONTAINER" "$TARGET_CONTAINER" >/dev/null 2>&1 || true
  docker volume rm "$TARGET_VOLUME" >/dev/null 2>&1 || true
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

# The image is read from the deployed compose file rather than pinned here, so
# the drill follows the engine production actually runs.
IMAGE=$(awk '/^  falkordb:$/{inside=1; next} inside && /^  [a-z]/{exit} inside && /image:/{print $2; exit}' "$COMPOSE_FILE")
if [[ -z "$IMAGE" ]]; then
  echo "Could not read the falkordb image from $COMPOSE_FILE" >&2
  exit 2
fi
echo "FalkorDB image under test: $IMAGE"

cli() { docker_exec "$1" redis-cli --no-auth-warning -a "$PASSWORD" "${@:2}"; }

# GRAPH.QUERY echoes its own execution time, which differs on every call and is
# not part of the graph. The drill compares semantics, not timings.
# `|| true` because grep exits 1 when it filters everything, and under
# `set -euo pipefail` that would abort the drill instead of reporting an empty
# graph - which is a result the comparison should be allowed to make.
normalise() { tr -d '\r' | { grep -viE 'execution time|cached execution' || true; }; }

wait_ready() {
  for _ in $(seq 1 60); do
    if cli "$1" PING 2>/dev/null | tr -d '\r' | grep -q PONG; then return 0; fi
    sleep 1
  done
  return 1
}

step "1. The production shape: persistence in the container writable layer"
# Deliberately NO volume and NO --dir, so `dir` defaults to the image WORKDIR.
docker_run -d --name "$SOURCE_CONTAINER" \
  -e FALKORDB_PASSWORD="$PASSWORD" \
  -e REDIS_ARGS="--save 900 1 --appendonly yes --requirepass $PASSWORD" \
  "$IMAGE" >/dev/null
wait_ready "$SOURCE_CONTAINER" || { fail "source container never answered PING"; exit 1; }
PERSISTENCE_DIR=$(cli "$SOURCE_CONTAINER" CONFIG GET dir | tr -d '\r' | sed -n '2p')
echo "persistence dir: $PERSISTENCE_DIR"
if [[ "$PERSISTENCE_DIR" != "/data" ]]; then
  pass "reproduces production: persistence is outside any mounted volume ($PERSISTENCE_DIR)"
else
  echo "NOTE: this image defaults to /data; the rescue path is being drilled anyway"
fi

step "2. Seed a graph"
cli "$SOURCE_CONTAINER" GRAPH.QUERY contraclaim \
  "CREATE (a:Letter {normCode:'L-001'})-[:REFERENCES]->(b:Letter {normCode:'L-002'}), (c:Clause {ref:'14.1'})" >/dev/null
BEFORE=$(cli "$SOURCE_CONTAINER" GRAPH.QUERY contraclaim \
  "MATCH (n) RETURN labels(n)[0] AS l, count(n) ORDER BY l" | normalise)
echo "$BEFORE" | sed 's/^/  /'

step "3. Take the rescue archive the maintenance runbook takes"
ARCHIVE="$WORK_DIR/falkordb-persistence-drill.tar.gz"
if bash "$ROOT_DIR/scripts/falkordb_rescue_archive.sh" "$SOURCE_CONTAINER" "$ARCHIVE"; then
  pass "rescue helper exited 0, having validated its own output"
else
  fail "rescue helper failed"
fi

step "4. Archive root shape"
tar -tzf "$ARCHIVE" | sed 's/^/  /'
echo "  bytes: $(wc -c <"$ARCHIVE")"
if tar -tzf "$ARCHIVE" | sed 's#^\./##' | grep -qx 'dump.rdb'; then
  pass "dump.rdb is at the archive root"
else
  fail "dump.rdb is NOT at the archive root - this archive restores to the wrong place"
fi
if tar -tzf "$ARCHIVE" | grep -q 'FalkorDB/'; then
  fail "archive carries a FalkorDB/ prefix - the R-A8O defect"
else
  pass "no FalkorDB/ prefix"
fi
if tar -tzf "$ARCHIVE" | grep -q 'falkordb.so'; then
  fail "archive carries image binaries, not just persistence"
else
  pass "no image binaries in the archive"
fi

step "5. Restore into a clean disposable volume with the production restore script"
docker volume create "$TARGET_VOLUME" >/dev/null
bash "$ROOT_DIR/scripts/production_restore_volumes.sh" --apply "$TARGET_VOLUME" "$ARCHIVE"
echo "restored layout:"
docker_run --rm -v "${TARGET_VOLUME}:/data" busybox sh -c 'ls -la /data' | sed 's/^/  /'

step "6. Start a fresh FalkorDB on the restored volume, --dir /data"
docker_run -d --name "$TARGET_CONTAINER" \
  -e FALKORDB_PASSWORD="$PASSWORD" \
  -e REDIS_ARGS="--dir /data --save 900 1 --appendonly yes --requirepass $PASSWORD" \
  -v "${TARGET_VOLUME}:/data" "$IMAGE" >/dev/null
wait_ready "$TARGET_CONTAINER" || { fail "restored container never answered PING"; exit 1; }

step "7. Graph inventory parity"
cli "$TARGET_CONTAINER" GRAPH.LIST | tr -d '\r' | sed 's/^/  GRAPH.LIST: /'
AFTER=$(cli "$TARGET_CONTAINER" GRAPH.QUERY contraclaim \
  "MATCH (n) RETURN labels(n)[0] AS l, count(n) ORDER BY l" | normalise)
echo "$AFTER" | sed 's/^/  /'
if [[ "$BEFORE" == "$AFTER" ]]; then
  pass "recovered graph inventory is identical"
else
  fail "recovered graph inventory differs"
  diff <(echo "$BEFORE") <(echo "$AFTER") || true
fi
cli "$TARGET_CONTAINER" GRAPH.QUERY contraclaim "MATCH ()-[r]->() RETURN count(r)" \
  | normalise | sed 's/^/  relationships: /'

step "8. Negative control - an archive shaped the way R-A8O's was"
mkdir -p "$WORK_DIR/nested/FalkorDB"
docker_cp "${SOURCE_CONTAINER}:${PERSISTENCE_DIR%/}/dump.rdb" \
  "$(native_path "$WORK_DIR")/nested/FalkorDB/dump.rdb" >/dev/null 2>&1 || true
tar -czf "$WORK_DIR/ra8o-shaped.tar.gz" -C "$WORK_DIR/nested" .
tar -tzf "$WORK_DIR/ra8o-shaped.tar.gz" | sed 's/^/  /'
if bash "$ROOT_DIR/scripts/backup_volume.sh" --verify "$WORK_DIR/ra8o-shaped.tar.gz" --profile redis-persistence; then
  fail "an archive rooted at FalkorDB/ was accepted"
else
  pass "an archive rooted at FalkorDB/ is refused"
fi

step "9. Negative control - the 89-byte archive of an empty /data"
mkdir -p "$WORK_DIR/empty"
tar -czf "$WORK_DIR/empty.tar.gz" -C "$WORK_DIR/empty" .
echo "  bytes: $(wc -c <"$WORK_DIR/empty.tar.gz")"
if bash "$ROOT_DIR/scripts/backup_volume.sh" --verify "$WORK_DIR/empty.tar.gz" --profile redis-persistence; then
  fail "the empty archive was accepted"
else
  pass "the empty archive is refused"
fi

echo
if [[ "$failures" -eq 0 ]]; then
  echo "FALKORDB RESCUE RESTORE DRILL: PASS"
  exit 0
fi
echo "FALKORDB RESCUE RESTORE DRILL: FAIL ($failures check(s) failed)" >&2
exit 1
