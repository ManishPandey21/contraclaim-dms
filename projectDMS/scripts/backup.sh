#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
ENV_FILE=${ENV_FILE:-"$PROJECT_DIR/.env"}
BACKUP_ROOT=${1:-"$PROJECT_DIR/backups"}
STAMP=$(date '+%Y%m%d-%H%M%S')
BACKUP_DIR="$BACKUP_ROOT/$STAMP"
mkdir -p "$BACKUP_DIR"

PROJECT_NAME=$(basename "$PROJECT_DIR")
if [[ -f "$ENV_FILE" ]]; then
  while IFS='=' read -r key value; do
    if [[ $key == "COMPOSE_PROJECT_NAME" ]]; then
      PROJECT_NAME=${value}
    fi
  done < <(grep -E "^COMPOSE_PROJECT_NAME=" "$ENV_FILE" || true)
fi

pushd "$PROJECT_DIR" >/dev/null
COMPOSE_CMD=(docker compose --env-file "$ENV_FILE")
SERVICES=(qdrant falkordb redis)
for svc in "${SERVICES[@]}"; do
  "${COMPOSE_CMD[@]}" pause "$svc" || true
done

backup_volume() {
  local volume=$1
  local archive=$2
  shift 2
  bash "$PROJECT_DIR/scripts/backup_volume.sh" "$volume" "$BACKUP_DIR/${archive}.tar.gz" "$@"
}

backup_volume "${PROJECT_NAME}_qdrant_data" qdrant_data "*/collections/*" "*raft_state*"
backup_volume "${PROJECT_NAME}_qdrant_snapshots" qdrant_snapshots
backup_volume "${PROJECT_NAME}_falkordb_data" falkordb_data "*dump.rdb" "*appendonlydir*"
backup_volume "${PROJECT_NAME}_redis_data" redis_data "*dump.rdb" "*appendonlydir*"

for svc in "${SERVICES[@]}"; do
  "${COMPOSE_CMD[@]}" unpause "$svc" || true
done

popd >/dev/null

echo "Backups stored in $BACKUP_DIR"
