#!/usr/bin/env bash
# Archive one Docker volume, and prove the archive is worth keeping.
#
# Extracted from production_backup.sh's `backup_volume` so the same code path a
# production backup runs can be exercised by the recovery drill
# (scripts/falkordb_recovery_drill.sh). A backup procedure that is only ever
# executed by itself cannot be tested.
#
# Usage:
#   scripts/backup_volume.sh <volume> <archive-path> [contract ...]
#   scripts/backup_volume.sh --verify <archive-path> [contract ...]
#
# A contract is one of:
#
#   --profile <name>   a semantic check for that kind of volume. The only one
#                      today is `redis-persistence`, used by FalkorDB and
#                      Redis: the archive must carry a usable RDB snapshot or a
#                      usable multi-part AOF directory **at its root**, so it
#                      restores straight into the mounted volume.
#   --require <glob>   every --require must match a non-empty regular file.
#   --any-of  <glob>   at least one --any-of must match a non-empty file.
#   <glob>             a bare pattern is an --any-of, which is what bare
#                      patterns have always meant.
#
# `--require` and `--any-of` are separate flags because they used to be the
# same flag. Multiple bare patterns were an OR while the maintenance runbook
# invoked them as `--verify "$ARCH" '*dump.rdb' '*appendonlydir*'` and read that
# as an AND, so an archive holding an empty `appendonlydir/` and no `dump.rdb`
# verified clean and was kept as the only copy of the graph (R-A8P F2).
#
# The decision itself lives in scripts/validate_backup_archive.py, which
# post_deploy_verify, the nightly backup status check and the recovery drill
# also call. One definition of "valid archive", not four.
set -euo pipefail

ROOT_DIR=${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
VALIDATOR="$ROOT_DIR/scripts/validate_backup_archive.py"
# shellcheck source=scripts/lib/docker_paths.sh
. "$ROOT_DIR/scripts/lib/docker_paths.sh"

usable_python() {
  command -v "$1" >/dev/null 2>&1 && "$1" -c "import sys" >/dev/null 2>&1
}

resolve_python() {
  # An explicit PYTHON_BIN is honoured or refused, never silently replaced: an
  # operator who names an interpreter is usually naming the only one with the
  # right environment, and falling back to another would verify the archive
  # somewhere other than where they meant.
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    if usable_python "$PYTHON_BIN"; then
      printf '%s' "$PYTHON_BIN"
      return 0
    fi
    return 1
  fi

  local candidate
  for candidate in python3 python; do
    if usable_python "$candidate"; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

verify_archive() {
  local archive=$1
  shift

  local python_bin
  if ! python_bin=$(resolve_python); then
    echo "Backup verification failed: no usable python interpreter." >&2
    echo "The archive contract is evaluated by $VALIDATOR; without an" >&2
    echo "interpreter it cannot be evaluated, and an unevaluated contract is" >&2
    echo "not a pass. Set PYTHON_BIN to an interpreter and re-run." >&2
    return 1
  fi

  local args=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --profile|--require|--any-of)
        if [[ $# -lt 2 ]]; then
          echo "Backup verification failed: $1 needs a value" >&2
          return 2
        fi
        args+=("$1" "$2")
        shift 2
        ;;
      --*)
        echo "Backup verification failed: unknown contract flag $1" >&2
        return 2
        ;;
      *)
        args+=(--any-of "$1")
        shift
        ;;
    esac
  done

  # native_path is a no-op on Linux. On a developer machine the interpreter
  # is python.exe, which cannot open a /c/... path MSYS did not rewrite.
  "$python_bin" "$(native_path "$VALIDATOR")" "$(native_path "$archive")" ${args[@]+"${args[@]}"}
}

if [[ "${1:-}" == "--verify" ]]; then
  shift
  if [[ $# -lt 1 ]]; then
    echo "usage: $0 --verify <archive-path> [contract ...]" >&2
    exit 2
  fi
  verify_archive "$@"
  exit $?
fi

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <volume> <archive-path> [contract ...]" >&2
  exit 2
fi

volume=$1
archive=$2
shift 2

archive_dir=$(cd "$(dirname "$archive")" && pwd)
archive_name=$(basename "$archive")

# `docker run -v NAME:/source` silently CREATES a named volume that does not
# exist. Empty, it archives as a valid fresh install under application-volume,
# so a misnamed or deleted volume would read as a healthy backup (R-A8X review).
if ! docker volume inspect "$volume" >/dev/null 2>&1; then
  echo "Backup failed: Docker volume $volume does not exist; refusing to archive a volume docker would create empty." >&2
  exit 1
fi

echo "Backing up Docker volume $volume to $archive_dir/$archive_name"
docker_run --rm \
  -v "${volume}:/source:ro" \
  -v "$(docker_host_path "$archive_dir"):/backup" \
  busybox sh -c "cd /source && tar -czf /backup/${archive_name} ."

verify_archive "$archive_dir/$archive_name" "$@"
