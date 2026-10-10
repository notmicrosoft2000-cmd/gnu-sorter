"""Multi-root background watcher for GNU Sorter.

Uses a single `inotifywait` process watching every enabled root when available,
falling back to lightweight polling otherwise. New arrivals are sorted after a
stability window so half-written downloads are never moved.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from .engine import Sorter, looks_in_progress, is_stable, fmt


def _notify(enabled: bool, message: str) -> None:
    if enabled and shutil.which("notify-send"):
        subprocess.Popen(["notify-send", "GNU Sorter", message])


class Watcher:
    def __init__(self, sorter: Sorter):
        self.sorter = sorter
        self.stop_event = threading.Event()
        self._proc: subprocess.Popen | None = None

    # -- public ----------------------------------------------------------
    def run(self, dry_run: bool = False) -> None:
        roots = [r for r in self.sorter.roots() if r.is_dir()]
        if not roots:
            print("no enabled folders to watch.", file=sys.stderr)
            return
        settings = self.sorter.settings
        notify = bool(settings.get("notifications", True))
        min_stable = float(settings.get("min_stable_seconds", 3))
        interval = float(settings.get("watch_interval", 2))

        print("GNU Sorter watching:")
        for r in roots:
            print(f"  • {fmt(r)}")
        sys.stdout.flush()

        if shutil.which("inotifywait"):
            self._run_inotify(roots, dry_run, notify, min_stable)
        else:
            print("  (inotifywait not found — using polling fallback)")
            sys.stdout.flush()
            self._run_poll(roots, dry_run, notify, min_stable, interval)

    def stop(self) -> None:
        self.stop_event.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    # -- inotify backend -------------------------------------------------
    def _ensure_root_dirs(self, roots: list[Path]) -> None:
        for r in roots:
            r.mkdir(parents=True, exist_ok=True)

    def _root_for(self, path: Path, roots: list[Path]) -> Path | None:
        for r in roots:
            try:
                if path.parent == r:
                    return r
            except OSError:
                continue
        return None

    def _run_inotify(self, roots: list[Path], dry_run: bool,
                     notify: bool, min_stable: float) -> None:
        cmd = ["inotifywait", "-m", "-e", "close_write", "-e", "moved_to",
               "--format", "%w%f", *[str(r) for r in roots]]
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, text=True)
        assert self._proc.stdout is not None
        try:
            for line in self._proc.stdout:
                if self.stop_event.is_set():
                    break
                raw = line.strip()
                if not raw:
                    continue
                path = Path(raw)
                if path.is_symlink() or path.name.startswith(".") or not path.is_file():
                    continue
                if looks_in_progress(path.name):
                    continue
                root = self._root_for(path, roots)
                if root is None:
                    continue
                # close_write fires the moment the writer stops: wait out the
                # stability window, then confirm the size really held still.
                deadline = time.time() + 60
                while time.time() < deadline and not self.stop_event.is_set():
                    if is_stable(path, min_stable, interval=1.0):
                        break
                else:
                    continue
                if not path.exists():
                    continue
                category = self.sorter.sort_file(path, root, dry_run=dry_run)
                _notify(notify, f"Sorted → {category}/")
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    # -- polling backend -------------------------------------------------
    def _run_poll(self, roots: list[Path], dry_run: bool, notify: bool,
                  min_stable: float, interval: float) -> None:
        start = time.time()
        try:
            while not self.stop_event.is_set():
                for root in roots:
                    if not root.is_dir():
                        continue
                    boundary = time.time() - min_stable
                    for entry in list(root.iterdir()):
                        if (entry.is_dir() or entry.is_symlink()
                                or entry.name.startswith(".")):
                            continue
                        try:
                            mtime = entry.stat().st_mtime
                        except FileNotFoundError:
                            continue
                        if mtime < start - 60:      # only new arrivals
                            continue
                        if mtime > boundary:
                            continue
                        if looks_in_progress(entry.name):
                            continue
                        if is_stable(entry, 0):
                            category = self.sorter.sort_file(entry, root, dry_run=dry_run)
                            _notify(notify, f"Sorted → {category}/")
                self.stop_event.wait(interval)
        except KeyboardInterrupt:
            pass
