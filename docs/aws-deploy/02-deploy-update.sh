#!/bin/bash
# ============================================================
#  Re-deploy script — run this every time you push new code
#  Usage: bash 02-deploy-update.sh
# ============================================================

set -e

APP_DIR="/home/ubuntu/app"
BRANCH="subhash"

echo "🔄 Pulling latest code from branch: $BRANCH"
cd "$APP_DIR"
git fetch origin
git checkout "$BRANCH"
git pull origin "$BRANCH"

echo "📦 Installing/updating Python dependencies..."
cd "$APP_DIR/backend_ocr"
source venv/bin/activate
pip install -r requirements.txt --quiet
deactivate

echo "🔁 Restarting API service..."
sudo systemctl restart onsumm-api
sleep 2
sudo systemctl status onsumm-api --no-pager

echo "✅ Backend updated and restarted!"
echo ""
echo "Tail logs: sudo journalctl -u onsumm-api -f"
