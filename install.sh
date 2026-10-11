#!/usr/bin/env bash
set -euo pipefail

# Funnel — user-local installer
# - Installs app to ~/.local/share/gnu-sorter/app/
# - Symlinks ~/.local/bin/gnu-sorter -> app/main.py
# - Installs desktop entry, icons (SVG + PNGs), KDE service menu
# - Optionally installs/enables the user systemd service (--service/--enable-service)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${HOME}/.local/share/gnu-sorter/app"
BIN_DIR="${HOME}/.local/bin"
ICON_DIR="${HOME}/.local/share/icons/hicolor"
DESK_DIR="${HOME}/.local/share/applications"
KIO_DIR="${HOME}/.local/share/kio/servicemenus"
SYSTEMD_USER="${HOME}/.config/systemd/user"

ENABLE_SERVICE=0
for arg in "$@"; do
  case "$arg" in
    --service|--enable-service) ENABLE_SERVICE=1 ;;
    *) : ;;
  esac
done

mkdir -p "$BIN_DIR" "$ICON_DIR" "$DESK_DIR" "$KIO_DIR" "$SYSTEMD_USER" "${HOME}/.config/gnu-sorter/roots.d"

echo "[install] Copying app to $APP_DIR"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR"
rsync -a --exclude='.git*' --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='dist' --exclude='build' --exclude='*.egg-info' \
  --exclude='tests' --exclude='docs' \
  "$REPO_ROOT/" "$APP_DIR/"

echo "[install] Symlink $BIN_DIR/gnu-sorter"
ln -sf "$APP_DIR/main.py" "$BIN_DIR/gnu-sorter"
chmod +x "$APP_DIR/main.py"

echo "[install] Desktop entry"
cp "$APP_DIR/data/gnu-sorter.desktop" "$DESK_DIR/gnu-sorter.desktop"

echo "[install] Icons (SVG + committed PNG ladder)"
if [ -d "$APP_DIR/data/icons/hicolor" ]; then
  cp -r "$APP_DIR/data/icons/hicolor/." "$ICON_DIR/"
else
  # Fallback for source trees without the committed ladder.
  cp "$APP_DIR/data/icons/gnu-sorter.svg" "$ICON_DIR/scalable/apps/gnu-sorter.svg" 2>/dev/null || true
  if command -v rsvg-convert >/dev/null 2>&1; then
    for sz in 16 22 24 32 48 64 128 256 512; do
      mkdir -p "$ICON_DIR/${sz}x${sz}/apps"
      rsvg-convert -w "$sz" -h "$sz" "$APP_DIR/data/icons/gnu-sorter.svg" -o "$ICON_DIR/${sz}x${sz}/apps/gnu-sorter.png" 2>/dev/null || true
    done
  fi
fi
command -v gtk-update-icon-cache >/dev/null 2>&1 && \
  gtk-update-icon-cache -q -t -f "$ICON_DIR" 2>/dev/null || true

echo "[install] KDE service menu"
cp "$APP_DIR/data/kio-servicemenu/gnu-sorter-sort.desktop" "$KIO_DIR/gnu-sorter-sort.desktop" 2>/dev/null || true

echo "[install] Systemd user unit"
cat > "$SYSTEMD_USER/gnu-sorter.service" << 'UNIT'
[Unit]
Description=Funnel background watcher
After=default.target

[Service]
ExecStart=%h/.local/bin/gnu-sorter --watch
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload >/dev/null 2>&1 || true

if [[ $ENABLE_SERVICE -eq 1 ]]; then
  echo "[install] Enabling and starting service"
  systemctl --user enable --now gnu-sorter.service >/dev/null 2>&1 || true
else
  echo "[install] Service installed but not enabled (use --service to enable+start)"
fi

echo "[install] Initialising defaults (idempotent)"
"$BIN_DIR/gnu-sorter" --list-roots >/dev/null 2>&1 || true

echo ""
echo "✓ Funnel installed for $(whoami)"
echo "  • App: $APP_DIR"
echo "  • CLI: $BIN_DIR/gnu-sorter"
echo "  • Launcher: $DESK_DIR/gnu-sorter.desktop"
echo "  • Service: $SYSTEMD_USER/gnu-sorter.service"
echo ""
echo "Run: gnu-sorter --gui"
echo "or:  systemctl --user {start,stop,status} gnu-sorter.service"

