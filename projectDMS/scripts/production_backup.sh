#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
ENV_FILE=${ENV_FILE:-"$ROOT_DIR/.env"}
COMPOSE_FILES=${COMPOSE_FILES:-"-f docker-compose.prod.yml"}
BACKUP_ROOT=${BACKUP_ROOT:-/var/backups/contractdms}
STAMP=${STAMP:-$(date '+%Y%m%d-%H%M%S')}
RETENTION_DAYS=${RETENTION_DAYS:-14}

cd "$ROOT_DIR"

# The environment file is data, never a program: `source` executed it, and one
# unquoted `&` in the staging DATABASE_URL backgrounded the assignment so this
# script died on "MONGO_URI or DATABASE_URL is required" with no backup taken
# (F-A8M-2). See scripts/lib/env_file.sh.
# shellcheck source=scripts/lib/env_file.sh
. "$ROOT_DIR/scripts/lib/env_file.sh"
env_file_load "$ENV_FILE"
MONGO_DB_NAME=${MONGO_DB:-${MONGODB_DATABASE:-contraclaim}}

mkdir -p "$BACKUP_ROOT/mongo" "$BACKUP_ROOT/volumes" "$BACKUP_ROOT/manifests"

echo "Writing deployment manifest..."
{
  echo "stamp=$STAMP"
  echo "git_sha=$(git rev-parse HEAD 2>/dev/null || true)"
  docker compose --env-file "$ENV_FILE" $COMPOSE_FILES ps || true
} >"$BACKUP_ROOT/manifests/deployment-$STAMP.txt"

echo "Backing up MongoDB..."
mongo_uri=${MONGO_URI:-${DATABASE_URL:-}}
mongo_archive="$BACKUP_ROOT/mongo/${MONGO_DB_NAME}-${STAMP}.archive.gz"
if [[ "$mongo_uri" == *"mongo1:"* ]]; then
  # Docker-internal replica-set hostnames do not resolve on the host. Run the
  # dump from a replica-set container and stream the archive to the host.
  rm -f "$mongo_archive"
  docker compose --env-file "$ENV_FILE" $COMPOSE_FILES \
    -f docker-compose.mongo-replicaset.yml exec -T mongo1 \
    mongodump --uri="$mongo_uri" --db="$MONGO_DB_NAME" --archive --gzip \
    >"$mongo_archive"
  find "$BACKUP_ROOT/mongo" -type f -name "${MONGO_DB_NAME}-*.archive.gz" \
    -mtime "+$RETENTION_DAYS" -delete
  echo "MongoDB backup written to $mongo_archive"
else
  MONGO_URI="$mongo_uri" \
  MONGO_DB="$MONGO_DB_NAME" \
  BACKUP_DIR="$BACKUP_ROOT/mongo" \
  RETENTION_DAYS="$RETENTION_DAYS" \
  STAMP="$STAMP" \
  bash "$ROOT_DIR/scripts/mongo_backup.sh"
fi

project_name=${COMPOSE_PROJECT_NAME:-$(basename "$ROOT_DIR" | tr '[:upper:]' '[:lower:]')}

backup_volume() {
  local volume=$1
  local label=$2
  shift 2
  local archive="$BACKUP_ROOT/volumes/${label}-${STAMP}.tar.gz"
  bash "$ROOT_DIR/scripts/backup_volume.sh" "$volume" "$archive" "$@"
}

echo "Flushing Redis/FalkorDB persistence, and proving it landed..."
# `|| true` around a compose exec made a flush that reached nothing look exactly
# like one that worked, and R-A9G's out-of-band FalkorDB engine turned that into
# a nightly no-op. The engine is now resolved by compose service *or* network
# alias, and LASTSAVE has to advance before the flush counts (R-A9H D1).
# shellcheck source=scripts/lib/redis_flush.sh
. "$ROOT_DIR/scripts/lib/redis_flush.sh"
REDIS_FLUSH_PROJECT=$project_name
flush_failures=0
flush_status_redis=ok
flush_status_falkordb=ok
redis_flush redis REDIS_PASSWORD redis ||
  { flush_status_redis=FAILED; flush_failures=$((flush_failures + 1)); }
redis_flush falkordb FALKORDB_PASSWORD falkordb ||
  { flush_status_falkordb=FAILED; flush_failures=$((flush_failures + 1)); }

# Uploads and Qdrant's snapshot scratch are legitimately empty on a fresh install.
# With no contract, backup_volume.sh refused that archive and set -e aborted the
# whole backup before any later volume (F-A8W-B1). `application-volume` accepts an
# empty volume and still refuses an unreadable or root-escaping archive; the
# stateful volumes below keep contracts that refuse emptiness.
backup_volume "${project_name}_backend_uploads" "backend-uploads" --profile application-volume
backup_volume "${project_name}_qdrant_data" "qdrant-data" --any-of "*/collections/*" --any-of "*raft_state*"
backup_volume "${project_name}_qdrant_snapshots" "qdrant-snapshots" --profile application-volume
backup_volume "${project_name}_falkordb_data" "falkordb-data" --profile redis-persistence
backup_volume "${project_name}_redis_data" "redis-data" --profile redis-persistence

echo "Writing backup checksums and completion manifest..."
checksum_file="$BACKUP_ROOT/manifests/checksums-$STAMP.sha256"
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$mongo_archive" "$BACKUP_ROOT"/volumes/*-"$STAMP".tar.gz >"$checksum_file"
else
  shasum -a 256 "$mongo_archive" "$BACKUP_ROOT"/volumes/*-"$STAMP".tar.gz >"$checksum_file"
fi

manifest_json="$BACKUP_ROOT/manifests/backup-$STAMP.json"
cat >"$manifest_json" <<EOF
{
  "stamp": "$STAMP",
  "completed_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "git_sha": "$(git rev-parse HEAD 2>/dev/null || true)",
  "mongo_archive": "$mongo_archive",
  "volume_archives": [
    "$BACKUP_ROOT/volumes/backend-uploads-$STAMP.tar.gz",
    "$BACKUP_ROOT/volumes/qdrant-data-$STAMP.tar.gz",
    "$BACKUP_ROOT/volumes/qdrant-snapshots-$STAMP.tar.gz",
    "$BACKUP_ROOT/volumes/falkordb-data-$STAMP.tar.gz",
    "$BACKUP_ROOT/volumes/redis-data-$STAMP.tar.gz"
  ],
  "checksum_file": "$checksum_file",
  "persistence_flush": {
    "redis": "$flush_status_redis",
    "falkordb": "$flush_status_falkordb"
  }
}
EOF
cp "$manifest_json" "$BACKUP_ROOT/manifests/latest.json"

# Do not invalidate a freshly completed backup when a legacy artifact is owned
# by another account. The warning now names the artifact, so the cleanup is an
# action rather than a hunt (R-A9H D2).
# shellcheck source=scripts/lib/retention.sh
. "$ROOT_DIR/scripts/lib/retention.sh"
retention_prune "$BACKUP_ROOT" "$RETENTION_DAYS" || true

echo "Production backup complete: $BACKUP_ROOT ($STAMP)"

# 3, not 1 and not 0. Every artefact above was written and is worth replicating,
# but the persistence flush was not proven, so an archive may hold only what
# happened to be on disk. `backup_offsite_s3.sh` carries 3 through its own sync
# for exactly that reason. Reporting 0 here is the defect R-A9G shipped with.
if [ "$flush_failures" -ne 0 ]; then
  echo "WARN: $flush_failures persistence flush(es) did not reach a live engine; the archives above hold only what was already on disk" >&2
  exit 3
fi
