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
import uuid
from datetime import datetime, timedelta
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


JOURNAL_VERSION = 2
_ROTATE_BYTES = 1_000_000     # journal grows to ~1 MB before trimming
_ROTATE_KEEP = 2500           # keep the most recent 2500 entries when trimming
_MTIME_MATCH_TOLERANCE = 2.0  # s — coarse filesystems vs. journal mtime

_DURATION_RE = re.compile(r"^(\d+)\s*([smhdw])$")


def _journal_record(ts: str, src: str, dest: str, *, size=None, mtime=None,
                    rule: str | None = None, root: str | None = None,
                    batch: str | None = None) -> dict:
    """Build one journal v2 entry with rich metadata.

    Batch ids group the moves of one sweep so undo can reason about whole
    operations; size/mtime power the already-restored detection.
    """
    rec: dict = {"v": JOURNAL_VERSION, "id": uuid.uuid4().hex,
                 "ts": ts, "src": src, "dest": dest}
    if size is not None:
        rec["size"] = size
    if mtime is not None:
        rec["mtime"] = mtime
    if rule is not None:
        rec["rule"] = rule
    if root is not None:
        rec["root"] = root
    if batch is not None:
        rec["batch"] = batch
    return rec


def _journal_line(ts: str, src: str, dest: str) -> str:
    """Serialize one minimal (v1) journal entry.

    JSON (rather than tab-separated fields) keeps names that contain tabs or
    newlines intact, so undo can always restore them.
    """
    return json.dumps({"ts": ts, "src": src, "dest": dest},
                      ensure_ascii=False) + "\n"


def _journal_line_v2(rec: dict) -> str:
    return json.dumps(rec, ensure_ascii=False) + "\n"


def _parse_record(line: str) -> dict | None:
    """Parse one journal line into a record dict (any schema version), else None."""
    line = line.strip()
    if not line:
        return None
    if line.startswith("{"):                 # v1 / v2 JSON
        try:
            d = json.loads(line)
        except ValueError:
            return None
        if "src" not in d or "dest" not in d:
            return None
        return {"v": d.get("v", 1), "id": d.get("id"), "batch": d.get("batch"),
                "ts": str(d.get("ts", "")), "src": str(d.get("src", "")),
                "dest": str(d.get("dest", "")), "size": d.get("size"),
                "mtime": d.get("mtime"), "rule": d.get("rule"),
                "root": d.get("root")}
    if line.count("\t") >= 2:                # legacy tab-separated (v0)
        ts, src, dest = line.split("\t", 2)
        return {"v": 0, "id": None, "batch": None, "ts": ts, "src": src,
                "dest": dest, "size": None, "mtime": None,
                "rule": None, "root": None}
    return None


def _ts_dt(ts: str):
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def _parse_duration(text: str) -> timedelta:
    m = _DURATION_RE.match(text.strip().lower())
    if not m:
        raise ValueError(f"bad duration {text!r} (use e.g. 30m, 2h, 7d)")
    n, unit = int(m.group(1)), m.group(2)
    return timedelta(seconds=n * {"s": 1, "m": 60, "h": 3600,
                                  "d": 86400, "w": 604800}[unit])


def _parse_before(text: str):
    t = text.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, fmt)
        except ValueError:
            continue
    raise ValueError(f"bad timestamp {text!r} "
                     f"(use YYYY-MM-DD or 'YYYY-MM-DD HH:MM:SS')")


def _same_file(rec: dict, path: Path) -> bool:
    """True if `path` looks like the same file the journal recorded (size and
    mtime match) — i.e. it is already back where it used to be."""
    if rec.get("size") is None or rec.get("mtime") is None:
        return False
    try:
        st = path.stat()
    except OSError:
        return False
    return (st.st_size == rec["size"]
            and abs(st.st_mtime - rec["mtime"]) <= _MTIME_MATCH_TOLERANCE)


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

    def classify(self, filenames: list[str],
                 rules: list[dict] | None = None) -> dict[str, str]:
        """Map each file name to the destination it would sort into.

        `rules` may be a raw ruleset (compiled here) or None to use the sorter's
        current rules. Used by the wizard's live preview / animation and by the
        rules-page filename tester; it never touches disk.
        """
        compiled = self._compile(rules) if rules is not None else self._rules
        return {name: self.categorize(name, rules=compiled)
                for name in filenames}

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

    def _journal(self, src: Path, dest: Path, rule: str, root: str,
                 batch: str, size=None, mtime=None) -> None:
        """Append one journal v2 entry; trim the file when it grows too
        large so the history never balloons."""
        rec = _journal_record(timestamp(), str(src), str(dest),
                              size=size, mtime=mtime, rule=rule, root=root,
                              batch=batch)
        self.config.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.config.history_path.open("a", encoding="utf-8") as fh:
            fh.write(_journal_line_v2(rec))
        try:
            if self.config.history_path.stat().st_size > _ROTATE_BYTES:
                self._rotate_journal()
        except OSError:
            pass

    def _rotate_journal(self) -> None:
        loaded = self._read_journal()
        kept = loaded[-_ROTATE_KEEP:]
        atomic_write_text(self.config.history_path,
                          "".join(line + "\n" for line, _rec in kept))

    def sort_file(self, path: Path, root: Path, dry_run: bool = False,
                  quiet: bool = False, batch: str | None = None) -> str:
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
        self._journal(
            path, dest, category, str(root),
            batch or uuid.uuid4().hex,
            size=stat.st_size if stat else None,
            mtime=stat.st_mtime if stat else None)
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
        batch = uuid.uuid4().hex    # one operation id for this whole sweep
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
                    category = self.sort_file(entry, root, dry_run=dry_run,
                                              batch=batch)
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

    # -- journal / undo (v2) --------------------------------------------
    def _read_journal(self) -> list[tuple[str, dict]]:
        """(raw_line, record) pairs in file order; malformed lines dropped."""
        if not self.config.history_path.is_file():
            return []
        out: list[tuple[str, dict]] = []
        for line in self.config.history_path.read_text(
                encoding="utf-8").splitlines():
            rec = _parse_record(line)
            if rec is not None:
                out.append((line, rec))
        return out

    def _select(self, loaded: list, limit=None, since=None, before=None,
                folder=None, rule=None) -> list:
        rows = loaded
        if limit is not None and limit <= 0:
            return []
        if since:
            cutoff = datetime.now() - _parse_duration(since)
            rows = [x for x in rows
                    if (dt := _ts_dt(x[1]["ts"])) is not None and dt >= cutoff]
        if before:
            cutoff = _parse_before(before)
            rows = [x for x in rows
                    if (dt := _ts_dt(x[1]["ts"])) is not None and dt < cutoff]
        if folder:
            folder_path = str(Path(folder))
            rows = [x for x in rows
                    if str(Path(x[1]["dest"]).parent) == folder_path]
        if rule:
            wanted = rule.lower()
            rows = [x for x in rows
                    if (x[1].get("rule") or "").lower() == wanted]
        if limit:
            rows = rows[-limit:]
        return rows

    def records(self, limit: int | None = None, since: str | None = None,
                before: str | None = None, folder: str | None = None,
                rule: str | None = None) -> list[dict]:
        """Journal entries (chronological order) as rich dicts, with selectors."""
        return [rec for _line, rec in
                self._select(self._read_journal(), limit, since, before,
                             folder, rule)]

    def rule_counts(self) -> dict[str, int]:
        """How many live journal entries each rule name has sorted.

        One pass over the journal; powers the "N sorted" badge on the rules
        page. The journal only keeps entries that have not been undone, so the
        count reflects what is currently in place.
        """
        counts: dict[str, int] = {}
        for _line, rec in self._read_journal():
            rule = rec.get("rule")
            if rule:
                counts[rule] = counts.get(rule, 0) + 1
        return counts

    def history(self, limit: int | None = None) -> list[tuple[str, str, str]]:
        return [(r["ts"], r["src"], r["dest"])
                for r in self.records(limit=limit)]

    def undo(self, dry_run: bool = False, limit: int | None = None,
             since: str | None = None, before: str | None = None,
             folder: str | None = None, rule: str | None = None) -> int:
        """Restore previously sorted files, dropping restored entries from the
        journal so a later undo never replays them.

        Selectors: ``limit`` (last N), ``since``/``before`` (time window),
        ``folder`` (entries sorted into this folder), ``rule`` (caught by this
        rule). Collision-safe: an occupied original path restores to
        "name (1).ext" unless the file already there matches the journal (size
        + mtime) — then it is treated as already restored and dropped.
        """
        loaded = self._read_journal()
        if not loaded:
            print("nothing to undo.")
            return 0
        to_undo = self._select(loaded, limit, since, before, folder, rule)
        if not to_undo:
            print("nothing matches those selectors.")
            return 0
        sel_ids = {id(x) for x in to_undo}
        rest = [x for x in loaded if id(x) not in sel_ids]
        restored = 0
        skipped = 0
        changed = 0
        for _raw, rec in reversed(to_undo):
            src_p, dest_p = Path(rec["src"]), Path(rec["dest"])
            if not dest_p.exists():
                print(f"  skipped      {fmt(dest_p)}  (no longer there)")
                rest.append((_raw, rec))
                skipped += 1
                continue
            if rec.get("size") is not None:
                try:
                    size_now = dest_p.stat().st_size
                except OSError:
                    size_now = None
                if size_now is not None and size_now != rec["size"]:
                    print(f"  * {fmt(dest_p)} changed size since sorting; "
                          f"restoring anyway")
                    changed += 1
            if src_p.exists():
                if _same_file(rec, src_p):
                    print(f"  already back {fmt(src_p)}")
                    skipped += 1
                    continue              # done — drop from the journal
                target = unique_path(src_p)     # collision-safe restore
                print(f"  {fmt(src_p)} occupied; restoring to {fmt(target)}")
                if not dry_run:
                    try:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(dest_p), str(target))
                    except OSError as exc:
                        print(f"!! undo failed {fmt(dest_p)} -> {fmt(target)}: {exc}",
                              file=sys.stderr)
                        rest.append((_raw, rec))
                        skipped += 1
                        continue
                restored += 1
                continue
            if dry_run:
                print(f"  would restore {fmt(dest_p)}  ->  {fmt(src_p)}")
                restored += 1
                continue
            try:
                src_p.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dest_p), str(src_p))
            except OSError as exc:
                print(f"!! undo failed {fmt(dest_p)}  ->  {fmt(src_p)}: {exc}",
                      file=sys.stderr)
                rest.append((_raw, rec))
                skipped += 1
                continue
            print(f"  restored     {fmt(dest_p)}  ->  {fmt(src_p)}")
            restored += 1
        if not dry_run:
            atomic_write_text(
                self.config.history_path,
                "".join(line + "\n" for line, _rec in rest))
        summary = f"undo complete: {restored} file(s) restored"
        if changed:
            summary += f", {changed} changed before restore"
        if skipped:
            summary += f", {skipped} skipped"
        print(summary + ".")
        return restored

    def repair_journal(self) -> int:
        """Drop malformed lines, keep the rest. Returns the count dropped."""
        if not self.config.history_path.is_file():
            return 0
        raw = self.config.history_path.read_text(encoding="utf-8").splitlines()
        kept = [ln for ln in raw if _parse_record(ln) is not None]
        dropped = len(raw) - len(kept)
        if dropped:
            atomic_write_text(self.config.history_path,
                              "".join(ln + "\n" for ln in kept))
        return dropped
