#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
ENV_FILE=${ENV_FILE:-"$ROOT_DIR/.env"}
BACKEND_ENV_FILE=${BACKEND_ENV_FILE:-"$ROOT_DIR/backend/.env"}
COMPOSE_FILES=${COMPOSE_FILES:-"-f docker-compose.prod.yml"}
MIN_DISK_GB=${MIN_DISK_GB:-20}
MIN_MEM_MB=${MIN_MEM_MB:-3500}
ALLOW_PUBLIC_DATA_PORTS=${ALLOW_PUBLIC_DATA_PORTS:-false}
REQUIRE_FRESH_BACKUP=${REQUIRE_FRESH_BACKUP:-false}
RUN_MIGRATION_DRY_RUN=${RUN_MIGRATION_DRY_RUN:-false}
REQUIRE_MIGRATION_DRY_RUN=${REQUIRE_MIGRATION_DRY_RUN:-false}
PYTHON_BIN=${PYTHON_BIN:-}

if [[ -z "$PYTHON_BIN" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN=$(command -v python3)
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN=$(command -v python)
  fi
fi

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

# Compose injects the root .env into the running services. Load the legacy
# backend file first only as a fallback; otherwise a stale backend/.env can
# make readiness checks authenticate with a token that is not deployed.
load_env_file "$BACKEND_ENV_FILE"
load_env_file "$ENV_FILE"

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

backup_root=$(get_env BACKUP_ROOT)
backup_max_age=$(get_env BACKUP_MAX_AGE_HOURS)
backup_bucket=$(get_env BACKUP_S3_BUCKET)
if [[ -n "$backup_bucket" ]]; then
  pass "BACKUP_S3_BUCKET is configured"
else
  fail "BACKUP_S3_BUCKET is required for offsite production backups"
fi
if [[ -n "$PYTHON_BIN" ]] && "$PYTHON_BIN" "$ROOT_DIR/scripts/backup_status.py" --root "${backup_root:-/var/backups/contractdms}" --max-age-hours "${backup_max_age:-26}"; then
  pass "Fresh local backup is present"
else
  if [[ "$REQUIRE_FRESH_BACKUP" == "true" || "$REQUIRE_FRESH_BACKUP" == "True" ]]; then
    fail "Fresh local backup is required before deploy"
  else
    warn "Fresh local backup not found; set REQUIRE_FRESH_BACKUP=true to make this a hard gate"
  fi
fi

if [[ -f "$ROOT_DIR/docs/OPERATIONS.md" && -f "$ROOT_DIR/docs/PRODUCTION_READINESS_RELEASE_GATE.md" ]]; then
  pass "Operations runbook and release gate exist"
else
  fail "Operations runbook or release gate is missing"
fi

if [[ -f "$ROOT_DIR/backend/rbac_backend/scripts/migrate_database.py" ]]; then
  pass "Versioned database migration runner exists"
else
  fail "backend/rbac_backend/scripts/migrate_database.py is missing"
fi

if [[ "$RUN_MIGRATION_DRY_RUN" == "true" || "$RUN_MIGRATION_DRY_RUN" == "True" ]]; then
  if [[ -n "$PYTHON_BIN" ]] && (cd "$ROOT_DIR/backend" && "$PYTHON_BIN" -m rbac_backend.scripts.migrate_database --fail-on-warning); then
    pass "Database migration dry-run passed"
  else
    fail "Database migration dry-run failed"
  fi
else
  if [[ "$REQUIRE_MIGRATION_DRY_RUN" == "true" || "$REQUIRE_MIGRATION_DRY_RUN" == "True" ]]; then
    fail "Database migration dry-run is required; set RUN_MIGRATION_DRY_RUN=true"
  else
    warn "Database migration dry-run not executed; set RUN_MIGRATION_DRY_RUN=true for staging/prod release checks"
  fi
fi

printf '\nPre-deploy readiness complete: %s failure(s), %s warning(s).\n' "$failures" "$warnings"
if [[ "$failures" -gt 0 ]]; then
  exit 1
fi
