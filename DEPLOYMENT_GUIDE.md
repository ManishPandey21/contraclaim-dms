# ContraClaim DMS — Ubuntu Server Deployment Guide

Bare-metal deployment guide for running the ContraClaim DMS on an Ubuntu server
without Docker. The backend runs natively with Python/uvicorn at
`/opt/contraclaim/backend`, the frontend is built with Node.js/Vite at
`/opt/contraclaim/client`, and the compiled SPA is served from `/var/www/web`
via Nginx.

---

## Table of Contents

1. [Server Requirements](#1-server-requirements)
2. [Initial Server Setup](#2-initial-server-setup)
3. [Install System Dependencies](#3-install-system-dependencies)
4. [Install Infrastructure Services](#4-install-infrastructure-services)
5. [Deploy the Code](#5-deploy-the-code)
6. [Configure the Backend](#6-configure-the-backend)
7. [Configure the Frontend](#7-configure-the-frontend)
8. [Build and Deploy the Frontend](#8-build-and-deploy-the-frontend)
9. [Set Up the Backend Python Environment](#9-set-up-the-backend-python-environment)
10. [Create Systemd Services](#10-create-systemd-services)
11. [Configure Nginx](#11-configure-nginx)
12. [Enable and Start Services](#12-enable-and-start-services)
13. [Verify Deployment](#13-verify-deployment)
14. [Update / Redeploy](#14-update--redeploy)
15. [Automated Deployment Script](#15-automated-deployment-script)
16. [Backup](#16-backup)
17. [Troubleshooting](#17-troubleshooting)
18. [Deployment Checklist](#18-deployment-checklist)

---

## 1. Server Requirements

| Resource     | Minimum         | Recommended      |
|--------------|-----------------|------------------|
| OS           | Ubuntu 22.04 LTS | Ubuntu 24.04 LTS |
| CPU          | 4 cores         | 8 cores          |
| RAM          | 8 GB            | 16 GB            |
| Disk         | 80 GB SSD       | 200 GB SSD       |
| Open Ports   | 22, 80          | 22, 80, 443      |

---

## 2. Initial Server Setup

```bash
ssh ubuntu@YOUR_SERVER_IP

# Update system packages
sudo apt update && sudo apt upgrade -y

# Set timezone
sudo timedatectl set-timezone Asia/Kolkata

# Configure firewall
sudo apt install -y ufw
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
```

---

## 3. Install System Dependencies

### Core tools

```bash
sudo apt install -y \
  build-essential git curl wget unzip nano htop \
  ca-certificates gnupg software-properties-common \
  libpq-dev libffi-dev libssl-dev \
  tesseract-ocr tesseract-ocr-eng \
  ghostscript qpdf unpaper pngquant \
  libheif-dev libxml2-dev libxslt1-dev
```

### Python 3.11

```bash
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev
```

### Node.js 20.x (LTS)

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
```

### Nginx

```bash
sudo apt install -y nginx
```

### ClamAV (optional — for antivirus scanning)

```bash
sudo apt install -y clamav clamav-daemon
sudo systemctl enable clamav-freshclam
sudo systemctl start clamav-freshclam
sudo systemctl enable clamav-daemon
sudo systemctl start clamav-daemon
```

Verify:

```bash
node --version        # v20.x
python3.11 --version  # 3.11.x
nginx -v              # nginx/1.x
```

---

## 4. Install Infrastructure Services

### MongoDB 8.0

```bash
curl -fsSL https://www.mongodb.org/static/pgp/server-8.0.asc | \
  sudo gpg --dearmor -o /usr/share/keyrings/mongodb-server-8.0.gpg

echo "deb [ signed-by=/usr/share/keyrings/mongodb-server-8.0.gpg ] \
  https://repo.mongodb.org/apt/ubuntu $(lsb_release -cs)/mongodb-org/8.0 multiverse" | \
  sudo tee /etc/apt/sources.list.d/mongodb-org-8.0.list

sudo apt update && sudo apt install -y mongodb-org
sudo systemctl enable mongod && sudo systemctl start mongod
```

Enable replica set for production (required by the backend config validator):

```bash
# Add to /etc/mongod.conf under replication:
#   replication:
#     replSetName: "rs0"
sudo nano /etc/mongod.conf
sudo systemctl restart mongod

# Initialize the replica set
mongosh --eval 'rs.initiate()'
```

### Redis 7.x

```bash
sudo apt install -y redis-server
sudo systemctl enable redis-server && sudo systemctl start redis-server
```

Set a password in `/etc/redis/redis.conf`:

```bash
sudo sed -i 's/^# requirepass .*/requirepass YOUR_REDIS_PASSWORD/' /etc/redis/redis.conf
sudo systemctl restart redis-server
```

### Qdrant (Docker container or binary)

The simplest approach is to run Qdrant via Docker:

```bash
sudo apt install -y docker.io
sudo systemctl enable docker && sudo systemctl start docker

sudo docker run -d --name qdrant \
  --restart unless-stopped \
  -p 6333:6333 \
  -v qdrant_data:/qdrant/storage \
  -e QDRANT__SERVICE__API_KEY=YOUR_QDRANT_API_KEY \
  qdrant/qdrant:v1.12.5
```

### FalkorDB (Docker container)

```bash
sudo docker run -d --name falkordb \
  --restart unless-stopped \
  -p 6380:6379 \
  -v falkordb_data:/data \
  falkordb/falkordb:v4.0.8 \
  --requirepass YOUR_FALKORDB_PASSWORD \
  --save 900 1 --save 300 10 \
  --appendonly yes \
  --maxmemory 2gb \
  --maxmemory-policy volatile-lru
```

---

## 5. Deploy the Code

Create the application directories:

```bash
sudo mkdir -p /opt/contraclaim/{backend,client}
sudo mkdir -p /var/www/web
sudo chown -R "$USER":"$USER" /opt/contraclaim /var/www/web
```

Copy or clone the code:

```bash
# Option A: Git clone
cd /opt/contraclaim
git clone YOUR_REPOSITORY_URL .

# Option B: SCP / rsync from local machine
# scp -r ./backend/* ubuntu@SERVER:/opt/contraclaim/backend/
# scp -r ./client/* ubuntu@SERVER:/opt/contraclaim/client/
```

After transfer, the layout should be:

```
/opt/contraclaim/
├── backend/
│   ├── rbac_backend/
│   │   ├── main.py
│   │   ├── core/config.py
│   │   ├── routers/
│   │   ├── services/
│   │   ├── models/
│   │   └── requirements.txt
│   ├── conftest.py
│   └── .env
├── client/
│   ├── src/
│   ├── package.json
│   ├── vite.config.ts
│   ├── .env.production
│   └── index.html
└── .env
```

---

## 6. Configure the Backend

Create the backend environment file:

```bash
cp /opt/contraclaim/.env.example /opt/contraclaim/backend/.env
nano /opt/contraclaim/backend/.env
```

Key production values to set:

```env
ENVIRONMENT=production
ENABLE_API_DOCS=false
ALLOW_DEV_HEADERS=false

# Database — localhost since MongoDB runs on the same server
DATABASE_URL=mongodb://localhost:27017/contraclaim?replicaSet=rs0
MONGODB_DATABASE=contraclaim
MONGODB_REPLICA_SET=rs0

# Authentication
SECRET_KEY=<generate-with: python3 -c "import secrets; print(secrets.token_hex(32))">
AUTH_COOKIE_SECURE=true
AUTH_COOKIE_SAMESITE=lax

# CORS — your production domain
CORS_ORIGINS=https://your-domain.com
PUBLIC_BASE_URL=https://your-domain.com
PUBLIC_API_URL=https://your-domain.com/api
APP_URL=https://your-domain.com

# AWS S3 storage
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
AWS_REGION=ap-south-1
AWS_BUCKET_NAME=your-bucket

# OpenAI
OPENAI_API_KEY=sk-...

# Infrastructure — localhost references
APP_REDIS_URL=redis://:YOUR_REDIS_PASSWORD@localhost:6379/1
RUNTIME_STATE_REDIS_URL=redis://:YOUR_REDIS_PASSWORD@localhost:6379/1
CONTRACT_QUEUE_REDIS_URL=redis://:YOUR_REDIS_PASSWORD@localhost:6379/0
VECTORDB_URL=http://localhost:6333
QDRANT_API_KEY=YOUR_QDRANT_API_KEY
FALKORDB_URL=redis://:YOUR_FALKORDB_PASSWORD@localhost:6380
FALKORDB_HOST=localhost
FALKORDB_PORT=6380
FALKORDB_PASSWORD=YOUR_FALKORDB_PASSWORD

# SMTP
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email
SMTP_PASSWORD=your-app-password
SMTP_FROM_EMAIL=noreply@your-domain.com
SMTP_SETTINGS_ENCRYPTION_KEY=<generate-fernet-key>

# Antivirus (if ClamAV is installed)
ANTIVIRUS_ENABLED=true
CLAMAV_HOST=localhost
CLAMAV_PORT=3310
CLAMAV_TIMEOUT=30
CLAMAV_FAIL_OPEN=false

# Metrics
METRICS_ENABLED=true
METRICS_TOKEN=<generate-a-scrape-token>
LOG_LEVEL=INFO

# LangGraph
LANGGRAPH_ENABLED=false
LANGGRAPH_API_TOKEN=change-me

# Uploads (absolute paths in production)
UPLOADS_DIR=/opt/contraclaim/backend/uploads
SECURE_UPLOADS_DIR=/opt/contraclaim/backend/uploads
```

Create the uploads and logs directories:

```bash
mkdir -p /opt/contraclaim/backend/uploads /opt/contraclaim/backend/logs
```

---

## 7. Configure the Frontend

Edit the production environment file:

```bash
nano /opt/contraclaim/client/.env.production
```

```env
VITE_API_BASE_URL=https://your-domain.com/api
VITE_LANGGRAPH_ENABLED=false
```

> **Important**: Vite reads `VITE_*` variables at **build time**. Always
> update `.env.production` before running `npm run build`.

---

## 8. Build and Deploy the Frontend

```bash
cd /opt/contraclaim/client

# Install dependencies
npm ci

# Build the production bundle
npm run build

# Copy built assets to the web root
rm -rf /var/www/web/*
cp -r dist/* /var/www/web/

# Set correct ownership for Nginx
sudo chown -R www-data:www-data /var/www/web
```

Verify the build output:

```bash
ls -la /var/www/web/
# Should contain: index.html, assets/, etc.
```

---

## 9. Set Up the Backend Python Environment

```bash
cd /opt/contraclaim/backend

# Create virtual environment
python3.11 -m venv venv

# Activate
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip setuptools wheel

# Install dependencies from the rbac_backend requirements
pip install -r rbac_backend/requirements.txt

# Verify uvicorn is available
python -m uvicorn --version
```

---

## 10. Create Systemd Services

### Backend API Service

```bash
sudo tee /etc/systemd/system/contraclaim-backend.service > /dev/null << 'EOF'
[Unit]
Description=ContraClaim DMS Backend (uvicorn)
After=network.target mongod.service redis-server.service
Wants=mongod.service redis-server.service

[Service]
Type=exec
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/contraclaim/backend
EnvironmentFile=/opt/contraclaim/backend/.env
ExecStart=/opt/contraclaim/backend/venv/bin/uvicorn \
  rbac_backend.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  --workers 4 \
  --log-level info \
  --access-log
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=contraclaim-backend

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/contraclaim/backend/uploads /opt/contraclaim/backend/logs
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
```

### Contract Worker Service (background queue processor)

```bash
sudo tee /etc/systemd/system/contraclaim-worker.service > /dev/null << 'EOF'
[Unit]
Description=ContraClaim Contract Ingestion Worker
After=contraclaim-backend.service
Wants=contraclaim-backend.service

[Service]
Type=exec
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/contraclaim/backend
EnvironmentFile=/opt/contraclaim/backend/.env
Environment=START_BACKGROUND_SERVICES=false
Environment=START_CONTRACT_QUEUE_WORKERS=true
ExecStart=/opt/contraclaim/backend/venv/bin/python \
  -m rbac_backend.worker
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=contraclaim-worker

NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/contraclaim/backend/uploads /opt/contraclaim/backend/logs
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
```

---

## 11. Configure Nginx

Create the Nginx site configuration:

```bash
sudo tee /etc/nginx/sites-available/contraclaim > /dev/null << 'NGINX'
# --- ContraClaim DMS ---

upstream backend_api {
    server 127.0.0.1:8000;
    keepalive 32;
}

server {
    listen 80;
    server_name your-domain.com;

    # -- Security headers --
    add_header X-Content-Type-Options    "nosniff"                           always;
    add_header X-Frame-Options           "SAMEORIGIN"                        always;
    add_header Referrer-Policy           "strict-origin-when-cross-origin"   always;
    add_header Permissions-Policy        "camera=(), microphone=(), geolocation=(), payment=()" always;
    add_header Content-Security-Policy   "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data: blob:; connect-src 'self' https: wss:; worker-src 'self' blob:; frame-ancestors 'self'; base-uri 'self'; form-action 'self'; object-src 'none'" always;

    # -- Upload size limit (match backend GENERAL_UPLOAD_MAX_FILE_SIZE_MB) --
    client_max_body_size 100M;

    # -- Backend API --
    location /api/ {
        proxy_pass         http://backend_api;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   Connection        "";
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }

    # -- WebSocket support (notifications, real-time) --
    location /api/ws {
        proxy_pass         http://backend_api;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade    $http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_set_header   Host       $host;
        proxy_read_timeout 86400s;
    }

    # -- Backend health check (direct) --
    location = /health/ready {
        proxy_pass http://backend_api/health/ready;
    }

    # -- Frontend SPA --
    location / {
        root  /var/www/web;
        index index.html;
        try_files $uri $uri/ /index.html;
    }

    # -- Static asset caching --
    location /assets/ {
        root    /var/www/web;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # -- Deny hidden files --
    location ~ /\. {
        deny all;
        return 404;
    }
}
NGINX
```

Enable the site and disable the default:

```bash
sudo ln -sf /etc/nginx/sites-available/contraclaim /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
```

---

## 12. Enable and Start Services

```bash
# Reload systemd to pick up new unit files
sudo systemctl daemon-reload

# Enable services to start on boot
sudo systemctl enable contraclaim-backend
sudo systemctl enable contraclaim-worker

# Start services
sudo systemctl start contraclaim-backend
sudo systemctl start contraclaim-worker
sudo systemctl restart nginx

# Check status
sudo systemctl status contraclaim-backend --no-pager
sudo systemctl status contraclaim-worker --no-pager
sudo systemctl status nginx --no-pager
```

---

## 13. Verify Deployment

```bash
# Backend health
curl -fsS http://127.0.0.1:8000/health/ready

# Through Nginx
curl -fsS http://localhost/api/health

# Frontend serves index.html
curl -fsS -o /dev/null -w "%{http_code}" http://localhost/

# MongoDB
mongosh --quiet --eval "db.adminCommand('ping')"

# Redis
redis-cli -a YOUR_REDIS_PASSWORD ping

# Qdrant
curl -H "api-key: YOUR_QDRANT_API_KEY" http://localhost:6333/collections

# Backend logs
sudo journalctl -u contraclaim-backend -f --no-pager

# Worker logs
sudo journalctl -u contraclaim-worker -f --no-pager
```

Open in browser: `http://your-domain.com`

---

## 14. Update / Redeploy

```bash
cd /opt/contraclaim

# Pull latest code
git pull origin main

# Rebuild frontend
cd client
npm ci
npm run build
sudo rm -rf /var/www/web/*
sudo cp -r dist/* /var/www/web/
sudo chown -R www-data:www-data /var/www/web

# Update backend dependencies
cd /opt/contraclaim/backend
source venv/bin/activate
pip install -r rbac_backend/requirements.txt

# Restart services
sudo systemctl restart contraclaim-backend
sudo systemctl restart contraclaim-worker

# Verify
curl -fsS http://127.0.0.1:8000/health/ready
```

---

## 15. Automated Deployment Script

A ready-to-use deployment script is available at
[`scripts/deploy_ubuntu.sh`](scripts/deploy_ubuntu.sh). It automates
every step: dependency installation, frontend build, backend virtualenv
update, and service restart. See the script header for usage.

```bash
# First-time full deployment (includes system package installation)
sudo bash scripts/deploy_ubuntu.sh --full

# Subsequent code-only deployments (skip system packages)
sudo bash scripts/deploy_ubuntu.sh
```

---

## 16. Backup

### MongoDB

```bash
mongodump --archive=/var/backups/contraclaim/mongo-$(date +%Y%m%d).gz --gzip
```

### Application uploads

```bash
tar czf /var/backups/contraclaim/uploads-$(date +%Y%m%d).tar.gz \
  -C /opt/contraclaim/backend uploads/
```

### Qdrant snapshots

```bash
curl -X POST -H "api-key: YOUR_QDRANT_API_KEY" \
  http://localhost:6333/collections/contracts/snapshots
```

### Automated daily backup (cron)

```bash
sudo tee /etc/cron.daily/contraclaim-backup > /dev/null << 'CRON'
#!/bin/bash
BACKUP_DIR=/var/backups/contraclaim/$(date +%Y%m%d)
mkdir -p "$BACKUP_DIR"
mongodump --archive="$BACKUP_DIR/mongo.gz" --gzip --quiet
tar czf "$BACKUP_DIR/uploads.tar.gz" -C /opt/contraclaim/backend uploads/ 2>/dev/null
find /var/backups/contraclaim -maxdepth 1 -type d -mtime +30 -exec rm -rf {} \;
CRON
sudo chmod +x /etc/cron.daily/contraclaim-backup
```

---

## 17. Troubleshooting

### Backend won't start

```bash
sudo journalctl -u contraclaim-backend --no-pager -n 100
# Common causes:
#   - Missing or invalid .env values (SECRET_KEY, DATABASE_URL)
#   - MongoDB not running or replica set not initialized
#   - Python dependency missing — re-run pip install
```

### Frontend shows blank page

```bash
ls -la /var/www/web/index.html
# If missing, re-run the build and copy steps
# If present, check Nginx try_files and browser console for 404s
```

### API returns 502 Bad Gateway

```bash
# Backend process not running
sudo systemctl status contraclaim-backend
# Nginx upstream misconfigured
sudo nginx -t
```

### Port conflicts

```bash
sudo ss -tulpn | grep -E ':(80|8000|27017|6333|6379|6380)\b'
```

### Permission denied on uploads

```bash
sudo chown -R ubuntu:ubuntu /opt/contraclaim/backend/uploads
```

---

## 18. Deployment Checklist

- [ ] Ubuntu 22.04+ freshly updated
- [ ] Firewall allows ports 22, 80, 443
- [ ] Python 3.11, Node.js 20, Nginx installed
- [ ] MongoDB running with replica set enabled
- [ ] Redis running with password set
- [ ] Qdrant running (Docker) with API key
- [ ] FalkorDB running (Docker) with password
- [ ] Code deployed to `/opt/contraclaim/`
- [ ] `backend/.env` configured with production values
- [ ] `client/.env.production` configured with correct API URL
- [ ] Frontend built and `dist/*` copied to `/var/www/web/`
- [ ] Python venv created and dependencies installed
- [ ] Systemd services created and enabled
- [ ] Nginx site configured, tested (`nginx -t`), and reloaded
- [ ] `curl http://localhost/api/health` returns success
- [ ] Browser loads the application at the configured domain
- [ ] Backup cron job configured
- [ ] ClamAV running (if `ANTIVIRUS_ENABLED=true`)
- [ ] SSL/TLS configured (Certbot or load balancer)

---

## Optional: HTTPS with Certbot

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com

# Auto-renewal is configured automatically.
# After certificate issuance, update environment files:
#   PUBLIC_BASE_URL=https://your-domain.com
#   CORS_ORIGINS=https://your-domain.com
#   VITE_API_BASE_URL=https://your-domain.com/api
# Then rebuild the frontend and restart the backend.
```
