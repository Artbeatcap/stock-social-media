#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# deploy.sh — push local changes to the production server.
#
# Usage:
#   ./deploy.sh                  # full deploy (sync, migrate, restart, smoke test)
#   ./deploy.sh --dry-run        # show what would change, make NO changes
#   ./deploy.sh --skip-migrate   # skip `flask db upgrade`
#   ./deploy.sh --skip-restart   # skip systemctl restart (rare)
#   ./deploy.sh --help
#
# Config: edit .deployrc (gitignored). See .deployrc.example for template.
#
# Windows: WSL2 recommended (bash+ssh+rsync). Git Bash often lacks rsync; use
# scripts/deploy_vps_tar.sh as fallback (no --delete-after). Drift check:
# scripts/reconcile_pull_server_tar.sh. PowerShell artifact sync: scripts/deploy_to_server.ps1.
#
# Ops: --delete-after drops server files missing locally (except excludes).
# No rollback — recover by redeploying a known-good commit. If flask db upgrade
# fails after rsync, fix migration or downgrade before retrying restart.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ─── Locate config ──────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/.deployrc"

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "❌ Missing $CONFIG_FILE"
  echo "   Copy .deployrc.example → .deployrc and fill in your server details."
  exit 1
fi
# shellcheck source=/dev/null
source "$CONFIG_FILE"

# Validate required vars from .deployrc
: "${SSH_HOST:?SSH_HOST not set in .deployrc}"
: "${SSH_USER:?SSH_USER not set in .deployrc}"
: "${REMOTE_PATH:?REMOTE_PATH not set in .deployrc}"
: "${SERVICE_NAME:?SERVICE_NAME not set in .deployrc}"
: "${HEALTH_URL:=http://127.0.0.1:8000/}"   # default if not set
: "${DEPLOY_SOURCE_DIR:=}"   # optional: e.g. server-backup — rsync this subdir to REMOTE_PATH root

# ─── Rsync source (repo root vs app subdir) ─────────────────────────────────
if [[ -n "$DEPLOY_SOURCE_DIR" ]]; then
  RSYNC_SRC="$(cd "$SCRIPT_DIR/$DEPLOY_SOURCE_DIR" && pwd)"
  if [[ ! -d "$RSYNC_SRC" ]]; then
    echo "❌ DEPLOY_SOURCE_DIR is set to \"$DEPLOY_SOURCE_DIR\" but $RSYNC_SRC is not a directory."
    exit 1
  fi
  EXCLUDE_SERVER_BACKUP=false
else
  RSYNC_SRC="$SCRIPT_DIR"
  EXCLUDE_SERVER_BACKUP=true
fi

# ─── Parse args ─────────────────────────────────────────────────────────────
DRY_RUN=false
SKIP_RESTART=false
SKIP_MIGRATE=false

for arg in "$@"; do
  case "$arg" in
    --dry-run)      DRY_RUN=true ;;
    --skip-restart) SKIP_RESTART=true ;;
    --skip-migrate) SKIP_MIGRATE=true ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \?//'
      exit 0 ;;
    *)
      echo "Unknown flag: $arg (try --help)"; exit 1 ;;
  esac
done

# ─── Pre-flight ─────────────────────────────────────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Deploy → $SSH_USER@$SSH_HOST:$REMOTE_PATH"
echo "  Source: $RSYNC_SRC/"
$DRY_RUN && echo "  Mode: DRY RUN (no changes will be made)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Warn (don't block) on uncommitted changes — sometimes you want to test before committing.
if command -v git >/dev/null 2>&1 && git -C "$SCRIPT_DIR" rev-parse --git-dir >/dev/null 2>&1; then
  if ! git -C "$SCRIPT_DIR" diff-index --quiet HEAD -- 2>/dev/null; then
    echo "⚠️  Uncommitted local changes — deploying them anyway."
  fi
  BRANCH=$(git -C "$SCRIPT_DIR" rev-parse --abbrev-ref HEAD)
  COMMIT=$(git -C "$SCRIPT_DIR" rev-parse --short HEAD)
  echo "ℹ️  Branch: $BRANCH @ $COMMIT"
fi

# Verify SSH connectivity (fail fast, before transfer)
echo "▶ Testing SSH connection..."
if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "$SSH_USER@$SSH_HOST" 'echo connected' >/dev/null 2>&1; then
  echo "❌ Can't SSH to $SSH_USER@$SSH_HOST."
  echo "   Check: SSH keys, hostname, firewall, or run: ssh $SSH_USER@$SSH_HOST"
  exit 1
fi

# ─── Rsync ──────────────────────────────────────────────────────────────────
# CRITICAL EXCLUDES — these would corrupt production if overwritten:
#   .env, .env.*    → server has production secrets
#   static/uploads/ → live user-uploaded files (charts, etc.)
#   instance/       → Flask runtime state
#   server-backup/  → only when syncing from repo root (local snapshots)
#   venv/, __pycache__/, *.pyc → local artifacts; server has its own venv
EXCLUDES=(
  --exclude='.git/'
  --exclude='.gitignore'
  --exclude='.env'
  --exclude='.env.*'
  --exclude='.deployrc'
  --exclude='.deployrc.example'
  --exclude='deploy.sh'
  --exclude='venv/'
  --exclude='.venv/'
  --exclude='__pycache__/'
  --exclude='*.pyc'
  --exclude='*.pyo'
  --exclude='.pytest_cache/'
  --exclude='.mypy_cache/'
  --exclude='.ruff_cache/'
  --exclude='.DS_Store'
  --exclude='node_modules/'
  --exclude='static/uploads/'
  --exclude='instance/'
  --exclude='.cursor/'
  --exclude='.cursorignore'
  --exclude='.cursorindexingignore'
  --exclude='.vscode/'
  --exclude='.idea/'
  --exclude='*.log'
  --exclude='*.swp'
)
if $EXCLUDE_SERVER_BACKUP; then
  EXCLUDES+=(--exclude='server-backup/')
fi

RSYNC_FLAGS=(-az --human-readable --info=stats1,progress2 --delete-after)
$DRY_RUN && RSYNC_FLAGS+=(--dry-run -v)

# --delete-after removes server files that don't exist locally — but ONLY for
# files NOT in the excludes list. Uploads, .env, instance/ are protected above.

echo "▶ Syncing files..."
rsync "${RSYNC_FLAGS[@]}" "${EXCLUDES[@]}" \
  "$RSYNC_SRC/" "$SSH_USER@$SSH_HOST:$REMOTE_PATH/"

if $DRY_RUN; then
  echo ""
  echo "✅ Dry run complete. Re-run without --dry-run to actually deploy."
  exit 0
fi

# ─── Remote: deps, migrate, restart, smoke test ─────────────────────────────
echo "▶ Running remote tasks (deps, migrate, restart)..."

# Build the remote script with substitutions, then send via stdin.
# Using a here-doc avoids quoting hell.
ssh "$SSH_USER@$SSH_HOST" \
  REMOTE_PATH="$REMOTE_PATH" \
  SERVICE_NAME="$SERVICE_NAME" \
  HEALTH_URL="$HEALTH_URL" \
  SKIP_MIGRATE="$SKIP_MIGRATE" \
  SKIP_RESTART="$SKIP_RESTART" \
  bash <<'REMOTE_EOF'
set -euo pipefail
cd "$REMOTE_PATH"

# Activate venv
if [[ ! -d venv ]]; then
  echo "❌ No venv at $REMOTE_PATH/venv. Create one on the server first."
  exit 1
fi
# shellcheck source=/dev/null
source venv/bin/activate

# Install / update deps if requirements.txt is present
if [[ -f requirements.txt ]]; then
  echo "  ↳ pip install -r requirements.txt (quiet)"
  pip install -q -r requirements.txt
fi

# Migrate
if [[ "$SKIP_MIGRATE" == "true" ]]; then
  echo "  ↳ Skipping migrations (--skip-migrate)"
else
  if [[ -d migrations ]]; then
    echo "  ↳ flask db upgrade"
    FLASK_APP=app.py flask db upgrade
  else
    echo "  ↳ No migrations/ folder — skipping db upgrade"
  fi
fi

# Restart
if [[ "$SKIP_RESTART" == "true" ]]; then
  echo "  ↳ Skipping restart (--skip-restart)"
else
  echo "  ↳ sudo systemctl restart $SERVICE_NAME"
  sudo systemctl restart "$SERVICE_NAME"

  # Wait for app to come back up
  echo "  ↳ Waiting for app to respond at $HEALTH_URL ..."
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if curl -fsS --max-time 2 "$HEALTH_URL" >/dev/null 2>&1; then
      echo "  ✅ Smoke test passed (responded on attempt $i)"
      exit 0
    fi
    sleep 1
  done
  echo "  ❌ Smoke test FAILED — app didn't respond within 10s"
  echo "     Check: sudo systemctl status $SERVICE_NAME"
  echo "     Logs:  sudo journalctl -u $SERVICE_NAME -n 50"
  exit 1
fi
REMOTE_EOF

echo ""
echo "✅ Deploy complete."
