# Deploy ContractDMS (FastAPI + React/Vite) to Ubuntu 20.04 VPS using Apache2

This guide walks through deploying the repository in `c:/SaaS/ContractDMS` to a fresh Ubuntu 20.04 VPS with:

- Backend: FastAPI/Uvicorn (served by systemd), proxied by Apache2 at /api
- Frontend: React (Vite build) served as static files by Apache2
- Optional: Free HTTPS using Let’s Encrypt (Certbot with Apache plugin)

Assumptions:

- You have sudo access on the VPS and a domain (optional but recommended).
- You will use `/srv/contractdms` as the application root on the server.
- The API base path is `/api` (already used by the frontend code).

Adjust names/paths as needed.

---

## 0) Connect to the VPS

From your local machine:

```bash
ssh ubuntu@YOUR_SERVER_IP
# or
ssh youruser@YOUR_SERVER_IP
```

Optionally create a non-root sudo user:

```bash
sudo adduser deployer
sudo usermod -aG sudo deployer
su - deployer
```

---

## 1) Update system and install core packages

```bash
sudo apt update && sudo apt -y upgrade
sudo apt -y install build-essential git ufw curl wget
```

Enable firewall:

```bash
sudo ufw allow OpenSSH
sudo ufw enable
sudo ufw status
```

---

## 2) Install Node.js (LTS) and NPM (for building frontend)

Use NodeSource (example: Node 20.x LTS):

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt -y install nodejs
node -v
npm -v
```

---

## 3) Install Python and venv

Ubuntu 20.04 includes Python 3.8 which is compatible with FastAPI/Uvicorn. If you prefer 3.11, use deadsnakes; otherwise keep 3.8.

Python 3.8 (default):

```bash
sudo apt -y install python3 python3-venv python3-dev
python3 --version
```

If you need Python 3.11 instead (optional):

```bash
sudo apt -y install software-properties-common
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update
sudo apt -y install python3.11 python3.11-venv python3.11-dev
python3.11 --version
```

Pick one and use consistently below (replace python3 with python3.11 if you installed it).

---

## 4) Install and configure Apache2

```bash
sudo apt -y install apache2
sudo ufw allow "Apache Full"
sudo systemctl enable apache2
sudo systemctl start apache2
```

Enable required Apache modules:

```bash
sudo a2enmod rewrite proxy proxy_http headers ssl
sudo systemctl reload apache2
```

Create app root:

```bash
sudo mkdir -p /srv/contractdms
sudo chown -R $USER:$USER /srv/contractdms
```

---

## 5) Get the application code onto the server

Option A: Clone from Git (recommended)

```bash
cd /srv/contractdms
git clone YOUR_REPO_URL app
```

Option B: Upload via scp/rsync (from your local machine)

```bash
# From local terminal (PowerShell/Bash):
# Replace with your user and server IP
scp -r c:/SaaS/ContractDMS/* youruser@YOUR_SERVER_IP:/srv/contractdms/app
```

Directory layout on server should look like:

```
/srv/contractdms/app
  ├── backend/
  │   └── rbac_backend/
  │       └── main.py
  └── client/
      └── package.json
```

---

## 6) Backend setup (FastAPI + Uvicorn)

Create virtual environment and install requirements:

```bash
cd /srv/contractdms/app/backend
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip

# requirements.txt is at backend/rbac_backend/requirements.txt
pip install -r rbac_backend/requirements.txt
deactivate
```

Create a backend environment file (adjust values):

```bash
nano /srv/contractdms/app/backend/.env
```

Example contents (placeholders):

```
# MongoDB
MONGO_URI=mongodb://localhost:27017/contractdms
MONGO_DB_NAME=contractdms

# Auth / Security
JWT_SECRET=change_this_secret
ACCESS_TOKEN_EXPIRE_MINUTES=60

# Optional AWS S3 config if used
AWS_ACCESS_KEY_ID=your_key
AWS_SECRET_ACCESS_KEY=your_secret
AWS_S3_BUCKET=your_bucket
AWS_REGION=ap-south-1
```

Create a systemd service for the backend (runs on 127.0.0.1:8000):

```bash
sudo nano /etc/systemd/system/contractdms-backend.service
```

Paste:

```
[Unit]
Description=ContractDMS FastAPI backend (Uvicorn)
After=network.target

[Service]
User=%i
Group=www-data
WorkingDirectory=/srv/contractdms/app/backend
EnvironmentFile=/srv/contractdms/app/backend/.env
ExecStart=/srv/contractdms/app/backend/.venv/bin/python -m uvicorn rbac_backend.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

# Hardening (optional)
ProtectSystem=full
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

Note:

- If your system user is not the same as `%i`, replace `User=%i` with `User=deployer` (or your sudo username).

Reload and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable contractdms-backend
sudo systemctl start contractdms-backend
sudo systemctl status contractdms-backend
```

Check logs:

```bash
journalctl -u contractdms-backend -f
```

---

## 7) Frontend build (Vite)

Build on the server:

```bash
cd /srv/contractdms/app/client
npm ci  # or `npm install` if no package-lock.json
npm run build
```

This creates `client/dist`.

Deploy static files to a web directory:

```bash
sudo mkdir -p /var/www/contractdms
sudo rsync -av --delete /srv/contractdms/app/client/dist/ /var/www/contractdms/
```

Any time you rebuild, rerun the rsync to update the served files.

---

## 8) Apache2 VirtualHost configuration

Create a new Apache site:

```bash
sudo nano /etc/apache2/sites-available/contractdms.conf
```

Paste (replace `example.com` with your domain or server IP):

```
<VirtualHost *:80>
    ServerName example.com
    ServerAlias www.example.com

    # Serve the React build
    DocumentRoot /var/www/contractdms

    <Directory /var/www/contractdms>
        Options Indexes FollowSymLinks
        AllowOverride None
        Require all granted

        # SPA fallback: serve index.html for non-file routes, but exclude /api
        RewriteEngine On
        # If the request is for an existing file or directory, serve it
        RewriteCond %{REQUEST_FILENAME} -f [OR]
        RewriteCond %{REQUEST_FILENAME} -d
        RewriteRule ^ - [L]

        # Do not rewrite API routes
        RewriteCond %{REQUEST_URI} !^/api/
        # Otherwise, serve index.html
        RewriteRule ^ /index.html [L]
    </Directory>

    # Reverse proxy to FastAPI backend
    ProxyPreserveHost On
    ProxyPass /api/ http://127.0.0.1:8000/
    ProxyPassReverse /api/ http://127.0.0.1:8000/

    # Optional: increase upload limits (50 MB)
    LimitRequestBody 52428800

    ErrorLog ${APACHE_LOG_DIR}/contractdms_error.log
    CustomLog ${APACHE_LOG_DIR}/contractdms_access.log combined
</VirtualHost>
```

Enable the site and disable the default site (optional):

```bash
sudo a2ensite contractdms.conf
sudo a2dissite 000-default.conf
sudo apachectl configtest
sudo systemctl reload apache2
```

Visit:

- http://example.com (frontend)
- http://example.com/api/docs (FastAPI docs, if enabled)

---

## 9) HTTPS with Let’s Encrypt (optional but recommended)

Install Certbot with Apache plugin:

```bash
sudo apt -y install certbot python3-certbot-apache
```

Obtain and install certificates:

```bash
sudo certbot --apache -d example.com -d www.example.com
```

Auto renewal is set by default (cron/systemd timer). Test:

```bash
sudo certbot renew --dry-run
```

---

## 10) Environment variables and secrets

- Keep secrets in `/srv/contractdms/app/backend/.env` with proper permissions:

```bash
sudo chown root:www-data /srv/contractdms/app/backend/.env
sudo chmod 640 /srv/contractdms/app/backend/.env
```

---

## 11) Deploy updates (pull/build/restart)

When you push new commits:

Backend-only changes:

```bash
cd /srv/contractdms/app
git pull
cd backend
source .venv/bin/activate
pip install -r rbac_backend/requirements.txt
deactivate
sudo systemctl restart contractdms-backend
journalctl -u contractdms-backend -f
```

Frontend-only changes:

```bash
cd /srv/contractdms/app/client
git pull || true   # if clone used; else skip
npm ci
npm run build
sudo rsync -av --delete /srv/contractdms/app/client/dist/ /var/www/contractdms/
sudo systemctl reload apache2
```

---

## 12) Troubleshooting

- Backend logs:

```bash
journalctl -u contractdms-backend -f
```

- Apache logs:

```bash
sudo tail -f /var/log/apache2/error.log
sudo tail -f /var/log/apache2/access.log
```

- Check service status:

```bash
sudo systemctl status contractdms-backend
sudo systemctl status apache2
```

- Verify port binding:

```bash
ss -tulpn | grep -E "8000|80|443"
```

- Health check:
  - `curl -I http://127.0.0.1:8000/` on the server (should return 200/404 depending on routes)
  - `curl -I http://example.com/api/` and `curl -I http://example.com` externally

---

## 13) Optional: MongoDB installation (if using local DB)

If you need a local MongoDB (not Atlas), install MongoDB Community for Ubuntu 20.04:

```bash
# MongoDB 6.0 example (check docs for up-to-date instructions)
wget -qO - https://www.mongodb.org/static/pgp/server-6.0.asc | sudo apt-key add -
echo "deb [ arch=amd64 ] https://repo.mongodb.org/apt/ubuntu focal/mongodb-org/6.0 multiverse" | sudo tee /etc/apt/sources.list.d/mongodb-org-6.0.list
sudo apt update
sudo apt -y install mongodb-org
sudo systemctl enable mongod
sudo systemctl start mongod
sudo systemctl status mongod
```

Set `MONGO_URI=mongodb://localhost:27017/contractdms` (or your own string) in the `.env`.

---

## 14) Optional: Zero-downtime restarts

Use `systemctl reload` when supported by your runtime; otherwise:

```bash
sudo systemctl restart contractdms-backend
```

For horizontal scaling or rolling restarts you’d use a process manager or container orchestration. For a single VPS, `systemd` is sufficient.

---

## 15) Security hardening (quick wins)

- Disable password SSH login; use SSH keys only.
- Fail2ban:

```bash
sudo apt -y install fail2ban
```

- Keep system updated:

```bash
sudo apt update && sudo apt -y upgrade
```

---

Deployment complete. Your app should now be accessible at your domain/IP with:

- Frontend: `http(s)://example.com`
- API: `http(s)://example.com/api/...`
