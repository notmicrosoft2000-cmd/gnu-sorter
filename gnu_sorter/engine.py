"""GNU Sorter core engine.

Pure-stdlib sorter that can tidy any folder (or a group of folders). It never
depends on GUI libraries, so the CLI, the systemd watcher and the GUI all share
exactly the same behaviour.

Design points carried over from download-sorter and hardened here:
  * top-level files only (category folders are never re-entered)
  * in-progress downloads (.part/.crdownload/...) are skipped
  * a file is only moved once its size has been stable for a few seconds
  * collisions never overwrite: file.png -> file (1).png
  * every move is journaled so it can be undone
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from .config import Config

# suffixes that mean "still being written"
IN_PROGRESS_SUFFIXES = (".part", ".crdownload", ".download", ".tmp", ".partial")


def expand(p) -> Path:
    return Path(os.path.expanduser(str(p)))


def fmt(path: Path) -> str:
    home = str(Path.home())
    s = str(path)
    return s.replace(home, "~", 1) if s.startswith(home) else s


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def looks_in_progress(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.endswith(s) for s in IN_PROGRESS_SUFFIXES)


def is_stable(path: Path, min_seconds: float, interval: float = 0.5) -> bool:
    """True only if the file is old enough and its size held still."""
    try:
        st1 = path.stat()
    except FileNotFoundError:
        return False
    if min_seconds > 0 and time.time() - st1.st_mtime < min_seconds:
        return False
    size1 = st1.st_size
    time.sleep(interval)
    try:
        st2 = path.stat()
    except FileNotFoundError:
        return False
    return st2.st_size == size1


def unique_path(dest: Path) -> Path:
    """Never overwrite: clash.png -> clash (1).png -> clash (2).png ..."""
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    n = 1
    while True:
        candidate = dest.with_name(f"{stem} ({n}){suffix}")
        if not candidate.exists():
            return candidate
        n += 1


class Sorter:
    """Stateful sorter bound to a particular Config."""

    def __init__(self, config: Config | None = None):
        self.config = config or Config()
        self._rules: list[dict] = []
        self._settings: dict = {}
        self._cache: dict[str, tuple[tuple, list[dict]]] = {}
        self.reload()

    # -- configuration ---------------------------------------------------
    def reload(self) -> None:
        self._rules = self._compile(self.config.load_rules())
        self._settings = self.config.load_settings()
        self._cache.clear()

    def _effective(self, root: Path) -> list[dict]:
        """Compiled rules for one folder (its own ruleset or the template)."""
        key = str(root)
        rs = self.config.ruleset_path(root)
        try:
            r_mtime = rs.stat().st_mtime_ns
        except OSError:
            r_mtime = 0                     # no ruleset file → inheriting
        try:
            t_mtime = self.config.rules_path.stat().st_mtime_ns
        except OSError:
            t_mtime = 0
        sig = (r_mtime, t_mtime)
        cached = self._cache.get(key)
        if cached and cached[0] == sig:
            return cached[1]
        raw, _ = self.config.rules_for(root)
        compiled = self._compile(raw)
        self._cache[key] = (sig, compiled)
        return compiled

    @property
    def settings(self) -> dict:
        return self._settings

    def roots(self, enabled_only: bool = True) -> list[Path]:
        out = []
        for r in self.config.load_roots():
            if enabled_only and not r["enabled"]:
                continue
            if r["path"]:
                out.append(expand(r["path"]))
        return out

    @staticmethod
    def _compile(rules: list[dict]) -> list[dict]:
        compiled = []
        for cat in rules:
            entry = {"name": cat.get("name", "Other")}
            if cat.get("match_name_regex"):
                try:
                    entry["_re"] = re.compile(cat["match_name_regex"], re.IGNORECASE)
                except re.error:
                    entry["_re"] = None
            exts = []
            for e in cat.get("extensions", []):
                e = str(e).strip().lower().lstrip(".")
                if e:
                    exts.append("." + e)
            entry["_ext"] = sorted(exts, key=len, reverse=True)  # longest first
            compiled.append(entry)
        return compiled

    # -- classification --------------------------------------------------
    def categorize(self, filename: str, rules: list[dict] | None = None) -> str:
        """Return the destination folder name for a file under `rules`.
        `None` means the template ruleset; an empty list means no rules."""
        name = filename.lower()
        for cat in (self._rules if rules is None else rules):
            re_ = cat.get("_re")
            if re_ and re_.search(name):
                return cat["name"]
            if any(name.endswith(ext) for ext in cat["_ext"]):
                return cat["name"]
        return "Needs-review"

    # -- moving ----------------------------------------------------------
    def _journal(self, src: Path, dest: Path) -> None:
        self.config.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.config.history_path.open("a") as fh:
            fh.write(f"{timestamp()}\t{src}\t{dest}\n")

    def sort_file(self, path: Path, root: Path, dry_run: bool = False,
                  quiet: bool = False) -> str:
        """Sort one file into a category folder inside `root`."""
        category = self.categorize(path.name, self._effective(root))
        dest_dir = root / category
        dest = unique_path(dest_dir / path.name)
        if dry_run:
            if not quiet:
                print(f"  would move  {fmt(path)}  ->  {fmt(dest_dir)}/")
            return category
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest))
        self._journal(path, dest)
        if not quiet:
            print(f"  moved       {fmt(path)}  ->  {category}/{dest.name}")
        return category

    # -- sweeping --------------------------------------------------------
    def sweep(self, roots: list[Path] | None = None, dry_run: bool = False) -> dict:
        roots = roots if roots is not None else self.roots()
        stats = {"moved": 0, "needs_review": 0, "skipped": 0}
        min_stable = float(self._settings.get("min_stable_seconds", 3))
        for root in roots:
            if not root.is_dir():
                continue
            try:
                entries = sorted(root.iterdir(), key=lambda p: p.name.lower())
            except OSError as exc:
                print(f"!! cannot read {fmt(root)}: {exc}", file=sys.stderr)
                continue
            for entry in entries:
                if entry.is_dir() or entry.is_symlink() or entry.name.startswith("."):
                    continue
                if looks_in_progress(entry.name):
                    stats["skipped"] += 1
                    continue
                if not is_stable(entry, min_stable):
                    stats["skipped"] += 1
                    continue
                category = self.sort_file(entry, root, dry_run=dry_run)
                stats["moved"] += 1
                if category == "Needs-review":
                    stats["needs_review"] += 1
        return stats

    # -- undo ------------------------------------------------------------
    def history(self, limit: int | None = None) -> list[tuple[str, str, str]]:
        if not self.config.history_path.is_file():
            return []
        lines = [l for l in self.config.history_path.read_text().splitlines() if l.count("\t") >= 2]
        rows = []
        for line in lines:
            ts, src, dest = line.split("\t", 2)
            rows.append((ts, src, dest))
        return rows[-limit:] if limit else rows

    def undo(self, dry_run: bool = False, limit: int | None = None) -> int:
        rows = self.history()
        if not rows:
            print("nothing to undo.")
            return 0
        to_undo = rows[-limit:] if limit else rows
        rest = rows[: len(rows) - len(to_undo)]
        restored = 0
        for ts, src, dest in reversed(to_undo):
            src_p, dest_p = Path(src), Path(dest)
            if dest_p.exists() and not src_p.exists():
                if dry_run:
                    print(f"  would restore  {fmt(dest_p)}  ->  {fmt(src_p)}")
                else:
                    src_p.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(dest_p), str(src_p))
                    print(f"  restored       {fmt(dest_p)}  ->  {fmt(src_p)}")
                restored += 1
            else:
                rest.append((ts, src, dest))   # not reversible, keep in the log
        if not dry_run:
            ordered = sorted(rest, key=lambda r: r[0])
            self.config.history_path.write_text(
                "".join(f"{t}\t{s}\t{d}\n" for t, s, d in ordered))
        print(f"undo complete: {restored} file(s) restored.")
        return restored
