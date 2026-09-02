#!/usr/bin/env bash
# Archive one Docker volume, and prove the archive is worth keeping.
#
# Extracted from production_backup.sh's `backup_volume` so the same code path a
# production backup runs can be exercised by the recovery drill
# (scripts/falkordb_recovery_drill.sh). A backup procedure that is only ever
# executed by itself cannot be tested.
#
# Usage:
#   scripts/backup_volume.sh <volume> <archive-path> [required-pattern ...]
#   scripts/backup_volume.sh --verify <archive-path> [required-pattern ...]
#
# Required patterns are shell globs matched against the archive's entry list.
# When any are given, the archive must contain an entry matching at least one
# of them or the backup fails. `tar` exiting 0 over an empty directory is not a
# backup: that is exactly how the FalkorDB graph went unprotected while every
# nightly run reported success.
set -euo pipefail

verify_archive() {
  local archive=$1
  shift
  local patterns=("$@")

  if [[ ! -f "$archive" ]]; then
    echo "Backup verification failed: no archive at $archive" >&2
    return 1
  fi

  local entries
  if ! entries=$(tar -tzf "$archive"); then
    echo "Backup verification failed: $archive is not a readable gzip tarball" >&2
    return 1
  fi

  if [[ ${#patterns[@]} -eq 0 ]]; then
    return 0
  fi

  local entry pattern
  while IFS= read -r entry; do
    for pattern in "${patterns[@]}"; do
      # shellcheck disable=SC2053
      if [[ "$entry" == $pattern ]]; then
        echo "Backup verification passed: $archive contains $entry"
        return 0
      fi
    done
  done <<<"$entries"

  echo "Backup verification FAILED: $archive contains none of: ${patterns[*]}" >&2
  echo "Archive holds $(printf '%s\n' "$entries" | wc -l) entry/entries:" >&2
  printf '%s\n' "$entries" | sed 's/^/  /' >&2
  echo "The volume this archived holds no persisted state. Check that the" >&2
  echo "service actually writes its snapshot inside the mounted volume." >&2
  return 1
}

if [[ "${1:-}" == "--verify" ]]; then
  shift
  if [[ $# -lt 1 ]]; then
    echo "usage: $0 --verify <archive-path> [required-pattern ...]" >&2
    exit 2
  fi
  verify_archive "$@"
  exit $?
fi

ROOT_DIR=${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
# shellcheck source=scripts/lib/docker_paths.sh
. "$ROOT_DIR/scripts/lib/docker_paths.sh"

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <volume> <archive-path> [required-pattern ...]" >&2
  exit 2
fi

volume=$1
archive=$2
shift 2

archive_dir=$(cd "$(dirname "$archive")" && pwd)
archive_name=$(basename "$archive")

echo "Backing up Docker volume $volume to $archive_dir/$archive_name"
docker_run --rm \
  -v "${volume}:/source:ro" \
  -v "$(docker_host_path "$archive_dir"):/backup" \
  busybox sh -c "cd /source && tar -czf /backup/${archive_name} ."

verify_archive "$archive_dir/$archive_name" "$@"
