"""Global preferences for Funnel.

Watcher controls live in the Inputs section. This dialog keeps only settings
that apply to the whole application. Every edit goes through the controller.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .. import APP_NAME, CREATOR, __version__  # noqa: E402
from .theme import ACCENT_CHOICES, apply_color_scheme  # noqa: E402

_SCHEMES = ["default", "light", "dark"]
_SCHEME_LABELS = ["System", "Light", "Dark"]


class PreferencesDialog(Adw.PreferencesDialog):
    """The application preferences dialog."""

    def __init__(self, window):
        super().__init__()
        self.win = window
        self.controller = window.controller
        self.set_title("Preferences")
        values = self._values()
        page = Adw.PreferencesPage()

        about = Adw.PreferencesGroup(title="About")
        version = Adw.ActionRow(
            title=APP_NAME, subtitle=f"Version {__version__}, by {CREATOR}")
        details = Gtk.Button(label="Details", valign=Gtk.Align.CENTER)
        details.connect("clicked", lambda _b: self.win.show_about())
        version.add_suffix(details)
        about.add(version)
        page.add(about)

        appearance = Adw.PreferencesGroup(title="Appearance")
        scheme = Adw.ComboRow(
            title="Colour scheme",
            subtitle="Follow the system, or force light or dark")
        scheme.set_model(Gtk.StringList.new(_SCHEME_LABELS))
        scheme_name = str(values.get("color_scheme", "default")).lower()
        scheme.set_selected(_SCHEMES.index(scheme_name)
                            if scheme_name in _SCHEMES else 0)
        scheme.connect("notify::selected", self._on_scheme)
        appearance.add(scheme)

        accent_keys = [key for key, _label in ACCENT_CHOICES]
        accent = Adw.ComboRow(
            title="Accent colour",
            subtitle="Follow the desktop, or pick a Funnel colour")
        accent.set_model(Gtk.StringList.new(
            [label for _key, label in ACCENT_CHOICES]))
        current = str(values.get("accent", "green")).lower()
        accent.set_selected(accent_keys.index(current)
                            if current in accent_keys else 1)
        accent.connect("notify::selected", self._on_accent)
        appearance.add(accent)
        page.add(appearance)

        behaviour = Adw.PreferencesGroup(title="Behaviour")
        stable = Adw.SpinRow.new_with_range(0, 30, 1)
        stable.set_title("Stability window")
        stable.set_subtitle(
            "Seconds a file must stop changing before it is moved")
        stable.set_value(float(values.get("min_stable_seconds", 3)))
        stable.connect("notify::value", self._on_number,
                       "min_stable_seconds", float)
        behaviour.add(stable)

        check = Adw.SpinRow.new_with_range(0.0, 10.0, 0.5)
        check.set_title("Stability check interval")
        check.set_subtitle("Seconds between size checks while a file settles")
        check.set_value(float(values.get("stability_interval", 0.5)))
        check.connect("notify::value", self._on_number,
                      "stability_interval", float)
        behaviour.add(check)

        interval = Adw.SpinRow.new_with_range(1, 60, 1)
        interval.set_title("Poll interval")
        interval.set_subtitle(
            "Seconds between checks when inotify is unavailable")
        interval.set_value(float(values.get("watch_interval", 2)))
        interval.connect("notify::value", self._on_number,
                         "watch_interval", float)
        behaviour.add(interval)
        page.add(behaviour)

        extras = Adw.PreferencesGroup(title="Extras")
        for key, title, subtitle, default in (
                ("notifications", "Notifications",
                 "Desktop notification after each sort", True),
                ("dry_run_default", "Dry-run by default",
                 "Preview sorting without moving anything", False),
                ("cautious_downloads", "Extra-cautious download detection",
                 "Wait for downloads to fully settle before sorting them",
                 True)):
            row = Adw.SwitchRow(title=title, subtitle=subtitle,
                                active=bool(values.get(key, default)))
            row.connect("notify::active", self._on_switch, key)
            extras.add(row)
        page.add(extras)

        updates = Adw.PreferencesGroup(
            title="Updates",
            description="Funnel only offers builds from its own channel "
                        "(indev, alpha, beta, rc or release).")
        automatic = Adw.SwitchRow(
            title="Check for updates automatically",
            subtitle="Look for a newer build shortly after launch",
            active=bool(values.get("check_for_updates", True)))
        automatic.connect("notify::active", self._on_switch,
                          "check_for_updates")
        updates.add(automatic)
        try:
            from .. import updater
            if updater.install_kind() == "flatpak":
                updates.add(Adw.ActionRow(
                    title="Installed as a Flatpak",
                    subtitle="Updates arrive through `flatpak update "
                             f"{updater.FLATPAK_ID}`"))
        except Exception:
            pass
        page.add(updates)

        history = Adw.PreferencesGroup(
            title="History",
            description="The move journal trims itself once it grows past "
                        "these limits, and keeps a full copy as "
                        "history.log.bak.")
        keep = Adw.SpinRow.new_with_range(100, 100000, 100)
        keep.set_title("Entries to keep")
        keep.set_subtitle(
            "Most recent moves kept when the journal is trimmed")
        keep.set_value(float(values.get("history_max_entries", 2500)))
        keep.connect("notify::value", self._on_number,
                     "history_max_entries", int)
        history.add(keep)

        size = Adw.SpinRow.new_with_range(1, 500, 1)
        size.set_title("Trim after (MB)")
        size.set_subtitle(
            "Maximum journal size before old entries are dropped")
        size.set_value(float(values.get("history_max_bytes", 1_000_000))
                       / 1_000_000)
        size.connect("notify::value", self._on_number, "history_max_bytes",
                     lambda value: int(value * 1_000_000))
        history.add(size)
        page.add(history)
        self.add(page)

    def _values(self) -> dict:
        return dict(self.controller.settings() or {})

    def _save(self, key, value, theme=False):
        values = self._values()
        values[key] = value
        self.controller.save_settings(values)
        if theme:
            apply_color_scheme(self.controller.cfg)

    def _on_number(self, row, _param, key, caster):
        self._save(key, caster(row.get_value()))

    def _on_switch(self, row, _param, key):
        self._save(key, bool(row.get_active()))

    def _on_scheme(self, row, _param):
        index = row.get_selected()
        if 0 <= index < len(_SCHEMES):
            self._save("color_scheme", _SCHEMES[index], theme=True)

    def _on_accent(self, row, _param):
        keys = [key for key, _label in ACCENT_CHOICES]
        index = row.get_selected()
        if 0 <= index < len(keys):
            self._save("accent", keys[index], theme=True)

    def close(self):
        self.dialog.close() if hasattr(self, "dialog") else super().close()


def show_preferences(window):
    """Store the dialog on the window, then present it."""
    dialog = getattr(window, "_prefs", None)
    if dialog is None:
        dialog = PreferencesDialog(window)
        window._prefs = dialog
    dialog.present(window)
    return dialog


open_preferences = show_preferences
