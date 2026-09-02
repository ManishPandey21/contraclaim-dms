#!/usr/bin/env bash
# Archive one Docker volume.
#
# Extracted verbatim from production_backup.sh's `backup_volume` so the same
# code path a production backup runs can be exercised by the recovery drill
# (scripts/falkordb_recovery_drill.sh). A backup procedure that is only ever
# executed by itself cannot be tested.
#
# Usage:
#   scripts/backup_volume.sh <volume> <archive-path> [required-pattern ...]
set -euo pipefail

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
