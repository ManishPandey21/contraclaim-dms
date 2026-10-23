# VPS Deployment Guide (Ubuntu 25.1)

This document is a step-by-step checklist for deploying the ContraClaim DMS stack (FastAPI backend + Vite/React frontend) on a clean Ubuntu 25.1 VPS.

## 0) Assumptions
- You control DNS for your domain (e.g., `app.example.com` for API + frontend).
- Firewall allows inbound 80/443 (and 8000 temporarily for testing), outbound HTTPS.
- You have sudo on the box and a GitHub account for repo access.

## 1) Prepare the server
```bash
# login as root or a sudo user
sudo apt update && sudo apt install -y build-essential git curl unzip pkg-config \
  python3.11 python3.11-venv python3.11-dev \
  libxml2-dev libxslt1-dev libffi-dev libjpeg-dev zlib1g-dev qpdf ghostscript \
  tesseract-ocr libtesseract-dev ocrmypdf poppler-utils pngquant \
  redis-server

# optional: set timezone
sudo timedatectl set-timezone UTC

# create deploy user (recommended)
sudo adduser deploy
sudo usermod -aG sudo deploy
sudo -u deploy mkdir -p /home/deploy/.ssh && sudo chmod 700 /home/deploy/.ssh
```

## 2) GitHub access (SSH)
```bash
sudo -u deploy ssh-keygen -t ed25519 -C "deploy@example.com" -f /home/deploy/.ssh/id_ed25519 -N ""
cat /home/deploy/.ssh/id_ed25519.pub  # add this to GitHub SSH keys
```

## 3) Install Node.js (for frontend build)
```bash
sudo -u deploy bash -lc "curl -fsSL https://fnm.vercel.app/install | bash"
# reload shell or source fnm env, then:
sudo -u deploy bash -lc "fnm install 20 && fnm use 20"
```

## 4) Databases & queues
- **MongoDB**: Install MongoDB 7.x (official repo) and set it to start on boot. Example:
```bash
curl -fsSL https://pgp.mongodb.com/server-7.0.asc | sudo gpg --dearmor -o /usr/share/keyrings/mongodb-server-7.0.gpg
echo "deb [arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg] https://repo.mongodb.org/apt/ubuntu noble/mongodb-org/7.0 multiverse" | sudo tee /etc/apt/sources.list.d/mongodb-org-7.0.list
sudo apt update && sudo apt install -y mongodb-org
sudo systemctl enable --now mongod
```
- **Redis**: already installed above; ensure it’s running:
```bash
sudo systemctl enable --now redis-server
```
- **Qdrant (optional, if you use VECTORDB_URL)**:
```bash
sudo docker run -d --name qdrant -p 6333:6333 -v /var/lib/qdrant:/qdrant/storage qdrant/qdrant:latest
```

## 5) Clone the project
```bash
sudo -u deploy mkdir -p /opt/contraclaim && sudo chown deploy:deploy /opt/contraclaim
sudo -u deploy bash -lc "cd /opt/contraclaim && git clone git@github.com:YOUR_ORG/YOUR_REPO.git ."
```

## 6) Environment configuration
```bash
cd /opt/contraclaim
cp .env.example .env
```
Edit `.env` (and any backend `.env` overrides) with real values:
- `MONGODB_URI=mongodb://localhost:27017/contractdms`
- `OPENAI_API_KEY=...` (or other provider keys)
- `VECTORDB_URL` / `QDRANT_*` if using Qdrant
- SMTP/EMAIL settings, Redis password if you set one
- Set strong secrets; never commit `.env`.

## 7) Backend setup (FastAPI)
```bash
cd /opt/contraclaim/backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r rbac_backend/requirements.txt
```

### Test run (foreground)
```bash
cd /opt/contraclaim/backend
source .venv/bin/activate
uvicorn rbac_backend.main:app --host 0.0.0.0 --port 8000
```
Visit `http://SERVER_IP:8000/api/docs` to confirm.

### Systemd service
Create `/etc/systemd/system/contraclaim-api.service`:
```
[Unit]
Description=ContraClaim API
After=network.target

[Service]
User=deploy
Group=deploy
WorkingDirectory=/opt/contraclaim/backend
Environment="PYTHONPATH=/opt/contraclaim/backend"
ExecStart=/opt/contraclaim/backend/.venv/bin/uvicorn rbac_backend.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
Then enable:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now contraclaim-api
sudo systemctl status contraclaim-api
```

## 8) Frontend build (Vite/React)
```bash
cd /opt/contraclaim/client
npm install
npm run build
```
The static site is in `client/dist`.

## 9) Nginx reverse proxy
Install Nginx:
```bash
sudo apt install -y nginx
```
Example `/etc/nginx/sites-available/contraclaim`:
```
server {
    listen 80;
    server_name app.example.com;

    # Proxy API
    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Serve frontend
    root /opt/contraclaim/client/dist;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```
Enable site:
```bash
sudo ln -s /etc/nginx/sites-available/contraclaim /etc/nginx/sites-enabled/contraclaim
sudo nginx -t && sudo systemctl reload nginx
```
Use Certbot/Let’s Encrypt for TLS:
```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d app.example.com
```

## 10) Background jobs & monitoring
- Ensure `contraclaim-api` service stays active (systemd handles restarts).
- Check logs: `journalctl -u contraclaim-api -f`.
- For Redis/Mongo/Qdrant, use their CLI tools to verify connectivity.

## 11) Updating the app
```bash
cd /opt/contraclaim
sudo -u deploy git pull
cd backend && source .venv/bin/activate && pip install -r rbac_backend/requirements.txt
cd ../client && npm install && npm run build
sudo systemctl restart contraclaim-api
sudo systemctl reload nginx
```

## 12) Troubleshooting quick checks
- API health: `curl -i http://127.0.0.1:8000/api/docs`
- Logs: `journalctl -u contraclaim-api -n 200 -f`
- Permissions: ensure `deploy` owns `/opt/contraclaim` (`sudo chown -R deploy:deploy /opt/contraclaim`).
- OCR dependencies: `ocrmypdf --version`, `tesseract --version` to confirm binaries are present.

## 13) Security hygiene
- Rotate all secrets in `.env`; never commit them.
- Restrict SSH to key auth; disable root login if possible.
- Keep system updated: `sudo unattended-upgrades` or regular `apt upgrade`.
- Limit access to Mongo/Redis/Qdrant to localhost or VPC.

## 14) Optional: Dockerized deployment
- Build images using the provided Dockerfile (backend) and `npm run build` (frontend) served via Nginx.
- Use docker-compose to run API + Mongo + Redis + Qdrant with volume mounts for data.
```
