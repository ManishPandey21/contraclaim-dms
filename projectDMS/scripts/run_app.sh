#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd "$SCRIPT_DIR/.." && pwd)

ENV_FILE=${ENV_FILE:-"$PROJECT_DIR/.env"}

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Environment file '$ENV_FILE' not found. Copy .env.example to .env and populate secrets." >&2
  exit 1
fi

COMPOSE_FILES=("$PROJECT_DIR/docker-compose.yml")
if [[ -f "$PROJECT_DIR/docker-compose.prod.yml" ]]; then
  COMPOSE_FILES+=("$PROJECT_DIR/docker-compose.prod.yml")
fi

COMPOSE_CMD=(docker compose --env-file "$ENV_FILE")
for file in "${COMPOSE_FILES[@]}"; do
  COMPOSE_CMD+=(-f "$file")
done

EXTRA_ARGS=("$@")
if [[ ${#EXTRA_ARGS[@]} -eq 0 ]]; then
  EXTRA_ARGS=("--build")
fi

echo "[run_app] Launching ContractDMS stack..."
"${COMPOSE_CMD[@]}" up -d "${EXTRA_ARGS[@]}"

printf '\n[run_app] Active services:\n'
"${COMPOSE_CMD[@]}" ps

printf '\n[run_app] Tail logs with:\n  %s logs -f\n' "${COMPOSE_CMD[*]}"