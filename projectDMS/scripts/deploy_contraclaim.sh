#!/bin/bash
set -e  # exit on any error

PYTHON_VERSION="3.12"
PYTHON_BIN="python${PYTHON_VERSION}"

# -------------------------------
# Color and logging functions
# -------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# -------------------------------
# Pre-installation checks
# -------------------------------
log_info "Running pre-installation checks..."

# Check OS
if ! grep -qi "ubuntu" /etc/os-release; then
    log_error "This script is designed for Ubuntu only."
fi

# Check required directories
[ -d "/opt/contraclaim/backend" ] || log_error "Backend directory missing: /opt/contraclaim/backend"
[ -d "/opt/contraclaim/client" ] || log_error "Frontend directory missing: /opt/contraclaim/client"

# Check Python & Node. Keep Ubuntu's default python3 untouched; application
# environments are explicitly built with the supported interpreter.
command -v "$PYTHON_BIN" >/dev/null || log_error "${PYTHON_BIN} not found"
command -v node >/dev/null || log_error "node not found"
command -v npm >/dev/null || log_error "npm not found"

# Check ports (optional)
if ss -tlnp | grep -q ":8000 "; then
    log_warn "Port 8000 is already in use. Backend may conflict."
fi
if ss -tlnp | grep -q ":80 "; then
    log_warn "Port 80 is already in use. Nginx may conflict."
fi

# -------------------------------
# Backend installation
# -------------------------------
log_info "Setting up backend..."
cd /opt/contraclaim/backend

# Recreate only the application venv when it was made by a different Python.
if [ -x "venv/bin/python" ] && [ "$(venv/bin/python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" != "$PYTHON_VERSION" ]; then
    mv venv "venv.python-$(date -u +%Y%m%dT%H%M%SZ)"
fi
if [ ! -d "venv" ]; then
    "$PYTHON_BIN" -m venv venv
fi
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
deactivate

# -------------------------------
# Frontend installation & build
# -------------------------------
log_info "Setting up frontend..."
cd /opt/contraclaim/client

npm install
npm run build

# -------------------------------
# Copy dist to web root
# -------------------------------
log_info "Copying build to /var/www/web..."
sudo mkdir -p /var/www/web
sudo rm -rf /var/www/web/*
sudo cp -r dist/* /var/www/web/
sudo chown -R www-data:www-data /var/www/web

# -------------------------------
# Setup systemd service for backend
# -------------------------------
log_info "Configuring backend service..."
cat > /etc/systemd/system/contraclaim-backend.service <<EOF
[Unit]
Description=ContraClaim Backend
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/contraclaim/backend
Environment="PATH=/opt/contraclaim/backend/venv/bin"
ExecStart=/opt/contraclaim/backend/venv/bin/gunicorn -w 4 -b 127.0.0.1:8000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable contraclaim-backend
systemctl restart contraclaim-backend

# -------------------------------
# Setup Nginx configuration
# -------------------------------
log_info "Configuring Nginx..."
if [ ! -f /etc/nginx/sites-available/contraclaim ]; then
    cat > /etc/nginx/sites-available/contraclaim <<EOF
server {
    listen 80;
    server_name _;

    root /var/www/web;
    index index.html;

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
}
EOF
fi

ln -sf /etc/nginx/sites-available/contraclaim /etc/nginx/sites-enabled/
nginx -t || log_error "Nginx configuration test failed"
systemctl restart nginx

# -------------------------------
# Post-installation checks
# -------------------------------
log_info "Running post-installation checks..."

# Check backend service
if systemctl is-active --quiet contraclaim-backend; then
    log_info "Backend service is running."
else
    log_error "Backend service failed to start."
fi

# Check web root content
if [ -f /var/www/web/index.html ]; then
    log_info "Frontend files copied successfully."
else
    log_error "index.html not found in /var/www/web"
fi

# Test local endpoints
if curl -s -o /dev/null -w "%{http_code}" http://localhost/ | grep -q "200"; then
    log_info "Frontend responds with HTTP 200."
else
    log_warn "Frontend did not return HTTP 200. Check Nginx."
fi

# Optional API health check (adjust endpoint as needed)
if curl -s http://localhost/api/health >/dev/null 2>&1; then
    log_info "Backend API health check passed."
else
    log_warn "Backend API health endpoint not responding (maybe /health not implemented)."
fi

# -------------------------------
# Final summary
# -------------------------------
log_info "Deployment completed successfully!"
echo "----------------------------------------------"
echo " Backend service : systemctl status contraclaim-backend"
echo " Nginx config    : /etc/nginx/sites-available/contraclaim"
echo " Web root        : /var/www/web"
echo " API base URL    : http://your-server-ip/api/"
echo " Frontend URL    : http://your-server-ip/"
echo "----------------------------------------------"
