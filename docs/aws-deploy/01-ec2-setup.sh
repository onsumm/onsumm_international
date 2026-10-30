#!/bin/bash
# ============================================================
#  EC2 Backend Setup Script
#  Run this on a fresh Ubuntu 22.04 EC2 instance as ubuntu user
#  Usage: bash 01-ec2-setup.sh
# ============================================================

set -e

echo "🚀 Starting EC2 setup for onsumm backend..."

# ── 1. System packages ───────────────────────────────────────
sudo apt-get update -y
sudo apt-get upgrade -y
sudo apt-get install -y \
  python3 python3-pip python3-venv \
  nginx \
  certbot python3-certbot-nginx \
  git \
  poppler-utils \
  build-essential \
  curl \
  ufw

echo "✅ System packages installed"

# ── 2. Firewall ──────────────────────────────────────────────
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'   # ports 80 + 443
sudo ufw --force enable
echo "✅ Firewall configured"

# ── 3. Clone your repo ───────────────────────────────────────
# Replace with your actual GitHub repo URL
REPO_URL="https://github.com/onsumm/PILOT-TESTING.git"
APP_DIR="/home/ubuntu/app"

if [ -d "$APP_DIR" ]; then
  echo "📁 App directory exists, pulling latest..."
  cd "$APP_DIR" && git pull origin subhash
else
  echo "📁 Cloning repo..."
  git clone -b subhash "$REPO_URL" "$APP_DIR"
fi

echo "✅ Repo cloned to $APP_DIR"

# ── 4. Python virtual environment ───────────────────────────
cd "$APP_DIR/backend_ocr"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

echo "✅ Python venv + dependencies installed"

# ── 5. Create .env file ──────────────────────────────────────
# Edit this file with your actual values after running this script
cat > "$APP_DIR/backend_ocr/.env" << 'ENVEOF'
DB_HOST=your-neon-host.neon.tech
DB_PORT=5432
DB_NAME=neondb
DB_USER=your_db_user
DB_PASSWORD=your_db_password
DB_SSL=require

TENANT_ID=your_tenant_id
TENANT_NAME=Your Company Name
TENANT_GSTN=your_gstn

OPENAI_API_KEY=sk-...

SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_USER=your@gmail.com
SMTP_PASSWORD=your_app_password
SMTP_ADMIN_EMAIL=admin@yourdomain.com

APPROVAL_SECRET=change-this-to-random-secret

POPPLER_PATH=/usr/bin
ENVEOF

echo ""
echo "⚠️  IMPORTANT: Edit the .env file now:"
echo "    nano $APP_DIR/backend_ocr/.env"
echo ""

# ── 6. Create uploads directory ──────────────────────────────
mkdir -p "$APP_DIR/backend_ocr/stored_uploads"
chmod 755 "$APP_DIR/backend_ocr/stored_uploads"

echo "✅ Uploads directory created"

# ── 7. Systemd service for FastAPI ───────────────────────────
sudo tee /etc/systemd/system/onsumm-api.service > /dev/null << SERVICEEOF
[Unit]
Description=Onsumm FastAPI Backend
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/app/backend_ocr
Environment="PATH=/home/ubuntu/app/backend_ocr/venv/bin"
ExecStart=/home/ubuntu/app/backend_ocr/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8001 --workers 2
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SERVICEEOF

sudo systemctl daemon-reload
sudo systemctl enable onsumm-api
sudo systemctl start onsumm-api

echo "✅ Systemd service created and started"

# ── 8. Nginx config ──────────────────────────────────────────
# Replace YOUR_DOMAIN with your actual domain e.g. api.onsumm.ai
sudo tee /etc/nginx/sites-available/onsumm-api > /dev/null << 'NGINXEOF'
server {
    listen 80;
    server_name YOUR_DOMAIN;    # <-- change this

    # Max upload size (for PDF uploads)
    client_max_body_size 50M;

    location / {
        proxy_pass         http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }
}
NGINXEOF

sudo ln -sf /etc/nginx/sites-available/onsumm-api /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx

echo "✅ Nginx configured"
echo ""
echo "=============================================="
echo "  NEXT STEPS:"
echo "=============================================="
echo ""
echo "1. Edit your .env file:"
echo "   nano $APP_DIR/backend_ocr/.env"
echo ""
echo "2. Replace YOUR_DOMAIN in nginx config:"
echo "   sudo nano /etc/nginx/sites-available/onsumm-api"
echo "   sudo nginx -t && sudo systemctl reload nginx"
echo ""
echo "3. Point your domain DNS → this EC2 public IP"
echo "   Then run SSL setup:"
echo "   sudo certbot --nginx -d YOUR_DOMAIN"
echo ""
echo "4. Restart the API after editing .env:"
echo "   sudo systemctl restart onsumm-api"
echo "   sudo systemctl status onsumm-api"
echo ""
echo "5. Check logs:"
echo "   sudo journalctl -u onsumm-api -f"
echo ""
echo "🎉 Backend setup complete!"
