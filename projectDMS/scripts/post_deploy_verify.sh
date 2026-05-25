#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
ENV_FILE=${ENV_FILE:-"$ROOT_DIR/.env"}
BACKEND_ENV_FILE=${BACKEND_ENV_FILE:-"$ROOT_DIR/backend/.env"}
COMPOSE_FILES=${COMPOSE_FILES:-"-f docker-compose.prod.yml"}
BACKEND_BASE_URL=${BACKEND_BASE_URL:-http://localhost:8000}
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
load_env_file "$ENV_FILE"
load_env_file "$BACKEND_ENV_FILE"

docker compose --env-file "$ENV_FILE" $COMPOSE_FILES ps || fail "docker compose ps failed"

SMOKE_BASE_URL="$BACKEND_BASE_URL" \
SMOKE_ATTEMPTS="$SMOKE_ATTEMPTS" \
SMOKE_SLEEP_SECONDS="$SMOKE_SLEEP_SECONDS" \
python "$ROOT_DIR/scripts/smoke_health.py" && pass "Backend live/ready smoke checks passed" || fail "Backend live/ready smoke checks failed"

http_check "$BACKEND_BASE_URL/health/observability" "Observability health endpoint responded"

metrics_token=$(get_env METRICS_TOKEN)
metrics_enabled=$(get_env METRICS_ENABLED)
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
if command -v mongosh >/dev/null 2>&1 && [[ -n "$database_url" ]]; then
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
  warn "mongosh or DATABASE_URL unavailable; relying on backend /health/ready for MongoDB verification"
fi

redis_password=$(get_env REDIS_PASSWORD)
redis_cmd=(redis-cli -h localhost)
if [[ -n "$redis_password" ]]; then
  redis_cmd+=(-a "$redis_password")
fi
redis_cmd+=(ping)
if docker compose --env-file "$ENV_FILE" $COMPOSE_FILES exec -T redis "${redis_cmd[@]}" >/tmp/redis_ping.out 2>&1; then
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
