#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
ENV_FILE=${ENV_FILE:-"$PROJECT_DIR/.env"}

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Environment file $ENV_FILE not found. Create it from .env.example." >&2
  exit 1
fi

pushd "$PROJECT_DIR" >/dev/null

# The checked-out commit is the release identity baked into every backend
# image (backend/Dockerfile, docs/OPERATIONS.md 7a).
RELEASE_SHA="$(git -C "$PROJECT_DIR" rev-parse HEAD)"
export RELEASE_SHA

COMPOSE_CMD=(docker compose --env-file "$ENV_FILE")
"${COMPOSE_CMD[@]}" pull
"${COMPOSE_CMD[@]}" up -d --build

popd >/dev/null

echo "Deployment complete. Use 'docker compose logs -f' for streaming logs."
