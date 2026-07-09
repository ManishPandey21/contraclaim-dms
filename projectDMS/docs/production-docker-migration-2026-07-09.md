# Contraclaim DMS Production Docker Migration Report

Date: 2026-07-09  
Host: `contraclaim` (`vps-5dec80c1`)  
Primary domain: `web.contraclaim.com`  
API domain retained: `api.contraclaim.com`  
Deployment path: `/opt/contraclaim-dms`  
Backup root: `/var/backups/contraclaim-migration/20260709-082356`

This report intentionally omits secret values.

## Deployment Summary

The production server was migrated from the existing systemd/static deployment to the Docker production stack from commit:

```text
3588bed118381fa85a54ebd8fbae500da019479c
```

Direct `git clone` on the server was blocked by non-interactive GitHub authentication. The deployed tree was created from a local archive of `projectDMS` after fetching `ManishPandey21/contraclaim-dms` through the configured local Git remote.

Nginx now proxies both `web.contraclaim.com` and `api.contraclaim.com` to the Docker gateway on `127.0.0.1:8080`. Docker data services are on internal Docker networks and are not exposed publicly.

## Backup Artifacts

Primary backup directory:

```text
/var/backups/contraclaim-migration/20260709-082356
```

The backup directory is approximately `1.1G` and includes:

- MongoDB full archive: `/var/backups/contraclaim-migration/20260709-082356/mongo/local-mongodb-all-20260709-082356.archive.gz`
- Existing frontend archive: `/var/backups/contraclaim-migration/20260709-082356/filesystem/var-www-web-20260709-082356.tar.gz`
- Existing backend/repo archive: `/var/backups/contraclaim-migration/20260709-082356/filesystem/opt-contraclaim-20260709-082356.tar.gz`
- Existing `/var/www/api` archive: `/var/backups/contraclaim-migration/20260709-082356/filesystem/var-www-api-20260709-082356.tar.gz`
- Existing Nginx/Apache/system config archive: `/var/backups/contraclaim-migration/20260709-082356/config/server-config-20260709-082356.tar.gz`
- Existing env file archive: `/var/backups/contraclaim-migration/20260709-082356/env/env-files-20260709-082356.tar.gz`
- Docker volume backups including Qdrant and FalkorDB under `/var/backups/contraclaim-migration/20260709-082356/docker-volumes`
- Docker container data backups under `/var/backups/contraclaim-migration/20260709-082356/docker-container-data`
- Backup checksums: `/var/backups/contraclaim-migration/20260709-082356/manifests/checksums-20260709-082356.sha256`
- Backup summary: `/var/backups/contraclaim-migration/20260709-082356/manifests/backup-summary.txt`

New Docker MongoDB pre-import backup:

```text
/var/backups/contraclaim-migration/20260709-082356/new-db-preimport/new-db-preimport-20260709-111734.archive.gz
```

Nginx cutover backup:

```text
/var/backups/contraclaim-migration/20260709-082356/nginx-cutover-20260709-123734
```

Selective import artifacts:

```text
/var/backups/contraclaim-migration/20260709-082356/selective-import/20260709-113920
```

## Runtime Status

Final compose status:

```text
contraclaim-backend-1           healthy
contraclaim-clamav-1            healthy
contraclaim-client-1            healthy
contraclaim-contract-worker-1   running
contraclaim-falkordb-1          healthy
contraclaim-gateway-1           healthy, bound to 127.0.0.1:8080
contraclaim-mongo1-1            healthy, PRIMARY
contraclaim-mongo2-1            healthy, SECONDARY
contraclaim-mongo3-1            healthy, SECONDARY
contraclaim-qdrant-1            healthy
contraclaim-redis-1             healthy
```

Final public/listening port surface:

```text
0.0.0.0:22      ssh
0.0.0.0:80      nginx
0.0.0.0:443     nginx
127.0.0.1:8080  Docker gateway
```

No public listeners remain for MongoDB, Qdrant, FalkorDB, or Redis.

Old services retired:

```text
contraclaim-api      inactive, disabled
contraclaim-backend  failed/inactive, disabled
mongod               inactive, disabled
```

Old Docker containers stopped, not deleted:

```text
qdrant
falkordb
redis
```

The previous `/var/www/web` contents were cleared only after the frontend backup was verified.

## SSL Status

Existing Certbot certificates were reused.

```text
api.contraclaim.com: valid until 2026-09-24 23:40:57 UTC
web.contraclaim.com, app.contraclaim.com: valid until 2026-09-22 19:07:45 UTC
```

HTTP redirects to HTTPS for:

```text
web.contraclaim.com
app.contraclaim.com
api.contraclaim.com
```

## Health Check Results

Verified through Docker and Nginx:

```text
http://127.0.0.1:8080/health -> ok
https://web.contraclaim.com/health -> ok
https://web.contraclaim.com/ -> 200
https://api.contraclaim.com/api/users/me -> 401 unauthenticated, confirming API routing
```

Backend internal readiness returned:

```text
mongo: ok
contract_queue_redis: ok
runtime_redis: ok
configuration: ok
local_storage: ok
qdrant: ok
falkordb: ok
clamav: ok
```

## Selective Data Migration

The old database was not blindly restored into the new database. Selected collections were dumped from the old `contraclaim` database and imported into the new Docker MongoDB database after taking a pre-import backup.

Final selected collection counts:

```text
roles=12
permissions=234
users=8
organizations=6
projects=5
organization_memberships=0
project_memberships=0
role_assignments=0
plans=9
subscriptions=3
addons=3
entitlements=0
security_terms_acceptances=2
terms_versions=1
storage_settings=2
smtp_settings=0
notification_preferences=3
```

## Remaining Validation

Infrastructure, routing, health checks, Docker services, TLS, port exposure, and selected data presence were verified.

The following user workflow checks still require valid application credentials and should be completed manually:

- Admin login using a real admin account.
- Existing users visible in the UI.
- Roles and permissions assigned as expected for named users.
- Organisations and projects visible to expected users.
- Project-level access control with at least two different user roles.
- Upload, document view, OCR, search, Contract Q&A, and letter drafting workflows.

## 1. Initial Server Inspection Commands

Use these before changing a production server:

```bash
hostnamectl
df -h
free -h
uptime
systemctl status nginx docker mongod contraclaim-api contraclaim-backend --no-pager
ss -tulpn
docker ps -a
docker volume ls
ls -la /opt /var/www /etc/nginx/sites-available /etc/nginx/sites-enabled
sudo nginx -T >/tmp/nginx-current.txt
sudo certbot certificates
```

For MongoDB:

```bash
mongosh --quiet --eval 'db.adminCommand("ping")'
mongosh contraclaim --quiet --eval 'db.getCollectionNames().sort().forEach(c => print(c + "\t" + db.getCollection(c).countDocuments({})))'
```

## 2. Backup Commands For MongoDB And Docker Volumes

Create a timestamped root:

```bash
stamp="$(date +%Y%m%d-%H%M%S)"
backup_root="/var/backups/contraclaim-migration/${stamp}"
sudo mkdir -p "$backup_root"/{mongo,docker-volumes,docker-container-data,filesystem,config,env,manifests}
```

MongoDB:

```bash
sudo mongodump --archive="$backup_root/mongo/local-mongodb-all-${stamp}.archive.gz" --gzip
sudo test -s "$backup_root/mongo/local-mongodb-all-${stamp}.archive.gz"
```

Docker volumes:

```bash
for volume in $(docker volume ls -q); do
  docker run --rm \
    -v "${volume}:/volume:ro" \
    -v "${backup_root}/docker-volumes:/backup" \
    alpine:3.20 \
    tar -czf "/backup/${volume}-${stamp}.tar.gz" -C /volume .
  test -s "${backup_root}/docker-volumes/${volume}-${stamp}.tar.gz"
done
```

Frontend/backend/config/env:

```bash
sudo tar -czf "$backup_root/filesystem/var-www-web-${stamp}.tar.gz" -C /var/www web
sudo tar -czf "$backup_root/filesystem/opt-contraclaim-${stamp}.tar.gz" -C /opt contraclaim
sudo tar -czf "$backup_root/config/server-config-${stamp}.tar.gz" /etc/nginx /etc/apache2 /etc/systemd/system /etc/mongod.conf
sudo find /opt /var/www /etc/systemd/system -name '.env*' -o -name '*env*' >"$backup_root/manifests/env-files.list"
sudo tar -czf "$backup_root/env/env-files-${stamp}.tar.gz" -T "$backup_root/manifests/env-files.list"
```

Checksums:

```bash
cd "$backup_root"
find . -type f ! -name '*.sha256' -print0 | sort -z | xargs -0 sha256sum >"manifests/checksums-${stamp}.sha256"
```

## 3. Stop The Old Systemd Backend

Stop and disable old app services, but keep service files:

```bash
sudo systemctl stop contraclaim-api
sudo systemctl disable contraclaim-api
sudo systemctl stop contraclaim-backend || true
sudo systemctl disable contraclaim-backend || true
```

If the host MongoDB is replaced by the Docker replica set and data has been migrated:

```bash
sudo systemctl stop mongod
sudo systemctl disable mongod
```

## 4. Clear Old Frontend Files Safely

Only run after the frontend backup exists and is non-empty:

```bash
test -s /var/backups/contraclaim-migration/<stamp>/filesystem/var-www-web-<stamp>.tar.gz
test "$(readlink -f /var/www/web)" = "/var/www/web"
sudo find /var/www/web -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
```

Do not delete uploads, databases, backups, or Docker volumes.

## 5. Clone The GitHub Repository

Preferred:

```bash
sudo git clone https://github.com/ManishPandey21/contraclaim-dms.git /opt/contraclaim-dms
cd /opt/contraclaim-dms
git rev-parse HEAD
```

If the server cannot authenticate to the repository non-interactively, create an archive from a trusted local checkout after fetching the GitHub remote, copy it to the server, and record the commit hash in:

```text
/opt/contraclaim-dms/.deployed-git-commit
```

## 6. Configure Production Environment Files

Use the repository templates and existing production values. Do not echo secrets to the terminal.

Files used in this deployment:

```text
/opt/contraclaim-dms/.env
/opt/contraclaim-dms/backend/.env
/opt/contraclaim-dms/client/.env.production
/opt/contraclaim-dms/config/secrets/qdrant_api_key
```

Confirm required keys by name only:

```bash
grep -E '^[A-Z0-9_]+=' .env | cut -d= -f1 | sort
grep -E '^[A-Z0-9_]+=' backend/.env | cut -d= -f1 | sort
```

Production antivirus settings should be fail-closed:

```text
ANTIVIRUS_ENABLED=true
ANTIVIRUS_REQUIRED_IN_PRODUCTION=true
CLAMAV_HOST=clamav
CLAMAV_PORT=3310
CLAMAV_FAIL_OPEN=false
```

## 7. Build And Start Docker Deployment

Use both production compose files:

```bash
cd /opt/contraclaim-dms
docker compose --env-file .env \
  -f docker-compose.prod.yml \
  -f docker-compose.mongo-replicaset.yml \
  up -d --build
```

Check status:

```bash
docker compose --env-file .env \
  -f docker-compose.prod.yml \
  -f docker-compose.mongo-replicaset.yml \
  ps
```

Check internal health:

```bash
curl -fsS http://127.0.0.1:8080/health
docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml exec -T backend curl -fsS http://localhost:8000/health/ready
```

## 8. Configure Nginx And SSL

Use Certbot certificates if already present:

```bash
sudo certbot certificates
```

Proxy public domains to the loopback Docker gateway:

```nginx
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 600;
    proxy_connect_timeout 600;
    proxy_send_timeout 600;
    proxy_request_buffering off;
}
```

Always validate before reload:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 9. Restore RBAC, Organisation, And Project Data

Identify collections first:

```bash
mongosh contraclaim --quiet --eval 'db.getCollectionNames().sort().forEach(c => print(c + "\t" + db.getCollection(c).countDocuments({})))'
```

Take a new DB backup before import:

```bash
docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml exec -T mongo1 \
  mongodump --archive=/tmp/new-db-preimport.archive.gz --gzip --db contraclaim
docker cp contraclaim-mongo1-1:/tmp/new-db-preimport.archive.gz /var/backups/contraclaim-migration/<stamp>/new-db-preimport/
```

Dump only selected old collections, then restore only those collections into the new DB. The minimum collection set used here was:

```text
roles
permissions
users
organizations
projects
organization_memberships
project_memberships
role_assignments
plans
subscriptions
addons
entitlements
security_terms_acceptances
terms_versions
storage_settings
smtp_settings
notification_preferences
```

Verify counts after import and compare them with the old DB counts.

## 10. Post-Deployment Validation Checklist

Infrastructure:

- `docker compose ps` shows all required services healthy or running.
- `curl http://127.0.0.1:8080/health` returns `ok`.
- Backend `/health/ready` returns `ready`.
- Mongo replica set is `PRIMARY`, `SECONDARY`, `SECONDARY`.
- `ss -tulpn` shows no public MongoDB, Qdrant, FalkorDB, or Redis listeners.
- Nginx `web.contraclaim.com` returns HTTP 200 over HTTPS.
- API returns expected unauthenticated status, for example 401 for `/api/users/me`.

Application:

- Admin login works.
- Existing users are visible.
- Roles and permissions are assigned correctly.
- Organisations and projects are visible.
- Project access control works across multiple users.
- Upload, document view, OCR, search, Contract Q&A, and letter drafting work.

## 11. Routine Future Update Method

Before updating:

```bash
cd /opt/contraclaim-dms
git rev-parse HEAD
docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml ps
```

Take backups:

```bash
# Repeat the MongoDB, Docker volume, config, env, and filesystem backup process above.
```

Update and deploy:

```bash
cd /opt/contraclaim-dms
git pull --ff-only
docker compose --env-file .env \
  -f docker-compose.prod.yml \
  -f docker-compose.mongo-replicaset.yml \
  up -d --build
```

Validate:

```bash
docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml ps
curl -fsS http://127.0.0.1:8080/health
curl -sk https://web.contraclaim.com/health
```

## 12. Rollback Method

Rollback should be deliberate and should preserve the failed state for analysis.

To roll back the Docker deployment to a previous commit:

```bash
cd /opt/contraclaim-dms
git checkout <previous-good-commit>
docker compose --env-file .env \
  -f docker-compose.prod.yml \
  -f docker-compose.mongo-replicaset.yml \
  up -d --build
```

To restore previous Nginx config:

```bash
sudo cp -a /var/backups/contraclaim-migration/20260709-082356/nginx-cutover-20260709-123734/sites-available/* /etc/nginx/sites-available/
sudo cp -a /var/backups/contraclaim-migration/20260709-082356/nginx-cutover-20260709-123734/sites-enabled/* /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

To restart old services for rollback/reference:

```bash
sudo systemctl enable --now mongod
sudo systemctl enable --now contraclaim-api
sudo systemctl enable --now contraclaim-backend || true
docker start qdrant falkordb redis
```

To restore the old static frontend:

```bash
sudo tar -xzf /var/backups/contraclaim-migration/20260709-082356/filesystem/var-www-web-20260709-082356.tar.gz -C /var/www
```

To restore the old MongoDB archive, use `mongorestore` only after deciding which MongoDB target should receive the rollback data:

```bash
mongorestore --archive=/var/backups/contraclaim-migration/20260709-082356/mongo/local-mongodb-all-20260709-082356.archive.gz --gzip --drop
```

Do not delete the migration backups, old Docker volumes, or old service files until the new deployment has been accepted and retained through at least one normal backup cycle.
