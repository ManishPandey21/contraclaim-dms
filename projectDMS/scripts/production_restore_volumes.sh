#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
# shellcheck source=scripts/lib/docker_paths.sh
. "$ROOT_DIR/scripts/lib/docker_paths.sh"
ENV_FILE=${ENV_FILE:-"$ROOT_DIR/.env"}
APPLY=false

usage() {
  cat <<'EOF'
Usage:
  scripts/production_restore_volumes.sh --apply <volume-name> <archive.tar.gz>

This restores one Docker volume from an archive produced by production_backup.sh.
The target volume contents are removed before extraction. Stop affected services
before running this command.
EOF
}

if [[ "${1:-}" == "--apply" ]]; then
  APPLY=true
  shift
fi

if [[ $# -ne 2 || "$APPLY" != "true" ]]; then
  usage >&2
  exit 1
fi

volume=$1
archive=$2

cd "$ROOT_DIR"

if [[ ! -f "$archive" ]]; then
  echo "Archive not found: $archive" >&2
  exit 1
fi

if ! tar -tzf "$archive" >/dev/null; then
  echo "Archive is not a readable gzip tarball: $archive" >&2
  exit 1
fi

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

echo "Restoring $archive into Docker volume $volume"
docker_run --rm \
  -v "${volume}:/target" \
  -v "$(docker_host_path "$(cd "$(dirname "$archive")" && pwd)"):/backup:ro" \
  busybox sh -c "rm -rf /target/* /target/.[!.]* /target/..?* 2>/dev/null || true; cd /target && tar -xzf /backup/$(basename "$archive")"

echo "Restore complete for volume $volume"
