#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <archive.gz>" >&2
  exit 1
fi

MONGO_URI=${MONGO_URI:-${DATABASE_URL:-}}
MONGO_DB=${MONGO_DB:-${MONGODB_DATABASE:-contraclaim}}
ARCHIVE=$1

if [[ -z "${MONGO_URI}" ]]; then
  echo "MONGO_URI or DATABASE_URL is required" >&2
  exit 1
fi

if [[ ! -f "${ARCHIVE}" ]]; then
  echo "Archive not found: ${ARCHIVE}" >&2
  exit 1
fi

if command -v gzip >/dev/null 2>&1 && ! gzip -t "${ARCHIVE}"; then
  echo "Archive failed gzip integrity check: ${ARCHIVE}" >&2
  exit 1
fi

mongorestore \
  --uri="${MONGO_URI}" \
  --archive="${ARCHIVE}" \
  --gzip \
  --nsInclude="${MONGO_DB}.*"

echo "MongoDB restore completed from ${ARCHIVE}"
