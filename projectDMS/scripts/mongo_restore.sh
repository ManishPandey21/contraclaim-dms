#!/usr/bin/env bash
# Restore a MongoDB logical dump produced by mongo_backup.sh / production_backup.sh.
#
# R-A8I ran the Gate 8 restore drill and found this script cannot reach the
# database it is documented to restore. `production_backup.sh` already knows that
# docker-internal replica-set hostnames do not resolve on the host and runs
# `mongodump` inside a replica-set container; this script had no counterpart and
# failed with `dial tcp: lookup mongo1 on 127.0.0.53:53: server misbehaving`. The
# drill completed only because the same script was re-run by hand inside
# `contraclaim-stg-mongo1-1`, which no runbook instructs. The production URI has
# the same shape.
#
# Two things are therefore explicit here, and neither is a hostname guess.
#
# 1. WHERE IT RUNS. `RESTORE_EXEC_CONTEXT` is `host`, `compose`, or `auto`.
#    `production_backup.sh` branches on the literal string `mongo1:` appearing in
#    the URI, which is right today and silently wrong the day the compose service
#    is renamed. `auto` performs a real name lookup instead: if every host in the
#    URI resolves on this machine it runs here, and if one does not it runs inside
#    the replica-set container where those names mean something. Either way the
#    chosen context is printed, so an operator reading the log knows which
#    topology the restore actually used.
#
# 2. WHAT IT MAY OVERWRITE. A restore is the one operation that can replace a live
#    database with an old one, and R-A8I recorded a restore racing a running
#    application doubling the permission catalogue. The database target has no
#    default at all - the previous default was `contraclaim`, the production
#    database, so an operator who forgot the variable was aimed at production by
#    this script's own fallback. A production database name or the production
#    replica set is refused unless `ALLOW_PRODUCTION_RESTORE=1` is given
#    deliberately and separately.
#
# This script never stops the application. Stopping it is the runbook's job and is
# stated there; what this script owes is a refusal loud enough that the runbook
# step cannot be skipped by accident.

set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage: MONGO_URI=... MONGO_DB=... mongo_restore.sh <archive.gz>

Required:
  MONGO_URI (or DATABASE_URL)  connection string for the target deployment
  MONGO_DB (or MONGODB_DATABASE)
                               database to restore; there is deliberately no
                               default, because the old one was production

Execution context:
  RESTORE_EXEC_CONTEXT         host | compose | auto      (default: auto)
  MONGO_EXEC_SERVICE           compose service to exec into  (default: mongo1)
  COMPOSE_FILES                compose file flags
  ENV_FILE                     env file passed to docker compose (default: .env)

Production:
  ALLOW_PRODUCTION_RESTORE=1   authorise a restore into the production database
                               or the production replica set
USAGE
}

ROOT_DIR=${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
ENV_FILE=${ENV_FILE:-"$ROOT_DIR/.env"}
COMPOSE_FILES=${COMPOSE_FILES:-"-f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml"}
RESTORE_EXEC_CONTEXT=${RESTORE_EXEC_CONTEXT:-auto}
MONGO_EXEC_SERVICE=${MONGO_EXEC_SERVICE:-mongo1}
ALLOW_PRODUCTION_RESTORE=${ALLOW_PRODUCTION_RESTORE:-}

#: Names that mean production. `contraclaim` is the production database; `rs0` is
#: the production replica set. A staging database name reached over the production
#: replica set is still a production connection, so both are checked.
PRODUCTION_DATABASES=${PRODUCTION_DATABASES:-"contraclaim"}
PRODUCTION_REPLICA_SET=${PRODUCTION_REPLICA_SET:-"rs0"}

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

# Absolute before anything else. The compose branch `cd`s to ROOT_DIR to run
# `docker compose`, and a relative archive path silently stops resolving at that
# point - the redirect would fail after the production checks had already passed.
ARCHIVE=$1
if [[ "${ARCHIVE}" != /* && ! "${ARCHIVE}" =~ ^[A-Za-z]:[/\\] ]]; then
  ARCHIVE="$PWD/${ARCHIVE}"
fi

MONGO_URI=${MONGO_URI:-${DATABASE_URL:-}}
MONGO_DB=${MONGO_DB:-${MONGODB_DATABASE:-}}

if [[ -z "${MONGO_URI}" ]]; then
  echo "MONGO_URI or DATABASE_URL is required" >&2
  exit 1
fi

if [[ -z "${MONGO_DB}" ]]; then
  echo "MONGO_DB or MONGODB_DATABASE is required; this script has no default target." >&2
  echo "It used to default to the production database, which is how a forgotten" >&2
  echo "variable became a production restore." >&2
  exit 1
fi

# --------------------------------------------------------------------------- #
# Production refusal - before anything is read, connected to or written
# --------------------------------------------------------------------------- #

production_reason=""
for name in $PRODUCTION_DATABASES; do
  if [[ "${MONGO_DB}" == "${name}" ]]; then
    production_reason="database ${MONGO_DB} is the production database"
    break
  fi
done

if [[ -z "$production_reason" && "${MONGO_URI}" == *"replicaSet=${PRODUCTION_REPLICA_SET}"* ]]; then
  production_reason="the URI names the production replica set ${PRODUCTION_REPLICA_SET}"
fi

if [[ -n "$production_reason" ]]; then
  if [[ "${ALLOW_PRODUCTION_RESTORE}" != "1" && "${ALLOW_PRODUCTION_RESTORE}" != "true" ]]; then
    echo "REFUSED: ${production_reason}." >&2
    echo "Set ALLOW_PRODUCTION_RESTORE=1 to authorise a production restore, and stop the" >&2
    echo "application first: a restore racing a running application duplicated the" >&2
    echo "permission catalogue in R-A8I." >&2
    exit 1
  fi
  echo "WARNING: production restore explicitly authorised (${production_reason})."
fi

# --------------------------------------------------------------------------- #
# The archive
# --------------------------------------------------------------------------- #

if [[ ! -f "${ARCHIVE}" ]]; then
  echo "Archive not found: ${ARCHIVE}" >&2
  exit 1
fi

if command -v gzip >/dev/null 2>&1 && ! gzip -t "${ARCHIVE}"; then
  echo "Archive failed gzip integrity check: ${ARCHIVE}" >&2
  exit 1
fi

# --------------------------------------------------------------------------- #
# Execution context
# --------------------------------------------------------------------------- #

uri_hosts() {
  # mongodb://[user:pass@]h1:p1,h2:p2/[db][?options] -> h1 h2
  local authority=${MONGO_URI#*://}
  authority=${authority%%/*}
  authority=${authority%%\?*}
  authority=${authority##*@}
  local pair host
  local IFS=','
  for pair in $authority; do
    host=${pair%%:*}
    [[ -n "$host" ]] && printf '%s\n' "$host"
  done
}

#: 0 resolves, 1 does not, 2 no resolver available. The third case is not folded
#: into the second: "cannot tell" and "definitely not local" send the restore to
#: different places, and guessing between them is how a host restore ends up
#: aimed at a container that does not exist.
#: The probe answers with a word, not an exit code. An interpreter that is on
#: PATH but cannot run - a Windows Store `python3` shim, a broken venv shebang -
#: also exits non-zero, and reading that as "the host does not resolve" sends a
#: perfectly local restore into a container. Only a literal YES or NO is an
#: answer; anything else means try the next resolver.
_RESOLVE_PROBE='
import socket, sys
try:
    socket.getaddrinfo(sys.argv[1], None)
except Exception:
    print("NO")
else:
    print("YES")
'

resolves_locally() {
  local host=$1 answer python_bin
  for python_bin in python3 python; do
    command -v "$python_bin" >/dev/null 2>&1 || continue
    answer=$("$python_bin" -c "$_RESOLVE_PROBE" "$host" 2>/dev/null || true)
    case "$answer" in
      YES) return 0 ;;
      NO) return 1 ;;
    esac
  done
  if command -v getent >/dev/null 2>&1; then
    if getent hosts "$host" >/dev/null 2>&1; then return 0; fi
    return 1
  fi
  return 2
}

resolve_context() {
  local host unresolved=0 status
  while read -r host; do
    [[ -z "$host" ]] && continue
    set +e
    resolves_locally "$host"
    status=$?
    set -e
    case "$status" in
      0) ;;
      1)
        unresolved=1
        echo "Host '${host}' from the URI does not resolve on this machine." >&2
        ;;
      *)
        echo "RESTORE_EXEC_CONTEXT=auto needs a name resolver (getent or python) and" >&2
        echo "found neither. Set RESTORE_EXEC_CONTEXT=host or =compose explicitly." >&2
        exit 1
        ;;
    esac
  done < <(uri_hosts)

  if [[ "$unresolved" -eq 1 ]]; then
    printf 'compose'
  else
    printf 'host'
  fi
}

case "${RESTORE_EXEC_CONTEXT}" in
  host|compose) context=${RESTORE_EXEC_CONTEXT} ;;
  auto) context=$(resolve_context) ;;
  *)
    echo "RESTORE_EXEC_CONTEXT must be 'host', 'compose' or 'auto' (got '${RESTORE_EXEC_CONTEXT}')" >&2
    exit 1
    ;;
esac

echo "Restoring ${MONGO_DB} from ${ARCHIVE} (execution context: ${context})"

case "$context" in
  host)
    mongorestore \
      --uri="${MONGO_URI}" \
      --archive="${ARCHIVE}" \
      --gzip \
      --nsInclude="${MONGO_DB}.*"
    ;;
  compose)
    # The archive lives on the host and the container has no route to it, so it is
    # streamed on stdin: `--archive` with no value reads standard input.
    cd "$ROOT_DIR"
    docker compose --env-file "$ENV_FILE" $COMPOSE_FILES exec -T "${MONGO_EXEC_SERVICE}" \
      mongorestore \
        --uri="${MONGO_URI}" \
        --archive \
        --gzip \
        --nsInclude="${MONGO_DB}.*" \
      <"${ARCHIVE}"
    ;;
esac

echo "MongoDB restore completed: ${MONGO_DB} from ${ARCHIVE} (${context})"
