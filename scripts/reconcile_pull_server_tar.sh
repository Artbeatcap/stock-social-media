#!/usr/bin/env bash
# Pull a read-only snapshot from production over SSH (tar stream) for drift checks.
# Uses the same repo-root .deployrc as deploy.sh (SSH_HOST, SSH_USER, REMOTE_PATH).
#
# Default: list archive contents (like a dry-run inventory). Does not modify tracked files.
# After any server-only edit, list or extract, diff against server-backup/, merge into git, then deploy.
#
# Usage:
#   ./scripts/reconcile_pull_server_tar.sh           # tar -tzf - (quiet list)
#   ./scripts/reconcile_pull_server_tar.sh -v       # tar -tzvf - (verbose list)
#   ./scripts/reconcile_pull_server_tar.sh --extract [DIR]
#       DIR optional; default: server-backup/.reconcile/YYYYMMDD-HHMMSS/
#   ./scripts/reconcile_pull_server_tar.sh --help
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$ROOT/.deployrc"
if [[ ! -f "$CONFIG" ]]; then
  echo "Missing $CONFIG — copy .deployrc.example to .deployrc"
  exit 1
fi
# shellcheck source=/dev/null
source "$CONFIG"
REMOTE_PATH="${REMOTE_PATH//$'\r'/}"
SSH_HOST="${SSH_HOST//$'\r'/}"
SSH_USER="${SSH_USER//$'\r'/}"

: "${SSH_HOST:?}"
: "${SSH_USER:?}"
: "${REMOTE_PATH:?}"

VERBOSE=false
EXTRACT=false
EXTRACT_DIR=""

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \?//'
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage ;;
    -v|--verbose) VERBOSE=true; shift ;;
    --extract)
      EXTRACT=true
      shift
      if [[ $# -gt 0 && "$1" != -* ]]; then
        EXTRACT_DIR="$1"
        shift
      fi
      ;;
    *) echo "Unknown: $1"; usage ;;
  esac
done

# shellcheck disable=SC2087
run_remote_tar() {
  local qpath
  qpath=$(printf '%q' "$REMOTE_PATH")
  ssh -o BatchMode=yes -o ConnectTimeout=10 "${SSH_USER}@${SSH_HOST}" \
    "cd ${qpath} && exec tar czf - \
      --exclude='./venv' \
      --exclude='./.venv' \
      --exclude='__pycache__' \
      --exclude='./.env' \
      --exclude='./.env.*' \
      --exclude='./static/uploads' \
      --exclude='./instance' \
      --exclude='./.git' \
      ."
}

if $EXTRACT; then
  if [[ -z "$EXTRACT_DIR" ]]; then
    TS="$(date +%Y%m%d-%H%M%S)"
    EXTRACT_DIR="$ROOT/server-backup/.reconcile/$TS"
  elif [[ "$EXTRACT_DIR" != /* ]]; then
    EXTRACT_DIR="$ROOT/$EXTRACT_DIR"
  fi
  mkdir -p "$EXTRACT_DIR"
  echo "Extracting server snapshot to $EXTRACT_DIR"
  run_remote_tar | tar xzf - -C "$EXTRACT_DIR"
  echo "Done. Compare with: diff -ru \"$ROOT/server-backup\" \"$EXTRACT_DIR\" | less"
else
  if $VERBOSE; then
    run_remote_tar | tar -tzvf -
  else
    run_remote_tar | tar -tzf -
  fi
fi
