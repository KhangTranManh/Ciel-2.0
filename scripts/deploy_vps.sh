#!/usr/bin/env bash
# Deploy Ciel from /srv/ciel. Run as root (or through sudo) on the VPS.
# It records the known-good source commit before each update and restores it
# automatically when the restarted service does not become healthy.
set -euo pipefail

APP_DIR="${CIEL_APP_DIR:-/srv/ciel}"
SERVICE="${CIEL_SERVICE:-ciel.service}"
APP_USER="${CIEL_APP_USER:-ciel}"
# The production venv is built by uv and has no pip; uv lives in .tools.
UV_BIN="${CIEL_UV:-$APP_DIR/.tools/uv}"
VENV_PY="${CIEL_VENV_PYTHON:-$APP_DIR/.venv/bin/python}"
PREVIOUS_FILE="${CIEL_PREVIOUS_RELEASE_FILE:-$APP_DIR/.ciel-previous-release}"
HEALTH_URL="${CIEL_HEALTH_URL:-http://127.0.0.1:8000/health}"
STARTED_AT="$(date '+%Y-%m-%d %H:%M:%S')"

die() { printf 'deploy_vps: %s\n' "$*" >&2; exit 1; }
[[ "$(id -u)" -eq 0 ]] || die "run as root so systemctl and sudo -u $APP_USER work"
[[ -d "$APP_DIR/.git" ]] || die "not a git checkout: $APP_DIR"
[[ -x "$UV_BIN" ]] || die "uv not found: $UV_BIN"
[[ -x "$VENV_PY" ]] || die "venv python not found: $VENV_PY"
cd "$APP_DIR"

# The checkout belongs to $APP_USER: run git as that user so root never hits
# git's "dubious ownership" guard or leaves root-owned files behind.
g() { sudo -u "$APP_USER" git "$@"; }
install_requirements() {
  # Releases older than wave 2 have no requirements-core.txt; their packages are
  # a subset of what is already installed, so keep the venv as it is.
  if [[ ! -f requirements-core.txt ]]; then
    printf 'deploy_vps: no requirements-core.txt in this release; keeping installed packages\n' >&2
    return 0
  fi
  sudo -u "$APP_USER" env UV_CACHE_DIR="$APP_DIR/.tools/cache" \
    "$UV_BIN" pip install --python "$VENV_PY" -r requirements-core.txt
}

health_ready() {
  local payload
  payload="$(curl --fail --silent --show-error --max-time 5 "$HEALTH_URL")" || return 1
  python3 -c 'import json,sys; data=json.load(sys.stdin); raise SystemExit(0 if data.get("ready") is True else 1)' <<<"$payload"
}

verify_release() {
  local deadline=$((SECONDS + 60))
  while (( SECONDS < deadline )); do
    if health_ready; then break; fi
    sleep 2
  done
  health_ready || return 1
  # ss prints "LISTEN 0 2048 127.0.0.1:8000 0.0.0.0:*": the local address is column 4.
  # Require at least one loopback listener and no listener on any other address.
  local listeners
  listeners="$(ss -ltnH 'sport = :8000' | awk '{print $4}')"
  grep -Eq '^(127\.0\.0\.1|\[::1\]):8000$' <<<"$listeners" || return 1
  ! grep -Evq '^(127\.0\.0\.1|\[::1\]):8000$' <<<"$listeners" || return 1
  journalctl -u "$SERVICE" --since "$STARTED_AT" --no-pager | grep -Fq '[Telegram] Bot online' || return 1
  ! journalctl -u "$SERVICE" --since "$STARTED_AT" --no-pager | grep -Fq 'Traceback (most recent call last)' || return 1
}

restore_previous() {
  local previous="$1"
  printf 'deploy_vps: restoring %s\n' "$previous" >&2
  # Keep the checked-out deployment branch attached: the next normal deploy
  # must still be able to fast-forward from origin after this rollback.
  g reset --hard "$previous"
  install_requirements
  systemctl restart "$SERVICE"
  STARTED_AT="$(date '+%Y-%m-%d %H:%M:%S')"
  verify_release || die "rollback started but verification failed; inspect: journalctl -u $SERVICE -n 200"
  printf '%s\n' "$previous" > "$PREVIOUS_FILE"
}

if [[ "${1:-}" == "--rollback" ]]; then
  [[ $# -eq 1 ]] || die "usage: $0 [--rollback]"
  [[ -s "$PREVIOUS_FILE" ]] || die "no previous release recorded in $PREVIOUS_FILE"
  restore_previous "$(<"$PREVIOUS_FILE")"
  printf 'deploy_vps: rollback complete at %s\n' "$(g rev-parse --short HEAD)"
  exit 0
fi
[[ $# -eq 0 ]] || die "usage: $0 [--rollback]"
g diff --quiet && g diff --cached --quiet || die "refusing to overwrite local source changes"
previous="$(g rev-parse HEAD)"
printf '%s\n' "$previous" > "$PREVIOUS_FILE"

if ! g pull --ff-only; then
  die "git pull failed; service was not restarted"
fi
if ! g diff --quiet "$previous" HEAD -- requirements-core.txt; then
  install_requirements || {
    restore_previous "$previous"
    die "requirements install failed; rollback complete"
  }
fi

STARTED_AT="$(date '+%Y-%m-%d %H:%M:%S')"
systemctl restart "$SERVICE" || { restore_previous "$previous"; die "restart failed; rollback complete"; }
if ! verify_release; then
  restore_previous "$previous"
  die "post-deploy verification failed; rollback complete"
fi
printf 'deploy_vps: deployed %s (previous %s)\n' "$(g rev-parse --short HEAD)" "${previous:0:7}"
