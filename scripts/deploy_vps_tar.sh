#!/usr/bin/env bash
# Deploy server-backup/ (or DEPLOY_SOURCE_DIR) over SSH when rsync is unavailable (e.g. Git Bash on Windows).
# Reads repo-root .deployrc (same as deploy.sh). Does not replace deploy.sh on servers with rsync.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
source "$ROOT/.deployrc"
REMOTE_PATH="${REMOTE_PATH//$'\r'/}"
SSH_HOST="${SSH_HOST//$'\r'/}"
SSH_USER="${SSH_USER//$'\r'/}"
SERVICE_NAME="${SERVICE_NAME//$'\r'/}"
HEALTH_URL="${HEALTH_URL//$'\r'/}"
SRC="${DEPLOY_SOURCE_DIR:-server-backup}"
if [[ ! -d "$ROOT/$SRC" ]]; then
  echo "Missing source dir: $ROOT/$SRC"
  exit 1
fi

echo "== Upload $SRC/ -> ${SSH_USER}@${SSH_HOST}:${REMOTE_PATH} (tar over ssh) =="
tar czf - \
  --exclude='./.env' \
  --exclude='./.env.*' \
  --exclude='./venv' \
  --exclude='./.venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='./static/uploads' \
  --exclude='./instance' \
  -C "$SRC" . \
| ssh "${SSH_USER}@${SSH_HOST}" "cd '${REMOTE_PATH}' && tar xzf - && chown -R tradingapp:tradingapp '${REMOTE_PATH}'"

echo "== Remote: pip, migrate, restart, smoke =="
ssh "${SSH_USER}@${SSH_HOST}" "set -euo pipefail
cd '${REMOTE_PATH}'
source venv/bin/activate
pip install -q -r requirements.txt
if [[ -d migrations ]]; then
  FLASK_APP=app.py flask db upgrade
else
  echo '(no migrations/ — skipping db upgrade)'
fi
sudo systemctl restart '${SERVICE_NAME}'
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS --max-time 2 '${HEALTH_URL}' >/dev/null 2>&1; then
    echo \"Smoke test passed (attempt \$i)\"
    exit 0
  fi
  sleep 1
done
echo 'Smoke test FAILED'
exit 1"

echo "== Done =="
