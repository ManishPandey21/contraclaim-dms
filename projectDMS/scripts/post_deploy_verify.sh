#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
ENV_FILE=${ENV_FILE:-"$ROOT_DIR/.env"}
BACKEND_ENV_FILE=${BACKEND_ENV_FILE:-"$ROOT_DIR/backend/.env"}
COMPOSE_FILES=${COMPOSE_FILES:-"-f docker-compose.prod.yml"}
BACKEND_BASE_URL=${BACKEND_BASE_URL:-}
PYTHON_BIN=${PYTHON_BIN:-}
PUBLIC_BASE_URL=${PUBLIC_BASE_URL:-}
SMOKE_ATTEMPTS=${SMOKE_ATTEMPTS:-12}
SMOKE_SLEEP_SECONDS=${SMOKE_SLEEP_SECONDS:-5}

failures=0
warnings=0

pass() { printf 'PASS: %s\n' "$1"; }
warn() { printf 'WARN: %s\n' "$1"; warnings=$((warnings + 1)); }
fail() { printf 'FAIL: %s\n' "$1"; failures=$((failures + 1)); }

load_env_file() {
  local file=$1
  if [[ -f "$file" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$file"
    set +a
  fi
}

resolve_python_bin() {
  if [[ -n "$PYTHON_BIN" ]]; then
    command -v "$PYTHON_BIN" >/dev/null 2>&1 || return 1
    printf '%s' "$PYTHON_BIN"
    return
  fi

  local candidate
  for candidate in python python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      printf '%s' "$candidate"
      return
    fi
  done
  return 1
}

resolve_backend_base_url() {
  if [[ -n "$BACKEND_BASE_URL" ]]; then
    printf '%s' "$BACKEND_BASE_URL"
    return
  fi

  local backend_container backend_ip
  backend_container=$(docker compose --env-file "$ENV_FILE" $COMPOSE_FILES ps -q backend 2>/dev/null || true)
  if [[ -n "$backend_container" ]]; then
    backend_ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{println .IPAddress}}{{end}}' "$backend_container" 2>/dev/null | sed -n '/^[0-9a-fA-F:.]\+$/ {p; q}')
    if [[ -n "$backend_ip" ]]; then
      printf 'http://%s:8000' "$backend_ip"
      return
    fi
  fi

  printf '%s' 'http://localhost:8000'
}

get_env() {
  local key=$1
  local value=${!key-}
  if [[ -n "$value" ]]; then
    printf '%s' "$value"
    return
  fi
  if [[ -f "$BACKEND_ENV_FILE" ]]; then
    grep -E "^${key}=" "$BACKEND_ENV_FILE" | tail -n 1 | cut -d= -f2- | sed 's/^"//; s/"$//' || true
  fi
  return 0
}

http_check() {
  local url=$1
  local label=$2
  local header_args=()
  if [[ $# -gt 2 && -n "${3:-}" ]]; then
    header_args=(-H "$3")
  fi
  if curl -fsS --max-time 8 "${header_args[@]}" "$url" >/tmp/post_deploy_check.out; then
    pass "$label"
  else
    fail "$label"
  fi
}

cd "$ROOT_DIR"
# Compose injects the root .env into the running services. Load the legacy
# backend file first only as a fallback; otherwise a stale backend/.env can
# make verification authenticate with a token that is not deployed.
load_env_file "$BACKEND_ENV_FILE"
load_env_file "$ENV_FILE"

python_bin=$(resolve_python_bin || true)
BACKEND_BASE_URL=$(resolve_backend_base_url)

docker compose --env-file "$ENV_FILE" $COMPOSE_FILES ps || fail "docker compose ps failed"

if [[ -n "$python_bin" ]]; then
  SMOKE_BASE_URL="$BACKEND_BASE_URL" \
  SMOKE_ATTEMPTS="$SMOKE_ATTEMPTS" \
  SMOKE_SLEEP_SECONDS="$SMOKE_SLEEP_SECONDS" \
  "$python_bin" "$ROOT_DIR/scripts/smoke_health.py" && pass "Backend live/ready smoke checks passed" || fail "Backend live/ready smoke checks failed"
else
  fail "No Python interpreter was available for backend health checks"
fi

metrics_token=$(get_env METRICS_TOKEN)
metrics_enabled=$(get_env METRICS_ENABLED)
if [[ -n "$metrics_token" ]]; then
  http_check "$BACKEND_BASE_URL/health/observability" "Observability health endpoint responded" "X-Metrics-Token: $metrics_token"
  http_check "$BACKEND_BASE_URL/health/operations" "Operations health endpoint responded" "X-Metrics-Token: $metrics_token"
else
  http_check "$BACKEND_BASE_URL/health/observability" "Observability health endpoint responded"
  http_check "$BACKEND_BASE_URL/health/operations" "Operations health endpoint responded"
fi

backup_root=$(get_env BACKUP_ROOT)
backup_max_age=$(get_env BACKUP_MAX_AGE_HOURS)
require_fresh_backup=${REQUIRE_FRESH_BACKUP:-false}
if [[ -n "$python_bin" ]] && "$python_bin" "$ROOT_DIR/scripts/backup_status.py" --root "${backup_root:-/var/backups/contractdms}" --max-age-hours "${backup_max_age:-26}"; then
  pass "Backup freshness check passed"
else
  if [[ "$require_fresh_backup" == "true" || "$require_fresh_backup" == "True" ]]; then
    fail "Backup freshness check failed"
  else
    warn "Backup freshness check failed; set REQUIRE_FRESH_BACKUP=true to make this a hard gate"
  fi
fi

if [[ "$metrics_enabled" == "false" || "$metrics_enabled" == "False" ]]; then
  warn "Metrics disabled"
else
  if [[ -n "$metrics_token" ]]; then
    http_check "$BACKEND_BASE_URL/metrics" "Metrics endpoint responded" "X-Metrics-Token: $metrics_token"
  else
    http_check "$BACKEND_BASE_URL/metrics" "Metrics endpoint responded"
  fi
fi

if [[ -n "$PUBLIC_BASE_URL" ]]; then
  http_check "${PUBLIC_BASE_URL%/}/health" "Public gateway health responded"
fi

database_url=$(get_env DATABASE_URL)
backend_container=$(docker compose --env-file "$ENV_FILE" $COMPOSE_FILES ps -q backend 2>/dev/null || true)
if [[ -n "$backend_container" ]]; then
  if docker compose --env-file "$ENV_FILE" $COMPOSE_FILES exec -T --interactive=false backend \
    python -c 'import os; from pymongo import MongoClient; client = MongoClient(os.environ["DATABASE_URL"], serverSelectionTimeoutMS=8000); assert client.admin.command("ping").get("ok") == 1' \
    >/tmp/mongo_ping.out 2>&1; then
    pass "MongoDB ping succeeded through backend DATABASE_URL"
  else
    fail "MongoDB ping failed through backend DATABASE_URL"
  fi

  replica_set=$(get_env MONGODB_REPLICA_SET)
  if [[ -n "$replica_set" ]]; then
    if docker compose --env-file "$ENV_FILE" $COMPOSE_FILES exec -T --interactive=false backend \
      python -c 'import os; from pymongo import MongoClient; client = MongoClient(os.environ["DATABASE_URL"], serverSelectionTimeoutMS=8000); assert client.admin.command("replSetGetStatus").get("ok") == 1' \
      >/tmp/mongo_rs.out 2>&1; then
      pass "MongoDB replica-set status succeeded through backend"
    else
      fail "MongoDB replica-set status failed through backend"
    fi
  fi
elif command -v mongosh >/dev/null 2>&1 && [[ -n "$database_url" ]]; then
  if mongosh "$database_url" --quiet --eval "db.adminCommand('ping').ok" >/tmp/mongo_ping.out 2>&1; then
    pass "MongoDB ping succeeded through DATABASE_URL"
  else
    fail "MongoDB ping failed through DATABASE_URL"
  fi

  replica_set=$(get_env MONGODB_REPLICA_SET)
  if [[ -n "$replica_set" ]]; then
    if mongosh "$database_url" --quiet --eval "rs.status().ok" >/tmp/mongo_rs.out 2>&1; then
      pass "MongoDB replica-set status succeeded"
    else
      fail "MongoDB replica-set status failed"
    fi
  fi
else
  warn "Backend container and host mongosh probe unavailable; relying on backend /health/ready for MongoDB verification"
fi

if docker compose --env-file "$ENV_FILE" $COMPOSE_FILES exec -T --interactive=false redis sh -lc 'if [ -n "${REDIS_PASSWORD:-}" ]; then redis-cli -a "$REDIS_PASSWORD" ping; else redis-cli ping; fi' >/tmp/redis_ping.out 2>&1; then
  pass "Redis ping succeeded"
else
  fail "Redis ping failed"
fi

if docker compose --env-file "$ENV_FILE" $COMPOSE_FILES logs --since=10m backend 2>/dev/null | grep -Ei "traceback|critical|unhandled|exception" >/tmp/backend_recent_errors.out; then
  warn "Recent backend logs contain errors; inspect /tmp/backend_recent_errors.out"
else
  pass "No obvious recent backend exception signatures"
fi

printf '\nPost-deploy verification complete: %s failure(s), %s warning(s).\n' "$failures" "$warnings"
if [[ "$failures" -gt 0 ]]; then
  exit 1
fi
