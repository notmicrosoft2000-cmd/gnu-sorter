"""Platform abstraction for GNU Sorter (Linux/Windows/macOS)."""
from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path

try:
    from platformdirs import user_config_dir, user_data_dir, user_log_dir
except Exception:  # pragma: no cover
    user_config_dir = user_data_dir = user_log_dir = None


def is_windows() -> bool:
    return platform.system() == "Windows"


def is_linux() -> bool:
    return platform.system() == "Linux"


def is_macos() -> bool:
    return platform.system() == "Darwin"


def config_dir() -> Path:
    if user_config_dir:
        return Path(user_config_dir("gnu-sorter", roaming=True))
    return Path.home() / ".config" / "gnu-sorter"


def data_dir() -> Path:
    if user_data_dir:
        return Path(user_data_dir("gnu-sorter", roaming=True))
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) if is_windows() else Path.home() / ".local" / "share"
    return base / "gnu-sorter"


def log_dir() -> Path:
    if user_log_dir:
        return Path(user_log_dir("gnu-sorter", opinion=True))
    return data_dir()


def default_history_path() -> Path:
    """Keep the journal in the data dir (matches existing installs)."""
    return data_dir() / "history.log"


def temp_dir() -> Path:
    td = Path(os.environ.get("TMPDIR") or os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp")
    gs = td / "gnu-sorter"
    gs.mkdir(parents=True, exist_ok=True)
    return gs


def has_inotify() -> bool:
    return is_linux() and shutil.which("inotifywait") is not None


def systemd_available() -> bool:
    return is_linux() and shutil.which("systemctl") is not None
