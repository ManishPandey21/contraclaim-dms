# shellcheck shell=bash
#
# Delete expired backup artefacts, and say which ones could not be deleted.
#
# `production_backup.sh` ran
#
#     find "$BACKUP_ROOT" -type f -mtime "+$RETENTION_DAYS" -delete 2>/dev/null
#
# and, on failure, printed "retention cleanup is required" - a warning with no
# subject. R-A9G reported it nightly and R-A9H had to reproduce it by hand to
# learn what it meant: `/var/backups/contractdms/tag-migration-20260710-005605/`
# is `root:root 755` and the backup runs as `ubuntu`, so unlink is denied in
# that directory for every file in it, whoever owns the file.
#
# `2>/dev/null` is what made the warning unactionable, so it is gone. The errors
# `find` already produces name the exact paths; they are forwarded instead of
# discarded. The outcome is still a warning and never an abort: an artefact
# another account owns must not invalidate a backup that completed.
retention_prune() {
  local root=$1 days=$2 errors status=0

  errors=$(find "$root" -type f -mtime "+$days" -delete 2>&1 >/dev/null) || status=1
  if [ "$status" -eq 0 ] && [ -z "$errors" ]; then
    return 0
  fi

  echo "WARN: unable to remove one or more expired backup artifacts; retention cleanup is required" >&2
  if [ -n "$errors" ]; then
    printf '%s\n' "$errors" | sed 's/^/WARN:   /' >&2
  fi
  return 1
}
