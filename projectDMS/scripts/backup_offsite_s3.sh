#!/usr/bin/env bash
#
# Offsite backup (Week 4.1)
#
# Runs the existing local backup (scripts/production_backup.sh - MongoDB +
# Docker volumes) and then mirrors the backup root to S3 so backups survive
# host loss. Intended to run from host cron (see docs/OPERATIONS.md).
#
# Required env (in addition to those used by production_backup.sh):
#   AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_REGION
#   BACKUP_S3_BUCKET           target bucket for backups (NOT the uploads bucket)
# Optional:
#   BACKUP_S3_PREFIX           default "contraclaim/backups"
#   BACKUP_ROOT                default /var/backups/contractdms (must match production_backup.sh)
#
set -euo pipefail

ROOT_DIR=${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
ENV_FILE=${ENV_FILE:-"$ROOT_DIR/.env"}

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

BACKUP_ROOT=${BACKUP_ROOT:-/var/backups/contractdms}
BACKUP_S3_PREFIX=${BACKUP_S3_PREFIX:-contraclaim/backups}

: "${BACKUP_S3_BUCKET:?BACKUP_S3_BUCKET is required}"
command -v aws >/dev/null 2>&1 || { echo "aws CLI is required" >&2; exit 1; }

log() { printf '[offsite-backup %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

# 1) Produce the local backup (Mongo dump + volume tarballs + manifest).
log "running local backup"
BACKUP_ROOT="$BACKUP_ROOT" bash "$ROOT_DIR/scripts/production_backup.sh"

# 2) Mirror to S3. Remote retention is enforced by an S3 lifecycle rule
#    (see docs/OPERATIONS.md); local retention is handled by production_backup.sh.
HOST_TAG=${HOST_TAG:-$(hostname -s 2>/dev/null || hostname)}
DEST="s3://${BACKUP_S3_BUCKET}/${BACKUP_S3_PREFIX}/${HOST_TAG}"
log "syncing ${BACKUP_ROOT} -> ${DEST}"
aws s3 sync "$BACKUP_ROOT" "$DEST" --only-show-errors

log "offsite backup complete"
