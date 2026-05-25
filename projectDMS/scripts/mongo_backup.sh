#!/usr/bin/env bash
set -euo pipefail

MONGO_URI=${MONGO_URI:-${DATABASE_URL:-}}
MONGO_DB=${MONGO_DB:-${MONGODB_DATABASE:-contraclaim}}
BACKUP_DIR=${BACKUP_DIR:-./backups/mongo}
RETENTION_DAYS=${RETENTION_DAYS:-14}

if [[ -z "${MONGO_URI}" ]]; then
  echo "MONGO_URI or DATABASE_URL is required" >&2
  exit 1
fi

mkdir -p "${BACKUP_DIR}"
STAMP=$(date '+%Y%m%d-%H%M%S')
ARCHIVE="${BACKUP_DIR}/${MONGO_DB}-${STAMP}.archive.gz"

mongodump \
  --uri="${MONGO_URI}" \
  --db="${MONGO_DB}" \
  --archive="${ARCHIVE}" \
  --gzip

find "${BACKUP_DIR}" -type f -name "${MONGO_DB}-*.archive.gz" -mtime "+${RETENTION_DAYS}" -delete

echo "MongoDB backup written to ${ARCHIVE}"
