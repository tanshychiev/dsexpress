#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${1:-/var/www/dsexpress}"
CONF_SRC="$PROJECT_DIR/deploy/nginx-stock-in-upload.conf"
CONF_DST="/etc/nginx/conf.d/dsexpress-stock-in-upload.conf"

if [ ! -f "$CONF_SRC" ]; then
  echo "Missing $CONF_SRC" >&2
  exit 1
fi

sudo cp "$CONF_SRC" "$CONF_DST"
sudo nginx -t
sudo systemctl reload nginx

if systemctl list-unit-files | grep -q '^dsexpress\.service'; then
  sudo systemctl restart dsexpress
fi

echo "Stock In upload limit enabled: 50 MB"
