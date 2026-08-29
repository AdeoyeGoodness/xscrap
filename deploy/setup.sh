#!/usr/bin/env bash
#
# Provision the WHALE bot on a fresh Debian/Ubuntu VPS.
# Run as root on the server:  bash deploy/setup.sh
#
# Idempotent: safe to re-run after a code update.

set -euo pipefail

APP_DIR=/opt/whale
APP_USER=whale
SERVICE=whale-bot

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo bash deploy/setup.sh" >&2
    exit 1
fi

echo "==> Installing system packages"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git rsync

echo "==> Creating service user"
# No login shell and no home directory: this account only runs the bot.
if ! id -u "$APP_USER" >/dev/null 2>&1; then
    useradd --system --shell /usr/sbin/nologin --home-dir "$APP_DIR" "$APP_USER"
fi

echo "==> Preparing $APP_DIR"
mkdir -p "$APP_DIR"

# Copy the checkout into place, minus local state that must not be shared.
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "$SRC_DIR" != "$APP_DIR" ]]; then
    rsync -a --delete \
        --exclude '.git' \
        --exclude '.venv' \
        --exclude '__pycache__' \
        --exclude '.pytest_cache' \
        --exclude 'accounts.db' \
        --exclude '.env' \
        "$SRC_DIR/" "$APP_DIR/"
fi

echo "==> Building virtualenv"
if [[ ! -d "$APP_DIR/.venv" ]]; then
    python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

if [[ ! -f "$APP_DIR/.env" ]]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    echo
    echo "!! $APP_DIR/.env was created from the example."
    echo "!! Add TELEGRAM_BOT_TOKEN before starting, or the bot will exit."
    echo
fi

echo "==> Locking down permissions"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
# .env holds the bot token and X cookies; owner-only.
chmod 600 "$APP_DIR/.env"
chmod 750 "$APP_DIR"

echo "==> Installing systemd unit"
install -m 644 "$APP_DIR/deploy/$SERVICE.service" "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE"

echo
echo "Done. Next:"
echo "  1. Edit $APP_DIR/.env and set TELEGRAM_BOT_TOKEN"
echo "  2. systemctl restart $SERVICE"
echo "  3. journalctl -u $SERVICE -f"
