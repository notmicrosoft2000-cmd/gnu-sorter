"""Funnel core engine.

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

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from .config import Config, atomic_write_text, safe_category_name

# suffixes that mean "still being written" — the file is a half-finished
# download and must never be sorted while the suffix is present. The browser
# or download manager renames the file to its final name when it finishes,
# which is the definitive "done" signal Tier 1 detection relies on.
IN_PROGRESS_SUFFIXES = (
    ".part",        # Firefox, wget, apt, most download managers
    ".crdownload",  # Chrome / Edge / Brave / Chromium
    ".download",    # Safari
    ".opdownload",  # Opera
    ".partial",     # Chrome stable channel / misc
    ".tmp",         # generic temp files
    ".filepart",    # some browsers / download managers
    ".ytdl",        # yt-dlp partial files
    ".!qb",         # qBittorrent incomplete download
    ".un~",         # uTorrent incomplete download
)

# extra-cautious settling: files this large get a longer settle window
_LARGE_FILE_MB = 100.0   # MB beyond which size scales the settle window
_EXTRA_PER_100_MB = 4.0   # +4 s per additional 100 MB
_MAX_EXTRA_SETTLE = 60.0  # ... capped at one extra minute
_MTIME_TOLERANCE = 0.25   # s — a rewrite bumps mtime; coarse FS is ~low


def settle_window_seconds(size_bytes: float, base_seconds: float) -> float:
    """How long a file of `size_bytes` must keep its size and mtime still.

    Extra-cautious by design: a 3 GB download must stay quiet for at least
    `base_seconds + 60` before we trust it, a 200 MB one for `+4`, while
    anything under 100 MB just uses the base window unchanged.
    """
    if base_seconds <= 0:
        return 0.0
    mb = size_bytes / (1024.0 * 1024.0)
    if mb <= _LARGE_FILE_MB:
        return base_seconds
    extra = (mb - _LARGE_FILE_MB) / 100.0 * _EXTRA_PER_100_MB
    return base_seconds + min(extra, _MAX_EXTRA_SETTLE)


def expand(p) -> Path:
    return Path(os.path.expanduser(str(p)))


def fmt(path: Path) -> str:
    home = str(Path.home())
    s = str(path)
    if s == home:
        return "~"
    if s.startswith(home + os.sep):
        return "~" + s[len(home):]
    return s


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _journal_line(ts: str, src: str, dest: str) -> str:
    """Serialize one journal entry.

    JSON (rather than tab-separated fields) keeps names that contain tabs or
    newlines intact, so undo can always restore them.
    """
    return json.dumps({"ts": ts, "src": src, "dest": dest},
                      ensure_ascii=False) + "\n"


def looks_in_progress(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.endswith(s) for s in IN_PROGRESS_SUFFIXES)


def is_stable(path: Path, min_seconds: float, interval: float = 0.5,
              min_probes: int = 3) -> bool:
    """True only if the file is old enough and its size AND mtime held still.

    Extra-cautious: samples ``min_probes`` times spaced ``interval`` apart and
    requires both size and mtime to stay constant — a download resuming (size
    grows) or a file being rewritten in place (mtime bumps) resets the check.
    Large files get a proportionally longer settle window first
    (:func:`settle_window_seconds`).
    """
    try:
        st1 = path.stat()
    except OSError:
        return False
    if min_seconds > 0:
        window = settle_window_seconds(st1.st_size, min_seconds)
        if time.time() - st1.st_mtime < window:
            return False
    size1 = st1.st_size
    mtime1 = st1.st_mtime
    for _ in range(max(min_probes - 1, 0)):
        time.sleep(interval)
        try:
            st2 = path.stat()
        except OSError:
            return False
        if (st2.st_size != size1
                or abs(st2.st_mtime - mtime1) > _MTIME_TOLERANCE):
            return False
    return True


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
            name = safe_category_name(cat.get("name", ""))
            if name is None:
                continue
            entry: dict = {"name": name}
            if cat.get("enabled") is False:
                entry["_enabled"] = False
            if cat.get("match_all"):
                entry["_match_all"] = True
            if cat.get("match_name_regex"):
                try:
                    entry["_re"] = re.compile(cat["match_name_regex"], re.IGNORECASE)
                except re.error:
                    entry["_re"] = None
            if cat.get("exclude_name_regex"):
                try:
                    entry["_exclude"] = re.compile(cat["exclude_name_regex"], re.IGNORECASE)
                except re.error:
                    entry["_exclude"] = None
            exts = []
            for e in cat.get("extensions", []):
                e = str(e).strip().lower().lstrip(".")
                if e:
                    exts.append("." + e)
            entry["_ext"] = sorted(set(exts), key=len, reverse=True)  # longest first
            for key, out_key in (("min_size_mb", "_min_mb"),
                                 ("max_size_mb", "_max_mb"),
                                 ("modified_within_days", "_days")):
                try:
                    val = float(cat.get(key, 0) or 0)
                except (TypeError, ValueError):
                    val = 0.0
                if val > 0:
                    entry[out_key] = val
            compiled.append(entry)
        return compiled

    # -- classification --------------------------------------------------
    def categorize(self, filename: str, rules: list[dict] | None = None,
                   stat: os.stat_result | None = None) -> str:
        """Return the destination folder name for a file under `rules`.

        `None` (rules) means the template ruleset; an empty list means no rules.
        Rules are evaluated in order and the first enabled positive rule that
        matches wins. `stat` (when given) powers the optional size/recency
        bounds; rules that need it are skipped when it is not available.
        """
        name = filename.lower()
        size_mb = age_days = None
        if stat is not None:
            size_mb = stat.st_size / (1024.0 * 1024.0)
            age_days = (time.time() - stat.st_mtime) / 86400.0
        for cat in (self._rules if rules is None else rules):
            if cat.get("_enabled") is False:
                continue
            matched = bool(cat.get("_match_all"))
            if not matched and cat.get("_re") and cat["_re"].search(name):
                matched = True
            if not matched and any(name.endswith(ext) for ext in cat["_ext"]):
                matched = True
            if not matched:
                continue
            exclude = cat.get("_exclude")
            if exclude and exclude.search(name):
                continue
            if cat.get("_min_mb") or cat.get("_max_mb"):
                if size_mb is None:
                    continue
                if cat.get("_min_mb") and size_mb < cat["_min_mb"]:
                    continue
                if cat.get("_max_mb") and size_mb > cat["_max_mb"]:
                    continue
            if cat.get("_days"):
                if age_days is None or age_days > cat["_days"]:
                    continue
            return cat["name"]
        return "Needs-review"

    # -- moving ----------------------------------------------------------
    def _destination(self, root: Path, category: str,
                     quiet: bool = False) -> tuple[Path, str]:
        """Resolve `root/category`, falling back to Needs-review when unsafe.

        Guards against path traversal and symlink escapes: the resolved
        destination must stay inside the resolved root.
        """
        safe = safe_category_name(category)
        if safe is not None:
            dest_dir = root / safe
            try:
                if dest_dir.resolve().is_relative_to(root.resolve()):
                    return dest_dir, safe
            except OSError:
                pass
        if not quiet:
            print(f"!! unsafe destination {category!r} for {fmt(root)}; "
                  f"using Needs-review/", file=sys.stderr)
        return root / "Needs-review", "Needs-review"

    def _journal(self, src: Path, dest: Path) -> None:
        self.config.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.config.history_path.open("a", encoding="utf-8") as fh:
            fh.write(_journal_line(timestamp(), str(src), str(dest)))

    def sort_file(self, path: Path, root: Path, dry_run: bool = False,
                  quiet: bool = False) -> str:
        """Sort one file into a category folder inside `root`."""
        try:
            stat = path.stat()
        except OSError:
            stat = None
        category = self.categorize(path.name, self._effective(root), stat)
        dest_dir, category = self._destination(root, category, quiet)
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
        stats = {"moved": 0, "needs_review": 0, "skipped": 0, "failed": 0,
                 "held": 0, "held_files": []}
        min_stable = float(self._settings.get("min_stable_seconds", 3))
        interval = float(self._settings.get("stability_interval", 0.5))
        cautious = bool(self._settings.get("cautious_downloads", True))
        for root in roots:
            if not root.is_dir():
                continue
            try:
                entries = sorted(root.iterdir(), key=lambda p: p.name.lower())
            except OSError as exc:
                print(f"!! cannot read {fmt(root)}: {exc}", file=sys.stderr)
                continue

            # First pass: pick the files that are eligible right now and
            # remember their size. Anything clearly mid-download (progress
            # suffix) or still inside its settle window is "held" — parked,
            # reported, and never sorted.
            held_here: list[str] = []
            candidates: list[tuple[Path, int, float]] = []
            for entry in entries:
                try:
                    if entry.is_dir() or entry.is_symlink() or entry.name.startswith("."):
                        continue
                except OSError:
                    stats["skipped"] += 1
                    continue
                if looks_in_progress(entry.name):
                    held_here.append(entry.name)
                    continue
                try:
                    st = entry.stat()
                except OSError:
                    stats["skipped"] += 1
                    continue
                if min_stable > 0:
                    window = settle_window_seconds(st.st_size, min_stable)
                    if time.time() - st.st_mtime < window:
                        held_here.append(entry.name)
                        continue
                candidates.append((entry, st.st_size, st.st_mtime))

            if not candidates:
                stats["held"] += len(held_here)
                stats["held_files"].extend(held_here)
                continue

            # Confirmation probes: wait once, twice in extra-cautious mode,
            # and only keep moving files whose size — and, when cautious,
            # mtime too — held still between probes. A single batch wait per
            # root instead of one wait per file.
            qualifying: list[tuple[Path, int, float]] = candidates
            probes = 2 if (cautious and not dry_run) else (1 if not dry_run else 0)
            for _ in range(probes):
                time.sleep(interval)
                kept: list[tuple[Path, int, float]] = []
                for entry, size1, mtime1 in qualifying:
                    if not entry.exists():
                        stats["skipped"] += 1
                        continue
                    try:
                        st = entry.stat()
                    except OSError:
                        stats["skipped"] += 1
                        continue
                    if (st.st_size != size1
                            or abs(st.st_mtime - mtime1) > _MTIME_TOLERANCE):
                        held_here.append(entry.name)
                        continue
                    kept.append((entry, size1, mtime1))
                qualifying = kept

            stats["held"] += len(held_here)
            stats["held_files"].extend(held_here)

            for entry, _size1, _mtime1 in qualifying:
                try:
                    category = self.sort_file(entry, root, dry_run=dry_run)
                except (OSError, shutil.Error) as exc:
                    # One un-sortable file (permissions, locks, …) must not
                    # abort the rest of the sweep.
                    print(f"!! could not sort {fmt(entry)}: {exc}",
                          file=sys.stderr)
                    stats["failed"] += 1
                    continue
                stats["moved"] += 1
                if category == "Needs-review":
                    stats["needs_review"] += 1
        return stats

    # -- undo ------------------------------------------------------------
    def history(self, limit: int | None = None) -> list[tuple[str, str, str]]:
        if not self.config.history_path.is_file():
            return []
        rows: list[tuple[str, str, str]] = []
        for line in self.config.history_path.read_text(
                encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("{"):                 # current JSON format
                try:
                    rec = json.loads(line)
                    rows.append((str(rec["ts"]), str(rec["src"]),
                                 str(rec["dest"])))
                except (ValueError, KeyError, TypeError):
                    continue
                continue
            if line.count("\t") >= 2:                # legacy tab-separated
                ts, src, dest = line.split("\t", 2)
                rows.append((ts, src, dest))
        return rows[-limit:] if limit else rows

    def undo(self, dry_run: bool = False, limit: int | None = None) -> int:
        rows = self.history()
        if not rows:
            print("nothing to undo.")
            return 0
        if limit is None:
            to_undo = rows
        elif limit > 0:
            to_undo = rows[-limit:]
        else:
            to_undo = []                             # --undo-last 0 = nothing
        rest = rows[: len(rows) - len(to_undo)]
        restored = 0
        skipped = 0
        for ts, src, dest in reversed(to_undo):
            src_p, dest_p = Path(src), Path(dest)
            if not dest_p.exists():
                print(f"  skipped        {fmt(dest_p)}  (no longer there)")
                rest.append((ts, src, dest))
                skipped += 1
                continue
            if src_p.exists():
                # Never clobber something that is already back at the source.
                print(f"  skipped        {fmt(src_p)}  (already exists; not overwriting)")
                rest.append((ts, src, dest))
                skipped += 1
                continue
            if dry_run:
                print(f"  would restore  {fmt(dest_p)}  ->  {fmt(src_p)}")
                restored += 1
                continue
            try:
                src_p.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dest_p), str(src_p))
            except OSError as exc:
                print(f"!! undo failed {fmt(dest_p)}  ->  {fmt(src_p)}: {exc}",
                      file=sys.stderr)
                rest.append((ts, src, dest))
                skipped += 1
                continue
            print(f"  restored       {fmt(dest_p)}  ->  {fmt(src_p)}")
            restored += 1
        if not dry_run:
            ordered = sorted(rest, key=lambda r: r[0])
            atomic_write_text(
                self.config.history_path,
                "".join(_journal_line(t, s, d) for t, s, d in ordered))
        summary = f"undo complete: {restored} file(s) restored"
        if skipped:
            summary += f", {skipped} skipped"
        print(summary + ".")
        return restored
