# ContraClaim Docker Deployment Update Guide

This guide explains how to update the ContraClaim Docker deployment on the Ubuntu production server.

Use it as a deployment checklist. Do not overwrite production configuration, environment files, uploaded documents, database volumes, backup directories, or runtime data.

> **CURRENT PRODUCTION FACTS (R-A9H, 2026-09-21) — read these first.**
>
> 1. **Branch.** Production tracks `release/contraclaim-rc1`, not `main`. Read
>    `git branch --show-current` on the server; §5 below says `main` and is wrong for now.
> 2. **Compose file set.** `docker-compose.prod.yml` + `docker-compose.mongo-replicaset.yml`.
>    The R-A8Z ClamAV override was **retired** on 2026-09-20; never add it.
> 3. **FalkorDB is in a temporary out-of-band topology** until normalization. Never run a
>    blanket `docker compose up -d`/`start`, and always pass `--no-deps` when recreating
>    `backend`, `contract-worker` or `document-worker` — each `depends_on` the compose
>    `falkordb` service, which is the preserved rollback container and must stay stopped.
>    See `PRODUCTION_FALKORDB_PERSISTENCE_CUTOVER.md` (status banner).
> 4. **Application services are four:** `backend` (web only), `contract-worker` (contract
>    queue + the only scheduler), `document-worker` (the only extraction owner, `legacy_v0`),
>    `client`. `document-worker-canary` runs **0** replicas outside an authorised canary.
> 5. **A certified release is retagged, not rebuilt** (§6).
>
> ~~TEMPORARY WARNING — PRODUCTION CLAMAV OVERRIDE (since R-A8Z, 2026-09-14).~~ *Retired
> 2026-09-20; kept below as history.*
> **UNTIL FULL CUTOVER, EVERY PRODUCTION COMPOSE COMMAND THAT CAN RECREATE CLAMAV
> MUST INCLUDE THE R-A8Z CLAMAV OVERRIDE:**
>
> ```bash
> docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml -f docker-compose.clamav-r-a8z.yml <command>
> ```
>
> The override is an untracked file in `/opt/contraclaim-dms/projectDMS`. Without
> it compose renders the old `clamav/clamav:1.4` service (no egress, no database
> volume) and recreates it, reinstating stale virus signatures. Details and the
> measured state: [CLAMAV_SIGNATURE_FRESHNESS.md](CLAMAV_SIGNATURE_FRESHNESS.md).

## 1. Connect to the Production Server

Connect using the configured SSH alias:

```bash
ssh contraclaim
```

Go to the production checkout:

```bash
cd /opt/contraclaim-dms/projectDMS
```

## 2. Confirm the Repository and GitHub Access

Use the GitHub CLI to confirm that the production server can access the correct repository:

```bash
gh auth status
gh repo view ManishPandey21/contraclaim-dms
```

Confirm that the local checkout points to the same repository and is on the expected branch:

```bash
git remote -v
git branch --show-current
git status --short
```

Expected state:

```text
Repository: ManishPandey21/contraclaim-dms
Branch: main
Git status: clean, or only known production-only untracked config/data files
```

If tracked files are modified, stop and review them before pulling. Do not discard production changes unless they are fully understood and backed up.

## 3. Review the Incoming Code

Fetch the latest commit metadata from GitHub:

```bash
git fetch origin main
```

Review incoming commits:

```bash
git log --oneline -n 10 HEAD..origin/main
git diff --stat HEAD..origin/main
```

If the incoming change includes database migrations, storage changes, queue changes, Docker volume changes, search/vector/graph changes, or unclear persistence changes, take and verify a backup before updating the checkout.

## 4. Verify or Take a Backup

Load the production environment values:

```bash
set -a
source .env
set +a
```

Check the latest backup status:

```bash
python3 scripts/backup_status.py \
  --root "${BACKUP_ROOT:-/var/backups/contractdms}" \
  --max-age-hours "${BACKUP_MAX_AGE_HOURS:-26}"
```

Confirm that the backup status is acceptable for the deployment. At minimum, verify the backup manifest and any data stores affected by the release, such as:

```text
MongoDB
Uploaded documents
Qdrant
FalkorDB
Redis
Backup manifest
```

If the backup is stale, missing, or incomplete, take a fresh backup using the project backup procedure, then rerun `backup_status.py` and confirm it passes before continuing.

## 5. Pull the Latest Code

Use a fast-forward pull so production does not create a merge commit:

```bash
git pull --ff-only origin main
```

Record the deployed commit hash:

```bash
git rev-parse HEAD
```

This commit is the release identity. Every image build below passes it as
`RELEASE_SHA`, which bakes it into the image (the `org.opencontainers.image.revision`
label; backend images also carry it as the process's `RELEASE_SHA`).

If `git pull --ff-only` fails, stop and inspect the cause. Do not use force reset or overwrite local files unless the impact is reviewed and approved.

### 5a. Declare the deployment scope

Every deploy declares exactly one scope. It decides which app services this deploy
replaces; `scripts/post_deploy_verify.sh` holds **every** app service to an exact image
either way (owner decision 2026-10-02 - strict, service-scoped):

| Scope | Deploys | Every other app service must |
|---|---|---|
| `FULL` | `backend`, `contract-worker`, `document-worker`, `client` | (none) |
| `BACKEND_ONLY` | `backend`, `contract-worker`, `document-worker` | run its approved image; `./client` unchanged since it was built |
| `CLIENT_ONLY` | `client` | run its approved image; `./backend` unchanged since it was built |
| `UNCHANGED` | nothing (maintenance, cutovers, restarts) | run its approved image |

There is no other value and no bypass. If the release changed `./backend`, it is not
`CLIENT_ONLY` - the verifier fails a scope that leaves changed code undeployed. The first
deploy under this contract is `FULL` (the running images predate their identity labels).

```bash
DEPLOY_SCOPE=CLIENT_ONLY            # or FULL, or BACKEND_ONLY
MANIFESTS=/opt/contraclaim-dms/release-manifests
RELEASE="$(git rev-parse HEAD)"
# compose names images <project>-<service>; production's project is `contraclaim`
PROJECT="$(docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')"
mkdir -p "$MANIFESTS"
```

`$MANIFESTS/current.json` is the approved manifest: the exact images production runs now.
Only `release_manifest.py promote` writes it, and only from a manifest whose verification
receipt (written by a fully green `post_deploy_verify.sh`) matches its bytes - a hand-edited
`current.json` fails every later check. Keep the directory outside the checkout and writable
only by the release operators. A manifest records each image's local image id
(`docker image inspect .Id`, which a retag keeps), not a registry digest.

## 6. Rebuild the Required Docker Images

> **A certified release is NOT rebuilt.** When the release programme has certified image ids
> (the production manifest records them), retag those ids onto the compose image names and
> start with `--no-build`; a rebuild yields a different, uncertified image:
>
> ```bash
> docker tag <certified-backend-id> "$PROJECT-backend:latest"
> docker tag <certified-backend-id> "$PROJECT-contract-worker:latest"
> docker tag <certified-backend-id> "$PROJECT-document-worker:latest"
> docker tag <certified-client-id>  "$PROJECT-client:latest"
> docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml \
>   up -d --no-deps --no-build backend contract-worker document-worker client
> ```
>
> Retag only the services your scope deploys. **A certified image must carry the certified
> commit as its identity:** certify it from `docker build --pull --no-cache --build-arg
> RELEASE_SHA=<certified commit> ...`; a retag keeps the label. The release programme hands
> over the certification record (`certified-<commit>.json`, from `release_manifest.py
> certify`, with the Trivy reports of those exact image ids); put both in `$MANIFESTS`. The certified commit must be the
> deployed checkout's HEAD. **Images certified before this contract are labelled `unknown`:
> certification refuses them.** Re-certify (rebuild with the argument, rescan) rather than
> overriding the result.
>
> The build path below is for ad-hoc fixes outside a certified release.

Rebuild only the services your scope deploys.

`FULL`:

```bash
RELEASE_SHA="$(git rev-parse HEAD)" docker compose --env-file .env \
  -f docker-compose.prod.yml \
  -f docker-compose.mongo-replicaset.yml \
  build backend contract-worker document-worker client
```

`CLIENT_ONLY`:

```bash
RELEASE_SHA="$(git rev-parse HEAD)" docker compose --env-file .env \
  -f docker-compose.prod.yml \
  -f docker-compose.mongo-replicaset.yml \
  build client
```

`BACKEND_ONLY`:

```bash
RELEASE_SHA="$(git rev-parse HEAD)" docker compose --env-file .env \
  -f docker-compose.prod.yml \
  -f docker-compose.mongo-replicaset.yml \
  build backend contract-worker document-worker
```

`RELEASE_SHA` is read at **build** time only; `up` neither needs it nor can change it. An
image built without it is labelled `unknown`, and the manifest step refuses it.

If Docker Compose or infrastructure service definitions changed, review the diff first and include only the affected services.

### 6a. Certify the images

The manifest accepts only images certified for this commit, and a certification needs a scan
of **that exact image id**. CI builds, scans and discards its own image of the commit, so its
scan is no evidence for an image built here: scan the images you just built on this host with
Trivy under the CI policy (HIGH/CRITICAL with a fix fails), as JSON. `certify` reads each
report, refuses one of another image id or with a fixable HIGH/CRITICAL finding, and records
the report's path and hash. Run Trivy against the host's Docker daemon (the default image
source), so the report's `Metadata.ImageID` is the id `docker image inspect` gives; capture
one such report on staging before the first certified production release. Scan and certify
only the services your scope deploys:

`FULL`:

```bash
for svc in backend contract-worker document-worker client; do
  trivy image --format json --severity HIGH,CRITICAL --ignore-unfixed \
    --output "$MANIFESTS/scan-$svc-$RELEASE.json" "$PROJECT-$svc:latest"
done
python3 scripts/release_manifest.py certify --out "$MANIFESTS/certified-$RELEASE.json" --scan backend="$MANIFESTS/scan-backend-$RELEASE.json" --scan contract-worker="$MANIFESTS/scan-contract-worker-$RELEASE.json" --scan document-worker="$MANIFESTS/scan-document-worker-$RELEASE.json" --scan client="$MANIFESTS/scan-client-$RELEASE.json" \
  --image backend="$PROJECT-backend:latest" \
  --image contract-worker="$PROJECT-contract-worker:latest" \
  --image document-worker="$PROJECT-document-worker:latest" \
  --image client="$PROJECT-client:latest"
```

`CLIENT_ONLY`:

```bash
for svc in client; do
  trivy image --format json --severity HIGH,CRITICAL --ignore-unfixed \
    --output "$MANIFESTS/scan-$svc-$RELEASE.json" "$PROJECT-$svc:latest"
done
python3 scripts/release_manifest.py certify --out "$MANIFESTS/certified-$RELEASE.json" --scan client="$MANIFESTS/scan-client-$RELEASE.json" \
  --image client="$PROJECT-client:latest"
```

`BACKEND_ONLY`:

```bash
for svc in backend contract-worker document-worker; do
  trivy image --format json --severity HIGH,CRITICAL --ignore-unfixed \
    --output "$MANIFESTS/scan-$svc-$RELEASE.json" "$PROJECT-$svc:latest"
done
python3 scripts/release_manifest.py certify --out "$MANIFESTS/certified-$RELEASE.json" --scan backend="$MANIFESTS/scan-backend-$RELEASE.json" --scan contract-worker="$MANIFESTS/scan-contract-worker-$RELEASE.json" --scan document-worker="$MANIFESTS/scan-document-worker-$RELEASE.json" \
  --image backend="$PROJECT-backend:latest" \
  --image contract-worker="$PROJECT-contract-worker:latest" \
  --image document-worker="$PROJECT-document-worker:latest"
```

### 6b. Write the release manifest

Before restarting anything, record the exact images this deploy will run. Services the
scope deploys come from the images just built or retagged (each must be labelled with
`$RELEASE`); every other service is copied unchanged from the approved manifest.

`FULL`:

```bash
python3 scripts/release_manifest.py target --scope FULL --out "$MANIFESTS/$RELEASE.json" \
  --certified "$MANIFESTS/certified-$RELEASE.json" \
  --image backend="$PROJECT-backend:latest" \
  --image contract-worker="$PROJECT-contract-worker:latest" \
  --image document-worker="$PROJECT-document-worker:latest" \
  --image client="$PROJECT-client:latest"
```

`CLIENT_ONLY`:

```bash
python3 scripts/release_manifest.py target --scope CLIENT_ONLY --out "$MANIFESTS/$RELEASE.json" \
  --certified "$MANIFESTS/certified-$RELEASE.json" \
  --approved "$MANIFESTS/current.json" \
  --image client="$PROJECT-client:latest"
```

`BACKEND_ONLY`:

```bash
python3 scripts/release_manifest.py target --scope BACKEND_ONLY --out "$MANIFESTS/$RELEASE.json" \
  --certified "$MANIFESTS/certified-$RELEASE.json" \
  --approved "$MANIFESTS/current.json" \
  --image backend="$PROJECT-backend:latest" \
  --image contract-worker="$PROJECT-contract-worker:latest" \
  --image document-worker="$PROJECT-document-worker:latest"
```

The command refuses an image that is not the certified one, a dirty checkout (modified,
untracked or ignored files the image would contain), an image built from another commit or
labelled `unknown`, a service the scope does not deploy, and a scoped manifest without an
approved manifest that verified green.

### 6c. Preflight before any restart

Still nothing has been restarted. The preflight is read-only and must pass first:

```bash
DEPLOY_SCOPE="$DEPLOY_SCOPE" RELEASE_MANIFEST="$MANIFESTS/$RELEASE.json" \
  scripts/release_preflight.sh
```

It fails a `FULL` or `BACKEND_ONLY` deploy while a `document-worker-canary` is running
that the release manifest does not declare. Such a canary is held to the
`document-worker` image, which these scopes replace while no step here recreates the
canary, so post-deploy verification could only fail once production had changed. Either
stop the canary first (scale `document-worker-canary` to 0, as outside an authorised
canary), or declare it: build, scan and certify its image with the others, pass
`--image document-worker-canary="$PROJECT-document-worker-canary:latest"` to `target`, and
recreate it with the scope's services. There is no override. `CLIENT_ONLY` keeps the
approved worker image, so a canary there is judged by the verifier as before.

## 7. Restart the Updated Containers

Recreate exactly the services your scope deploys, without recreating dependencies.

`FULL`:

```bash
docker compose --env-file .env \
  -f docker-compose.prod.yml \
  -f docker-compose.mongo-replicaset.yml \
  up -d --no-deps backend contract-worker document-worker client
```

`CLIENT_ONLY`:

```bash
docker compose --env-file .env \
  -f docker-compose.prod.yml \
  -f docker-compose.mongo-replicaset.yml \
  up -d --no-deps client
```

`BACKEND_ONLY`:

```bash
docker compose --env-file .env \
  -f docker-compose.prod.yml \
  -f docker-compose.mongo-replicaset.yml \
  up -d --no-deps backend contract-worker document-worker
```

Avoid recreating database or data services unless the release explicitly requires it and a verified backup exists.

### 7a. Verify every service against the manifest

`post_deploy_verify.sh` reads the scope and both manifests. A missing scope or manifest is
a failure, not a skip.

`FULL`:

```bash
DEPLOY_SCOPE=FULL RELEASE_MANIFEST="$MANIFESTS/$RELEASE.json" \
  COMPOSE_FILES="-f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml" \
  scripts/post_deploy_verify.sh
```

`CLIENT_ONLY`:

```bash
DEPLOY_SCOPE=CLIENT_ONLY RELEASE_MANIFEST="$MANIFESTS/$RELEASE.json" \
  APPROVED_MANIFEST="$MANIFESTS/current.json" \
  COMPOSE_FILES="-f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml" \
  scripts/post_deploy_verify.sh
```

`BACKEND_ONLY`:

```bash
DEPLOY_SCOPE=BACKEND_ONLY RELEASE_MANIFEST="$MANIFESTS/$RELEASE.json" \
  APPROVED_MANIFEST="$MANIFESTS/current.json" \
  COMPOSE_FILES="-f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml" \
  scripts/post_deploy_verify.sh
```

A fully green run writes a receipt next to the manifest (`$RELEASE.json.verified`). Then,
and only then, make this release the approved state for the next deploy:

```bash
python3 scripts/release_manifest.py promote --manifest "$MANIFESTS/$RELEASE.json" \
  --current "$MANIFESTS/current.json"
```

`promote` refuses a manifest without a receipt matching its bytes.

**Maintenance with no app deploy** (a data-service cutover, a restart, a scheduled check) is
`UNCHANGED`: every app service must still run exactly its approved image. Point it at
`current.json`: `UNCHANGED` accepts any manifest that verified green, so naming an older one
would verify an older state without making it the approved one.

```bash
DEPLOY_SCOPE=UNCHANGED RELEASE_MANIFEST="$MANIFESTS/current.json" \
  COMPOSE_FILES="-f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml" \
  scripts/post_deploy_verify.sh
```

A failure here means a service runs an image the release did not approve. Do not promote
the manifest; fix the deployment (or roll back, section 13) and verify again.

## 8. Confirm Container Health

Check the Docker Compose service table:

```bash
docker compose --env-file .env \
  -f docker-compose.prod.yml \
  -f docker-compose.mongo-replicaset.yml \
  ps
```

Check restart counts and OOM status:

```bash
docker inspect -f '{{.Name}} restarts={{.RestartCount}} oom={{.State.OOMKilled}} status={{.State.Status}}' \
  contraclaim-backend-1 \
  contraclaim-client-1 \
  contraclaim-contract-worker-1 \
  contraclaim-mongo1-1 \
  contraclaim-mongo2-1 \
  contraclaim-mongo3-1 \
  contraclaim-falkordb-1 \
  contraclaim-qdrant-1 \
  contraclaim-redis-1 \
  contraclaim-clamav-1
```

Expected state:

```text
Application containers are running
Healthchecked services are healthy
Restart counts are not increasing
OOMKilled is false
```

## 9. Review Logs

Review recent logs for the updated services:

```bash
docker compose --env-file .env \
  -f docker-compose.prod.yml \
  -f docker-compose.mongo-replicaset.yml \
  logs --tail=200 backend contract-worker client
```

If gateway or proxy configuration changed, include the gateway logs:

```bash
docker compose --env-file .env \
  -f docker-compose.prod.yml \
  -f docker-compose.mongo-replicaset.yml \
  logs --tail=200 gateway
```

Investigate any of these patterns before marking the deployment healthy:

```text
Traceback
Unhandled
Exception
CRITICAL
ERROR
connection refused
authentication failed
migration failed
```

## 10. Confirm Backend Readiness

Call the backend readiness endpoint from inside the Docker network:

```bash
backend_ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{"\n"}}{{end}}' contraclaim-backend-1 | sed -n '1p')
curl -fsS "http://$backend_ip:8000/health/ready"
```

The readiness response should report required dependencies as available, including MongoDB, Redis, Qdrant, FalkorDB, storage, configuration, and ClamAV if enabled.

## 11. Confirm the Public Application

Check the public health endpoint:

```bash
curl -ksSI https://web.contraclaim.com/health
```

Check the public frontend:

```bash
curl -ksSI https://web.contraclaim.com/
```

Expected result:

```text
HTTP/2 200
```

## 12. Record the Deployment Result

Capture the final deployed commit:

```bash
git rev-parse HEAD
```

Capture the final service state:

```bash
docker compose --env-file .env \
  -f docker-compose.prod.yml \
  -f docker-compose.mongo-replicaset.yml \
  ps
```

Deployment notes should include:

```text
Repository: ManishPandey21/contraclaim-dms
Deployed commit: <commit hash>
Deployment scope: FULL / CLIENT_ONLY / BACKEND_ONLY
Release manifest: release-manifests/<commit>.json (promoted to current.json: yes/no)
Backup status: verified / not required, with reason
Rebuilt services: backend, contract-worker, client, etc.
Restarted services: backend, contract-worker, client, etc.
Service health: pass/fail
Public URL check: pass/fail
Errors encountered: none, or exact errors and resolution
```

## 13. Rollback Guidance

If the deployment fails and the issue cannot be fixed quickly:

1. Keep the failed commit hash and logs for investigation.
2. Confirm that the previous good commit is known.
3. Confirm that no migration or data change prevents rollback.
4. Checkout the previous good commit only after the data impact is reviewed.
5. Rebuild and restart the same services using the steps above. A rollback is a deploy like
   any other: declare its scope, certify and write its manifest against `current.json`,
   verify and promote it (sections 5a-7a). The previous release's manifest and
   certification in `$MANIFESTS` name the images to return to.
6. Recheck logs, readiness, and the public application.

Do not restore a database backup unless the release changed data and rollback requires restoring data to a previous state.
