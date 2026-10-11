"""Configuration for Funnel.

Files live in the platform config/data dirs (see gnu_sorter.platform):
    config_dir()/rules.json      — default ruleset ("template")
    config_dir()/roots.json      — the group of folders to sort
    config_dir()/roots.d/*.json  — optional per-folder rulesets
    config_dir()/settings.json   — timers, notifications, defaults
    data_dir()/history.log       — move journal (overridable via env)
"""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import shutil
import tempfile
from pathlib import Path

from . import APP_NAME
from . import platform

CONFIG_DIR = platform.config_dir()
DATA_DIR = platform.data_dir()
LOG_DIR = platform.log_dir()

_history_env = os.environ.get("GNU_SORTER_HISTORY")

# Kept for backwards compatibility; prefer Config(...).history_path at runtime.
HISTORY_LOG = Path(_history_env) if _history_env else platform.default_history_path()

# Where download-sorter kept its data (Linux only, for one-way migration).
OLD_CONFIG_DIR = (Path(os.path.expanduser("~/.config/download-sorter"))
                  if platform.is_linux() else None)
OLD_HISTORY_LOG = (Path(os.path.expanduser("~/.local/share/download-sorter/history.log"))
                   if platform.is_linux() else None)

# Image extensions shared by the default "Photos" rules.
IMAGE_EXTENSIONS = [
    "jpg", "jpeg", "png", "gif", "webp", "heic", "bmp", "tiff",
    "avif", "svg", "ico", "raw", "psd", "xcf"]

# "screenshot"-style names, but only when they are actually image files. A
# rule's extensions and regex are OR-ed, so binding the extension into the
# regex keeps `screen_capture.mp4` out of the Screenshots folder.
SCREENSHOT_RE = (
    "(screenshot|ibis|scree.?shot|capture).*\\."
    "(jpg|jpeg|png|gif|webp|heic|bmp|tiff|avif|dng|svg)$")

DEFAULT_RULES = [
    # Name-based rules come first: the first matching rule wins, so the
    # Wallpapers rule must precede the broad Photos extension rule below.
    {"name": "Photos/Screenshots", "extensions": [],
     "match_name_regex": SCREENSHOT_RE},
    {"name": "Wallpapers", "extensions": [],
     "match_name_regex": "wallpaper|white.?sur"},
    {"name": "Photos", "extensions": list(IMAGE_EXTENSIONS)},
    {"name": "Videos", "extensions": [
        "mp4", "mkv", "webm", "mov", "avi", "flv", "m4v", "wmv",
        "mpeg", "mpg", "3gp", "ts", "m2ts"]},
    {"name": "Music", "extensions": [
        "mp3", "flac", "wav", "ogg", "m4a", "aac", "opus", "wma",
        "mid", "midi"]},
    {"name": "Documents", "extensions": [
        "pdf", "doc", "docx", "odt", "txt", "md", "rtf", "xls", "xlsx",
        "csv", "ppt", "pptx", "odp", "epub"]},
    {"name": "Archives", "extensions": [
        "tar.gz", "tar.xz", "tar.bz2", "tar.zst", "tgz", "zip", "tar",
        "gz", "xz", "bz2", "7z", "rar", "zst", "lz4"]},
    {"name": "Installers", "extensions": [
        "appimage", "deb", "rpm", "apk", "exe", "msi", "dmg", "pkg",
        "run", "flatpak", "flatpakref"]},
    {"name": "ISOs", "extensions": ["iso", "img"]},
    {"name": "Code", "extensions": [
        "py", "js", "ts", "jsx", "tsx", "html", "css", "go", "rs", "c",
        "cpp", "h", "hpp", "java", "json", "sh", "fish", "yaml", "yml",
        "toml", "xml", "sql", "lua", "rb", "kt", "swift", "php", "vue",
        "svelte"]},
    {"name": "Fonts", "extensions": ["ttf", "otf", "woff", "woff2", "eot"]},
    {"name": "Torrents", "extensions": ["torrent"]},
]

DEFAULT_ROOTS = {"roots": [{"path": "~/Downloads", "enabled": True}]}

DEFAULT_SETTINGS = {
    "min_stable_seconds": 3,
    "stability_interval": 0.5,
    "cautious_downloads": True,
    "watch_interval": 2,
    "notifications": True,
    "dry_run_default": False,
}

# Quick rule-sets offered in the GUI ("Presets" menu).
PHOTOGRAPHY_RULES = [
    {"name": "Photos/Screenshots", "extensions": [],
     "match_name_regex": SCREENSHOT_RE},
    {"name": "Wallpapers", "extensions": [],
     "match_name_regex": "wallpaper|white.?sur"},
    {"name": "Photos", "extensions": [
        "jpg", "jpeg", "png", "gif", "webp", "heic", "bmp", "tiff",
        "avif", "raw", "psd", "xcf", "dng"]},
    {"name": "Videos", "extensions": [
        "mp4", "mkv", "webm", "mov", "avi", "m4v", "3gp"]},
]

CODE_RULES = [
    {"name": "Code", "extensions": [
        "py", "js", "ts", "jsx", "tsx", "html", "css", "go", "rs", "c",
        "cpp", "h", "hpp", "java", "json", "sh", "yaml", "yml", "toml",
        "xml", "sql", "lua", "rb", "kt", "swift", "php", "vue", "svelte"]},
    {"name": "Documents", "extensions": ["md", "txt", "pdf", "odt", "rtf"]},
    {"name": "Archives", "extensions": ["zip", "tar", "gz", "xz", "7z", "zst"]},
]

PRESETS: list[tuple[str, list[dict]]] = [
    ("GNOME defaults", DEFAULT_RULES),
    ("Photography", PHOTOGRAPHY_RULES),
    ("Code projects", CODE_RULES),
    ("Empty", []),
]


def atomic_write_text(path: Path, text: str) -> None:
    """Write `text` atomically: temp file in the same dir, then os.replace.

    The previous file is copied to `<path>.bak` first when it exists, so a
    failed or interrupted update never leaves a half-written config behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                               prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if path.exists():
            try:
                shutil.copy2(path, str(path) + ".bak")
            except OSError:
                pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read(path: Path, default):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError) as exc:
        print(f"!! broken config {path}: {exc}", flush=True)
        return default


def _write(path: Path, data) -> None:
    atomic_write_text(path, json.dumps(data, indent=2) + "\n")


# ------------------------------------------------------------------ rules ---
# Rule shape (every key except "name" is optional):
#   {
#     "name": "Photos",                 destination folder (required)
#     "enabled": true,                  false → rule is skipped entirely
#     "extensions": ["jpg", "png"],     match any of these end-of-name suffixes
#     "match_name_regex": "shot|cap",   match if the name searches this regex
#     "exclude_name_regex": "thumb",    veto if the name searches this regex
#     "match_all": false,               true → catch-all (matches every file)
#     "min_size_mb": 0,                 optional lower size bound
#     "max_size_mb": 0,                 optional upper size bound
#     "modified_within_days": 0         optional recency bound
#   }
# A rule is "positive" if it has extensions, match_name_regex or match_all.
# Rules are evaluated in order; the first enabled positive rule that matches
# wins (array order = priority).


# Characters Windows forbids in a file/folder name.
_WINDOWS_INVALID_CHARS = '<>:"|?*'

# Windows device names that cannot be used as (or as the stem of) a folder.
_WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)})


def safe_category_name(name) -> str | None:
    """Return a sanitised relative destination folder name, or None.

    Names may use subfolders ("Photos/Screenshots"), but must stay inside the
    watched root. Absolute paths, drive letters / UNC prefixes, NUL bytes and
    empty, "." or ".." components are rejected. Characters that are invalid
    on Windows, trailing dots/spaces (which Windows silently strips) and the
    reserved device names (CON, NUL, COM1, …) are rejected too so the same
    rule behaves identically on every platform.
    """
    if not isinstance(name, str):
        return None
    name = name.strip()
    if not name or "\x00" in name:
        return None
    if os.path.isabs(name):
        return None
    drive, _ = ntpath.splitdrive(name)
    if drive:
        return None
    for comp in name.replace("\\", "/").split("/"):
        if comp in ("", ".", ".."):
            return None
        if comp != comp.rstrip(" ."):
            return None
        if any(ch in _WINDOWS_INVALID_CHARS for ch in comp):
            return None
        if comp.split(".")[0].lower() in _WINDOWS_RESERVED_NAMES:
            return None
    return name


def normalize_rule(rule) -> dict | None:
    """Coerce one rule into the canonical shape. None if unusable."""
    if not isinstance(rule, dict):
        return None
    name = safe_category_name(rule.get("name", ""))
    if not name:
        return None
    out: dict = {"name": name}

    exts: list[str] = []
    raw_exts = rule.get("extensions")
    if isinstance(raw_exts, str):            # a lone "jpg" is one extension
        raw_exts = [raw_exts]
    elif not isinstance(raw_exts, (list, tuple)):
        raw_exts = []                        # anything else is ignored
    for e in raw_exts:
        e = str(e).strip().lower().lstrip(".")
        if e and e not in exts:
            exts.append(e)
    if exts:
        out["extensions"] = exts

    for key in ("match_name_regex", "exclude_name_regex"):
        val = rule.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()

    if rule.get("match_all"):
        out["match_all"] = True
    if rule.get("enabled") is False:
        out["enabled"] = False

    for key in ("min_size_mb", "max_size_mb", "modified_within_days"):
        try:
            val = float(rule.get(key, 0) or 0)
        except (TypeError, ValueError):
            val = 0.0
        if val > 0:
            out[key] = int(val) if val.is_integer() else val
    return out


def normalize_rules(rules) -> list[dict]:
    out = []
    for rule in rules or []:
        norm = normalize_rule(rule)
        if norm is not None:
            out.append(norm)
    return out


def rule_has_positive(rule: dict) -> bool:
    """True when the rule can actually match something."""
    return bool(rule.get("match_all")
                or rule.get("extensions")
                or rule.get("match_name_regex"))


def move_rule(rules: list[dict], name: str, new_index: int) -> list[dict]:
    """Return a new list with the rule called `name` moved to `new_index`."""
    rules = list(rules)
    found = None
    for i, rule in enumerate(rules):
        if rule.get("name") == name:
            found = i
            break
    if found is None:
        return rules
    rule = rules.pop(found)
    new_index = max(0, min(int(new_index), len(rules)))
    rules.insert(new_index, rule)
    return rules


def duplicate_rule(rules: list[dict], name: str) -> list[dict]:
    """Return a new list with a copy of `name` inserted right after it."""
    rules = list(rules)
    names = {r.get("name") for r in rules}
    for i, rule in enumerate(rules):
        if rule.get("name") != name:
            continue
        copy = json.loads(json.dumps(rule))
        base = f"{name} copy"
        candidate, n = base, 2
        while candidate in names:
            candidate = f"{base} {n}"
            n += 1
        copy["name"] = candidate
        rules.insert(i + 1, copy)
        return rules
    return rules


class Config:
    def __init__(self, config_dir: str | Path | None = None):
        explicit = config_dir is not None
        self.dir = Path(config_dir) if config_dir else CONFIG_DIR
        self.rules_path = self.dir / "rules.json"
        self.roots_path = self.dir / "roots.json"
        self.settings_path = self.dir / "settings.json"
        # The journal lives next to an explicitly configured directory so that
        # --config fully isolates a run. The default install keeps it in the
        # shared data dir (and honours the GNU_SORTER_HISTORY override).
        if explicit:
            self.history_path = self.dir / "history.log"
        elif _history_env:
            self.history_path = Path(_history_env)
        else:
            self.history_path = platform.default_history_path()

    # -- rules -----------------------------------------------------------
    def load_rules(self) -> list[dict]:
        data = _read(self.rules_path, None)
        if isinstance(data, dict):                       # legacy download-sorter schema
            data = data.get("categories", None)
        if not isinstance(data, list):
            return normalize_rules(json.loads(json.dumps(DEFAULT_RULES)))
        return normalize_rules(data)

    def save_rules(self, rules: list[dict]) -> None:
        _write(self.rules_path, normalize_rules(rules))

    # -- per-folder rulesets ---------------------------------------------
    @staticmethod
    def key(root_path) -> str:
        """Normalise a folder path so rulesets always match the same folder."""
        return os.path.abspath(os.path.expanduser(str(root_path)))

    def ruleset_path(self, root_path) -> Path:
        digest = hashlib.sha256(self.key(root_path).encode("utf-8")).hexdigest()[:40]
        return self.dir / "roots.d" / f"{digest}.json"

    def is_custom(self, root_path) -> bool:
        return self.ruleset_path(root_path).exists()

    def custom_roots(self) -> set[str]:
        """Normalised paths of every folder that has its own ruleset."""
        out = set()
        for path in (self.dir / "roots.d").glob("*.json"):
            data = _read(path, None)
            if isinstance(data, dict) and data.get("path"):
                out.add(self.key(data["path"]))
        return out

    def rules_for(self, root_path) -> tuple[list[dict], bool]:
        """Return (rules, is_custom) for one folder."""
        path = self.ruleset_path(root_path)
        if not path.exists():
            return self.load_rules(), False
        data = _read(path, None)
        if isinstance(data, dict) and isinstance(data.get("rules"), list):
            return normalize_rules(data["rules"]), True
        return self.load_rules(), False          # broken file → treat as inheriting

    def save_rules_for(self, root_path, rules: list[dict]) -> None:
        _write(self.ruleset_path(root_path),
               {"path": self.key(root_path), "rules": normalize_rules(rules)})

    def editable_rules_for(self, root_path) -> list[dict]:
        """Detach the folder if needed and return its editable rules list."""
        self.detach_rules(root_path)             # no-op if already custom
        rules, _ = self.rules_for(root_path)
        return rules

    def detach_rules(self, root_path) -> bool:
        """Give a folder its own copy of the ruleset. False if already custom."""
        if self.is_custom(root_path):
            return False
        self.save_rules_for(root_path, self.load_rules())
        return True

    def attach_rules(self, root_path) -> bool:
        """Revert a folder to inheriting the template. False if not custom."""
        path = self.ruleset_path(root_path)
        if not path.exists():
            return False
        path.unlink()
        return True

    # -- roots -----------------------------------------------------------
    def load_roots(self) -> list[dict]:
        data = _read(self.roots_path, DEFAULT_ROOTS)
        raw = data.get("roots", []) if isinstance(data, dict) else data
        if isinstance(raw, dict):                        # legacy double-nesting
            raw = raw.get("roots", [])
        if not isinstance(raw, list):                    # broken shape → sane default
            raw = []
        out = []
        for r in raw:
            if isinstance(r, str):
                r = {"path": r, "enabled": True}
            if isinstance(r, dict) and r.get("path"):
                out.append({"path": r["path"], "enabled": bool(r.get("enabled", True))})
        return out

    def save_roots(self, roots: list[dict]) -> None:
        _write(self.roots_path, {"roots": roots})

    # -- settings --------------------------------------------------------
    def load_settings(self) -> dict:
        s = dict(DEFAULT_SETTINGS)
        raw = _read(self.settings_path, {})
        if isinstance(raw, dict):
            s.update(raw)
        return s

    def save_settings(self, settings: dict) -> None:
        merged = dict(DEFAULT_SETTINGS)
        merged.update(settings)
        _write(self.settings_path, merged)

    # -- lifecycle -------------------------------------------------------
    def ensure_defaults(self) -> bool:
        """Create config files if missing. Returns True if migration ran."""
        self.dir.mkdir(parents=True, exist_ok=True)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        created = not self.rules_path.exists() and not self.roots_path.exists()
        if not self.rules_path.exists():
            self.save_rules(DEFAULT_RULES)
        if not self.roots_path.exists():
            self.save_roots(DEFAULT_ROOTS["roots"])
        if not self.settings_path.exists():
            self.save_settings(DEFAULT_SETTINGS)
        if created and OLD_CONFIG_DIR and OLD_CONFIG_DIR.exists():
            self._migrate()
            return True
        return False

    def _migrate(self) -> None:
        """Seed from an existing download-sorter install (rules, tuning, history)."""
        old_rules = OLD_CONFIG_DIR / "rules.json"
        if old_rules.exists():
            try:
                data = json.loads(old_rules.read_text())
            except (json.JSONDecodeError, OSError):
                data = None
            if isinstance(data, dict) and isinstance(data.get("categories"), list):
                self.save_rules(data["categories"])
                settings = self.load_settings()
                for key in ("min_stable_seconds", "watch_interval", "notifications"):
                    if key in data:
                        settings[key] = data[key]
                self.save_settings(settings)
                if data.get("downloads_dir"):
                    roots = self.load_roots()
                    if not any(r["path"] == data["downloads_dir"] for r in roots):
                        roots.insert(0, {"path": data["downloads_dir"], "enabled": True})
                    self.save_roots(roots)
            elif isinstance(data, list):
                self.save_rules(data)
        if OLD_HISTORY_LOG and OLD_HISTORY_LOG.exists():
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(OLD_HISTORY_LOG, self.history_path)