# Letter Bench release: deployment and rollback runbook

Release branch `release/contraclaim-rc1-letterbench`, built from the production
baseline `release/contraclaim-rc1-prebench` @ `79324e6` plus the Letter Bench
work from `main` @ `6d65bc38`. This runbook is for that one release. Nothing in
it runs until the owner has approved the release package.

It follows the certified-release procedure in
[CONTRACLAIM_DOCKER_DEPLOYMENT_UPDATE_GUIDE.md](CONTRACLAIM_DOCKER_DEPLOYMENT_UPDATE_GUIDE.md)
(sections 5 to 8) with scope `FULL`, and adds what is specific to this release:
three migrations, the preserved rollback images, and the feature flag. Where the
two disagree, the guide wins; tell the release owner.

Read [LETTER_BENCH.md](LETTER_BENCH.md) for what the feature does, its flags and
permissions, and the *Deployment* section of the repository `CLAUDE.md`.

## What changes in production

| Area | Change | Visible to users on deploy? |
| --- | --- | --- |
| Backend | New routes under `/api/letters/bench/...`, `/api/letters/{id}/bench/...`, `/api/skills/...` | No. Every Bench route answers 404 while `LETTER_BENCH_ROLLOUT_MODE=off`, the default |
| Backend | Three permissions added to the catalogue at startup: `drafting.skill.read`, `drafting.skill.publish`, `drafting.skill.bind` | No. Startup adds them to `permissions` and to `superadmin`; no other role document changes |
| Database | Migrations `20261114_0001`..`_0003`: five **new, empty** collections and their indexes, three seeded system skills | No. No existing collection, document or index is touched (verified, see the release PR) |
| Client | Letter Bench pages, Skills admin page, a "Reply to a letter" strip on the letters page | Only when the server says the Bench is on |
| Workers | Rebuilt from the same backend tree; no worker code changed | No |

The deploy itself is dark. Turning the Bench on is a separate, later change
(section 8).

## 0. Variables

On the server, in `/opt/contraclaim-dms/projectDMS`:

```bash
export DC="docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml"
export COMPOSE_FILES="-f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml"
export DEPLOY_SCOPE=FULL
export MANIFESTS=/opt/contraclaim-dms/release-manifests
export PROJECT=contraclaim
export TS=$(date -u +%Y%m%dT%H%M%SZ)
```

`COMPOSE_FILES` matters: `pre_deploy_readiness.sh`, `post_deploy_verify.sh` and
`production_backup.sh` default to `docker-compose.prod.yml` alone, which is not
the live file set. Confirm `PROJECT` with the `config --format json` command in
guide section 5.

## 1. Pre-deployment readiness (read-only)

Stop if any answer differs from the expected one.

```bash
git -C /opt/contraclaim-dms branch --show-current          # release/contraclaim-rc1-prebench
git -C /opt/contraclaim-dms rev-parse HEAD                 # 79324e6dbb12bf549d4ca0c254bade58b29772e1
python3 -c "import json;print(json.load(open('$MANIFESTS/current.json'))['release_sha'])"   # 79324e6d...
docker inspect contraclaim-backend-1 --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}'
$DC exec -T backend python -m rbac_backend.scripts.migrate_database --list | grep -c '"status": "applied"'   # 19
REQUIRE_FRESH_BACKUP=true COMPOSE_FILES="$COMPOSE_FILES" bash scripts/pre_deploy_readiness.sh
```

## 2. Backup and verified restoration

1. Take a backup now, even if the nightly one is fresh, and note the archive
   path it prints (`MongoDB backup written to ...`):

   ```bash
   COMPOSE_FILES="$COMPOSE_FILES" bash scripts/production_backup.sh
   python3 scripts/validate_backup_archive.py <mongo archive path>
   ```

2. **Restore drill (required before this deploy).** Restore the archive into a
   disposable MongoDB that is not a member of the production replica set, with
   `scripts/mongo_restore.sh` and a `MONGO_URI` that names only the disposable
   server. Compare per-collection counts with production. Record the archive
   name, size and the comparison in the release PR. This is the release-gate
   item still uncaptured (CLAUDE.md, *Known open debt*); without it the database
   path in section 7 is unproven.

## 3. Preserve the current production images

`current.json` already records the image ids production runs; tag them too so
`docker image prune` cannot remove them:

```bash
for s in backend client contract-worker document-worker document-worker-canary; do
  docker tag "$PROJECT-$s:latest" "$PROJECT-$s:rollback-79324e6-$TS"
done
sha256sum "$MANIFESTS/current.json" "$MANIFESTS/79324e6dbb12bf549d4ca0c254bade58b29772e1.json"   # record both
docker image ls | grep "rollback-79324e6-$TS"        # five lines
```

## 4. Get the code

The release branch must be on `origin` on the server (`ManishPandey21/contraclaim-dms`)
and its HEAD must equal the approved release SHA.

```bash
git -C /opt/contraclaim-dms fetch origin
git -C /opt/contraclaim-dms checkout -B release/contraclaim-rc1-letterbench origin/release/contraclaim-rc1-letterbench
export RELEASE="$(git -C /opt/contraclaim-dms rev-parse HEAD)"    # must equal the approved SHA
git -C /opt/contraclaim-dms status --porcelain --untracked-files=no                # empty
```

The untracked operator files in the checkout (`.deployed-*`, `.rollback-*`,
`evidence/`, ...) are not touched. Do not clean them.

## 5. Build, certify, migrate

1. Build the five app images labelled with the release (guide section 6, `FULL`,
   plus the canary that `current.json` declares):

   ```bash
   RELEASE_SHA="$RELEASE" $DC build backend contract-worker document-worker document-worker-canary client
   ```

2. Scan and certify (guide 6a). Any fixable HIGH/CRITICAL finding stops the
   release:

   ```bash
   for svc in backend contract-worker document-worker document-worker-canary client; do
     docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v "$MANIFESTS:$MANIFESTS" aquasec/trivy:latest \
       image --format json --severity HIGH,CRITICAL --ignore-unfixed \
       --output "$MANIFESTS/scan-$svc-$RELEASE.json" "$PROJECT-$svc:latest"
   done
   python3 scripts/release_manifest.py certify --out "$MANIFESTS/certified-$RELEASE.json" \
     --scan backend="$MANIFESTS/scan-backend-$RELEASE.json" \
     --scan contract-worker="$MANIFESTS/scan-contract-worker-$RELEASE.json" \
     --scan document-worker="$MANIFESTS/scan-document-worker-$RELEASE.json" \
     --scan document-worker-canary="$MANIFESTS/scan-document-worker-canary-$RELEASE.json" \
     --scan client="$MANIFESTS/scan-client-$RELEASE.json" \
     --image backend="$PROJECT-backend:latest" \
     --image contract-worker="$PROJECT-contract-worker:latest" \
     --image document-worker="$PROJECT-document-worker:latest" \
     --image document-worker-canary="$PROJECT-document-worker-canary:latest" \
     --image client="$PROJECT-client:latest"
   ```

3. Write the release manifest (guide 6b) and run the read-only preflight (6c):

   ```bash
   python3 scripts/release_manifest.py target --scope FULL --out "$MANIFESTS/$RELEASE.json" \
     --certified "$MANIFESTS/certified-$RELEASE.json" \
     --image backend="$PROJECT-backend:latest" \
     --image contract-worker="$PROJECT-contract-worker:latest" \
     --image document-worker="$PROJECT-document-worker:latest" \
     --image document-worker-canary="$PROJECT-document-worker-canary:latest" \
     --image client="$PROJECT-client:latest"
   DEPLOY_SCOPE=FULL RELEASE_MANIFEST="$MANIFESTS/$RELEASE.json" scripts/release_preflight.sh
   ```

4. Migration dry run **in the new backend image**, against production, before
   anything restarts. It must exit 0 and show exactly `20261114_0001`,
   `20261114_0002` and `20261114_0003` as pending:

   ```bash
   $DC run --rm --no-deps backend python -m rbac_backend.scripts.migrate_database --list
   $DC run --rm --no-deps backend python -m rbac_backend.scripts.migrate_database --fail-on-warning
   ```

5. Apply. The running 79324e6 backend keeps serving: these migrations only
   create new collections, which its code never reads.

   ```bash
   $DC run --rm --no-deps backend python -m rbac_backend.scripts.migrate_database --apply --fail-on-warning
   ```

## 6. Deploy and verify

1. Recreate the scope's services (guide section 7), with `--no-build` so nothing
   but the certified images can start. Leave `LETTER_BENCH_ROLLOUT_MODE` unset.

   ```bash
   $DC up -d --no-deps --no-build backend contract-worker document-worker document-worker-canary client
   ```

2. Verify every service against the manifest (guide 7a). A fully green run writes
   the receipt `$MANIFESTS/$RELEASE.json.verified`:

   ```bash
   DEPLOY_SCOPE=FULL RELEASE_MANIFEST="$MANIFESTS/$RELEASE.json" COMPOSE_FILES="-f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml" scripts/post_deploy_verify.sh
   ```

3. Smoke checks:

   ```bash
   SMOKE_BASE_URL=https://contraclaim.com python3 scripts/smoke_health.py
   $DC exec -T backend python -m rbac_backend.scripts.migrate_database --list | grep -c '"status": "applied"'   # 22
   ```

   As a signed-in user: documents list and viewer, correspondence and letters,
   a letter's existing drafting screens, claims, arbitration, Variation, BG and
   IPC registers load as before; `GET /api/letters/bench/availability` reports
   the Bench as off and no "Reply to a letter" strip appears; the hashed client
   asset names served by `https://contraclaim.com/` differ from the pre-deploy
   ones.

4. Watch backend and worker logs for 30 minutes (`$DC logs -f --since 5m backend`).

5. Only after a green verification and a quiet watch, promote (guide section 8):

   ```bash
   python3 scripts/release_manifest.py promote --manifest "$MANIFESTS/$RELEASE.json" --current "$MANIFESTS/current.json"
   ```

## 7. Rollback

### Triggers (any one)

- the migration dry run lists anything other than the three `20261114_*`
  versions, prints a warning, or exits non-zero: **stop before applying**;
- certification refuses an image (fixable HIGH/CRITICAL): **stop before restarting**;
- `--apply` fails;
- the backend is not healthy within 5 minutes, or `post_deploy_verify.sh`
  reports a FAIL;
- any 5xx on an existing page in the smoke test, or an error rate above the
  pre-deploy level for 15 minutes;
- an authorization regression: a user sees another organisation's or project's
  data, or loses access they had;
- worker queues stop draining.

### Application rollback (no database restore)

The release's migrations only add collections. The 79324e6 code ignores them,
and its migration runner reports the three versions as `applied_not_in_catalogue`
without failing (verified, see the release PR). Rollback is the preserved
images plus the baseline checkout, verified against the manifest that was
approved before this release:

```bash
for s in backend client contract-worker document-worker document-worker-canary; do
  docker tag "$PROJECT-$s:rollback-79324e6-$TS" "$PROJECT-$s:latest"
done
git -C /opt/contraclaim-dms checkout release/contraclaim-rc1-prebench
git -C /opt/contraclaim-dms rev-parse HEAD     # 79324e6d...
$DC up -d --no-deps --no-build backend contract-worker document-worker document-worker-canary client
```

If `current.json` was not promoted yet it still names 79324e6; verify against it:

```bash
DEPLOY_SCOPE=UNCHANGED RELEASE_MANIFEST="$MANIFESTS/current.json" COMPOSE_FILES="-f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml" scripts/post_deploy_verify.sh
```

If it was promoted, `promote` the 79324e6 manifest again
(`$MANIFESTS/79324e6dbb12bf549d4ca0c254bade58b29772e1.json`, which has its
receipt) before running that check.

Leave the five new collections and the three permission rows in place: nothing
in 79324e6 reads them.

### Database recovery (only if data was damaged)

This release writes no existing collection, so a restore should not be needed.
If one is, it is a full restore of the section 2 archive with the application
stopped, per `docs/OPERATIONS.md`; every write since the backup is lost. That is
an owner decision, not an operator one.

## 8. Turning the Bench on (separate change, after the deploy is stable)

Not part of this deploy. When approved:

1. Confirm the people who will use it hold `drafting.request.view` and
   `drafting.draft.create` for their letters, and an expert allocation covering
   them (LETTER_BENCH.md, *To check before switching on*).
2. Set `LETTER_BENCH_ROLLOUT_MODE=canary` and
   `LETTER_BENCH_CANARY_TENANT_IDS=<one organisation id>` in the server `.env`,
   then `$DC up -d --no-deps --no-build backend`.
3. Run `client/e2e/staging/letter-bench-api.spec.ts` per
   [LETTER_BENCH_STAGING_CHECKS.md](LETTER_BENCH_STAGING_CHECKS.md) with a test
   letter in the canary organisation.
4. Feature rollback: set the mode back to `off` and restart the backend.
   Letters keep their runs and drafts.
