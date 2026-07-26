#!/usr/bin/env bash
# ==============================================================================
# ContraClaim DMS — Ubuntu Server Deployment Script
# ==============================================================================
#
# Usage:
#   sudo bash deploy_ubuntu.sh          # Code-only deployment (skip system deps)
#   sudo bash deploy_ubuntu.sh --full   # Full deployment (install system deps)
#
# Paths:
#   Backend code:     /opt/contraclaim/backend
#   Frontend code:    /opt/contraclaim/client
#   Frontend build:   /var/www/web  (contents of client/dist)
#   Python venv:      /opt/contraclaim/backend/venv
#
# Prerequisites:
#   - Ubuntu 22.04 LTS or 24.04 LTS
#   - Root or sudo access
#   - /opt/contraclaim/backend/.env configured
#   - /opt/contraclaim/client/.env.production configured
#   - MongoDB, Redis, Qdrant, and FalkorDB running
# ==============================================================================

set -euo pipefail
IFS=$'\n\t'

# ─── Configuration ────────────────────────────────────────────────────────────

BACKEND_DIR="/opt/contraclaim/backend"
CLIENT_DIR="/opt/contraclaim/client"
WEB_ROOT="/var/www/web"
VENV_DIR="${BACKEND_DIR}/venv"
REQUIREMENTS="${BACKEND_DIR}/rbac_backend/requirements.txt"
PYTHON_VERSION="3.12"
PYTHON_BIN="python${PYTHON_VERSION}"
NGINX_SITE="/etc/nginx/sites-available/contraclaim"
BACKEND_SERVICE="contraclaim-backend"
WORKER_SERVICE="contraclaim-worker"
APP_USER="${SUDO_USER:-ubuntu}"

# ─── Colors ───────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { printf "${CYAN}[DEPLOY]${NC} %s\n" "$*"; }
ok()   { printf "${GREEN}[  OK  ]${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}[ WARN ]${NC} %s\n" "$*"; }
err()  { printf "${RED}[ERROR ]${NC} %s\n" "$*" >&2; }
die()  { err "$*"; exit 1; }

# ─── Pre-flight checks ───────────────────────────────────────────────────────

[[ $EUID -eq 0 ]] || die "This script must be run as root (use sudo)."
[[ -d "$BACKEND_DIR" ]] || die "Backend directory not found: $BACKEND_DIR"
[[ -d "$CLIENT_DIR" ]] || die "Client directory not found: $CLIENT_DIR"
[[ -f "$BACKEND_DIR/.env" ]] || die "Backend .env not found. Copy from .env.example and configure."
[[ -f "$CLIENT_DIR/.env.production" ]] || die "Client .env.production not found. Create it with VITE_API_BASE_URL."
[[ -f "$REQUIREMENTS" ]] || die "Requirements file not found: $REQUIREMENTS"

FULL_INSTALL=false
for arg in "$@"; do
  case "$arg" in
    --full) FULL_INSTALL=true ;;
    --help|-h)
      echo "Usage: sudo bash $0 [--full]"
      echo "  --full  Install system packages, Python, Node.js, Nginx"
      echo "  (none)  Code-only deployment: build, install deps, restart services"
      exit 0
      ;;
  esac
done

# ─── Step 1: System Dependencies (--full only) ───────────────────────────────

install_system_deps() {
  log "Installing system dependencies..."

  apt-get update -qq
  apt-get upgrade -y -qq

  apt-get install -y -qq \
    build-essential git curl wget unzip nano htop \
    ca-certificates gnupg software-properties-common \
    libpq-dev libffi-dev libssl-dev \
    tesseract-ocr tesseract-ocr-eng \
    ghostscript qpdf unpaper pngquant \
    libheif-dev libxml2-dev libxslt1-dev \
    ufw

  ok "System packages installed."
}

install_python() {
  if command -v "$PYTHON_BIN" &>/dev/null; then
    ok "Python ${PYTHON_VERSION} already installed: $($PYTHON_BIN --version)"
    return
  fi
  log "Installing Python ${PYTHON_VERSION} alongside the system Python..."
  add-apt-repository -y ppa:deadsnakes/ppa
  apt-get update -qq
  apt-get install -y -qq "${PYTHON_BIN}" "${PYTHON_BIN}-venv" "${PYTHON_BIN}-dev"
  ok "Python ${PYTHON_VERSION} installed without changing the system default interpreter."
}

install_node() {
  if command -v node &>/dev/null; then
    local node_major
    node_major=$(node --version | grep -oP '(?<=v)\d+')
    if [[ "$node_major" -ge 20 ]]; then
      ok "Node.js already installed: $(node --version)"
      return
    fi
  fi
  log "Installing Node.js 20.x..."
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y -qq nodejs
  ok "Node.js installed: $(node --version)"
}

install_nginx() {
  if command -v nginx &>/dev/null; then
    ok "Nginx already installed."
    return
  fi
  log "Installing Nginx..."
  apt-get install -y -qq nginx
  ok "Nginx installed."
}

if $FULL_INSTALL; then
  install_system_deps
  install_python
  install_node
  install_nginx
else
  # Verify required tools exist
  command -v "$PYTHON_BIN" &>/dev/null || die "${PYTHON_BIN} not found. Run with --full to install Python ${PYTHON_VERSION}."
  command -v node &>/dev/null       || die "node not found. Run with --full to install."
  command -v nginx &>/dev/null      || die "nginx not found. Run with --full to install."
fi

# ─── Step 2: Frontend Build ──────────────────────────────────────────────────

log "Building frontend..."

cd "$CLIENT_DIR"

# Install dependencies (clean install for reproducibility)
sudo -u "$APP_USER" npm ci --ignore-scripts 2>/dev/null || npm ci --ignore-scripts

# Build the production bundle
sudo -u "$APP_USER" npm run build 2>&1 | tail -5

if [[ ! -d "$CLIENT_DIR/dist" ]]; then
  die "Frontend build failed — dist/ directory not found."
fi

# Deploy to web root
log "Deploying frontend to $WEB_ROOT..."
mkdir -p "$WEB_ROOT"
rm -rf "${WEB_ROOT:?}"/*
cp -r "$CLIENT_DIR/dist/"* "$WEB_ROOT/"
chown -R www-data:www-data "$WEB_ROOT"

ok "Frontend deployed to $WEB_ROOT ($(find "$WEB_ROOT" -type f | wc -l) files)."

# ─── Step 3: Backend Virtual Environment ─────────────────────────────────────

log "Setting up backend Python environment..."

cd "$BACKEND_DIR"

# Preserve a previous-runtime venv for rollback, then create a clean 3.12 venv.
# This deliberately never alters the operating system's python3 alternative.
if [[ -x "$VENV_DIR/bin/python" ]]; then
  VENV_VERSION=$(sudo -u "$APP_USER" "$VENV_DIR/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "unknown")
  if [[ "$VENV_VERSION" != "$PYTHON_VERSION" ]]; then
    VENV_BACKUP="${VENV_DIR}.python-${VENV_VERSION//./_}-$(date -u +%Y%m%dT%H%M%SZ)"
    log "Preserving Python ${VENV_VERSION} venv at $VENV_BACKUP before rebuilding with Python ${PYTHON_VERSION}."
    mv "$VENV_DIR" "$VENV_BACKUP"
  fi
fi
if [[ ! -d "$VENV_DIR" ]]; then
  log "Creating virtual environment..."
  sudo -u "$APP_USER" "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# Install/update dependencies
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install --upgrade pip setuptools wheel -q
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install -r "$REQUIREMENTS" -q

# Create required directories
sudo -u "$APP_USER" mkdir -p "$BACKEND_DIR/uploads" "$BACKEND_DIR/logs"

ok "Backend dependencies installed."

# ─── Step 4: Systemd Service Files ───────────────────────────────────────────

log "Configuring systemd services..."

cat > /etc/systemd/system/${BACKEND_SERVICE}.service << EOF
[Unit]
Description=ContraClaim DMS Backend (uvicorn)
After=network.target mongod.service redis-server.service
Wants=mongod.service redis-server.service

[Service]
Type=exec
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${BACKEND_DIR}
EnvironmentFile=${BACKEND_DIR}/.env
ExecStart=${VENV_DIR}/bin/uvicorn \\
  rbac_backend.main:app \\
  --host 127.0.0.1 \\
  --port 8000 \\
  --workers 4 \\
  --log-level info \\
  --access-log
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${BACKEND_SERVICE}

NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${BACKEND_DIR}/uploads ${BACKEND_DIR}/logs
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/${WORKER_SERVICE}.service << EOF
[Unit]
Description=ContraClaim Contract Ingestion Worker
After=${BACKEND_SERVICE}.service
Wants=${BACKEND_SERVICE}.service

[Service]
Type=exec
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${BACKEND_DIR}
EnvironmentFile=${BACKEND_DIR}/.env
Environment=START_BACKGROUND_SERVICES=false
Environment=START_CONTRACT_QUEUE_WORKERS=true
ExecStart=${VENV_DIR}/bin/python \\
  -m rbac_backend.worker
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${WORKER_SERVICE}

NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${BACKEND_DIR}/uploads ${BACKEND_DIR}/logs
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
ok "Systemd unit files written."

# ─── Step 5: Nginx Configuration ─────────────────────────────────────────────

if [[ ! -f "$NGINX_SITE" ]]; then
  log "Creating Nginx site configuration..."

  # Extract domain from backend .env PUBLIC_BASE_URL
  DOMAIN=$(grep -oP '(?<=PUBLIC_BASE_URL=https?://)[\w.-]+' "$BACKEND_DIR/.env" 2>/dev/null || echo "_")

  cat > "$NGINX_SITE" << NGINX
upstream backend_api {
    server 127.0.0.1:8000;
    keepalive 32;
}

server {
    listen 80;
    server_name ${DOMAIN};

    add_header X-Content-Type-Options    "nosniff"                           always;
    add_header X-Frame-Options           "SAMEORIGIN"                        always;
    add_header Referrer-Policy           "strict-origin-when-cross-origin"   always;
    add_header Permissions-Policy        "camera=(), microphone=(), geolocation=(), payment=()" always;

    client_max_body_size 100M;

    location /api/ {
        proxy_pass         http://backend_api;
        proxy_http_version 1.1;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_set_header   Connection        "";
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }

    location /api/ws {
        proxy_pass         http://backend_api;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade    \$http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_set_header   Host       \$host;
        proxy_read_timeout 86400s;
    }

    location = /health/ready {
        proxy_pass http://backend_api/health/ready;
    }

    location / {
        root  ${WEB_ROOT};
        index index.html;
        try_files \$uri \$uri/ /index.html;
    }

    location /assets/ {
        root    ${WEB_ROOT};
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    location ~ /\\. {
        deny all;
        return 404;
    }
}
NGINX

  ln -sf "$NGINX_SITE" /etc/nginx/sites-enabled/
  rm -f /etc/nginx/sites-enabled/default
  ok "Nginx site created for domain: $DOMAIN"
else
  ok "Nginx site already exists — skipping."
fi

# Test Nginx configuration
nginx -t 2>&1 || die "Nginx configuration test failed."

# ─── Step 6: Enable and Restart Services ─────────────────────────────────────

log "Restarting services..."

systemctl enable "$BACKEND_SERVICE" --quiet
systemctl enable "$WORKER_SERVICE" --quiet
systemctl restart "$BACKEND_SERVICE"
systemctl restart "$WORKER_SERVICE"
systemctl restart nginx

ok "All services restarted."

# ─── Step 7: Health Checks ───────────────────────────────────────────────────

log "Running health checks..."
sleep 3

CHECKS_PASSED=0
CHECKS_TOTAL=3

# Backend direct
if curl -fsS --max-time 10 http://127.0.0.1:8000/health/ready >/dev/null 2>&1; then
  ok "Backend health check passed (port 8000)."
  ((CHECKS_PASSED++))
else
  warn "Backend health check failed. Check: journalctl -u $BACKEND_SERVICE -n 50"
fi

# Nginx proxy
if curl -fsS --max-time 10 http://127.0.0.1/api/health >/dev/null 2>&1; then
  ok "Nginx → Backend proxy check passed."
  ((CHECKS_PASSED++))
else
  warn "Nginx proxy check failed. Check: journalctl -u nginx -n 50"
fi

# Frontend
HTTP_CODE=$(curl -fsS --max-time 10 -o /dev/null -w "%{http_code}" http://127.0.0.1/ 2>/dev/null || echo "000")
if [[ "$HTTP_CODE" == "200" ]]; then
  ok "Frontend serving (HTTP $HTTP_CODE)."
  ((CHECKS_PASSED++))
else
  warn "Frontend returned HTTP $HTTP_CODE. Check /var/www/web/index.html exists."
fi

# ─── Summary ─────────────────────────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
printf "${CYAN}  ContraClaim DMS Deployment Complete${NC}\n"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
printf "  Backend:    ${GREEN}http://127.0.0.1:8000${NC}\n"
printf "  Frontend:   ${GREEN}http://127.0.0.1${NC}  →  ${WEB_ROOT}\n"
printf "  Health:     ${CHECKS_PASSED}/${CHECKS_TOTAL} checks passed\n"
echo ""
echo "  Logs:"
echo "    journalctl -u $BACKEND_SERVICE -f"
echo "    journalctl -u $WORKER_SERVICE -f"
echo "    tail -f /var/log/nginx/access.log"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [[ $CHECKS_PASSED -lt $CHECKS_TOTAL ]]; then
  warn "Some health checks failed. Review the warnings above."
  exit 1
fi

exit 0
