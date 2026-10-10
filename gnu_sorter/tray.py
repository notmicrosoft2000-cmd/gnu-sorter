"""System tray integration for GNU Sorter (Windows/Linux)."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional

from . import platform

try:
    from PIL import Image
    import pystray
except Exception as exc:  # pragma: no cover
    Image = None  # type: ignore
    pystray = None  # type: ignore
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


class Tray:
    def __init__(self, on_open_gui: Callable[[], None] | None = None,
                 on_sort_now: Callable[[], None] | None = None,
                 on_start_watch: Callable[[], None] | None = None,
                 on_stop_watch: Callable[[], None] | None = None,
                 on_quit: Callable[[], None] | None = None):
        self.on_open_gui = on_open_gui
        self.on_sort_now = on_sort_now
        self.on_start_watch = on_start_watch
        self.on_stop_watch = on_stop_watch
        self.on_quit = on_quit
        self._icon = None
        self._thread: Optional[threading.Thread] = None

    def _load_image(self) -> "Image.Image":
        # Try common locations
        candidates = [
            Path(__file__).parent.parent / "data" / "icons" / "gnu-sorter.png",
            Path.home() / ".local" / "share" / "icons" / "hicolor" / "48x48" / "apps" / "gnu-sorter.png",
            Path.home() / ".local" / "share" / "icons" / "hicolor" / "64x64" / "apps" / "gnu-sorter.png",
            Path.home() / ".local" / "share" / "icons" / "hicolor" / "128x128" / "apps" / "gnu-sorter.png",
        ]
        for c in candidates:
            if c.exists():
                try:
                    return Image.open(c)
                except Exception:
                    pass
        return Image.new('RGB', (64, 64), color=(30, 100, 180))

    def start(self) -> None:
        if pystray is None or Image is None:
            raise RuntimeError(f"pystray/PIL not available: {_IMPORT_ERROR}")
        menu = pystray.Menu(
            pystray.MenuItem("Open GUI", lambda: self._call(self.on_open_gui)),
            pystray.MenuItem("Sort now", lambda: self._call(self.on_sort_now)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Start watch", lambda: self._call(self.on_start_watch)),
            pystray.MenuItem("Stop watch", lambda: self._call(self.on_stop_watch)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: self._call_quit()),
        )
        self._icon = pystray.Icon("gnu-sorter", self._load_image(), "GNU Sorter", menu)
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass

    def _call(self, fn: Callable[[], None] | None):
        try:
            if fn:
                fn()
        except Exception:
            pass

    def _call_quit(self):
        self._call(self.on_quit)
        self.stop()
