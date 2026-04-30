#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <backup-directory>" >&2
  exit 1
fi

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
ENV_FILE=${ENV_FILE:-"$PROJECT_DIR/.env"}
BACKUP_DIR=$(cd "$1" && pwd)

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

restore_volume() {
  local archive=$1
  local volume=$2
  if [[ ! -f "$BACKUP_DIR/${archive}.tar.gz" ]]; then
    echo "Archive $BACKUP_DIR/${archive}.tar.gz not found" >&2
    exit 1
  fi
  docker run --rm -v "${volume}:/restore" -v "$BACKUP_DIR:/backup" busybox \
    sh -c "rm -rf /restore/* && cd /restore && tar xzf /backup/${archive}.tar.gz"
}

restore_volume qdrant_data "${PROJECT_NAME}_qdrant_data"
restore_volume qdrant_snapshots "${PROJECT_NAME}_qdrant_snapshots"
restore_volume falkordb_data "${PROJECT_NAME}_falkordb_data"
restore_volume redis_data "${PROJECT_NAME}_redis_data"

for svc in "${SERVICES[@]}"; do
  "${COMPOSE_CMD[@]}" unpause "$svc" || true
done

popd >/dev/null

echo "Restore completed from $BACKUP_DIR"
