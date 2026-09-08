#!/usr/bin/env bash
# Capture FalkorDB's persistence out of a running container, restore-root shaped.
#
# Production FalkorDB persists into the image WORKDIR (`/FalkorDB`) rather than
# the mounted volume, so the nightly volume archive is 89 bytes of empty
# directory and the graph's only on-host copy lives in the container's writable
# layer. Until the cutover moves persistence to `/data`, every maintenance
# window has to take a rescue archive by hand before production is stopped.
#
# "By hand" is what produced R-A8O's archive: 14.7 MB, entries under a
# `FalkorDB/` prefix, `bin/src/falkordb.so` included. Restoring it into a volume
# mounted at `/data` yields `/data/FalkorDB/dump.rdb`, where the engine does not
# look. It was verified, checksummed, kept - and unrestorable. Size is not the
# signal (R-A8P F4).
#
# This script exists so the shape is produced by code rather than by typing:
#
#   * the persistence directory is READ FROM THE CONTAINER (`CONFIG GET dir`),
#     so it keeps working after the cutover moves it to /data;
#   * `dump.rdb` and `appendonlydir/` are copied to the ROOT of a staging
#     directory and the archive is written with `tar -C "$staging" .`, so its
#     entries are `./dump.rdb` and `./appendonlydir/...` - exactly what
#     `scripts/production_restore_volumes.sh` extracts into the volume root;
#   * nothing else is copied, so the archive is persistence and not a container
#     image;
#   * the result is validated by the shared contract before this script exits
#     zero. An unverified archive is not a rescue archive.
#
# Usage:
#   scripts/falkordb_rescue_archive.sh <container> <archive-path> [--no-bgsave]
#
# The container is read, never written, apart from the BGSAVE that flushes the
# snapshot. It is never stopped, removed or recreated.
set -euo pipefail

ROOT_DIR=${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
# shellcheck source=scripts/lib/docker_paths.sh
. "$ROOT_DIR/scripts/lib/docker_paths.sh"

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <container> <archive-path> [--no-bgsave]" >&2
  exit 2
fi

container=$1
archive=$2
shift 2
bgsave=true
for argument in "$@"; do
  case "$argument" in
    --no-bgsave) bgsave=false ;;
    *) echo "unknown argument: $argument" >&2; exit 2 ;;
  esac
done

if ! docker inspect "$container" >/dev/null 2>&1; then
  echo "No such container: $container" >&2
  exit 1
fi

# The password is read inside the container and never leaves it: it is
# interpolated by the container's own shell, so it appears in no argument list
# on the host and in no output here.
redis_cli() {
  docker_exec "$container" sh -c \
    'PW=$(printf "%s" "${REDIS_ARGS:-}" | sed -n "s/.*--requirepass \([^ ]*\).*/\1/p"); \
     [ -n "$PW" ] || PW="${FALKORDB_PASSWORD:-}"; \
     if [ -n "$PW" ]; then redis-cli --no-auth-warning -a "$PW" '"$1"'; else redis-cli '"$1"'; fi'
}

persistence_dir=$(redis_cli "CONFIG GET dir" | tr -d '\r' | sed -n '2p')
if [[ -z "$persistence_dir" ]]; then
  echo "Could not read the persistence directory from $container." >&2
  echo "Without it this script would have to guess where the graph is, and" >&2
  echo "guessing is what produced an archive rooted at FalkorDB/ instead of ./" >&2
  exit 1
fi
echo "FalkorDB in $container persists into $persistence_dir"

if [[ "$bgsave" == "true" ]]; then
  echo "Flushing the snapshot (BGSAVE) ..."
  redis_cli "BGSAVE" >/dev/null
  saved=false
  for _ in $(seq 1 60); do
    if redis_cli "INFO persistence" | tr -d '\r' | grep -q '^rdb_bgsave_in_progress:0'; then
      saved=true
      break
    fi
    sleep 1
  done
  # Falling through this loop silently would archive whatever dump.rdb happened
  # to be on disk - possibly hours old, and structurally valid enough to pass
  # every check downstream. A stale rescue archive that looks current is worse
  # than no rescue archive, so the timeout is fatal.
  if [[ "$saved" != "true" ]]; then
    echo "BGSAVE did not finish within 60s in $container." >&2
    echo "Archiving now would capture whatever snapshot predates it, and nothing" >&2
    echo "downstream can tell a stale dump.rdb from a current one. Re-run once the" >&2
    echo "save completes, or pass --no-bgsave to archive the existing files" >&2
    echo "deliberately." >&2
    exit 1
  fi
fi

staging=$(mktemp -d "${RESCUE_WORK_DIR:-${TMPDIR:-/tmp}}/falkor-rescue.XXXXXX")
cleanup() { rm -rf "$staging"; }
trap cleanup EXIT

copied=0
# Copy each persistence member to the staging ROOT, never the directory that
# contains them. `docker cp <container>:/FalkorDB "$staging"` would recreate the
# `FalkorDB/` component inside the archive, which is precisely R-A8O's defect.
for member in dump.rdb appendonlydir; do
  # The container side must not be rewritten by MSYS and the host side must be
  # a path the docker client can resolve. Both are no-ops on Linux.
  if docker_cp "${container}:${persistence_dir%/}/${member}" "$(native_path "$staging")/$member" >/dev/null 2>&1; then
    echo "  captured $member"
    copied=$((copied + 1))
  fi
done

if [[ "$copied" -eq 0 ]]; then
  echo "Neither dump.rdb nor appendonlydir exists under $persistence_dir in $container." >&2
  echo "There is nothing to rescue, and an empty archive is worse than none." >&2
  exit 1
fi

archive_dir=$(cd "$(dirname "$archive")" && pwd)
archive_name=$(basename "$archive")
tar -czf "$archive_dir/$archive_name" -C "$staging" .

# A file is not a backup. The same contract post_deploy_verify and the nightly
# backup apply decides this one, so a rescue archive cannot be valid by a
# standard the monitoring does not share.
bash "$ROOT_DIR/scripts/backup_volume.sh" --verify "$archive_dir/$archive_name" --profile redis-persistence

if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$archive_dir/$archive_name"
elif command -v shasum >/dev/null 2>&1; then
  shasum -a 256 "$archive_dir/$archive_name"
fi
