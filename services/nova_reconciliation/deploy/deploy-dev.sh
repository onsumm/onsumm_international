#!/usr/bin/env bash
# ============================================================================
#  Redeploy app-dev (branch uat2) — API + Nova Reconciliation Service.
#  Run ON the EC2 box:  bash ~/app-dev/services/nova_reconciliation/deploy/deploy-dev.sh
#
#  First-time Nova install (systemd unit, nginx, env vars) is NOT done here —
#  see docs/RELEASE_2026-08-26_v1.1.0.md §4. This script is the repeatable
#  every-push path once that one-time setup exists.
#
#  Issues no DDL. The nova_reconciliation_* tables already exist on the shared
#  platform DB; this never creates or alters them.
# ============================================================================
set -euo pipefail

APP_DIR="/home/ec2-user/app-dev"
BRANCH="uat2"
PY="/usr/bin/python3.11"

echo "==> Pulling $BRANCH"
cd "$APP_DIR"
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull origin "$BRANCH"
echo "    now at: $(git rev-parse --short HEAD) — $(git log -1 --pretty=%s)"

echo "==> Python deps"
"$PY" -m pip install --user -q -r backend_ocr/requirements.txt
"$PY" -m pip install --user -q -r services/nova_reconciliation/requirements.txt

echo "==> Restarting services"
sudo systemctl restart onsumm-api-dev
sudo systemctl restart onsumm-nova-dev

echo "==> Health"
sleep 3
for u in onsumm-api-dev onsumm-nova-dev; do
  printf '    %-18s %s\n' "$u" "$(systemctl is-active "$u")"
done
printf '    api  /docs        %s\n' "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8002/docs)"
printf '    nova /health      %s\n' "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8102/health)"
printf '    nova via nginx    %s\n' "$(curl -s -o /dev/null -w '%{http_code}' https://api.onsumm.ai/dev-nova/health)"

echo
echo "Logs:  sudo journalctl -u onsumm-api-dev  -n 100 --no-pager"
echo "       sudo journalctl -u onsumm-nova-dev -n 100 --no-pager"
