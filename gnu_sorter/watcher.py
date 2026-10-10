"""GNU Sorter background watcher.

Backends, chosen automatically:
  1. watchdog (cross-platform: inotify on Linux, ReadDirectoryChangesW on
     Windows, FSEvents on macOS) — preferred when installed.
  2. inotifywait (Linux, when watchdog is unavailable) — original fast path.
  3. polling (last resort everywhere).

All backends keep the same safety rules: top-level files only, in-progress
suffixes skipped, and a stability window before moving.
"""
from __future__ import annotations

import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import platform
from .engine import is_stable, looks_in_progress


def _notify(enabled: bool, message: str) -> None:
    if enabled and shutil.which("notify-send"):
        subprocess.Popen(["notify-send", "GNU Sorter", message])


def _watchdog_available() -> bool:
    try:
        import watchdog  # noqa: F401
        return True
    except Exception:
        return False


class Watcher:
    def __init__(self, sorter):
        self.sorter = sorter
        self.stop_event = threading.Event()
        self._roots: list[Path] = []
        self._proc: subprocess.Popen | None = None

    # -- public ----------------------------------------------------------
    def run(self, dry_run: bool = False) -> None:
        roots = [r for r in self.sorter.roots() if r.is_dir()]
        if not roots:
            print("no enabled folders to watch.", file=sys.stderr)
            return
        self._roots = roots
        settings = self.sorter.settings
        notify = bool(settings.get("notifications", True))
        min_stable = float(settings.get("min_stable_seconds", 3))
        interval = float(settings.get("watch_interval", 2))

        print("GNU Sorter watching:")
        for r in roots:
            print(f"  • {r}")
        if _watchdog_available():
            print("  (backend: watchdog)")
            self._run_watchdog(dry_run, notify, min_stable)
        elif platform.has_inotify():
            print("  (backend: inotifywait)")
            self._run_inotify(dry_run, notify, min_stable)
        else:
            print("  (backend: polling)")
            self._run_poll(dry_run, notify, min_stable, interval)

    def stop(self) -> None:
        self.stop_event.set()
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    # -- shared helpers --------------------------------------------------
    def _owner(self, path: Path) -> Path | None:
        """Return the watched root containing `path` (top-level file only)."""
        if path.name.startswith(".") or path.is_symlink():
            return None
        if looks_in_progress(path.name):
            return None
        try:
            if not path.is_file():
                return None
        except OSError:
            return None
        for root in self._roots:
            try:
                if path.parent == root:
                    return root
            except OSError:
                continue
        return None

    def _wait_and_sort(self, path: Path, root: Path, dry_run: bool,
                       notify: bool, min_stable: float) -> None:
        deadline = time.time() + 60
        while time.time() < deadline and not self.stop_event.is_set():
            if is_stable(path, min_stable, interval=1.0):
                break
        else:
            return
        if not path.exists():
            return
        category = self.sorter.sort_file(path, root, dry_run=dry_run)
        _notify(notify, f"Sorted → {category}/")

    def _handle(self, path: Path, dry_run: bool, notify: bool,
                min_stable: float) -> None:
        root = self._owner(path)
        if root is None:
            return
        self._wait_and_sort(path, root, dry_run, notify, min_stable)

    # -- watchdog backend ------------------------------------------------
    def _run_watchdog(self, dry_run: bool, notify: bool,
                      min_stable: float) -> None:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        jobs: "queue.Queue[Path]" = queue.Queue()

        class Handler(FileSystemEventHandler):
            def on_created(self, event):
                if not event.is_directory:
                    jobs.put(Path(event.src_path))

            def on_moved(self, event):
                if not event.is_directory:
                    jobs.put(Path(event.dest_path))

        handler = Handler()

        def worker():
            while not self.stop_event.is_set():
                try:
                    path = jobs.get(timeout=0.5)
                except queue.Empty:
                    continue
                try:
                    self._handle(path, dry_run, notify, min_stable)
                except Exception as exc:  # keep the watcher alive
                    print(f"!! watcher error on {path}: {exc}", file=sys.stderr)

        observer = Observer()
        for root in self._roots:
            root.mkdir(parents=True, exist_ok=True)
            observer.schedule(handler, str(root), recursive=False)
        observer.start()
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            while not self.stop_event.wait(0.5):
                pass
        except KeyboardInterrupt:
            pass
        finally:
            observer.stop()
            observer.join(timeout=3)

    # -- inotify backend -------------------------------------------------
    def _run_inotify(self, dry_run: bool, notify: bool,
                     min_stable: float) -> None:
        cmd = ["inotifywait", "-m", "-e", "close_write", "-e", "moved_to",
               "--format", "%w%f", *[str(r) for r in self._roots]]
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, text=True)
        assert self._proc.stdout is not None
        try:
            for line in self._proc.stdout:
                if self.stop_event.is_set():
                    break
                raw = line.strip()
                if raw:
                    self._handle(Path(raw), dry_run, notify, min_stable)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    # -- polling backend -------------------------------------------------
    def _run_poll(self, dry_run: bool, notify: bool, min_stable: float,
                  interval: float) -> None:
        start = time.time()
        try:
            while not self.stop_event.is_set():
                for root in self._roots:
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
                            owner = self._owner(entry)
                            if owner is None:
                                continue
                            category = self.sorter.sort_file(entry, owner,
                                                             dry_run=dry_run)
                            _notify(notify, f"Sorted → {category}/")
                self.stop_event.wait(interval)
        except KeyboardInterrupt:
            pass
