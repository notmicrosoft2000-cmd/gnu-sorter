"""Management + control of the GNU Sorter background service."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from . import platform

if platform.is_linux() and platform.systemd_available():
    UNIT_NAME = "gnu-sorter.service"
    UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
    OLD_UNIT = "download-sorter.service"
else:
    UNIT_NAME = "gnu-sorter.service"
    UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
    OLD_UNIT = "download-sorter.service"

UNIT_TEMPLATE = """\
[Unit]
Description=GNU Sorter (background folder watcher)
After=graphical-session.target

[Service]
Type=simple
ExecStart={exec_path} --watch
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    if not (platform.is_linux() and platform.systemd_available()):
        return subprocess.CompletedProcess(args, 1, "", "systemd not available")
    return subprocess.run(["systemctl", "--user", *args],
                          capture_output=True, text=True)


def is_installed() -> bool:
    return (UNIT_DIR / UNIT_NAME).exists()


def is_active() -> bool:
    res = _systemctl("is-active", UNIT_NAME)
    return res.returncode == 0 and res.stdout.strip() == "active"


def is_enabled() -> bool:
    res = _systemctl("is-enabled", UNIT_NAME)
    return res.returncode == 0 and res.stdout.strip() == "enabled"


def install(exec_path: str | None = None) -> Path:
    if platform.is_linux() and platform.systemd_available():
        exec_path = exec_path or str(Path.home() / ".local" / "bin" / "gnu-sorter")
    else:
        exec_path = exec_path or "gnu-sorter"
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    unit = UNIT_DIR / UNIT_NAME
    unit.write_text(UNIT_TEMPLATE.format(exec_path=exec_path))
    if platform.is_linux() and platform.systemd_available():
        _systemctl("daemon-reload")
    return unit


def _legacy_cleanup() -> None:
    """Remove the old download-sorter unit if it is still around."""
    old = UNIT_DIR / OLD_UNIT
    if old.exists():
        _systemctl("disable", "--now", OLD_UNIT)
        try:
            old.unlink()
        except OSError:
            pass
        _systemctl("daemon-reload")


def enable(now: bool = True) -> None:
    if not is_installed():
        install()
    args = ["enable"] + (["--now"] if now else []) + [UNIT_NAME]
    print(_systemctl(*args).stdout.strip() or f"{UNIT_NAME} enabled")


def disable(now: bool = True) -> None:
    args = ["disable"] + (["--now"] if now else []) + [UNIT_NAME]
    print(_systemctl(*args).stdout.strip() or f"{UNIT_NAME} disabled")


def start() -> None:
    print(_systemctl("start", UNIT_NAME).stdout.strip() or "started")


def stop() -> None:
    print(_systemctl("stop", UNIT_NAME).stdout.strip() or "stopped")


def restart() -> None:
    print(_systemctl("restart", UNIT_NAME).stdout.strip() or "restarted")


def status() -> str:
    if not is_installed():
        return "not installed"
    state = _systemctl("is-active", UNIT_NAME).stdout.strip() or "unknown"
    enabled = "enabled" if is_enabled() else "disabled"
    return f"{state} ({enabled})"


def migrate_from_download_sorter() -> None:
    """Stop and remove the old download-sorter service (used by --finish-migration)."""
    _legacy_cleanup()
