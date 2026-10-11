#!/usr/bin/env bash
# Release preflight: what must hold BEFORE a deploy restarts anything.
#
# Read-only. Run after the release manifest is written (guide step 6b) and
# before any container is recreated (step 7). A deploy is refused while a
# document-worker canary runs that verification would fail (undeclared under
# FULL/BACKEND_ONLY, or not on the image it is held to): post_deploy_verify.sh
# would otherwise fail only after production had already changed. See
# `release_manifest.py preflight`.
#
#   DEPLOY_SCOPE=FULL RELEASE_MANIFEST="$MANIFESTS/$RELEASE.json" scripts/release_preflight.sh
#
# Any failure to establish the facts (no scope, no manifest, containers that
# cannot be listed) is a failure, never a pass.
set -euo pipefail

ROOT_DIR=${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
ENV_FILE=${ENV_FILE:-"$ROOT_DIR/.env"}
# Production runs exactly these two files (CLAUDE.md, Deployment).
COMPOSE_FILES=${COMPOSE_FILES:-"-f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml"}
DEPLOY_SCOPE=${DEPLOY_SCOPE:-}
RELEASE_MANIFEST=${RELEASE_MANIFEST:-}
PYTHON_BIN=${PYTHON_BIN:-$(command -v python3 || command -v python || true)}

if [[ -z "$PYTHON_BIN" ]]; then
  echo "FAIL no python interpreter for the release preflight (set PYTHON_BIN)"
  exit 1
fi

cd "$ROOT_DIR"
running_json=$(mktemp)
trap 'rm -f "$running_json"' EXIT

# Fails the script if compose cannot answer: an unlisted canary is not "none".
canary_ids=$(docker compose --env-file "$ENV_FILE" $COMPOSE_FILES ps -q document-worker-canary)

{
  printf '{"document-worker-canary":['
  first_row=1
  for cid in $canary_ids; do
    [[ $first_row -eq 1 ]] || printf ','
    first_row=0
    printf '{"id":"%s","image_id":"%s"}' "$cid" "$(docker inspect -f '{{.Image}}' "$cid")"
  done
  printf ']}'
} >"$running_json"

"$PYTHON_BIN" "$ROOT_DIR/scripts/release_manifest.py" preflight \
  --scope "$DEPLOY_SCOPE" --target "$RELEASE_MANIFEST" --running "$running_json"
