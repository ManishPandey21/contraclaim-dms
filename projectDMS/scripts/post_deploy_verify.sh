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

# The environment file is data, never a program. `source` executed it, and one
# unquoted `&` in a URI backgrounded the assignment so the variable never
# arrived - F-A8M-2. See scripts/lib/env_file.sh.
# shellcheck source=scripts/lib/env_file.sh
. "$ROOT_DIR/scripts/lib/env_file.sh"


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
env_file_load "$BACKEND_ENV_FILE"
env_file_load "$ENV_FILE"

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

# ---------------------------------------------------------------------------
# Unified page extraction: canary topology verification (Task 7.6)
#
# READ-ONLY. Everything below inspects running state and reports; nothing
# starts, stops, scales, or writes. Values are read from the CONTAINERS, not
# from .env, because .env is what someone intended and the container is what is
# actually running. See docs/operations/unified_extraction_canary_and_rollback.md
# ---------------------------------------------------------------------------

printf '\n--- Unified extraction canary topology ---\n'

compose_ps_q() {
  docker compose --env-file "$ENV_FILE" $COMPOSE_FILES ps -q "$1" 2>/dev/null || true
}

# Read one environment variable out of a running container.
container_env() {
  local cid=$1 key=$2
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$cid" 2>/dev/null \
    | grep -E "^${key}=" | tail -n 1 | cut -d= -f2- || true
}

# 1. Deployed branch and commit - what is actually checked out here.
deployed_commit=$(git -C "$ROOT_DIR" rev-parse HEAD 2>/dev/null || true)
deployed_branch=$(git -C "$ROOT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || true)
if [[ -n "$deployed_commit" ]]; then
  pass "Deployed checkout: branch=${deployed_branch:-<detached>} commit=${deployed_commit}"
  if ! git -C "$ROOT_DIR" diff --quiet 2>/dev/null || \
     ! git -C "$ROOT_DIR" diff --cached --quiet 2>/dev/null; then
    warn "Deployed checkout has uncommitted modifications; the running code may not match ${deployed_commit}"
  fi
else
  fail "Could not determine deployed branch/commit from $ROOT_DIR"
fi

# 2. The compose file set the RUNNING stack was created from. The stack is
#    started with prod + mongo-replicaset; including the base compose file is a
#    known deployment error that only surfaces at `up`.
backend_cid=$(compose_ps_q backend)
if [[ -n "$backend_cid" ]]; then
  actual_config_files=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "$backend_cid" 2>/dev/null || true)
  if [[ -n "$actual_config_files" ]]; then
    pass "Running stack compose files: $actual_config_files"
    if grep -qE '(^|,|/)docker-compose\.yml' <<<"$actual_config_files"; then
      warn "Running stack includes the base docker-compose.yml; production expects prod + mongo-replicaset only"
    fi
  else
    warn "Could not read com.docker.compose.project.config_files from the backend container"
  fi
else
  warn "No running backend container; skipping compose file-set verification"
fi

# 3/4. Global flag and canary allowlist, as the worker actually sees them.
worker_cid=$(compose_ps_q document-worker)
canary_cids=$(compose_ps_q document-worker-canary)
canary_replicas=$(printf '%s' "$canary_cids" | grep -c . || true)

if [[ -n "$worker_cid" ]]; then
  unified_enabled=$(container_env "$worker_cid" UNIFIED_EXTRACTION_ENABLED)
  canary_orgs=$(container_env "$worker_cid" UNIFIED_EXTRACTION_CANARY_ORG_IDS)
  pass "document-worker UNIFIED_EXTRACTION_ENABLED=${unified_enabled:-<unset>}"
  pass "document-worker UNIFIED_EXTRACTION_CANARY_ORG_IDS=[${canary_orgs}]"

  # Global enablement is a separate authorised change. Flag it loudly.
  if [[ "$unified_enabled" == "true" || "$unified_enabled" == "True" ]]; then
    warn "UNIFIED_EXTRACTION_ENABLED is true: every organisation is on the unified pipeline, not just the allowlist"
  fi
else
  fail "No running document-worker container; document extraction has no owner"
fi

# 5/6/7. Claim restrictions and canary scale.
worker_versions=""
if [[ -n "$worker_cid" ]]; then
  worker_versions=$(container_env "$worker_cid" DOCUMENT_WORKER_PIPELINE_VERSIONS)
  pass "document-worker DOCUMENT_WORKER_PIPELINE_VERSIONS=[${worker_versions}]"
fi

canary_versions=""
if [[ "$canary_replicas" -gt 0 ]]; then
  first_canary=$(printf '%s' "$canary_cids" | head -n 1)
  canary_versions=$(container_env "$first_canary" DOCUMENT_WORKER_PIPELINE_VERSIONS)
  pass "document-worker-canary replicas=${canary_replicas} DOCUMENT_WORKER_PIPELINE_VERSIONS=[${canary_versions}]"
  if [[ "$canary_versions" != "unified_v1" ]]; then
    fail "document-worker-canary must be pinned to unified_v1, found [${canary_versions}]"
  fi
else
  pass "document-worker-canary replicas=0 (normal, non-canary state)"
fi

# 9. No worker may be unrestricted while a canary is running. An unrestricted
#    worker claims anything, which defeats the whole isolation property.
canary_mode=false
if [[ "$canary_replicas" -gt 0 || -n "${canary_orgs:-}" ]]; then
  canary_mode=true
fi

if [[ "$canary_mode" == "true" ]]; then
  pass "Canary mode is ACTIVE (replicas=${canary_replicas}, allowlist=[${canary_orgs:-}])"
  if [[ -z "$worker_versions" ]]; then
    fail "Canary mode with an UNRESTRICTED document-worker: it can claim unified_v1 jobs and drain the canary queue"
  fi
  if [[ -n "$worker_versions" ]] && grep -q "unified_v1" <<<"$worker_versions"; then
    fail "Canary mode but document-worker may also claim unified_v1: claim domains are not disjoint"
  fi
  if [[ "$canary_replicas" -gt 0 && -z "${canary_orgs:-}" ]]; then
    warn "Canary worker is running but the allowlist is empty; no new unified jobs will be created (expected mid-rollback)"
  fi
else
  pass "Canary mode is INACTIVE; single-worker operation"
fi

# 12. Disjointness, stated explicitly from the two observed sets.
if [[ "$canary_replicas" -gt 0 ]]; then
  overlap=""
  for v in ${worker_versions//,/ }; do
    for c in ${canary_versions//,/ }; do
      [[ "$v" == "$c" ]] && overlap="$v"
    done
  done
  if [[ -z "$worker_versions" ]]; then
    fail "Claim domains NOT provably disjoint: document-worker is unrestricted"
  elif [[ -n "$overlap" ]]; then
    fail "Claim domains overlap on [${overlap}]: both workers can claim the same jobs"
  else
    pass "Claim domains are disjoint: document-worker=[${worker_versions}] canary=[${canary_versions}]"
  fi
fi

# 8. Exactly one scheduler owner across every running service.
scheduler_owners=""
for svc in backend contract-worker document-worker document-worker-canary; do
  for cid in $(compose_ps_q "$svc"); do
    [[ -z "$cid" ]] && continue
    if [[ "$(container_env "$cid" RUN_SCHEDULER)" == "true" ]]; then
      scheduler_owners="$scheduler_owners $svc"
    fi
  done
done
scheduler_count=$(printf '%s' "$scheduler_owners" | wc -w | tr -d ' ')
if [[ "$scheduler_count" == "1" ]]; then
  pass "Exactly one scheduler owner:${scheduler_owners}"
else
  fail "Expected exactly 1 scheduler owner, found ${scheduler_count}:${scheduler_owners:-<none>}"
fi

# Extraction must not also run on the request-serving tier.
if [[ -n "$backend_cid" ]]; then
  backend_extraction=$(container_env "$backend_cid" START_DOCUMENT_EXTRACTION_WORKERS)
  if [[ "$backend_extraction" == "true" || "$backend_extraction" == "True" ]]; then
    fail "backend has START_DOCUMENT_EXTRACTION_WORKERS=true: the web tier is acting as an extraction worker"
  else
    pass "backend is not an extraction worker (START_DOCUMENT_EXTRACTION_WORKERS=${backend_extraction:-<unset>})"
  fi
fi

# 10. Migration and index presence required by Task 7.6. Read-only index read.
if [[ -n "$backend_cid" ]]; then
  if docker compose --env-file "$ENV_FILE" $COMPOSE_FILES exec -T --interactive=false backend \
    python -c '
import os, sys
from pymongo import MongoClient
c = MongoClient(os.environ["DATABASE_URL"], serverSelectionTimeoutMS=8000)
db = c.get_default_database()
missing = []
pages = {tuple(k["key"].items()): k for k in db["document_ocr_pages"].list_indexes()}
want = (("document_id",1),("extraction_run_id",1),("page_number",1))
hit = [i for k,i in pages.items() if k == want]
if not hit or not hit[0].get("unique"):
    missing.append("document_ocr_pages unique(document_id,extraction_run_id,page_number)")
heads = [i for i in db["document_extraction_heads"].list_indexes()
         if tuple(i["key"].items()) == (("document_id",1),)]
if not heads or not heads[0].get("unique"):
    missing.append("document_extraction_heads unique(document_id)")
applied = db["schema_migrations"].find_one({"version": "20260814_0001"})
if not applied:
    missing.append("migration 20260814_0001 not recorded as applied")
if missing:
    print("MISSING: " + "; ".join(missing)); sys.exit(1)
print("ok")
' >/tmp/extraction_indexes.out 2>&1; then
    pass "Task 7.6 page-evidence indexes and migration 20260814_0001 present"
  else
    fail "Page-evidence index/migration check failed: $(cat /tmp/extraction_indexes.out 2>/dev/null | tail -n 3)"
  fi
fi

# 10b. Permission seeding is best effort inside application startup: it logs a
#      warning and continues when it fails, so the application comes up healthy
#      with an incomplete catalogue and every route gated on a missing
#      permission denies. Assert the catalogue rather than trusting the log.
if [[ -n "$backend_cid" ]]; then
  if docker compose --env-file "$ENV_FILE" $COMPOSE_FILES exec -T --interactive=false backend \
    python -c '
import os, sys
from pymongo import MongoClient
# The catalogue ensure_permission_catalog_and_superadmin() seeds from, so the
# assertion cannot drift from the seeder.
from rbac_backend.models.permission import DEFAULT_PERMISSIONS

expected = {p["name"] for p in DEFAULT_PERMISSIONS if p.get("name")}
c = MongoClient(os.environ["DATABASE_URL"], serverSelectionTimeoutMS=8000)
db = c.get_default_database()
seeded = {d.get("name") for d in db["permissions"].find({}, {"name": 1})}
missing = sorted(expected - seeded)
if missing:
    print(f"MISSING {len(missing)} of {len(expected)} permissions: " + ", ".join(missing[:10]))
    sys.exit(1)
superadmin = db["roles"].find_one({"_id": "superadmin"})
if superadmin is None:
    # Startup seeds the catalogue and unions it onto an existing superadmin
    # role; it never creates the role. On a database where the setup flow has
    # not run there is nothing to union onto, which is a setup question rather
    # than a seeding regression.
    print(f"ok {len(seeded)} permissions seeded; WARN no superadmin role document to grant them to")
    sys.exit(0)
granted = set(superadmin.get("permissions") or [])
ungranted = set() if "*" in granted else (expected - granted)
if ungranted:
    print(f"superadmin is missing {len(ungranted)}: " + ", ".join(sorted(ungranted)[:10]))
    sys.exit(1)
print(f"ok {len(seeded)} permissions seeded, superadmin holds all {len(expected)}")
' >/tmp/permission_seed.out 2>&1; then
    pass "Permission catalogue seeded: $(tail -n 1 /tmp/permission_seed.out 2>/dev/null)"
  else
    fail "Permission seeding check failed: $(cat /tmp/permission_seed.out 2>/dev/null | tail -n 3)"
  fi
fi

# 11. Archive MIME configuration. ZIP is stored intact and never unpacked; RAR
#     stays disabled until clamd is proven to scan inside a .rar (Task 0.4).
if [[ -n "$worker_cid" ]]; then
  rar_enabled=$(container_env "$worker_cid" RAR_UPLOAD_ENABLED)
  allowed_doc_mimes=$(container_env "$worker_cid" ALLOWED_DOCUMENT_MIMES)
  pass "RAR_UPLOAD_ENABLED=${rar_enabled:-<unset>}"
  if [[ "$rar_enabled" == "true" || "$rar_enabled" == "True" ]]; then
    warn "RAR uploads are ENABLED; Task 0.4 requires positive ClamAV inner-member proof before this is permitted"
  fi
  if [[ -n "$allowed_doc_mimes" ]]; then
    pass "ALLOWED_DOCUMENT_MIMES=${allowed_doc_mimes}"
    if grep -q "rar" <<<"$allowed_doc_mimes" && [[ "$rar_enabled" != "true" ]]; then
      warn "A RAR MIME appears in ALLOWED_DOCUMENT_MIMES while RAR_UPLOAD_ENABLED is not true; confirm which gate wins"
    fi
  else
    warn "ALLOWED_DOCUMENT_MIMES not visible on the worker container"
  fi
fi

printf '\nPost-deploy verification complete: %s failure(s), %s warning(s).\n' "$failures" "$warnings"
if [[ "$failures" -gt 0 ]]; then
  exit 1
fi
