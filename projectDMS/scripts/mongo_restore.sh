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

Verification:
  RESTORE_VERIFY               1 | 0   compare what the archive offered against
                               what landed (default: 1). 0 restores without
                               certifying anything, and says so.
  RESTORE_EXPECTED_DOCS        operator-declared document count for the target
                               namespaces; overrides the archive's own accounting
  RESTORE_ALLOW_EMPTY=1        declare that this archive is genuinely empty. An
                               undeclared zero-document restore is a FAILURE,
                               because "empty archive" and "wrong target" look
                               identical from here.

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
RESTORE_VERIFY=${RESTORE_VERIFY:-1}
RESTORE_EXPECTED_DOCS=${RESTORE_EXPECTED_DOCS:-}
RESTORE_ALLOW_EMPTY=${RESTORE_ALLOW_EMPTY:-}
ALLOW_PRODUCTION_RESTORE=${ALLOW_PRODUCTION_RESTORE:-}

#: Names that mean production. `contraclaim` is the production database; `rs0` is
#: the production replica set. A staging database name reached over the production
#: replica set is still a production connection, so both are checked.
PRODUCTION_DATABASES=${PRODUCTION_DATABASES:-"contraclaim"}
PRODUCTION_REPLICA_SET=${PRODUCTION_REPLICA_SET:-"rs0"}
#: The production replica-set members by container hostname. A restore aimed at
#: one of these with `directConnection=true` names no replica set, so the option
#: check above is blind to it.
PRODUCTION_HOSTS=${PRODUCTION_HOSTS:-"mongo1 mongo2 mongo3 contraclaim-mongo1-1 contraclaim-mongo2-1 contraclaim-mongo3-1"}

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

# Validated HERE, with the other inputs, and not beside the verification that
# consumes it. A malformed expectation discovered after the restore is a refusal
# arriving one irreversible operation too late.
if [[ -n "${RESTORE_EXPECTED_DOCS}" && ! "${RESTORE_EXPECTED_DOCS}" =~ ^[0-9]+$ ]]; then
  echo "RESTORE_EXPECTED_DOCS must be a document count (got a non-number)" >&2
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

# MongoDB connection-string options are case-insensitive, so the refusal has to
# be too. `replicaSet=rs0` was matched literally and `?replicaset=rs0` - the same
# connection, accepted by every driver - walked straight past it. Both sides are
# lowercased before the comparison.
uri_lower=${MONGO_URI,,}
rs_lower=${PRODUCTION_REPLICA_SET,,}

if [[ -z "$production_reason" && "${uri_lower}" == *"replicaset=${rs_lower}"* ]]; then
  production_reason="the URI names the production replica set ${PRODUCTION_REPLICA_SET}"
fi

# A production member addressed directly carries no replica-set option at all -
# `directConnection=true` against `mongo1` is a production connection that the
# option check above cannot see.
#
# The compose SERVICE name is the same in both stacks (`mongo1` is a staging
# member too, on the staging network), so a host match alone cannot separate
# them. What can: a URI that names its replica set has already been checked
# against the production one above and passed. So this rule fires only on a URI
# that names NO replica set - the genuinely ambiguous case - and its remedy is
# to say which set is meant rather than to widen the list.
if [[ -z "$production_reason" && "${uri_lower}" != *"replicaset="* ]]; then
  for host in $PRODUCTION_HOSTS; do
    host_lower=${host,,}
    [[ -z "$host_lower" ]] && continue
    if [[ "${uri_lower}" == *"@${host_lower}:"* || "${uri_lower}" == *"//${host_lower}:"*        || "${uri_lower}" == *",${host_lower}:"* || "${uri_lower}" == *"@${host_lower}/"*        || "${uri_lower}" == *"//${host_lower}/"* ]]; then
      production_reason="the URI addresses ${host} and names no replica set, so it cannot be told apart from the production member of that name; add replicaSet=<set> to say which stack is meant"
      break
    fi
  done
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

append_unique() {
  # $1 = current space-delimited list, $2 = candidate. Prints the new list.
  case " $1 " in
    *" $2 "*) printf '%s' "$1" ;;
    *) printf '%s' "${1:+$1 }$2" ;;
  esac
}

# One invocation shape for both passes. `$@` carries the pass-specific flags, so
# the inventory pass and the real restore cannot drift apart in how they reach
# the deployment - which is the whole point of an inventory taken with the same
# tool through the same route.
run_mongorestore() {
  case "$context" in
    host)
      mongorestore --uri="${MONGO_URI}" --archive="${ARCHIVE}" --gzip "$@"
      ;;
    compose)
      # The archive lives on the host and the container has no route to it, so it is
      # streamed on stdin: `--archive` with no value reads standard input.
      # The `cd` is contained: every caller runs this inside a command
      # substitution, which is a subshell.
      cd "$ROOT_DIR"
      docker compose --env-file "$ENV_FILE" $COMPOSE_FILES exec -T "${MONGO_EXEC_SERVICE}" \
        mongorestore --uri="${MONGO_URI}" --archive --gzip "$@" \
        <"${ARCHIVE}"
      ;;
  esac
}

run_restore() {
  run_mongorestore --nsInclude="${MONGO_DB}.*"
}

# The archive's own inventory, read BEFORE anything is written.
#
# `--dryRun -v` with NO `--nsInclude` makes `mongorestore` read the archive's
# prelude and name every namespace it carries:
#
#   archive prelude `contraclaim_staging.permissions`
#
# That is a genuine pre-restore inventory and it is what catches a restore aimed
# at the wrong database: the archive offers collections, the restore applies
# none, and the two disagree. Measured against mongo:8.0.
#
# Document counts are NOT available here - a dry run reports `0 document(s)
# restored successfully` by construction, and a mongodump archive does not carry
# per-collection counts in its prelude. That is a property of the format, not an
# omission, and it is why the document axis below is reconciled from the real
# pass's own accounting instead.
archive_namespaces() {
  local output
  output=$(run_mongorestore --dryRun -v 2>&1) || return 1
  local line names=""
  while IFS= read -r line; do
    if [[ "$line" =~ archive\ prelude\ ([^[:space:]]+) ]]; then
      names=$(append_unique "$names" "${BASH_REMATCH[1]//\`/}")
    fi
  done <<<"$output"
  printf '%s' "$names"
}

# --------------------------------------------------------------------------- #
# The restore, and then what it actually did
# --------------------------------------------------------------------------- #
#
# F-A8M-3. The Gate 8 idempotency run reported
#
#   0 document(s) restored successfully. 222 document(s) failed to restore.
#
# and this script printed "MongoDB restore completed" and exited 0, because
# duplicate-key failures are not fatal to `mongorestore` and the script read
# only its exit status. That is the Qdrant 401 vector-loss shape again: a
# success reported for a step that was skipped.
#
# Exit status is now a necessary condition and not a sufficient one. What the
# archive offered and what landed are counted separately and compared, and the
# comparison - not the log prose - decides the exit code.
#
# `mongorestore` writes its progress and its summary to stderr, so both streams
# are captured. The output is still shown to the operator afterwards: a
# verification that hides the tool's own diagnostics is worse than none.
#
# Captured into a variable rather than a temporary file, for two reasons. The
# tool's diagnostics can carry the connection URI, and a restore is frequently
# run as root - a world-readable `/tmp` copy of that is a secret leak this
# script would be creating itself. And the parsing below is pure bash, so this
# file needs no `grep`, `sed`, `awk` or `mktemp` on PATH, which matters because
# the execution-context tests deliberately run it against a PATH holding only
# the directories they built.
#
# What IS printed is redacted first. Capturing the output into a variable keeps
# it off the disk; it does not keep it out of the log an operator pipes to a
# file, and release evidence is exactly such a file. `redact_userinfo` removes
# the `user:password@` half of any URI in the text, by value, before anything is
# echoed - the lesson `compose config leaks secrets into evidence` records.

redact_userinfo() {
  # mongodb://user:pass@host -> mongodb://***:***@host, for every occurrence.
  local text=$1 out="" rest="$1" before scheme
  out=""
  while [[ "$rest" =~ ([a-zA-Z][a-zA-Z0-9+.-]*://)([^/@[:space:]]+)@ ]]; do
    scheme=${BASH_REMATCH[1]}
    before=${rest%%"${scheme}${BASH_REMATCH[2]}@"*}
    out+="${before}${scheme}***:***@"
    rest=${rest#*"${scheme}${BASH_REMATCH[2]}@"}
  done
  printf '%s' "${out}${rest}"
}

# The archive's inventory, taken BEFORE anything is written. Best effort by
# design: an older `mongorestore` without `--dryRun` simply yields nothing here,
# and the verification says so rather than pretending it had an inventory.
archive_collections=""
archive_inventory_source="unavailable (mongorestore reported no archive prelude)"
if [[ "${RESTORE_VERIFY}" != "0" && "${RESTORE_VERIFY}" != "false" ]]; then
  set +e
  archive_collections=$(archive_namespaces)
  set -e
  [[ -n "$archive_collections" ]] && archive_inventory_source="archive prelude (pre-restore dry run)"
fi

set +e
restore_output=$(run_restore 2>&1)
restore_status=$?
set -e

printf '%s\n' "$(redact_userinfo "$restore_output")"

if [[ "$restore_status" -ne 0 ]]; then
  echo "REFUSED: mongorestore exited ${restore_status}; nothing is certified." >&2
  exit "$restore_status"
fi

if [[ "${RESTORE_VERIFY}" == "0" || "${RESTORE_VERIFY}" == "false" ]]; then
  echo "STATUS=UNVERIFIED (RESTORE_VERIFY=0): the restore ran and nothing was checked." >&2
  echo "MongoDB restore ran, UNVERIFIED: ${MONGO_DB} from ${ARCHIVE} (${context})"
  exit 0
fi

#: `N document(s) restored successfully. M document(s) failed to restore.`
#: Both numbers come from one line, so a version that renames one renames the
#: other and the parse fails as a unit rather than half-succeeding.
SUMMARY_RE='([0-9]+) document\(s\) restored successfully\.[[:space:]]+([0-9]+) document\(s\) failed to restore'
#: Two different namespace inventories, deliberately read from two different
#: lines. `reading metadata for <ns> from archive ...` is what the archive
#: offered; `finished restoring <ns> (...)` is what was applied. Reading both
#: from the same line would make the comparison below vacuous - a collection the
#: archive carried and the restore never reached would simply be absent from
#: both sides and the parity would still say YES.
OFFERED_RE='reading metadata for ([^ ]+) from'
APPLIED_RE='finished restoring ([^ ]+)'
#: Gate 8's restore drill turns on `uq_permissions_name` surviving, so index work
#: is reported too. `mongorestore` says one of two things per collection, and
#: both are recorded: an index that was restored, and a collection that had none.
INDEX_RESTORED_RE='restoring indexes for collection ([^ ]+)'
NO_INDEX_RE='no indexes to restore for collection ([^ ]+)'

restored_docs=""
failed_docs=0
expected_collections=""
restored_collections=""
indexed_collections=""

while IFS= read -r line; do
  if [[ "$line" =~ $SUMMARY_RE ]]; then
    restored_docs=${BASH_REMATCH[1]}
    failed_docs=${BASH_REMATCH[2]}
  fi
  # The real tool quotes namespaces in backticks and an older one does not, so
  # they are stripped on both sides. Comparing a quoted name against an unquoted
  # one would report every collection as missing.
  if [[ "$line" =~ $OFFERED_RE ]]; then
    expected_collections=$(append_unique "$expected_collections" "${BASH_REMATCH[1]//\`/}")
  fi
  if [[ "$line" =~ $APPLIED_RE ]]; then
    restored_collections=$(append_unique "$restored_collections" "${BASH_REMATCH[1]//\`/}")
  fi
  if [[ "$line" =~ $INDEX_RESTORED_RE ]]; then
    indexed_collections=$(append_unique "$indexed_collections" "${BASH_REMATCH[1]//\`/}")
  fi
done <<<"$restore_output"

# A namespace may legally contain a glob character, and these lists are split by
# word rather than iterated as an array. `set -f` for the duration so a
# collection called `a*` cannot path-expand into whatever is in the working
# directory. (The `case` membership tests below are quoted and were always safe.)
set -f

restored_collection_count=0
for _ns in $restored_collections; do
  restored_collection_count=$((restored_collection_count + 1))
done

# What the archive offered, in preference order, each source named in the
# evidence so a reader knows which one answered:
#
#   1. the pre-restore dry run's `archive prelude` lines - a genuine inventory,
#      taken before anything was written, and the only source that can see a
#      collection the restore never reached because it was aimed elsewhere;
#   2. the real pass's `reading metadata for` lines - what the archive offered
#      WITHIN the target namespace, so blind to a wrong `--nsInclude`;
#   3. the restore's own progress. This compares a set against itself and proves
#      nothing about collections; it is a floor, and it says so.
if [[ -n "$archive_collections" ]]; then
  expected_collections=$archive_collections
  expected_collection_source=$archive_inventory_source
elif [[ -n "$expected_collections" ]]; then
  expected_collection_source="restore metadata (post-restore; blind to a wrong --nsInclude)"
else
  expected_collections=$restored_collections
  expected_collection_source="restore progress only - NOT an independent inventory"
fi

# The dry-run inventory covers the whole archive; the restore is bounded to one
# database. Compare like with like, and keep what was excluded so the wrong-target
# case can be diagnosed rather than merely failed.
in_target=""
out_of_target=""
for ns in $expected_collections; do
  if [[ "$ns" == "${MONGO_DB}."* ]]; then
    in_target=$(append_unique "$in_target" "$ns")
  else
    out_of_target=$(append_unique "$out_of_target" "$ns")
  fi
done
expected_collections=$in_target

missing_collections=""
for ns in $expected_collections; do
  case " $restored_collections " in
    *" $ns "*) ;;
    *) missing_collections="${missing_collections}${ns} " ;;
  esac
done
missing_collections=${missing_collections% }

set +f

if [[ -z "$restored_docs" ]]; then
  {
    echo "STATUS=UNVERIFIED"
    echo "mongorestore exited 0 but produced no document summary this script can read,"
    echo "so there is no evidence the archive was applied. A restore is not certified"
    echo "by an exit code. Re-run with a mongorestore whose summary this script"
    echo "understands, or set RESTORE_VERIFY=0 to state deliberately that this run"
    echo "certifies nothing."
  } >&2
  exit 4
fi

# What the archive offered for these namespaces. `mongorestore` accounts for
# every document it read as either restored or failed, so the two together are
# the inventory it actually saw - which is precisely the number R-A8M's run
# reported (0 + 222) while calling itself complete. An operator who knows the
# expected count may state it instead, and then it is the archive's accounting
# that has to match.
if [[ -n "${RESTORE_EXPECTED_DOCS}" ]]; then
  if ! [[ "${RESTORE_EXPECTED_DOCS}" =~ ^[0-9]+$ ]]; then
    echo "RESTORE_EXPECTED_DOCS must be a document count (got a non-number)" >&2
    exit 1
  fi
  expected_docs=${RESTORE_EXPECTED_DOCS}
  expected_source="operator-declared"
else
  expected_docs=$((restored_docs + failed_docs))
  expected_source="archive accounting (restored + failed)"
fi

status="OK"
reasons=()

if [[ "$failed_docs" -ne 0 ]]; then
  status="FAILED"
  reasons+=("${failed_docs} document(s) failed to restore")
fi

if [[ "$restored_docs" -ne "$expected_docs" ]]; then
  status="FAILED"
  reasons+=("restored ${restored_docs} of ${expected_docs} expected document(s)")
fi

if [[ "$expected_docs" -eq 0 && "$restored_docs" -eq 0 ]]; then
  if [[ -n "$out_of_target" && -z "$expected_collections" ]]; then
    # The pre-restore inventory answers what the document counts cannot: the
    # archive DOES carry collections, and not one of them is in the database
    # this restore was aimed at. That is a wrong target, diagnosed rather than
    # merely refused - and it is why the inventory pass exists.
    status="FAILED"
    reasons+=("the archive carries no collection in '${MONGO_DB}'. It carries: ${out_of_target}. This restore is aimed at the wrong database.")
  elif [[ "${RESTORE_ALLOW_EMPTY}" == "1" || "${RESTORE_ALLOW_EMPTY}" == "true" ]]; then
    # A genuinely empty archive. Declared, because from the document counts
    # alone it is indistinguishable from a wrong target.
    status="OK_EMPTY"
    reasons=()
  else
    status="FAILED"
    reasons+=("no documents were restored and none were offered; an empty archive is indistinguishable from a wrong --nsInclude, a wrong database or a wrong endpoint. Set RESTORE_ALLOW_EMPTY=1 if this archive is genuinely empty.")
  fi
fi

if [[ -n "$missing_collections" ]]; then
  status="FAILED"
  reasons+=("the archive carried collections the restore never applied: ${missing_collections}")
fi

if [[ "$status" != "OK_EMPTY" && "$restored_collection_count" -eq 0 && "$restored_docs" -gt 0 ]]; then
  status="FAILED"
  reasons+=("documents were reported restored but no collection was named")
fi

if [[ "$status" == "FAILED" ]]; then match=NO; else match=YES; fi

# Printed with builtins, not `cat`: this block has to survive the built-PATH
# layouts the execution-context tests run the script under, where nothing is on
# PATH except the directories those tests created.
echo "--- RESTORE VERIFICATION ---"
echo "EXPECTED_DATABASE=${MONGO_DB}"
echo "EXPECTED_COLLECTIONS=${expected_collections:-<none>}"
echo "EXPECTED_COLLECTION_SOURCE=${expected_collection_source}"
echo "EXPECTED_DOCS=${expected_docs}"
echo "EXPECTED_SOURCE=${expected_source}"
echo "RESTORED_DATABASE=${MONGO_DB}"
echo "RESTORED_COLLECTIONS=${restored_collections:-<none>}"
echo "RESTORED_DOCS=${restored_docs}"
echo "FAILED_DOCS=${failed_docs}"
echo "MISSING_COLLECTIONS=${missing_collections:-<none>}"
echo "EXCLUDED_BY_TARGET=${out_of_target:-<none>}"
echo "COLLECTIONS_WITH_RESTORED_INDEXES=${indexed_collections:-<none>}"
echo "MATCH=${match}"
echo "STATUS=${status}"

if [[ "$status" == "FAILED" ]]; then
  echo "RESTORE FAILED VERIFICATION:" >&2
  for reason in "${reasons[@]}"; do
    echo "  - ${reason}" >&2
  done
  echo "The restore is NOT certified. Do not treat this database as recovered." >&2
  exit 5
fi

echo "MongoDB restore completed: ${MONGO_DB} from ${ARCHIVE} (${context})"
