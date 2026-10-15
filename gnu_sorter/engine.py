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

import fnmatch
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


def _name_matches(pattern: str, filename: str) -> bool:
    """Case-insensitive name search: substring, or glob when the pattern has
    glob metacharacters (``*``, ``?``, ``[``)."""
    p = pattern.strip().lower()
    if not p:
        return True
    if any(ch in p for ch in "*?["):
        return fnmatch.fnmatch(filename.lower(), p)
    return p in filename.lower()


def _norm_exts(ext) -> set[str]:
    """'pdf, .PNG' -> {'pdf', 'png'} (leading dots and case ignored)."""
    out: set[str] = set()
    for part in str(ext).replace(",", " ").split():
        cleaned = part.strip().lstrip(".").lower()
        if cleaned:
            out.add(cleaned)
    return out


def _rec_size(rec: dict):
    """The record's byte size, or None when unknown."""
    value = rec.get("size")
    return value if isinstance(value, int) else None


def parse_size(text) -> int:
    """'500', '2k', '3M', '1G' (also '2kb', '1 mb') -> bytes.

    Shared by the CLI's size selectors and config validation.
    """
    s = str(text).strip().lower().replace(" ", "")
    if not s:
        raise ValueError("empty size")
    mult = 1
    for suffix, factor in (("kb", 1024), ("mb", 1024 ** 2), ("gb", 1024 ** 3),
                           ("k", 1024), ("m", 1024 ** 2), ("g", 1024 ** 3),
                           ("b", 1)):
        if s.endswith(suffix):
            s, mult = s[:-len(suffix)], factor
            break
    return int(float(s) * mult)


def read_journal(path: Path) -> list[tuple[str, dict]]:
    """Read a journal file into ``(raw_line, record)`` pairs, dropping
    malformed lines. Missing or unreadable files yield an empty list."""
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[tuple[str, dict]] = []
    for line in text.splitlines():
        rec = _parse_record(line)
        if rec is not None:
            out.append((line, rec))
    return out


class JournalIndex:
    """One pass over the move journal, then fast lookups.

    Building the index is O(n); afterwards counts by rule, entries by folder or
    batch, filename/date searches and restore previews are simple walks over
    pre-grouped buckets. Shared by the CLI, the rules page (``N sorted``
    badges) and the Activity view.
    """

    def __init__(self, entries: list[tuple[str, dict]] | None = None):
        self.entries: list[tuple[str, dict]] = list(entries or [])
        self.by_rule: dict[str, list[dict]] = {}
        self.by_folder: dict[str, list[dict]] = {}
        self.by_batch: dict[str, list[dict]] = {}
        for _raw, rec in self.entries:
            rule = rec.get("rule")
            if rule:
                self.by_rule.setdefault(rule, []).append(rec)
            dest = rec.get("dest")
            if dest:
                self.by_folder.setdefault(str(Path(dest).parent), []).append(rec)
            batch = rec.get("batch")
            if batch:
                self.by_batch.setdefault(batch, []).append(rec)

    @classmethod
    def load(cls, path: Path) -> "JournalIndex":
        return cls(read_journal(path))

    def __len__(self) -> int:
        return len(self.entries)

    def counts(self) -> dict[str, int]:
        """Number of live entries per rule name."""
        return {rule: len(rows) for rule, rows in self.by_rule.items()}

    def select(self, limit=None, since=None, before=None, folder=None,
               rule=None, batch=None, name=None, ext=None,
               min_size=None, max_size=None) -> list[tuple[str, dict]]:
        """Filter to ``(raw_line, record)`` pairs in chronological order.

        ``since``/``before`` bound the time window, ``folder`` matches the
        destination folder, ``rule``/``batch`` match a rule name or a sweep id,
        ``name`` is a case-insensitive substring or glob on the file name,
        ``ext`` matches the source extension (comma list allowed), and
        ``min_size``/``max_size`` bound the recorded byte size.
        ``limit`` keeps the most recent N (an empty/zero limit yields nothing).
        """
        if limit is not None and limit <= 0:
            return []
        rows = self.entries
        if since:
            cutoff = datetime.now() - _parse_duration(since)
            rows = [x for x in rows
                    if (dt := _ts_dt(x[1]["ts"])) is not None and dt >= cutoff]
        if before:
            cutoff = _parse_before(before)
            rows = [x for x in rows
                    if (dt := _ts_dt(x[1]["ts"])) is not None and dt < cutoff]
        if folder:
            want = str(Path(folder))
            rows = [x for x in rows
                    if str(Path(x[1]["dest"]).parent) == want]
        if rule:
            wanted = rule.lower()
            rows = [x for x in rows
                    if (x[1].get("rule") or "").lower() == wanted]
        if batch:
            rows = [x for x in rows if x[1].get("batch") == batch]
        if name:
            rows = [x for x in rows
                    if _name_matches(name, Path(x[1]["src"]).name)]
        if ext:
            wanted_ext = _norm_exts(ext)
            rows = [x for x in rows
                    if Path(x[1]["src"]).suffix.lstrip(".").lower()
                    in wanted_ext]
        if min_size is not None:
            rows = [x for x in rows
                    if (sz := _rec_size(x[1])) is not None and sz >= min_size]
        if max_size is not None:
            rows = [x for x in rows
                    if (sz := _rec_size(x[1])) is not None and sz <= max_size]
        if limit:
            rows = rows[-limit:]
        return rows


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


def plan_target(base: Path, taken: set) -> Path:
    """A collision-free restore target: ``base`` unless it is occupied on disk
    or already claimed by an earlier entry in the same plan (then
    ``name (1).ext``, ``name (2).ext``, ...)."""
    if not base.exists() and base not in taken:
        return base
    stem, suffix = base.stem, base.suffix
    n = 1
    while True:
        candidate = base.with_name(f"{stem} ({n}){suffix}")
        if not candidate.exists() and candidate not in taken:
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
            if cat.get("match_mode") == "all":
                entry["_mode_all"] = True
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
            ext_hit = any(name.endswith(ext) for ext in cat["_ext"])
            re_hit = bool(cat.get("_re")) and cat["_re"].search(name) is not None
            has_ext = bool(cat["_ext"])
            has_re = bool(cat.get("_re"))
            has_all = bool(cat.get("_match_all"))
            if cat.get("_mode_all"):
                # "All": every specified matcher must hit (the catch-all flag
                # counts as always true). A rule with no matchers never matches.
                matched = has_ext or has_re or has_all
                if has_ext and not ext_hit:
                    matched = False
                if has_re and not re_hit:
                    matched = False
            else:
                matched = has_all or ext_hit or re_hit
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

    def preview_plan(self, roots: list[Path] | None = None,
                     rules: list[dict] | None = None,
                     limit: int = 5000) -> dict[str, int]:
        """Read-only tally of where a folder's files *would* go.

        Destination folder → number of eligible top-level files. Mirrors the
        sweep's eligibility rule (top-level files only; hidden, symlinked and
        in-progress names skipped) but never touches disk, and unlike
        :meth:`sweep` it reports a per-destination breakdown. ``rules`` is a
        raw ruleset (compiled here) or None for the sorter's own rules.
        """
        roots = roots if roots is not None else self.roots()
        compiled = self._compile(rules) if rules is not None else None
        counts: dict[str, int] = {}
        seen = 0
        for root in roots:
            if not root.is_dir():
                continue
            try:
                entries = sorted(root.iterdir(), key=lambda p: p.name.lower())
            except OSError:
                continue
            for entry in entries:
                if seen >= limit:
                    break
                try:
                    if not entry.is_file() or entry.is_symlink():
                        continue
                    stat = entry.stat()
                except OSError:
                    continue
                if entry.name.startswith(".") or looks_in_progress(entry.name):
                    continue
                dest = self.categorize(entry.name, rules=compiled, stat=stat)
                counts[dest] = counts.get(dest, 0) + 1
                seen += 1
        return counts

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
            keep, max_bytes = self._rotation_limits()
            if self.config.history_path.stat().st_size > max_bytes:
                self._rotate_journal(keep)
        except OSError:
            pass

    def _rotation_limits(self) -> tuple[int, int]:
        """``(keep_entries, max_bytes)`` from settings, with safe fallbacks."""
        settings = self.config.load_settings()
        try:
            keep = int(settings.get("history_max_entries", _ROTATE_KEEP))
            max_bytes = int(settings.get("history_max_bytes", _ROTATE_BYTES))
        except (TypeError, ValueError):
            keep, max_bytes = _ROTATE_KEEP, _ROTATE_BYTES
        return max(keep, 1), max(max_bytes, 1)

    def _rotate_journal(self, keep: int | None = None) -> None:
        if keep is None:
            keep = self._rotation_limits()[0]
        self.backup_journal()           # preserve the full log before trimming
        loaded = self._read_journal()
        kept = loaded[-keep:]
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
        return read_journal(self.config.history_path)

    def journal_index(self) -> JournalIndex:
        """Read the journal once and build the by-rule/folder/batch index."""
        return JournalIndex(self._read_journal())

    def _select(self, loaded: list, limit=None, since=None, before=None,
                folder=None, rule=None, name=None, batch=None, ext=None,
                min_size=None, max_size=None) -> list:
        return JournalIndex(loaded).select(limit, since, before, folder, rule,
                                           batch, name, ext, min_size, max_size)

    def records(self, limit: int | None = None, since: str | None = None,
                before: str | None = None, folder: str | None = None,
                rule: str | None = None, name: str | None = None,
                batch: str | None = None, ext: str | None = None,
                min_size: int | None = None,
                max_size: int | None = None) -> list[dict]:
        """Journal entries (chronological order) as rich dicts, with selectors.

        ``name`` is a case-insensitive substring or glob on the file name;
        ``ext`` matches the source extension (comma list allowed);
        ``min_size``/``max_size`` bound the recorded byte size;
        ``batch`` selects one sweep's moves.
        """
        return [rec for _line, rec in
                self._select(self._read_journal(), limit, since, before,
                             folder, rule, name, batch, ext, min_size, max_size)]

    def rule_counts(self) -> dict[str, int]:
        """How many live journal entries each rule name has sorted.

        One pass over the journal (via the index); powers the "N sorted" badge
        on the rules page. The journal only keeps entries that have not been
        undone, so the count reflects what is currently in place.
        """
        return self.journal_index().counts()

    def history(self, limit: int | None = None) -> list[tuple[str, str, str]]:
        return [(r["ts"], r["src"], r["dest"])
                for r in self.records(limit=limit)]

    def restore_plan(self, to_undo: list,
                     target: str | None = None,
                     on_conflict: str = "rename") -> list[dict]:
        """Describe what ``undo`` would do, without touching the disk.

        Each item carries ``raw``/``rec`` plus: ``src``, ``dest``, ``size``,
        ``base`` (the intended original path or its name inside ``target``),
        ``target`` (where the file would actually land), ``status`` (one of
        ``restore``, ``collision``, ``overwrite``, ``skip``, ``already`` or
        ``missing``), ``changed`` (size drifted since sorting) and a human
        ``note``. ``on_conflict`` decides what happens when the original path is
        occupied: ``rename`` (default, ``name (1).ext``), ``overwrite`` (replace
        it) or ``skip`` (leave it, drop nothing? no — keep the entry).
        """
        if on_conflict not in ("rename", "overwrite", "skip"):
            raise ValueError(f"bad on_conflict {on_conflict!r}")
        plan: list[dict] = []
        taken: set = set()
        for raw, rec in reversed(to_undo):
            src_p, dest_p = Path(rec["src"]), Path(rec["dest"])
            base = (Path(target) / src_p.name) if target else src_p
            changed = False
            if rec.get("size") is not None:
                try:
                    changed = dest_p.stat().st_size != rec["size"]
                except OSError:
                    changed = False
            item = {"raw": raw, "rec": rec, "src": src_p, "dest": dest_p,
                    "size": rec.get("size"), "base": base, "target": base,
                    "status": "restore", "changed": changed, "note": None}
            if not dest_p.exists():
                item["status"] = "missing"
                item["note"] = "no longer there"
            elif not target and _same_file(rec, base):
                item["status"] = "already"
                item["note"] = "already back"
            elif on_conflict == "overwrite":
                taken.add(base)
                if base.exists():
                    item["status"] = "overwrite"
                    item["note"] = "replacing the file already there"
            elif on_conflict == "skip":
                if base.exists():
                    item["status"] = "skip"
                    item["note"] = "occupied — skipped"
                else:
                    taken.add(base)
            else:                                   # rename (default)
                resolved = plan_target(base, taken)
                taken.add(resolved)
                item["target"] = resolved
                if resolved != base:
                    item["status"] = "collision"
                    item["note"] = f"restoring to {resolved.name}"
            plan.append(item)
        return plan

    @staticmethod
    def _report_plan(plan: list[dict], preview: bool) -> int:
        """Print a restore plan; return how many files would move."""
        verb = "would restore" if preview else "restored"
        actionable = 0
        for item in plan:
            dest_p, target_p = item["dest"], item["target"]
            size = item.get("size")
            size_s = f"  ({size} B)" if isinstance(size, int) else ""
            status = item["status"]
            if status == "missing":
                print(f"  skipped      {fmt(dest_p)}  (no longer there)")
            elif status == "already":
                print(f"  already back {fmt(target_p)}")
            elif status == "skip":
                print(f"  skipped      {fmt(dest_p)}  (occupied — left in "
                      f"place)")
            else:
                extra = ""
                if status == "collision":
                    extra = f"  (occupied → {target_p.name})"
                elif status == "overwrite":
                    extra = "  (replacing existing)"
                if item.get("changed"):
                    extra += "  [size changed]"
                print(f"  {verb} {fmt(dest_p)}  ->  {fmt(target_p)}{size_s}{extra}")
                actionable += 1
        if preview:
            print(f"(preview) {actionable} file(s) would be restored.")
        return actionable

    def undo(self, dry_run: bool = False, limit: int | None = None,
             since: str | None = None, before: str | None = None,
             folder: str | None = None, rule: str | None = None,
             name: str | None = None, batch: str | None = None,
             target: str | None = None, ext: str | None = None,
             min_size: int | None = None, max_size: int | None = None,
             on_conflict: str = "rename") -> int:
        """Restore previously sorted files, dropping restored entries from the
        journal so a later undo never replays them.

        Selectors: ``limit`` (last N), ``since``/``before`` (time window),
        ``folder`` (entries sorted into this folder), ``rule`` (caught by this
        rule), ``name`` (filename substring/glob), ``batch`` (one sweep),
        ``ext`` (source extension, comma list) and ``min_size``/``max_size``
        (recorded byte size). ``target`` restores everything into that folder
        instead of each file's original location. ``on_conflict`` is
        ``rename`` (default; an occupied target becomes "name (1).ext"),
        ``overwrite`` (replace it) or ``skip`` (leave the file in place and keep
        the journal entry). A file already back where it started (size + mtime
        match) is dropped as already restored.
        """
        loaded = self._read_journal()
        if not loaded:
            print("nothing to undo.")
            return 0
        to_undo = self._select(loaded, limit, since, before, folder, rule,
                               name, batch, ext, min_size, max_size)
        if not to_undo:
            print("nothing matches those selectors.")
            return 0
        sel_ids = {id(x) for x in to_undo}
        rest = [x for x in loaded if id(x) not in sel_ids]
        plan = self.restore_plan(to_undo, target=target, on_conflict=on_conflict)
        if dry_run:
            return self._report_plan(plan, preview=True)
        restored = skipped = changed = 0
        for item in plan:
            rec = item["rec"]
            dest_p, target_p = item["dest"], item["target"]
            if item["status"] == "missing":
                print(f"  skipped      {fmt(dest_p)}  (no longer there)")
                rest.append((item["raw"], rec))
                skipped += 1
                continue
            if item["status"] == "already":
                print(f"  already back {fmt(target_p)}")
                skipped += 1
                continue
            if item["status"] == "skip":
                print(f"  skipped      {fmt(dest_p)}  (occupied — left in "
                      f"place)")
                rest.append((item["raw"], rec))
                skipped += 1
                continue
            if item["changed"]:
                print(f"  * {fmt(dest_p)} changed size since sorting; "
                      f"restoring anyway")
                changed += 1
            if item["status"] == "collision":
                print(f"  {fmt(item['base'])} occupied; restoring to "
                      f"{fmt(target_p)}")
            elif item["status"] == "overwrite":
                print(f"  {fmt(item['base'])} occupied; replacing it")
            try:
                target_p.parent.mkdir(parents=True, exist_ok=True)
                if item["status"] == "overwrite" and target_p.exists():
                    target_p.unlink()
                shutil.move(str(dest_p), str(target_p))
            except OSError as exc:
                print(f"!! undo failed {fmt(dest_p)} -> {fmt(target_p)}: {exc}",
                      file=sys.stderr)
                rest.append((item["raw"], rec))
                skipped += 1
                continue
            print(f"  restored     {fmt(dest_p)}  ->  {fmt(target_p)}")
            restored += 1
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

    # -- journal backup / export / import --------------------------------
    def backup_journal(self) -> Path | None:
        """Copy the journal to ``history.log.bak`` on demand.

        Returns the backup path, or None when there is no journal yet. (Every
        atomic rewrite of the journal already preserves a ``.bak``; this makes
        the same copy explicit and callable from the UI/CLI.)
        """
        src = self.config.history_path
        if not src.is_file():
            return None
        bak = src.with_name(src.name + ".bak")
        shutil.copy2(src, bak)
        return bak

    def export_journal(self, path, **selectors) -> int:
        """Write journal entries (optionally filtered) to ``path``.

        Accepts the same selectors as :meth:`records`. Returns the number of
        entries written. The output is newline-delimited JSON, re-importable by
        :meth:`import_journal`.
        """
        recs = self.records(**selectors)
        atomic_write_text(Path(path),
                          "".join(_journal_line_v2(rec) for rec in recs))
        return len(recs)

    def import_journal(self, path, merge: bool = True) -> int:
        """Read a journal file and add its entries to the live journal.

        ``merge`` (default) skips entries whose ``id`` already exists; a record
        without an id is always appended. With ``merge=False`` the live journal
        is replaced entirely. Returns the number of entries added.
        """
        incoming = read_journal(Path(path))
        if not merge:
            atomic_write_text(self.config.history_path,
                              "".join(raw + "\n" for raw, _rec in incoming))
            return len(incoming)
        kept = self._read_journal()
        seen = {rec.get("id") for _raw, rec in kept if rec.get("id")}
        added = 0
        for raw, rec in incoming:
            rid = rec.get("id")
            if rid and rid in seen:
                continue
            if rid:
                seen.add(rid)
            kept.append((raw, rec))
            added += 1
        atomic_write_text(self.config.history_path,
                          "".join(raw + "\n" for raw, _rec in kept))
        return added

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
