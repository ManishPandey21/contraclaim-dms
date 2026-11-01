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

# Which edge must answer, and what "answer" means for this deployment. The check
# below used to be conditional on PUBLIC_BASE_URL being set, so an unset variable
# silently skipped the only control over the surface users arrive through - while
# a staging stack, which by design has no public DNS, could satisfy it only by
# pointing at production. See scripts/lib/edge_target.sh.
# shellcheck source=scripts/lib/edge_target.sh
. "$ROOT_DIR/scripts/lib/edge_target.sh"

# Antivirus readiness beyond "clamd answers": loaded signature age, a clean
# scan and an EICAR detection. See scripts/lib/clamav_readiness.sh.
# shellcheck source=scripts/lib/clamav_readiness.sh
. "$ROOT_DIR/scripts/lib/clamav_readiness.sh"

# Which exact image every app service must run after this deploy: the declared
# scope (FULL / CLIENT_ONLY / BACKEND_ONLY), the release manifest of this deploy
# and, for a scoped deploy, the approved manifest of the release it replaces.
# See scripts/release_manifest.py and the deployment guide.
DEPLOY_SCOPE=${DEPLOY_SCOPE:-}
RELEASE_MANIFEST=${RELEASE_MANIFEST:-}
APPROVED_MANIFEST=${APPROVED_MANIFEST:-}


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
# Freshness alone once certified an 89-byte archive of an empty directory as
# `falkordb-data: ok`. backup_status.py now also opens every archive that has a
# declared content contract, so this line claims validity or it claims nothing.
if [[ -z "$python_bin" ]]; then
  fail "No Python interpreter was available to validate the backup archives"
elif "$python_bin" "$ROOT_DIR/scripts/backup_status.py" --root "${backup_root:-/var/backups/contractdms}" --max-age-hours "${backup_max_age:-26}"; then
  pass "Backups are fresh, and every archive with a content contract is a valid recovery artefact"
else
  if [[ "$require_fresh_backup" == "true" || "$require_fresh_backup" == "True" ]]; then
    fail "Backup validation failed (freshness, or archive content that will not restore)"
  else
    warn "Backup validation failed (freshness, or archive content that will not restore); set REQUIRE_FRESH_BACKUP=true to make this a hard gate"
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

# The edge, in the mode this deployment is actually in. A plan that cannot be
# resolved is a FAILURE: "we could not work out which edge to check" and "the
# edge answered 200" must never produce the same exit code.
if edge_plan=$(edge_verification_plan 2>/tmp/edge_plan.err); then
  edge_url=""
  edge_mode=""
  for edge_field in $edge_plan; do
    case "$edge_field" in
      url=*) edge_url=${edge_field#url=} ;;
      mode=*) edge_mode=${edge_field#mode=} ;;
    esac
  done
  pass "Edge verification plan: $edge_plan"
  http_check "${edge_url}/health" "${edge_mode} edge health responded (${edge_url})"
else
  fail "Edge verification could not be resolved: $(tr -d '' </tmp/edge_plan.err | tail -n 1)"
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

# Gate 5 live antivirus. Production refuses to boot with antivirus disabled
# unless an override is recorded, so a disabled scanner here is reported as a
# failure of this verification, never skipped. The maximum age is policy
# (CLAMAV_SIGNATURE_MAX_AGE_HOURS, default 48); a mirror outage that leaves the
# loaded database inside it is a WARN at most and never touches /health/live.
if [[ -n "$backend_container" ]]; then
  if clamav_antivirus_disabled "$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$backend_container" 2>/dev/null | grep -E '^ANTIVIRUS_ENABLED=' | tail -n 1 | cut -d= -f2- || true)"; then
    fail "ANTIVIRUS_ENABLED is false on the running backend; uploads are not scanned and ClamAV readiness was not verified"
  else
    clamav_readiness_check "$(get_env CLAMAV_SIGNATURE_MAX_AGE_HOURS)" "$(get_env CLAMAV_SIGNATURE_WARN_HOURS)"
  fi
else
  fail "No running backend container; ClamAV readiness (signature age, clean scan, EICAR) could not be verified"
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

# ---------------------------------------------------------------------------
# Contract Master reprojection ownership (docs/OPERATIONS.md section 7a)
#
# READ-ONLY. Promotion writes PENDING and only the contract-worker's
# reprojection runtime moves it to CURRENT; the 2026-09-25 staging incident was
# PENDING with no running owner. The flag alone proves nothing (a stale image,
# a crashed loop), so the running image and the runtime's own heartbeat are
# checked too.
# ---------------------------------------------------------------------------
printf '\n--- Contract Master reprojection ownership ---\n'

image_revision() {
  docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$1" 2>/dev/null || true
}

contract_worker_hosts=""
contract_worker_releases=""
contract_worker_cids=$(compose_ps_q contract-worker)
if [[ -z "$contract_worker_cids" ]]; then
  fail "No running contract-worker container: nothing owns Contract Master reprojection"
fi
for cid in $contract_worker_cids; do
  flag=$(container_env "$cid" START_CONTRACT_REPROJECTION_WORKERS)
  if [[ "$flag" == "true" || "$flag" == "True" ]]; then
    pass "contract-worker ${cid:0:12} owns reprojection (START_CONTRACT_REPROJECTION_WORKERS=true)"
  else
    fail "contract-worker ${cid:0:12} has START_CONTRACT_REPROJECTION_WORKERS=${flag:-<unset>}: promoted contracts would stay PENDING"
  fi
  # Which image it must run is the release-image section's question; here, the
  # identity its runtime will report.
  revision=$(image_revision "$cid")
  # The process reports the identity baked into its image. A different value
  # means something set RELEASE_SHA at `up`, and the heartbeat would vouch for
  # code the container does not run.
  process_release=$(container_env "$cid" RELEASE_SHA)
  if [[ "$process_release" != "$revision" ]]; then
    fail "contract-worker ${cid:0:12} process RELEASE_SHA is '${process_release:-<unset>}' but its image was built from '${revision:-<none>}'"
  fi
  contract_worker_releases="$contract_worker_releases $revision"
  contract_worker_hosts="$contract_worker_hosts $(docker inspect -f '{{.Config.Hostname}}' "$cid" 2>/dev/null || true)"
done

non_owner_hosts=""
for svc in backend document-worker document-worker-canary; do
  for cid in $(compose_ps_q "$svc"); do
    [[ -z "$cid" ]] && continue
    non_owner_hosts="$non_owner_hosts $(docker inspect -f '{{.Config.Hostname}}' "$cid" 2>/dev/null || true)"
    flag=$(container_env "$cid" START_CONTRACT_REPROJECTION_WORKERS)
    if [[ "$flag" == "true" || "$flag" == "True" ]]; then
      fail "$svc ${cid:0:12} has START_CONTRACT_REPROJECTION_WORKERS=true: only contract-worker may own reprojection"
    else
      pass "$svc ${cid:0:12} is not a reprojection owner"
    fi
  done
done

# ---------------------------------------------------------------------------
# Release images, per service (owner decision 2026-10-02: strict, scoped)
#
# READ-ONLY. Every app service must run exactly the image the release manifest
# records: a service this scope deploys, the image built from the deployed
# commit; a service it does not deploy, the image the approved manifest of the
# previous release recorded - unchanged, with an unchanged build context. A
# running service the manifest does not name is drift. No scope, no manifest, an
# image without its identity: FAIL, never a skip.
# ---------------------------------------------------------------------------
printf '\n--- Release images (scope %s) ---\n' "${DEPLOY_SCOPE:-<undeclared>}"

running_json=$(mktemp)
{
  printf '{'
  first_service=1
  for svc in backend contract-worker document-worker document-worker-canary client; do
    [[ $first_service -eq 1 ]] || printf ','
    first_service=0
    printf '"%s":[' "$svc"
    first_row=1
    for cid in $(compose_ps_q "$svc"); do
      [[ -z "$cid" ]] && continue
      [[ $first_row -eq 1 ]] || printf ','
      first_row=0
      printf '{"id":"%s","image_id":"%s","revision":"%s"}' "$cid" \
        "$(docker inspect -f '{{.Image}}' "$cid" 2>/dev/null || true)" "$(image_revision "$cid")"
    done
    printf ']'
  done
  # Every other container of the compose project: one running an app image
  # under another service name is drift the per-service lists cannot see.
  # The project and its services come from the compose configuration, not from
  # one container's label (several backend ids during a recreate made that
  # empty, and an empty project silently scanned nothing).
  project=""
  known_services=""
  if config_python=$(resolve_python_bin); then
    config_json=$(docker compose --env-file "$ENV_FILE" $COMPOSE_FILES config --format json 2>/dev/null || true)
    project=$("$config_python" -c 'import json,sys; print(json.loads(sys.stdin.read() or "{}").get("name", ""))' <<<"$config_json" 2>/dev/null || true)
    known_services=$("$config_python" -c 'import json,sys; print(" ".join(json.loads(sys.stdin.read() or "{}").get("services", {})))' <<<"$config_json" 2>/dev/null || true)
  fi
  printf ',"_project":"%s","_others":[' "$project"
  first_row=1
  if [[ -n "$project" ]]; then
    for cid in $(docker ps -q --filter "label=com.docker.compose.project=$project" 2>/dev/null || true); do
      other_service=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$cid" 2>/dev/null || true)
      oneoff=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.oneoff"}}' "$cid" 2>/dev/null || true)
      known=false
      [[ " $known_services " == *" $other_service "* ]] && known=true
      is_oneoff=false
      case "$other_service" in
        backend|contract-worker|document-worker|document-worker-canary|client)
          # `compose ps` lists the service's containers; a `compose run` one-off
          # of an app service is invisible there, so it is reported here.
          [[ "$oneoff" == "True" ]] || continue
          is_oneoff=true ;;
      esac
      [[ $first_row -eq 1 ]] || printf ','
      first_row=0
      printf '{"id":"%s","service":"%s","image_id":"%s","revision":"%s","oneoff":%s,"known":%s}' \
        "$cid" "$other_service" "$(docker inspect -f '{{.Image}}' "$cid" 2>/dev/null || true)" \
        "$(image_revision "$cid")" "$is_oneoff" "$known"
    done
  fi
  printf ']}'
} >"$running_json"

# A pending receipt from an earlier run never carries over into this one, and
# none survives this run unless its very end confirms it: an abort, an
# interrupt or any later failure removes it.
receipt_args=()
if [[ -n "$RELEASE_MANIFEST" && "$DEPLOY_SCOPE" != "UNCHANGED" ]]; then
  # An earlier green run's receipt is withdrawn too: approval is THIS run's
  # verdict, so a manifest that fails now cannot be promoted on an old one.
  rm -f "$RELEASE_MANIFEST.verified.pending" "$RELEASE_MANIFEST.verified"
  trap 'rm -f "$RELEASE_MANIFEST.verified.pending"' EXIT
  receipt_args=(--receipt)
fi
if manifest_python=$(resolve_python_bin); then
  manifest_rc=0
  manifest_out=$("$manifest_python" "$ROOT_DIR/scripts/release_manifest.py" verify \
    --scope "$DEPLOY_SCOPE" --target "$RELEASE_MANIFEST" --approved "$APPROVED_MANIFEST" \
    --checkout "$ROOT_DIR" --running "$running_json" ${receipt_args[@]+"${receipt_args[@]}"} 2>&1) || manifest_rc=$?
  while IFS= read -r line; do
    case "$line" in
      "PASS "*) pass "${line#PASS }" ;;
      "FAIL "*) fail "${line#FAIL }" ;;
      "") ;;
      *) fail "release image check: $line" ;;
    esac
  done <<<"$manifest_out"
  if [[ $manifest_rc -ne 0 ]] && ! grep -q '^FAIL ' <<<"$manifest_out"; then
    fail "release image check exited $manifest_rc without a finding"
  fi
else
  fail "No python interpreter for the release image check (set PYTHON_BIN)"
fi
rm -f "$running_json"

document_worker_cids=$(compose_ps_q document-worker)
if [[ -n "$document_worker_cids" ]]; then
  for cid in $document_worker_cids; do
    flag=$(container_env "$cid" START_DOCUMENT_EXTRACTION_WORKERS)
    if [[ "$flag" == "true" || "$flag" == "True" ]]; then
      pass "document-worker ${cid:0:12} owns extraction"
    else
      fail "document-worker ${cid:0:12} has START_DOCUMENT_EXTRACTION_WORKERS=${flag:-<unset>}"
    fi
  done
else
  warn "No running document-worker container; extraction ownership not verified"
fi

if [[ -n "$backend_cid" && -n "$contract_worker_cids" ]]; then
  if docker compose --env-file "$ENV_FILE" $COMPOSE_FILES exec -T --interactive=false \
    -e "CW_HOSTS=${contract_worker_hosts}" \
    -e "NON_OWNER_HOSTS=${non_owner_hosts}" \
    -e "DEPLOYED_COMMIT=${deployed_commit}" \
    -e "CW_RELEASES=${contract_worker_releases}" \
    -e "PROBE_INSTRUMENT=${REPROJECTION_PROBE_INSTRUMENT_ID:-}" \
    -e "PROBE_TIMEOUT=${REPROJECTION_PROBE_TIMEOUT_SECONDS:-300}" \
    backend python -c '
import os, sys, time
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient

from rbac_backend.services.contract_reprojection_runtime import RUNTIME_HEARTBEATS_COLLECTION

hosts = set(os.environ.get("CW_HOSTS", "").split())
# Only a RUNNING container that must not reproject counts as a second owner. A
# row left by the contract-worker this deploy replaced names a host that no
# longer exists and is ignored (a stopped runtime also removes its own row).
non_owners = set(os.environ.get("NON_OWNER_HOSTS", "").split())
deployed = os.environ.get("DEPLOYED_COMMIT", "")
db = MongoClient(os.environ["DATABASE_URL"], serverSelectionTimeoutMS=8000).get_default_database()

def aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value

now = datetime.now(timezone.utc)
beats = list(db[RUNTIME_HEARTBEATS_COLLECTION].find({}))
fresh = []
for beat in beats:
    window = max(180.0, 3 * float(beat.get("interval_seconds") or 60))
    # Liveness is written during a pass too (per instrument and per build
    # step), so a long pass over a backlog does not read as a dead owner.
    last = aware(beat.get("last_alive_at") or beat.get("last_pass_at"))
    if last and now - last <= timedelta(seconds=window):
        fresh.append(beat)
problems = []
owners = [b for b in fresh if b.get("host") in hosts]
strangers = [b for b in fresh if b.get("host") in non_owners]
if not owners:
    problems.append("no live reprojection runtime recorded by a contract-worker container")
if strangers:
    problems.append("reprojection runtime running in non-contract-worker containers: " + ", ".join(sorted({str(b.get("host")) for b in strangers})))
# A pass still running after the claim lease (30 min) plus one interval is
# stuck: its liveness ticker keeps writing, but no generation is moving.
stuck = []
for beat in owners:
    started = aware(beat.get("pass_started_at"))
    finished = aware(beat.get("last_pass_at"))
    limit = timedelta(seconds=1800 + float(beat.get("interval_seconds") or 60))
    if started and (finished is None or finished < started) and now - started > limit:
        stuck.append(beat)
if stuck:
    problems.append("reprojection pass running for more than 30 minutes on: " + ", ".join(sorted({str(b.get("host")) for b in stuck})))
# The runtime must report the release baked into a running contract-worker
# image - which the shell checks above tie to the deployed checkout. Comparing
# it with HEAD itself failed every client-only deploy, which rebuilds no
# backend image.
images = set(os.environ.get("CW_RELEASES", "").split()) - {"unknown"}
stale_code = [b for b in owners if b.get("release") not in images]
if stale_code:
    problems.append("contract-worker runtime release " + ", ".join(sorted({str(b.get("release")) for b in stale_code})) + " is not the release of a running contract-worker image (" + (", ".join(sorted(images)) or "none known") + "); deployed " + deployed)

probe = os.environ.get("PROBE_INSTRUMENT", "")
if probe and not problems:
    deadline = time.time() + float(os.environ.get("PROBE_TIMEOUT") or 300)
    owner_ids = {b["_id"] for b in owners}
    while True:
        inst = db["contract_documents"].find_one({"_id": probe})
        if inst is None:
            problems.append(f"probe instrument {probe} does not exist"); break
        revision = int(inst.get("classification_revision") or 0)
        if inst.get("projection_status") == "CURRENT" and int(inst.get("projection_revision") or 0) == revision:
            claim = db["contract_reprojection_claims"].find_one({"_id": f"contract-reprojection:{probe}:{revision}"}) or {}
            worker = claim.get("worker_id")
            if claim.get("status") != "complete" or worker not in owner_ids:
                problems.append(f"probe {probe} is CURRENT but its claim was completed by {worker!r}, not a live contract-worker runtime")
            break
        state = inst.get("projection_status")
        if time.time() > deadline:
            problems.append(f"probe {probe} still {state} after the timeout"); break
        time.sleep(5)

if problems:
    print("; ".join(problems)); sys.exit(1)
suffix = f"; probe {probe} reached CURRENT through it" if probe else ""
first = owners[0]
runtime_id, host, release = first["_id"], first.get("host"), first.get("release")
print(f"ok runtime {runtime_id} on {host} release {release}{suffix}")
' >/tmp/reprojection_owner.out 2>&1; then
    pass "Reprojection runtime is live: $(tail -n 1 /tmp/reprojection_owner.out 2>/dev/null)"
  else
    fail "Reprojection runtime check failed: $(tail -n 3 /tmp/reprojection_owner.out 2>/dev/null)"
  fi
elif [[ -z "$backend_cid" ]]; then
  fail "Cannot verify reprojection runtime liveness: no running backend container to run the check in"
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
  [[ -n "$RELEASE_MANIFEST" ]] && rm -f "$RELEASE_MANIFEST.verified.pending"
  exit 1
fi

# Only a run with no failure at all confirms the receipt the release-image check
# left pending for the exact bytes it evaluated. `release_manifest.py promote`
# makes a manifest the approved state only with that receipt, so a hand-edited,
# unverified or failed manifest is never approval. Receipts sit next to the
# manifest; nothing in the running stack changes.
if [[ -n "$RELEASE_MANIFEST" && "$DEPLOY_SCOPE" != "UNCHANGED" ]]; then
  if "$manifest_python" "$ROOT_DIR/scripts/release_manifest.py" confirm \
    --manifest "$RELEASE_MANIFEST"; then
    printf 'Promote it: python3 scripts/release_manifest.py promote --manifest %s --current <manifests>/current.json\n' "$RELEASE_MANIFEST"
  else
    printf 'FAIL: could not record the verification receipt for %s\n' "$RELEASE_MANIFEST"
    exit 1
  fi
fi
