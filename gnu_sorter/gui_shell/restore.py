"""Preview-then-commit restore sheet for Funnel.

The plan is read-only and comes from the controller. Changing the conflict
strategy or the optional target rebuilds it. Overwrite needs a second
confirmation because the engine replaces files on disk.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .widgets import dim, eyebrow, human_size, listbox, scroll  # noqa: E402


class RestoreDialog:
    """Show what a filtered restore would do before changing any file."""

    _CONFLICTS = [("Rename on clash", "rename"),
                  ("Overwrite", "overwrite"),
                  ("Skip occupied", "skip")]
    _HINT = {
        "rename": "Occupied names get a “(1)” suffix. Nothing is overwritten.",
        "overwrite": "Occupied names are replaced by the files being "
                     "restored.",
        "skip": "Occupied names are left alone, and their entries stay in the "
                "journal.",
    }
    _ACTIONABLE = ("restore", "collision", "overwrite")

    def __init__(self, window, name: str | None = None,
                 rule: str | None = None, ext: str | None = None,
                 min_size: int | None = None, max_size: int | None = None):
        self.win = window
        self.controller = window.controller
        self.name = name
        self.rule = rule
        self.ext = ext
        self.min_size = min_size
        self.max_size = max_size
        self.on_conflict = "rename"
        self.target = None
        self.plan = []

        self.dialog = Adw.Dialog(title="Restore files")
        self.dialog.set_content_width(760)
        self.dialog.set_content_height(640)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for setter in (box.set_margin_top, box.set_margin_start,
                       box.set_margin_end):
            setter(18)
        box.set_margin_bottom(8)

        box.append(eyebrow("Restore preview"))
        self.scope_label = dim("")
        box.append(self.scope_label)
        self.hint_label = dim("")
        box.append(self.hint_label)
        self.warning = Adw.Banner(
            title="Overwrite replaces files already at the original path.",
            revealed=False)
        box.append(self.warning)

        settings = listbox()
        self.target_row = Adw.ActionRow(
            title="Restore into",
            subtitle="Original paths")
        choose = Gtk.Button(label="Choose folder")
        choose.set_valign(Gtk.Align.CENTER)
        choose.connect("clicked", self._choose_target)
        clear = Gtk.Button(icon_name="edit-clear-symbolic")
        clear.add_css_class("flat")
        clear.set_tooltip_text("Restore to the original paths")
        clear.set_valign(Gtk.Align.CENTER)
        clear.connect("clicked", self._clear_target)
        self.target_row.add_suffix(choose)
        self.target_row.add_suffix(clear)
        settings.append(self.target_row)

        self.conflict = Adw.ComboRow(
            title="On clash",
            subtitle="What to do when the original name is taken")
        self.conflict.set_model(Gtk.StringList.new(
            [label for label, _value in self._CONFLICTS]))
        self.conflict.set_selected(0)
        self.conflict.connect("notify::selected", self._on_conflict)
        settings.append(self.conflict)
        box.append(settings)

        self.listing = listbox()
        box.append(scroll(self.listing))
        outer.append(box)
        outer.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for setter in (footer.set_margin_top, footer.set_margin_bottom,
                       footer.set_margin_start, footer.set_margin_end):
            setter(14)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.close())
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        footer.append(spacer)
        self.restore = Gtk.Button(label="Restore")
        self.restore.add_css_class("suggested-action")
        self.restore.connect("clicked", self._restore)
        footer.append(cancel)
        footer.append(self.restore)
        outer.append(footer)

        self.dialog.set_child(outer)
        self._build_plan()

    def open(self):
        self.dialog.present(self.win)

    def close(self):
        self.dialog.close()

    # -- selectors and planning -------------------------------------------
    def _selectors(self) -> dict:
        selectors = {}
        for key in ("name", "rule", "ext", "min_size", "max_size", "target"):
            value = getattr(self, key, None)
            if value is not None:
                selectors[key] = value
        return selectors

    @staticmethod
    def _scope_text(name, rule, ext, min_size, max_size, target) -> str:
        parts = []
        if name:
            parts.append(f"name like “{name}”")
        if rule:
            parts.append(f"rule “{rule}”")
        if ext:
            parts.append(f"type {ext}")
        if min_size is not None:
            parts.append(f"at least {human_size(min_size)}")
        if max_size is not None:
            parts.append(f"at most {human_size(max_size)}")
        parts.append(f"into {target}" if target else "original paths")
        return ", ".join(parts)

    def _on_conflict(self, *_args):
        index = self.conflict.get_selected()
        if not (0 <= index < len(self._CONFLICTS)):
            return
        self.on_conflict = self._CONFLICTS[index][1]
        self._build_plan()

    def _choose_target(self, _button):
        if not hasattr(Gtk, "FileDialog"):
            self.win.toast("The folder picker needs a newer GTK")
            return
        chooser = Gtk.FileDialog()
        chooser.set_title("Choose where restored files go")
        chooser.select_folder(self.win, None, self._choose_target_done)

    def _choose_target_done(self, chooser, result):
        try:
            gfile = chooser.select_folder_finish(result)
        except GLib.Error:
            return
        if gfile is None:
            return
        self.target = gfile.get_path()
        self.target_row.set_subtitle(self.target or "Original paths")
        self._build_plan()

    def _clear_target(self, _button):
        self.target = None
        self.target_row.set_subtitle("Original paths")
        self._build_plan()

    def _build_plan(self):
        selectors = self._selectors()
        try:
            self.plan = self.controller.restore_plan(
                on_conflict=self.on_conflict, **selectors)
        except TypeError:
            # A controller that treats conflict as commit-only still returns a
            # usable read-only plan; the strategy is passed again at commit.
            self.plan = self.controller.restore_plan(**selectors)
        actionable = [item for item in self.plan
                      if item.get("status") in self._ACTIONABLE]

        scope = self._scope_text(self.name, self.rule, self.ext,
                                 self.min_size, self.max_size, self.target)
        self.scope_label.set_text(
            f"{len(actionable)} file(s) can be restored, {scope}")
        self.hint_label.set_text(self._HINT[self.on_conflict])
        self.warning.set_revealed(self.on_conflict == "overwrite")
        self.restore.set_label(f"Restore {len(actionable)} file(s)")
        self.restore.set_sensitive(bool(actionable))

        self.listing.remove_all()
        if not self.plan:
            self.listing.append(Adw.ActionRow(
                title="Nothing can be restored with these filters."))
            return
        for item in self.plan:
            src = item.get("src")
            dest = item.get("dest")
            status = item.get("status")
            note = self._note(item)
            title = src.name if src is not None else "Unknown file"
            row = Adw.ActionRow(title=title, subtitle=note)
            if dest is not None:
                row.set_tooltip_text(str(dest))
            size = item.get("size")
            if isinstance(size, int):
                size_label = Gtk.Label(label=human_size(size))
                size_label.add_css_class("funnel-eyebrow")
                row.add_suffix(size_label)
            self.listing.append(row)

    @staticmethod
    def _note(item) -> str:
        status = item.get("status")
        dest = item.get("dest")
        base = item.get("base")
        target = item.get("target")
        source = dest.parent.name if dest is not None else "folder"
        if status == "missing":
            return f"missing from {source}/"
        if status == "already":
            return "already back in place"
        if status == "skip":
            name = base.name if base is not None else "file"
            return f"kept, {name} is occupied"
        parts = [f"from {source}/"]
        if status == "collision":
            name = target.name if target is not None else "a new name"
            parts.append(f"name taken, restore as {name}")
        elif status == "overwrite":
            name = base.name if base is not None else "the existing file"
            parts.append(f"replaces {name}")
        if item.get("changed"):
            parts.append("size changed")
        return ", ".join(parts)

    # -- commit ------------------------------------------------------------
    def _restore(self, _button):
        if self.on_conflict != "overwrite":
            self._commit()
            return
        alert = Adw.AlertDialog(
            heading="Overwrite files?",
            body="The files already at the original paths will be replaced. "
                 "Continue with the restore?")
        alert.add_response("cancel", "Cancel")
        alert.add_response("overwrite", "Overwrite")
        alert.set_default_response("cancel")
        alert.set_close_response("cancel")
        alert.connect("response", self._confirm_overwrite)
        alert.present(self.win)

    def _confirm_overwrite(self, alert, response):
        alert.close()
        if response == "overwrite":
            self._commit()

    def _commit(self):
        count = self.controller.restore_commit(
            self.on_conflict, **self._selectors())
        self.close()
        self.win.toast(f"Restored {count} file(s)")
        navigate = getattr(self.win, "navigate", None)
        if callable(navigate):
            navigate("activity")
