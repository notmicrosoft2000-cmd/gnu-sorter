"""Application lifecycle for the Funnel shell.

``FunnelApp`` keeps the compatibility surface used by ``cli.py`` and
``tray.py``: ``app.window``, ``app._tray``, the app actions and their
accelerators. The window itself is created lazily, on ``activate``.

Update checks are deliberately split from installing: the startup check only
notifies, and a manual check hands the result to ``window.open_update_result``
so the UI can ask before anything is installed.
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib  # noqa: E402

from .. import APP_ID  # noqa: E402
from ..config import Config  # noqa: E402
from .controller import DeskController  # noqa: E402
from .theme import apply_color_scheme  # noqa: E402


class FunnelApp(Adw.Application):
    def __init__(self, cfg: Config, migrated: bool = False):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.cfg = cfg
        self.controller = DeskController(self, cfg)
        self.sorter = self.controller.sorter  # compatibility alias
        self.migrated = migrated
        self.window = None
        self._tray = None
        self._update_scheduled = False
        self.connect("activate", self.on_activate)
        self._add_actions()

    # ------------------------------------------------------------ actions ---
    def _add_actions(self) -> None:
        handlers = {
            "about": lambda *_: self.window and self.window.show_about(),
            "sort-now": lambda *_: self.window and self.window.on_sort_now(None),
            "update": lambda *_: self.on_update_clicked(),
            "quit": lambda *_: self.quit(),
        }
        for name, handler in handlers.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)
        for action, accel in (("app.quit", "<Control>q"),
                              ("app.sort-now", "<Control>n"),
                              ("win.preferences", "<Control>comma"),
                              ("win.shortcuts", "<Control>question")):
            self.set_accels_for_action(action, [accel])

    def _make_window(self):
        """Build the window with the one controller this app owns."""
        from .window import MainWindow
        return MainWindow(self, controller=self.controller)

    def on_activate(self, _app=None) -> None:
        apply_color_scheme(self.cfg)
        if self.window is None:
            self.window = self._make_window()
        self.window.present()
        if self.migrated:
            self.window.toast(
                "Imported your rules and history from download-sorter")
        if not self._update_scheduled:
            self._update_scheduled = True
            GLib.timeout_add_seconds(3, self._background_update_check)

    # -------------------------------------------------------------- toasts ---
    def toast(self, message: str) -> None:
        if self.window is not None:
            self.window.toast(message)

    # ------------------------------------------------------------- updates ---
    def on_update_clicked(self, *_args) -> None:
        self.toast("Checking for updates\u2026")
        threading.Thread(target=lambda: self._update_worker(notify=True),
                         daemon=True).start()

    def _background_update_check(self) -> bool:
        """One-shot startup check. It never installs anything."""
        if not self.cfg.load_settings().get("check_for_updates", True):
            return False
        threading.Thread(target=lambda: self._update_worker(notify=False),
                         daemon=True).start()
        return False

    def _update_worker(self, notify: bool) -> None:
        try:
            from .. import updater
            current, latest, needed = updater.check()
            kind = updater.install_kind()
            if not needed:
                if notify:
                    GLib.idle_add(self._show_update_result,
                                  current, latest, False, kind, True)
                return
            if kind == "flatpak":
                # Read-only sandbox: never self-replace, just point at Flatpak.
                message = (f"{latest} is available. Run "
                           f"`flatpak update {updater.FLATPAK_ID}`")
                GLib.idle_add(self.toast, message)
                if notify:
                    GLib.idle_add(self._show_update_result,
                                  current, latest, True, kind, True)
                return
            if notify:
                GLib.idle_add(self._show_update_result,
                              current, latest, True, kind, False)
            else:
                GLib.idle_add(
                    self.toast,
                    f"Update {latest} is available. Check the app menu "
                    "for details.")
        except Exception as exc:
            if notify:
                GLib.idle_add(self.toast, f"Update check failed: {exc}")

    def _show_update_result(self, current, latest, needed, kind,
                            up_to_date=False) -> bool:
        """Hand the result to the window, tolerating either call convention."""
        win = self.window
        if win is None:
            return False
        opener = getattr(win, "open_update_result", None)
        if callable(opener):
            attempts = (
                lambda: opener(current=current, latest=latest,
                               needed=needed, kind=kind),
                lambda: opener(current, latest, needed, kind),
                lambda: opener({"current": current, "latest": latest,
                                "needed": needed, "kind": kind}),
            )
            for attempt in attempts:
                try:
                    attempt()
                    return False
                except TypeError:
                    continue
        if up_to_date:
            self.toast(f"Up to date, version {current}")
        elif needed:
            self.toast(f"Update {latest} is available")
        return False

    def install_update(self) -> None:
        """Install only after the UI has asked the user to confirm."""
        from .. import updater
        if updater.install_kind() == "flatpak":
            self.toast(f"Run `flatpak update {updater.FLATPAK_ID}`")
            return

        def work():
            try:
                _ok, message = updater.update(dry_run=False)
            except Exception as exc:
                message = f"Update failed: {exc}"
            GLib.idle_add(self.toast, message)

        threading.Thread(target=work, daemon=True).start()


def run(cfg: Config, migrated: bool = False) -> int:
    Adw.init()
    app = FunnelApp(cfg, migrated=migrated)
    return app.run([])
