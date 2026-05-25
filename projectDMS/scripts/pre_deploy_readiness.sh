#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
ENV_FILE=${ENV_FILE:-"$ROOT_DIR/.env"}
BACKEND_ENV_FILE=${BACKEND_ENV_FILE:-"$ROOT_DIR/backend/.env"}
COMPOSE_FILES=${COMPOSE_FILES:-"-f docker-compose.prod.yml"}
MIN_DISK_GB=${MIN_DISK_GB:-20}
MIN_MEM_MB=${MIN_MEM_MB:-3500}
ALLOW_PUBLIC_DATA_PORTS=${ALLOW_PUBLIC_DATA_PORTS:-false}

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

cd "$ROOT_DIR"

[[ -f "$ENV_FILE" ]] && pass "Root .env exists" || fail "Root .env is missing"
[[ -f "$BACKEND_ENV_FILE" ]] && pass "backend/.env exists" || warn "backend/.env is missing; using root .env only"

load_env_file "$ENV_FILE"
load_env_file "$BACKEND_ENV_FILE"

command -v docker >/dev/null 2>&1 && pass "docker is installed" || fail "docker is not installed"
docker compose version >/dev/null 2>&1 && pass "docker compose is installed" || fail "docker compose plugin is not installed"

available_disk_gb=$(df -BG "$ROOT_DIR" | awk 'NR==2 {gsub("G","",$4); print $4}')
if [[ "${available_disk_gb:-0}" -ge "$MIN_DISK_GB" ]]; then
  pass "Available disk is ${available_disk_gb}GB"
else
  fail "Available disk is ${available_disk_gb:-unknown}GB; require at least ${MIN_DISK_GB}GB"
fi

available_mem_mb=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)
if [[ "$available_mem_mb" -ge "$MIN_MEM_MB" ]]; then
  pass "Available memory is ${available_mem_mb}MB"
else
  warn "Available memory is ${available_mem_mb}MB; recommended at least ${MIN_MEM_MB}MB"
fi

environment=$(get_env ENVIRONMENT)
if [[ "$environment" == "production" ]]; then
  pass "ENVIRONMENT=production"
else
  fail "ENVIRONMENT must be production"
fi

secret_key=$(get_env SECRET_KEY)
if [[ ${#secret_key} -ge 32 && "$secret_key" != "replace-with-a-secure-random-string" && "$secret_key" != "SECRET_KEY" ]]; then
  pass "SECRET_KEY length and placeholder check passed"
else
  fail "SECRET_KEY must be a real 32+ character secret"
fi

database_url=$(get_env DATABASE_URL)
mongodb_replicaset=$(get_env MONGODB_REPLICA_SET)
allow_standalone=$(get_env MONGODB_ALLOW_STANDALONE_PRODUCTION)
if [[ "$database_url" == *localhost* || "$database_url" == *127.0.0.1* ]]; then
  fail "DATABASE_URL must not point to localhost in production"
elif [[ "$database_url" == *"replicaSet="* || -n "$mongodb_replicaset" || "$allow_standalone" == "true" ]]; then
  pass "MongoDB replica-set configuration is present or standalone override is explicit"
else
  fail "MongoDB production deployment must use replicaSet or explicit MONGODB_ALLOW_STANDALONE_PRODUCTION=true"
fi

metrics_enabled=$(get_env METRICS_ENABLED)
metrics_token=$(get_env METRICS_TOKEN)
if [[ "$metrics_enabled" == "false" || "$metrics_enabled" == "False" ]]; then
  warn "METRICS_ENABLED=false; production monitoring will be limited"
elif [[ -n "$metrics_token" && "$metrics_token" != "replace-with-internal-scrape-token" ]]; then
  pass "METRICS_TOKEN is configured"
else
  fail "METRICS_TOKEN is required when metrics are enabled"
fi

runtime_redis=$(get_env RUNTIME_STATE_REDIS_URL)
app_redis=$(get_env APP_REDIS_URL)
if [[ -n "$runtime_redis" || -n "$app_redis" ]]; then
  pass "Runtime Redis URL is configured"
else
  fail "APP_REDIS_URL or RUNTIME_STATE_REDIS_URL is required"
fi

if [[ -f "$ROOT_DIR/config/secrets/qdrant_api_key" && -s "$ROOT_DIR/config/secrets/qdrant_api_key" ]]; then
  pass "Qdrant secret file exists"
else
  fail "config/secrets/qdrant_api_key is missing or empty"
fi

compose_output=$(mktemp)
if docker compose --env-file "$ENV_FILE" $COMPOSE_FILES config >"$compose_output"; then
  pass "docker compose config succeeded"
else
  fail "docker compose config failed"
fi

if grep -E 'published: "?((27017)|(6379)|(6380)|(6333)|(6334))"?' "$compose_output" >/dev/null; then
  if [[ "$ALLOW_PUBLIC_DATA_PORTS" == "true" ]]; then
    warn "Compose publishes data-service ports; allowed by ALLOW_PUBLIC_DATA_PORTS=true"
  else
    fail "Compose publishes data-service ports. Remove public DB/Redis/Qdrant/FalkorDB port mappings for production or set ALLOW_PUBLIC_DATA_PORTS=true for controlled staging."
  fi
else
  pass "No obvious public data-service port mappings in compose config"
fi
rm -f "$compose_output"

if [[ -x "$ROOT_DIR/scripts/mongo_backup.sh" || -f "$ROOT_DIR/scripts/mongo_backup.sh" ]]; then
  pass "Mongo backup script exists"
else
  fail "scripts/mongo_backup.sh is missing"
fi

if [[ -f "$ROOT_DIR/docs/Phase6_Observability_Incident_Response.md" && -f "$ROOT_DIR/docs/Phase7_Collaboration_Auditability.md" ]]; then
  pass "Phase 6/7 runbooks exist"
else
  fail "Phase 6/7 runbooks are missing"
fi

printf '\nPre-deploy readiness complete: %s failure(s), %s warning(s).\n' "$failures" "$warnings"
if [[ "$failures" -gt 0 ]]; then
  exit 1
fi
