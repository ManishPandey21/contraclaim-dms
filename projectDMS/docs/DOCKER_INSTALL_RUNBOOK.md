# Contraclaim DMS — Docker Production Install Runbook

Single Ubuntu VPS. Full-Docker deployment using the repo's shipped
`docker-compose.prod.yml` + `docker-compose.mongo-replicaset.yml`.

**Target:** Ubuntu 22.04/24.04 LTS, ≥ 4 vCPU, ≥ 8 GB RAM, ≥ 40 GB SSD.
(Preflight hard-fails under 20 GB free disk; warns under ~3.5 GB free RAM. OCR +
ClamAV + Qdrant + 3× Mongo want headroom — 8 GB is the realistic floor.)

Estimated time: 45–90 min. Each step is idempotent unless noted.

---

## 0. Conventions

- Run as a non-root sudo user named `deploy`.
- App lives in `/opt/contraclaim`.
- All commands assume you are in `/opt/contraclaim` unless stated.
- `$` = shell prompt, not part of the command.

---

## 1. Server hardening (do this first — it fixes leaked-credential risk)

> Your old `installation.txt` contained VPS IPs + SSH passwords in plaintext.
> Rotate those passwords now and switch to key-only SSH. Delete that file from
> any machine and chat history.

```bash
# as the initial root/ubuntu user
adduser deploy
usermod -aG sudo deploy

# copy your SSH public key to the deploy user
mkdir -p /home/deploy/.ssh
# paste your ~/.ssh/id_ed25519.pub into the file below:
nano /home/deploy/.ssh/authorized_keys
chmod 700 /home/deploy/.ssh && chmod 600 /home/deploy/.ssh/authorized_keys
chown -R deploy:deploy /home/deploy/.ssh
```

Harden SSH (`/etc/ssh/sshd_config`), then `sudo systemctl restart ssh`:

```
PasswordAuthentication no
PermitRootLogin no
```

Firewall — **only** SSH + HTTP/HTTPS are public. Data ports are never exposed:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

Timezone:

```bash
sudo timedatectl set-timezone UTC
sudo apt update && sudo apt -y upgrade
```

> ⚠️ **UFW does not block Docker-published ports.** This runbook keeps Qdrant,
> Redis, FalkorDB, and Mongo on `internal: true` Docker networks with **no**
> host port mappings. Do not add `-p 6333:6333`-style mappings. The preflight
> script hard-fails if it detects published data ports.

---

## 2. Install Docker Engine + Compose plugin

```bash
sudo apt install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
  sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker deploy
newgrp docker   # or log out/in
docker compose version   # verify the plugin is present
```

---

## 3. Get the code

```bash
sudo mkdir -p /opt/contraclaim && sudo chown deploy:deploy /opt/contraclaim
git clone <your-repo-url> /opt/contraclaim
cd /opt/contraclaim
git checkout main   # or your release tag
```

Tell Compose to always use both prod files (app stack + Mongo replica set) so
Mongo and the backend share the internal `data-net`:

```bash
# add to ~/.bashrc so every session picks it up
echo 'export COMPOSE_FILE=docker-compose.prod.yml:docker-compose.mongo-replicaset.yml' >> ~/.bashrc
echo 'export COMPOSE_FILES="-f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml"' >> ~/.bashrc
source ~/.bashrc
```

---

## 4. Generate secrets

```bash
# 64-char app secret
openssl rand -hex 32                       # -> SECRET_KEY

# strong service passwords
openssl rand -base64 24                    # -> REDIS_PASSWORD
openssl rand -base64 24                    # -> FALKORDB_PASSWORD
openssl rand -base64 24                    # -> QDRANT_API_KEY
openssl rand -base64 24                    # -> METRICS_TOKEN
openssl rand -base64 24                    # -> LANGGRAPH_API_TOKEN (if enabling AI drafting)

# Fernet key for SMTP-settings encryption
python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"  # -> SMTP_SETTINGS_ENCRYPTION_KEY
```

The preflight also expects Qdrant's key as a file:

```bash
mkdir -p config/secrets
printf '%s' '<the QDRANT_API_KEY you generated>' > config/secrets/qdrant_api_key
chmod 600 config/secrets/qdrant_api_key
```

---

## 5. Configure `.env`

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

Set at minimum (everything else can keep example defaults):

| Variable | Value |
|---|---|
| `ENVIRONMENT` | `production` |
| `PUBLIC_BASE_URL` / `APP_URL` | `https://app.yourdomain.com` |
| `PUBLIC_API_URL` | `https://app.yourdomain.com/api` |
| `CORS_ORIGINS` | `https://app.yourdomain.com` (no localhost!) |
| `SECRET_KEY` | the 64-char hex from step 4 |
| `AUTH_COOKIE_SECURE` | `true` |
| `DATABASE_URL` | `mongodb://mongo1:27017,mongo2:27017,mongo3:27017/contraclaim?replicaSet=rs0` |
| `MONGODB_URI` | same as `DATABASE_URL` |
| `MONGODB_REPLICA_SET` | `rs0` |
| `MONGODB_ALLOW_STANDALONE_PRODUCTION` | `false` |
| `REDIS_PASSWORD` | from step 4 |
| `FALKORDB_PASSWORD` | from step 4 |
| `QDRANT_API_KEY` | from step 4 (same value as the secret file) |
| `METRICS_TOKEN` | from step 4 |
| `SMTP_SETTINGS_ENCRYPTION_KEY` | Fernet key from step 4 |
| `OPENAI_API_KEY` | your key |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_BUCKET_NAME` / `AWS_REGION` | S3 for uploads/backups |
| `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_FROM_EMAIL` / `CONTACT_RECIPIENT_EMAIL` | mail |
| `BACKUP_S3_BUCKET` | offsite backup bucket |
| `BACKUP_ROOT` | `/var/backups/contractdms` (absolute path — required) |
| `ANTIVIRUS_ENABLED` | `true` |
| `CLAMAV_FAIL_OPEN` | `false` |
| `HTTP_PORT` | leave unset — defaults to `127.0.0.1:8080` (loopback-only; host Nginx does TLS — step 10). Set `80` only behind an external TLS load balancer |
| `LANGGRAPH_ENABLED` / `LANGGRAPH_API_TOKEN` | `true` + token only if you want AI letter drafting |

> The backend refuses to start in production if any critical secret is a
> placeholder, if `DATABASE_URL` points at localhost, if CORS includes dev
> origins, or if antivirus/backup guards are misconfigured
> (`core/config.py: validate_runtime_configuration`). That is intentional —
> a failed boot here means a real misconfig.

```bash
sudo mkdir -p /var/backups/contractdms && sudo chown deploy:deploy /var/backups/contractdms
```

---

## 6. Bring up the MongoDB replica set (first, on its own)

```bash
docker compose $COMPOSE_FILES up -d mongo1 mongo2 mongo3 mongo-init
# mongo-init runs scripts/mongo/init-replica-set.sh and exits 0 when rs0 is formed
docker compose $COMPOSE_FILES logs mongo-init
# expect: "Replica set initialization complete"
```

Verify the set elected a primary:

```bash
docker compose $COMPOSE_FILES exec mongo1 mongosh --quiet --eval "rs.status().members.map(m=>m.stateStr)"
# expect one PRIMARY and two SECONDARY
```

> The shipped RS runs without auth and is only reachable on the internal
> `data-net` (never published). Hardening to keyfile auth is optional — see
> Appendix A. Do this before onboarding real tenants if your threat model
> includes other containers on the host.

---

## 7. Run database migrations

```bash
docker compose $COMPOSE_FILES run --rm backend \
  python -m rbac_backend.scripts.migrate_database --fail-on-warning
```

Fix any FAIL before continuing. This creates indexes, unique constraints
(share-token / webhook idempotency), and TTL cleanups.

---

## 8. Preflight readiness gate

```bash
REQUIRE_FRESH_BACKUP=false RUN_MIGRATION_DRY_RUN=true \
  bash scripts/pre_deploy_readiness.sh
```

Must end with `0 failure(s)`. Common fails and fixes:

- *SECRET_KEY placeholder* → set a real 32+ char secret.
- *config/secrets/qdrant_api_key missing* → step 4.
- *Compose publishes data-service ports* → remove any `ports:` on data services.
- *BACKUP_S3_BUCKET required* → set it in `.env`.

---

## 9. Deploy the application stack

```bash
docker compose $COMPOSE_FILES pull
docker compose $COMPOSE_FILES up -d --build
docker compose $COMPOSE_FILES ps      # all services healthy?
```

Services that come up: `backend` (API, no queue workers), `contract-worker`
(OCR/ingest + leader-locked scheduler), `client`, `qdrant`, `falkordb`,
`redis`, `clamav`, `gateway`. ClamAV takes 1–2 min to load signatures on first
start — the backend waits on it.

Watch readiness:

```bash
docker compose $COMPOSE_FILES exec backend curl -fsS http://localhost:8000/health/ready
# expect {"status":"ready", ...}
```

---

## 10. TLS + public entry (host Nginx in front of the gateway)

The bundled `gateway` (Apache) speaks plain HTTP and — as of the C1 fix — binds
to `127.0.0.1:8080` by default (`docker-compose.prod.yml`, `HTTP_PORT`).
Terminate TLS on the host with Nginx + Let's Encrypt using the tracked
template **`config/nginx-contraclaim.conf`**:

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
sudo cp config/nginx-contraclaim.conf /etc/nginx/sites-available/contraclaim
sudo nano /etc/nginx/sites-available/contraclaim   # set server_name to your domain
sudo ln -s /etc/nginx/sites-available/contraclaim /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d app.yourdomain.com   # provisions cert + HTTPS redirect
```

Point your DNS `A` record at the VPS first, then run certbot. Certbot adds the
443 server block and auto-renews via a systemd timer. The template already
handles websockets (`/ws`), 300 s timeouts for the agentic AI endpoints, and
unbuffered large uploads.

---

## 11. Post-deploy verification

```bash
bash scripts/post_deploy_verify.sh          # or:
python3 scripts/smoke_health.py

# from your laptop:
curl -fsS https://app.yourdomain.com/health
curl -fsS https://app.yourdomain.com/api/health/live
```

Manual spine test in the browser (the one flow that touches every service):

1. Log in.
2. Upload a scanned PDF → wait for OCR to complete (contract-worker logs).
3. Open Contract QA → ask a question → confirm a cited answer.
4. Draft a letter (if `LANGGRAPH_ENABLED=true`).

Metrics (token-gated):

```bash
curl -fsS -H "X-Metrics-Token: <METRICS_TOKEN>" https://app.yourdomain.com/api/metrics | head
```

---

## 12. Backups + offsite (schedule immediately)

Use **`scripts/production_backup.sh`** — it is the one script that satisfies
the `/api/health/operations` contract end-to-end: MongoDB dump into
`$BACKUP_ROOT/mongo/`, every required Docker volume into
`$BACKUP_ROOT/volumes/<label>-<stamp>.tar.gz`, plus manifests and sha256
checksums under `$BACKUP_ROOT/manifests/`.

```bash
sudo BACKUP_ROOT=/var/backups/contractdms \
  COMPOSE_FILES="-f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml" \
  bash scripts/production_backup.sh
```

Offsite to S3 + freshness check:

```bash
bash scripts/backup_offsite_s3.sh
python3 scripts/backup_status.py --root /var/backups/contractdms --max-age-hours 26
```

Cron (`crontab -e` as `deploy`):

```
15 2 * * *  cd /opt/contraclaim && BACKUP_ROOT=/var/backups/contractdms COMPOSE_FILES="-f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml" bash scripts/production_backup.sh >> /var/log/cc-backup.log 2>&1
45 2 * * *  cd /opt/contraclaim && bash scripts/backup_offsite_s3.sh >> /var/log/cc-backup.log 2>&1
```

Verify the contract is green after the first run:

```bash
curl -fsS -H "X-Metrics-Token: <METRICS_TOKEN>" \
  https://app.yourdomain.com/api/health/operations
# expect "status":"ok" with mongo + all volume artifacts "ok"
```

> **How the health contract works (H5):** `/api/health/operations`
> (`services/operations_health.py`) checks a `mongo` artifact at
> `$BACKUP_ROOT/mongo/*.archive.gz` automatically — it is *not* part of
> `BACKUP_REQUIRED_VOLUME_LABELS` (those labels map to
> `$BACKUP_ROOT/volumes/<label>-*.tar.gz` archives). A missing or stale Mongo
> dump flips the endpoint to `degraded`/503. `scripts/mongo_backup.sh` also
> defaults its output to `$BACKUP_ROOT/mongo` now, so ad-hoc dumps count too.

---

## 13. Monitoring & logs (day-2 cheat sheet)

```bash
docker compose $COMPOSE_FILES ps                    # health status
docker compose $COMPOSE_FILES logs -f backend       # API logs
docker compose $COMPOSE_FILES logs -f contract-worker  # OCR/ingest/scheduler
docker stats                                        # live CPU/RAM per container

# operational health (backup freshness, etc.)
curl -fsS -H "X-Metrics-Token: <token>" https://app.yourdomain.com/api/health/operations
```

Log rotation is already configured (json-file, 10 MB × 5) in the compose files.

---

## 14. Deploy a new version (rollback-safe)

```bash
cd /opt/contraclaim
git fetch --tags
git checkout <new-tag>
docker compose $COMPOSE_FILES run --rm backend \
  python -m rbac_backend.scripts.migrate_database --fail-on-warning
docker compose $COMPOSE_FILES up -d --build
bash scripts/post_deploy_verify.sh
```

**Rollback:**

```bash
git checkout <previous-tag>
docker compose $COMPOSE_FILES up -d --build
```

Because images are rebuilt from a pinned commit, rollback is deterministic.
Take a fresh Mongo dump before any deploy that includes a migration.

---

## 15. Restore drill (do this once before go-live)

Restore one volume at a time; the script takes a volume name and one archive,
not a backup directory. Stop the affected service first — restoring under a
running container leaves it serving the pre-restore state from memory and
overwriting your archive on its next save.

```bash
docker compose $COMPOSE_FILES stop falkordb
sudo bash scripts/production_restore_volumes.sh --apply \
  contraclaim_falkordb_data /var/backups/contractdms/volumes/falkordb-data-<STAMP>.tar.gz
docker compose $COMPOSE_FILES up -d falkordb
```

The volume name is `<COMPOSE_PROJECT_NAME>_<volume>`; confirm it with
`docker volume ls` rather than guessing the project prefix.

Mongo, on a database suffering *partial* corruption, must be dropped first:
`mongorestore` runs without `--drop`, so it restores deleted documents, skips
every diverged one with a duplicate-key error, and still exits 0.

```bash
# total loss: restore directly
bash scripts/mongo_restore.sh /var/backups/contractdms/mongo/<dump>.archive.gz
# corruption: drop the database first, then restore
```

Practise on a throwaway VPS. A backup you have never restored is not a backup.

### 15.1 FalkorDB — verifying recovery, and the one-time move

`scripts/falkordb_recovery_drill.sh` performs the whole cycle against
disposable containers and volumes: it reads the image, `REDIS_ARGS` and the
volume mount out of `docker-compose.prod.yml`, seeds a graph, backs it up,
**destroys the container and the volume**, restores, and diffs a semantic
snapshot (graph names, nodes, edges, properties, counts, indexes). It also
checks that a missing archive, a corrupted archive and a volume holding no
persisted state each fail visibly.

```bash
bash scripts/falkordb_recovery_drill.sh
```

It touches nothing that already exists: every container, volume and graph it
creates carries an `ra4drill_` prefix and is removed on exit. Run it after any
change to the falkordb service definition or to the backup scripts.

**One-time move when adopting this release.** Before this release the FalkorDB
service had no `--dir`, so the engine persisted to the image's working
directory (`/FalkorDB`) inside the container's writable layer while the backup
archived the volume mounted at `/data` — which was empty. On a host that has
been running the old configuration the graph is still in the old container's
layer, and the new configuration will come up with an empty `/data`. Move the
state across **before** recreating the container:

```bash
# 1. flush to disk, then stop writers
docker compose $COMPOSE_FILES exec -T falkordb \
  sh -c 'redis-cli -a "$FALKORDB_PASSWORD" BGSAVE'
docker compose $COMPOSE_FILES exec -T falkordb \
  sh -c 'redis-cli -a "$FALKORDB_PASSWORD" INFO persistence | grep rdb_bgsave_in_progress'
docker compose $COMPOSE_FILES stop falkordb

# 2. copy the real persistence file into the mounted volume
docker compose $COMPOSE_FILES cp falkordb:/FalkorDB/dump.rdb /tmp/falkor-dump.rdb
docker run --rm -v contraclaim_falkordb_data:/data -v /tmp:/in busybox \
  sh -c 'cp /in/falkor-dump.rdb /data/dump.rdb && ls -la /data'

# 3. bring up the new configuration and confirm the graphs are there
docker compose $COMPOSE_FILES up -d falkordb
docker compose $COMPOSE_FILES exec -T falkordb \
  sh -c 'redis-cli -a "$FALKORDB_PASSWORD" GRAPH.LIST'
```

Copy `dump.rdb` rather than `appendonlydir/`: the AOF manifest names its files
by sequence, and an AOF copied out of step with its manifest will not load.
With `appendonly yes` the engine rewrites the AOF from the loaded dataset once
it starts, so the RDB carries the state across on its own.

**GRAPH.LIST must show the same graphs as before the move.** If it is empty,
stop: the old container still holds the data in its writable layer, and that
data is lost only when the container is removed.

The graph is derived state — Mongo is canonical — so
`scripts/backfill_falkordb.py` remains the fallback if a restore is ever
unavailable. That path has not been rehearsed and its runtime against a
production-sized corpus is unmeasured; it is not a substitute for a verified
backup.

---

## Appendix A — Optional: MongoDB keyfile auth

The shipped RS has no auth (safe only because it's on an internal network). To
add auth:

1. Generate a keyfile, mount it read-only into all three mongo services, add
   `--keyFile /etc/mongo-keyfile --auth` to each `command:`.
2. After `rs.initiate()`, create an admin + `app_user` with `readWrite` on
   `contraclaim`.
3. Update `DATABASE_URL` to
   `mongodb://app_user:<pw>@mongo1:27017,mongo2:27017,mongo3:27017/contraclaim?replicaSet=rs0&authSource=admin`.

This requires editing `docker-compose.mongo-replicaset.yml`; keep the edit in a
tracked overlay file so it survives `git pull`.

---

## Appendix B — Service map

| Container | Role | Exposed? |
|---|---|---|
| gateway (httpd) | reverse proxy | `127.0.0.1:8080` only |
| client | React/Vite static server | internal |
| backend | FastAPI (API only) | internal `:8000` |
| contract-worker | OCR, ingest queue, cron scheduler | internal |
| qdrant | vector DB | internal `data-net` |
| falkordb | knowledge graph | internal `data-net` |
| redis | queue + runtime state | internal `service-net` |
| clamav | upload AV | internal |
| mongo1/2/3 | replica set `rs0` | internal `data-net` |

Public surface = host Nginx (443) → gateway (127.0.0.1:8080). Nothing else.
