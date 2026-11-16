#!/usr/bin/env bash
# Read-only production checks for the post-cutover operational assessment
# (docs/POST_CUTOVER_OPERATIONAL_ASSESSMENT_2026-11-15.md).
#
# Run on the production host, from the deployed checkout:
#
#   cd /opt/contraclaim-dms/projectDMS && bash scripts/post_cutover_readonly_checks.sh
#
# It answers four questions with what the running system says today:
#   A. what is deployed (branch, commit, compose file set, containers)
#   B. FalkorDB: which container is the live engine, is it the only one that
#      answers `falkordb`, where does it persist, does the .env credential work
#   C. third-party images: is production still on the digests whose exception
#      expired on 2026-10-15
#   D. migrations: are the three Letter Bench migrations (20261114_0001 to
#      _0003) applied, and what does the ledger hold
#   F. Phase 4b readings P-02 to P-12 (claims, arbitration drafts, reviewer
#      roles, skills, perspective, usage), counts only, through
#      scripts/claim_arbitration_readonly_readings.py piped into the backend
# plus the newest backup manifest and whether it recorded a persistence flush.
#
# READ-ONLY, by construction:
#   - docker: only `ps`, `inspect`, `image inspect`, `network inspect`, `logs`
#     (section F counts gateway requests), and `exec` of commands that read
#     (redis-cli PING / CONFIG GET / GRAPH.LIST / GRAPH.RO_QUERY, getent,
#     `migrate_database --list`, and the section F reader, whose database access
#     is limited to find/count/distinct/aggregate by a guard it carries).
#   - it never runs `docker compose up`, `start`, `stop`, `restart`, `rm`,
#     `update` or `rename`, and never writes inside a container.
#   - it writes one report directory under OUT_DIR on the host and nothing else.
#
# CREDENTIALS: no secret is ever placed on a command line or printed. The
# FalkorDB credential is read from the env file into this shell and handed to
# `docker exec` through the environment (`-e REDISCLI_AUTH`, no value on argv).
# This script does NOT search host logs for a credential: doing that with
# `sudo grep <value>` is what wrote the credential into auth.log in R-A9H.
#
# Options (environment):
#   PROJECT_DIR   deployed checkout (default: the directory above this script)
#   ENV_FILE      default $PROJECT_DIR/.env
#   BACKUP_ROOT   default /var/backups/contraclaim
#   OUT_DIR       default /tmp; the report goes in a new directory under it
#   RUN_TRIVY=1   also scan the four excepted images with Trivy (CRITICAL,HIGH).
#                 Needs trivy on the host; it downloads its vulnerability
#                 database, so it is off unless asked for.
#
# Exit code: 0 when no check FAILED, 1 otherwise. WARN and UNKNOWN do not fail
# the run; they are findings to read, not to ignore.

set -u

PROJECT_DIR=${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
ENV_FILE=${ENV_FILE:-"$PROJECT_DIR/.env"}
BACKUP_ROOT=${BACKUP_ROOT:-/var/backups/contraclaim}
OUT_DIR=${OUT_DIR:-/tmp}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
RUN_DIR="$OUT_DIR/post-cutover-readonly-$STAMP"
REPORT="$RUN_DIR/report.txt"
mkdir -p "$RUN_DIR" || { echo "cannot create $RUN_DIR" >&2; exit 1; }

# The four third-party images carried under the R-A9E exception, by the index
# digest that exception names (docs/THIRD_PARTY_IMAGE_SECURITY_DISPOSITION.md).
EXCEPTED_DIGESTS="
mongo sha256:ffa440e8d62533e24a67696ae1bbb46e610ebb3167d65abd122b496ae06d28e6
falkordb sha256:af5f2aa035390f04fa6d1f0c6353669f5f75c101f6b4f385fcb672a288b4edb8
qdrant sha256:05fecce7dce45d1254e0468bc037e8210e187fd56fa847688b012293d5f08aae
httpd sha256:393435ee1a31437adeb1f03c134224c9ce5fa5f527e8c0cb9bea576b0d6fc742
"
BENCH_VERSIONS="20261114_0001 20261114_0002 20261114_0003"

passes=0 warns=0 fails=0 unknowns=0
exec > >(tee "$REPORT") 2>&1

section() { printf '\n=== %s\n' "$*"; }
info() { printf 'INFO     %s\n' "$*"; }
pass() { printf 'PASS     %s\n' "$*"; passes=$((passes + 1)); }
warn() { printf 'WARN     %s\n' "$*"; warns=$((warns + 1)); }
fail() { printf 'FAIL     %s\n' "$*"; fails=$((fails + 1)); }
unknown() { printf 'UNKNOWN  %s\n' "$*"; unknowns=$((unknowns + 1)); }

printf 'POST-CUTOVER READ-ONLY CHECKS  %s\n' "$STAMP"
printf 'Host %s; project %s\n' "$(hostname)" "$PROJECT_DIR"
printf 'This run changed nothing: no container was started, stopped or recreated.\n'

if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  fail "docker is not available to this user; nothing below can be measured"
  printf '\nSUMMARY  pass=%d warn=%d fail=%d unknown=%d\n' "$passes" "$warns" "$fails" "$unknowns"
  exit 1
fi

inspect() { docker inspect -f "$2" "$1" 2>/dev/null; }

# --------------------------------------------------------------------------- #
section "A. What is deployed"
# --------------------------------------------------------------------------- #
if git -C "$PROJECT_DIR" rev-parse HEAD >/dev/null 2>&1; then
  info "branch $(git -C "$PROJECT_DIR" branch --show-current 2>/dev/null || echo '?')"
  info "HEAD   $(git -C "$PROJECT_DIR" rev-parse HEAD)"
  info "HEAD date and subject: $(git -C "$PROJECT_DIR" log -1 --format='%cI %s')"
  dirty=$(git -C "$PROJECT_DIR" status --porcelain | wc -l | tr -d ' ')
  if [[ "$dirty" == "0" ]]; then pass "checkout is clean"; else warn "checkout has $dirty uncommitted or untracked path(s)"; fi
else
  unknown "$PROJECT_DIR is not a git checkout"
fi

BACKEND=$(docker ps --filter 'label=com.docker.compose.service=backend' --format '{{.Names}}' | head -1)
if [[ -n "$BACKEND" ]]; then
  info "backend container: $BACKEND"
  info "compose file set (from the running container's label): $(inspect "$BACKEND" '{{ index .Config.Labels "com.docker.compose.project.config_files" }}')"
  info "backend image revision label: $(inspect "$BACKEND" '{{ index .Config.Labels "org.opencontainers.image.revision" }}')"
else
  unknown "no running container carries the compose label service=backend"
fi

printf '\n%-42s %-11s %-9s %-15s %-12s %s\n' CONTAINER STATE HEALTH RESTART-POLICY LOG-MAX-SIZE IMAGE-ID
docker ps -a --format '{{.Names}}' | sort | while read -r name; do
  [[ -n "$name" ]] || continue
  printf '%-42s %-11s %-9s %-15s %-12s %s\n' "$name" \
    "$(inspect "$name" '{{.State.Status}}')" \
    "$(inspect "$name" '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}')" \
    "$(inspect "$name" '{{.HostConfig.RestartPolicy.Name}}')" \
    "$(inspect "$name" '{{with index .HostConfig.LogConfig.Config "max-size"}}{{.}}{{else}}unbounded{{end}}')" \
    "$(inspect "$name" '{{.Image}}' | cut -c8-19)"
done

unhealthy=$(docker ps --filter health=unhealthy --format '{{.Names}}' | tr '\n' ' ')
if [[ -z "$unhealthy" ]]; then pass "no running container reports unhealthy"; else fail "unhealthy: $unhealthy"; fi

# --------------------------------------------------------------------------- #
section "B. FalkorDB topology and credential"
# --------------------------------------------------------------------------- #
# Every container, running or not, that is configured to answer `falkordb`.
answering_running=""
answering_stopped=""
for name in $(docker ps -a --format '{{.Names}}'); do
  aliases=$(inspect "$name" '{{range $n, $c := .NetworkSettings.Networks}}{{range $c.Aliases}}{{.}} {{end}}{{end}}')
  case " $aliases " in
    *" falkordb "*)
      if [[ "$(inspect "$name" '{{.State.Running}}')" == "true" ]]; then
        answering_running="$answering_running $name"
      else
        answering_stopped="$answering_stopped $name"
      fi
      ;;
  esac
done
info "running containers with the network alias falkordb:${answering_running:- none}"
info "stopped containers with the network alias falkordb:${answering_stopped:- none}"
count=$(echo $answering_running | wc -w | tr -d ' ')
LIVE=""
case "$count" in
  1) LIVE=$(echo $answering_running); pass "exactly one running container answers falkordb: $LIVE" ;;
  0) fail "no running container answers falkordb" ;;
  *) fail "$count running containers answer falkordb - consumers may reach stale data on a dead credential" ;;
esac

if [[ -n "$LIVE" ]]; then
  service=$(inspect "$LIVE" '{{ index .Config.Labels "com.docker.compose.service" }}')
  if [[ "$service" == "falkordb" ]]; then
    pass "the live engine is the compose service falkordb - topology is NORMALIZED"
  else
    warn "the live engine ($LIVE) carries no compose service label - topology is still the TEMPORARY out-of-band one (R-A9H section 4)"
  fi
  [[ "$(inspect "$LIVE" '{{if .Config.Healthcheck}}yes{{else}}no{{end}}')" == "yes" ]] \
    && pass "the live engine has a healthcheck" || warn "the live engine has no healthcheck"
  [[ -n "$(inspect "$LIVE" '{{ index .HostConfig.LogConfig.Config "max-size" }}')" ]] \
    && pass "the live engine has log rotation" || warn "the live engine's logs are unbounded"
  info "live engine restart policy: $(inspect "$LIVE" '{{.HostConfig.RestartPolicy.Name}}'); image $(inspect "$LIVE" '{{.Config.Image}}')"
  info "live engine mounts: $(inspect "$LIVE" '{{range .Mounts}}{{.Name}}:{{.Destination}} {{end}}')"

  # The credential goes through the environment, never through argv.
  FALKOR_CRED=""
  if [[ -r "$ENV_FILE" ]]; then
    while IFS= read -r line; do
      case "$line" in FALKORDB_PASSWORD=*) FALKOR_CRED=${line#FALKORDB_PASSWORD=} ;; esac
    done <"$ENV_FILE"
    FALKOR_CRED=${FALKOR_CRED%\"}; FALKOR_CRED=${FALKOR_CRED#\"}
    FALKOR_CRED=${FALKOR_CRED%\'}; FALKOR_CRED=${FALKOR_CRED#\'}
  fi
  if [[ -z "$FALKOR_CRED" ]]; then
    unknown "FALKORDB_PASSWORD could not be read from $ENV_FILE; the authenticated checks were skipped"
  else
    export REDISCLI_AUTH="$FALKOR_CRED"
    rcli() { docker exec -e REDISCLI_AUTH "$LIVE" redis-cli --no-auth-warning "$@" 2>&1; }
    ping=$(rcli PING)
    if [[ "$ping" == "PONG" ]]; then
      pass "the live engine accepts the credential in $ENV_FILE"
      dir=$(rcli CONFIG GET dir | tail -1)
      [[ "$dir" == "/data" ]] && pass "persistence directory is /data" || fail "persistence directory is '$dir', expected /data"
      info "last successful save (unix time): $(rcli LASTSAVE)"
      graphs=$(rcli GRAPH.LIST | tr '\n' ' ')
      info "graphs: ${graphs:-none}"
      for graph in $graphs; do
        nodes=$(rcli GRAPH.RO_QUERY "$graph" 'MATCH (n) RETURN count(n)' | sed -n 2p)
        rels=$(rcli GRAPH.RO_QUERY "$graph" 'MATCH ()-[r]->() RETURN count(r)' | sed -n 2p)
        info "graph $graph: nodes=$nodes relationships=$rels (R-A9H recorded 156 / 220 on 2026-09-21)"
      done
    else
      fail "the live engine refused the credential in $ENV_FILE (answer: $(printf '%s' "$ping" | cut -c1-40))"
    fi
    unset REDISCLI_AUTH FALKOR_CRED
  fi
fi

if [[ -n "$BACKEND" ]]; then
  resolved=$(docker exec "$BACKEND" getent hosts falkordb 2>/dev/null | awk '{print $1}' | tr '\n' ' ')
  live_ip=$([[ -n "$LIVE" ]] && inspect "$LIVE" '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}')
  info "backend resolves falkordb to: ${resolved:-nothing}; live engine addresses: ${live_ip:-none}"
  for ip in $resolved; do
    case " $live_ip " in *" $ip "*) : ;; *) fail "backend resolves falkordb to $ip, which is not the live engine" ;; esac
  done
fi

info "rollback artefacts present (names only):"
for f in "$PROJECT_DIR/.env.bak-ra9g-a2" "$PROJECT_DIR/.env.bak-ra9i"; do
  [[ -e "$f" ]] && info "  $(basename "$f") exists, modified $(date -u -r "$f" +%Y-%m-%dT%H:%MZ)" || info "  $(basename "$f") absent"
done
[[ -e "$ENV_FILE" ]] && info "  $(basename "$ENV_FILE") last modified $(date -u -r "$ENV_FILE" +%Y-%m-%dT%H:%MZ) (R-A9G rotation was 2026-09-20)"
docker image ls --format '{{.Repository}}:{{.Tag}}' | grep -i 'falkordb-rollback' | sed 's/^/INFO       rollback image /' || true
unknown "whether the current credential is still in /var/log/auth.log or the journal: not checked here on purpose. Use the root-shell method in R-A9H section 9; never pass the value to sudo or grep on a command line"

# --------------------------------------------------------------------------- #
section "C. Third-party images against the exception that expired 2026-10-15"
# --------------------------------------------------------------------------- #
echo "$EXCEPTED_DIGESTS" | while read -r image digest; do
  [[ -n "$image" ]] || continue
  found=0
  for name in $(docker ps --format '{{.Names}}'); do
    image_id=$(inspect "$name" '{{.Image}}')
    repo_digests=$(docker image inspect -f '{{range .RepoDigests}}{{.}} {{end}}' "$image_id" 2>/dev/null)
    case "$repo_digests" in
      *"$image"*)
        found=1
        case "$repo_digests" in
          *"$digest"*) printf 'FAIL     %s runs %s on the EXCEPTED digest %s - the exception expired 2026-10-15\n' "$name" "$image" "${digest:0:19}" ;;
          *) printf 'PASS     %s runs %s on a different digest: %s\n' "$name" "$image" "$repo_digests" ;;
        esac
        ;;
    esac
  done
  [[ $found -eq 1 ]] || printf 'UNKNOWN  no running container uses an image named %s\n' "$image"
done | tee "$RUN_DIR/.images"
fails=$((fails + $(grep -c '^FAIL' "$RUN_DIR/.images")))
passes=$((passes + $(grep -c '^PASS' "$RUN_DIR/.images")))
unknowns=$((unknowns + $(grep -c '^UNKNOWN' "$RUN_DIR/.images")))
rm -f "$RUN_DIR/.images"

if [[ "${RUN_TRIVY:-0}" == "1" ]]; then
  if command -v trivy >/dev/null 2>&1; then
    for name in $(docker ps --format '{{.Names}}'); do
      ref=$(inspect "$name" '{{.Config.Image}}')
      case "$ref" in *mongo*|*falkordb*|*qdrant*|*httpd*)
        info "trivy $ref (image id of $name):"
        trivy image --quiet --severity CRITICAL,HIGH --format table "$(inspect "$name" '{{.Image}}')" 2>&1 | tail -25
        ;;
      esac
    done
  else
    unknown "RUN_TRIVY=1 but trivy is not installed on this host"
  fi
else
  info "no fresh scan was run (set RUN_TRIVY=1). A digest comparison is not a scan."
fi

# --------------------------------------------------------------------------- #
section "D. Migrations"
# --------------------------------------------------------------------------- #
if [[ -n "$BACKEND" ]]; then
  list=$(docker exec "$BACKEND" python -m rbac_backend.scripts.migrate_database --list 2>/dev/null)
  if [[ -z "$list" ]]; then
    unknown "migrate_database --list returned nothing inside $BACKEND"
  else
    summary=$(printf '%s' "$list" | docker exec -i "$BACKEND" python -c '
import json, sys, collections
rows = json.load(sys.stdin)
counts = collections.Counter(r["status"] for r in rows)
print("catalogue_and_ledger_rows=%d applied=%d pending=%d applied_not_in_catalogue=%d" % (
    len(rows), counts["applied"], counts["pending"], counts["applied_not_in_catalogue"]))
for r in rows:
    if r["status"] != "applied":
        print("ROW %s %s %s" % (r["version"], r["status"], r["name"]))
for r in rows:
    if r["version"].startswith("20261114_"):
        print("BENCH %s %s" % (r["version"], r["status"]))
' 2>/dev/null)
    info "$(printf '%s\n' "$summary" | head -1)"
    printf '%s\n' "$summary" | grep '^ROW ' | sed 's/^ROW /INFO       not plainly applied: /'
    for version in $BENCH_VERSIONS; do
      status=$(printf '%s\n' "$summary" | awk -v v="$version" '$1=="BENCH" && $2==v {print $3}')
      case "$status" in
        "") pass "Letter Bench migration $version: not in this image's catalogue and not in the ledger (unapplied)" ;;
        pending) pass "Letter Bench migration $version: in this image's catalogue, pending (unapplied)" ;;
        *) fail "Letter Bench migration $version is recorded as '$status' on production" ;;
      esac
    done
  fi
else
  unknown "no backend container; the migration ledger was not read"
fi

# --------------------------------------------------------------------------- #
section "E. Backups"
# --------------------------------------------------------------------------- #
if [[ -d "$BACKUP_ROOT/manifests" ]]; then
  newest=$(ls -1t "$BACKUP_ROOT"/manifests/backup-*.json 2>/dev/null | head -1)
  if [[ -n "$newest" ]]; then
    age_h=$(( ( $(date +%s) - $(date -r "$newest" +%s) ) / 3600 ))
    info "newest backup manifest: $(basename "$newest"), ${age_h} h old"
    [[ $age_h -le 30 ]] && pass "a backup manifest was written in the last 30 hours" || fail "the newest backup manifest is ${age_h} h old"
    if grep -q '"persistence_flush"' "$newest"; then
      info "persistence_flush block: $(tr -d '\n' <"$newest" | sed -n 's/.*"persistence_flush"[^{]*{\([^}]*\)}.*/\1/p' | tr -s ' ')"
    else
      warn "the newest manifest has no persistence_flush block"
    fi
  else
    fail "no backup-*.json manifest under $BACKUP_ROOT/manifests"
  fi
else
  unknown "$BACKUP_ROOT/manifests does not exist or is not readable by this user (set BACKUP_ROOT)"
fi
info "a manifest proves a backup was written, not that it restores. Restore evidence is a disposable restore drill, recorded separately."

# --------------------------------------------------------------------------- #
section "F. Phase 4b readings: claims, arbitration, reviewer roles, skills (P-02 to P-12)"
# --------------------------------------------------------------------------- #
# Plan: docs/superpowers/specs/2026-11-16-claim-bench-phase-4b-hardening.md 8.1.
# Each line is a reading; one that could not be taken says "not_read" and why.
# An unread value is never zero: record it as "Not read" in the assessment.
READER="$PROJECT_DIR/scripts/claim_arbitration_readonly_readings.py"
if [[ -z "$BACKEND" ]]; then
  unknown "no backend container; readings P-02 to P-12 were not taken"
elif [[ ! -f "$READER" ]]; then
  unknown "$READER is not in this checkout; readings P-02 to P-12 were not taken (copy it from main, see section 5 of the assessment)"
else
  readings=$(docker exec -i "$BACKEND" python - <"$READER" 2>/dev/null)
  if [[ -z "$readings" ]]; then
    unknown "the reader printed nothing inside $BACKEND"
  else
    printf '%s\n' "$readings" | sed 's/^/INFO     reading /'
    not_read=$(printf '%s\n' "$readings" | grep -c '"status": "not_read"')
    if [[ "$not_read" == "0" ]]; then pass "every Phase 4b reading was taken"; else unknown "$not_read Phase 4b reading(s) could not be taken; see the lines above"; fi
  fi
fi
GATEWAY=$(docker ps --filter 'label=com.docker.compose.service=gateway' --format '{{.Names}}' | head -1)
if [[ -n "$GATEWAY" ]]; then
  # Request counts only; no request line is copied into the report.
  since=2160h
  info "gateway requests in the last 90 days (P-11, P-12), from docker logs --since $since:"
  for pattern in "/api/claims" "/api/claims/[^/ ]*/assess" "POST /api/arbitration/drafts " "/api/arbitration/drafts"; do
    count=$(docker logs --since "$since" "$GATEWAY" 2>&1 | grep -cE "$pattern")
    info "  $pattern: $count"
  done
  info "log retention bounds these counts: a smaller window than 90 days reads as fewer requests, not as no use"
else
  unknown "no running container carries the compose label service=gateway; request counts for P-11 and P-12 were not taken"
fi

printf '\nSUMMARY  pass=%d warn=%d fail=%d unknown=%d\n' "$passes" "$warns" "$fails" "$unknowns"
printf 'Report   %s\n' "$REPORT"
printf 'Before sharing the report, scan it by value: scripts/evidence_secret_scan.py %s %s\n' "$RUN_DIR" "$ENV_FILE"
[[ $fails -eq 0 ]]
