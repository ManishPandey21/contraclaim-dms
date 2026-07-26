#!/usr/bin/env bash
set -euo pipefail

MONGO_URI=${MONGO_URI:-${DATABASE_URL:-}}
MONGO_DB=${MONGO_DB:-${MONGODB_DATABASE:-contraclaim}}
# H5: /health/operations (services/operations_health.py) verifies backup
# freshness by globbing $BACKUP_ROOT/mongo/*.archive.gz. Default the standalone
# run to that location so an ad-hoc dump also satisfies the health contract;
# production_backup.sh passes BACKUP_DIR explicitly and is unaffected.
BACKUP_ROOT=${BACKUP_ROOT:-/var/backups/contractdms}
BACKUP_DIR=${BACKUP_DIR:-"${BACKUP_ROOT}/mongo"}
RETENTION_DAYS=${RETENTION_DAYS:-14}

if [[ -z "${MONGO_URI}" ]]; then
  echo "MONGO_URI or DATABASE_URL is required" >&2
  exit 1
fi

mkdir -p "${BACKUP_DIR}"
STAMP=${STAMP:-$(date '+%Y%m%d-%H%M%S')}
ARCHIVE="${BACKUP_DIR}/${MONGO_DB}-${STAMP}.archive.gz"

mongodump \
  --uri="${MONGO_URI}" \
  --db="${MONGO_DB}" \
  --archive="${ARCHIVE}" \
  --gzip

if ! find "${BACKUP_DIR}" -type f -name "${MONGO_DB}-*.archive.gz" -mtime "+${RETENTION_DAYS}" -delete 2>/dev/null; then
  # A legacy archive may have a different owner. The new backup is valid and
  # must not be reported as failed merely because retention needs operator
  # cleanup; this condition is visible on stderr for follow-up.
  echo "WARN: unable to remove one or more expired MongoDB backup archives; retention cleanup is required" >&2
fi

echo "MongoDB backup written to ${ARCHIVE}"
