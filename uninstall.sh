#!/usr/bin/env bash
set -euo pipefail

# GNU Sorter v2.0 — user-local uninstaller
# no --purge: keep config (~/.config/gnu-sorter) and history (~/.local/share/gnu-sorter/history.log)
# --purge: remove config + history too

PURGE=0
for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
  esac
done

APP_DIR="${HOME}/.local/share/gnu-sorter/app"
BIN_LINK="${HOME}/.local/bin/gnu-sorter"
ICON_DIR="${HOME}/.local/share/icons/hicolor"
DESK_DIR="${HOME}/.local/share/applications"
KIO_DIR="${HOME}/.local/share/kio/servicemenus"
SYSTEMD_USER="${HOME}/.config/systemd/user"

echo "[uninstall] Stopping/ disabling service if present"
systemctl --user disable --now gnu-sorter.service >/dev/null 2>&1 || true
rm -f "$SYSTEMD_USER/gnu-sorter.service"
systemctl --user daemon-reload >/dev/null 2>&1 || true

echo "[uninstall] Removing files"
rm -f "$BIN_LINK"
rm -f "$DESK_DIR/gnu-sorter.desktop"
rm -f "$KIO_DIR/gnu-sorter-sort.desktop"
rm -f "$ICON_DIR/scalable/apps/gnu-sorter.svg"
for sz in 16 22 24 32 48 64 128 256 512; do
  rm -f "$ICON_DIR/${sz}x${sz}/apps/gnu-sorter.png" 2>/dev/null || true
done

rm -rf "$APP_DIR"

if [[ $PURGE -eq 1 ]]; then
  echo "[uninstall] PURGE: removing config and history"
  rm -rf "${HOME}/.config/gnu-sorter"
  rm -f "${HOME}/.local/share/gnu-sorter/history.log"
  rmdir "${HOME}/.local/share/gnu-sorter" 2>/dev/null || true
fi

echo ""
echo "✓ GNU Sorter uninstalled for $(whoami)"
if [[ $PURGE -eq 0 ]]; then
  echo "  Config and history kept: ~/.config/gnu-sorter/, ~/.local/share/gnu-sorter/history.log"
  echo "  (use --purge to remove them too)"
fi

