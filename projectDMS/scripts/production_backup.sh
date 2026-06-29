#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
ENV_FILE=${ENV_FILE:-"$ROOT_DIR/.env"}
COMPOSE_FILES=${COMPOSE_FILES:-"-f docker-compose.prod.yml"}
BACKUP_ROOT=${BACKUP_ROOT:-/var/backups/contractdms}
STAMP=${STAMP:-$(date '+%Y%m%d-%H%M%S')}
RETENTION_DAYS=${RETENTION_DAYS:-14}

cd "$ROOT_DIR"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi
MONGO_DB_NAME=${MONGO_DB:-${MONGODB_DATABASE:-contraclaim}}

mkdir -p "$BACKUP_ROOT/mongo" "$BACKUP_ROOT/volumes" "$BACKUP_ROOT/manifests"

echo "Writing deployment manifest..."
{
  echo "stamp=$STAMP"
  echo "git_sha=$(git rev-parse HEAD 2>/dev/null || true)"
  docker compose --env-file "$ENV_FILE" $COMPOSE_FILES ps || true
} >"$BACKUP_ROOT/manifests/deployment-$STAMP.txt"

echo "Backing up MongoDB..."
MONGO_URI="${MONGO_URI:-${DATABASE_URL:-}}" \
MONGO_DB="$MONGO_DB_NAME" \
BACKUP_DIR="$BACKUP_ROOT/mongo" \
RETENTION_DAYS="$RETENTION_DAYS" \
STAMP="$STAMP" \
"$ROOT_DIR/scripts/mongo_backup.sh"

project_name=${COMPOSE_PROJECT_NAME:-$(basename "$ROOT_DIR" | tr '[:upper:]' '[:lower:]')}

backup_volume() {
  local volume=$1
  local label=$2
  local archive="$BACKUP_ROOT/volumes/${label}-${STAMP}.tar.gz"
  echo "Backing up Docker volume $volume to $archive"
  docker run --rm \
    -v "${volume}:/source:ro" \
    -v "$BACKUP_ROOT/volumes:/backup" \
    busybox sh -c "cd /source && tar -czf /backup/$(basename "$archive") ."
}

echo "Flushing Redis/FalkorDB persistence where available..."
docker compose --env-file "$ENV_FILE" $COMPOSE_FILES exec -T redis \
  sh -c 'redis-cli -a "$REDIS_PASSWORD" BGSAVE' || true
docker compose --env-file "$ENV_FILE" $COMPOSE_FILES exec -T falkordb \
  sh -c 'redis-cli -a "$FALKORDB_PASSWORD" BGSAVE' || true

backup_volume "${project_name}_backend_uploads" "backend-uploads"
backup_volume "${project_name}_qdrant_data" "qdrant-data"
backup_volume "${project_name}_qdrant_snapshots" "qdrant-snapshots"
backup_volume "${project_name}_falkordb_data" "falkordb-data"
backup_volume "${project_name}_redis_data" "redis-data"

echo "Writing backup checksums and completion manifest..."
checksum_file="$BACKUP_ROOT/manifests/checksums-$STAMP.sha256"
mongo_archive="$BACKUP_ROOT/mongo/${MONGO_DB_NAME}-${STAMP}.archive.gz"
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
  "checksum_file": "$checksum_file"
}
EOF
cp "$manifest_json" "$BACKUP_ROOT/manifests/latest.json"

find "$BACKUP_ROOT" -type f -mtime "+$RETENTION_DAYS" -delete

echo "Production backup complete: $BACKUP_ROOT ($STAMP)"
