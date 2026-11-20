"""Single form rule editor for Funnel.

The editor keeps the full rule schema, including the presentation metadata,
and writes every change through the desk controller.
"""

from __future__ import annotations

import re
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from ..config import describe_rule, normalize_rule, safe_category_name  # noqa: E402
from ..engine import expand  # noqa: E402
from .widgets import (dim, eyebrow, listbox, scroll, split_exts,  # noqa: E402
                      unescape_simple)


class AdvancedDialog:
    """One form for destination, matching, filters and presentation."""

    def __init__(self, window, root: str, rule: dict | None = None):
        self.win = window
        self.controller = window.controller
        self.root = root
        self.original = dict(rule or {})
        self.old_name = self.original.get("name")
        self.exts = [str(e).lstrip(".")
                     for e in self.original.get("extensions", [])]
        self._building = True
        self._clearing_color = False

        self.dialog = Adw.Dialog(
            title="Edit rule" if self.old_name else "Advanced rule")
        self.dialog.set_content_width(760)
        self.dialog.set_content_height(760)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for setter in (box.set_margin_top, box.set_margin_start,
                       box.set_margin_end):
            setter(18)
        box.set_margin_bottom(8)

        box.append(eyebrow("Destination"))
        where = listbox()
        self.folder_row = Adw.EntryRow(title="Destination folder")
        self.folder_row.set_text(str(self.original.get("name", "")))
        self.folder_row.connect("changed", self._preview)
        browse = Gtk.Button(icon_name="folder-open-symbolic")
        browse.add_css_class("flat")
        browse.set_tooltip_text("Pick a folder")
        browse.set_valign(Gtk.Align.CENTER)
        browse.connect("clicked", self._browse)
        self.folder_row.add_suffix(browse)
        where.append(self.folder_row)
        box.append(where)

        box.append(eyebrow("Match"))
        match = listbox()
        self.ext_entry = Adw.EntryRow(title="Add an extension, then press Enter")
        self.ext_entry.connect("entry-activated", self._add_ext)
        self.ext_entry.connect("changed", self._ext_typed)
        match.append(self.ext_entry)
        literal = unescape_simple(self.original.get("match_name_regex"))
        self.contains_row = Adw.EntryRow(title="File name contains (optional)")
        self.contains_row.set_text(literal)
        self.contains_row.connect("changed", self._preview)
        self.regex_row = Adw.EntryRow(title="or matches regex (optional)")
        self.regex_row.set_text(
            "" if literal else str(self.original.get("match_name_regex", "")))
        self.regex_row.connect("changed", self._preview)
        self.exclude_row = Adw.EntryRow(
            title="Skip if name matches regex (optional)")
        self.exclude_row.set_text(
            str(self.original.get("exclude_name_regex", "")))
        self.exclude_row.connect("changed", self._preview)
        for row in (self.contains_row, self.regex_row, self.exclude_row):
            match.append(row)
        box.append(match)

        self.ext_chips = Gtk.FlowBox()
        self.ext_chips.set_selection_mode(Gtk.SelectionMode.NONE)
        self.ext_chips.set_max_children_per_line(14)
        self.ext_chips.set_row_spacing(6)
        self.ext_chips.set_column_spacing(6)
        box.append(self.ext_chips)

        mode_box = listbox()
        self.match_all_row = Adw.SwitchRow(
            title="Require every condition",
            subtitle="Off matches any condition, on matches all conditions.")
        self.match_all_row.set_active(
            self.original.get("match_mode") == "all"
            or bool(self.original.get("match_all")))
        self.match_all_row.connect("notify::active", self._preview)
        mode_box.append(self.match_all_row)
        box.append(mode_box)

        box.append(eyebrow("Fine-tune"))
        refine = listbox()
        self.min_row = Adw.EntryRow(title="Minimum size in MB")
        self.max_row = Adw.EntryRow(title="Maximum size in MB")
        self.days_row = Adw.EntryRow(
            title="Only files modified in the last N days")
        self.enabled_row = Adw.SwitchRow(title="Enabled")
        self.enabled_row.set_subtitle("Paused rules are skipped")
        self.enabled_row.set_active(self.original.get("enabled") is not False)
        for row, key in ((self.min_row, "min_size_mb"),
                         (self.max_row, "max_size_mb"),
                         (self.days_row, "modified_within_days")):
            value = self.original.get(key)
            if value:
                row.set_text(str(value))
            row.connect("changed", self._preview)
            refine.append(row)
        refine.append(self.enabled_row)

        self._color = self.original.get("color") or None
        self.color_row = Adw.ActionRow(
            title="Accent colour",
            subtitle="Optional, shows a coloured dot beside this rule")
        self.color_btn = Gtk.ColorDialogButton()
        self.color_btn.set_valign(Gtk.Align.CENTER)
        color_dialog = Gtk.ColorDialog()
        color_dialog.set_with_alpha(False)
        self.color_btn.set_dialog(color_dialog)
        if self._color:
            rgba = Gdk.RGBA()
            if rgba.parse(str(self._color)):
                self.color_btn.set_rgba(rgba)
        self.color_btn.connect("notify::rgba", self._on_color_picked)
        clear = Gtk.Button(icon_name="edit-clear-symbolic")
        clear.add_css_class("flat")
        clear.set_tooltip_text("No accent colour")
        clear.set_valign(Gtk.Align.CENTER)
        clear.connect("clicked", self._clear_color)
        self.color_row.add_suffix(self.color_btn)
        self.color_row.add_suffix(clear)
        refine.append(self.color_row)
        box.append(refine)

        self.preview = dim("")
        box.append(self.preview)

        outer.append(scroll(box))
        outer.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for setter in (footer.set_margin_top, footer.set_margin_bottom,
                       footer.set_margin_start, footer.set_margin_end):
            setter(14)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.close())
        save = Gtk.Button(label="Save rule" if self.old_name else "Add rule")
        save.add_css_class("suggested-action")
        save.connect("clicked", self._apply)
        footer.append(cancel)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        footer.append(spacer)
        footer.append(save)
        outer.append(footer)

        self.dialog.set_child(outer)
        self._rebuild_chips()
        self._building = False
        self._preview()

    def open(self):
        self.dialog.present(self.win)

    def close(self):
        self.dialog.close()

    # -- extension chips ---------------------------------------------------
    def _rebuild_chips(self):
        child = self.ext_chips.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.ext_chips.remove(child)
            child = nxt
        for ext in self.exts:
            chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            chip.add_css_class("pill")
            chip.append(Gtk.Label(label=f".{ext}"))
            remove = Gtk.Button(icon_name="window-close-symbolic")
            remove.add_css_class("flat")
            remove.add_css_class("circular")
            remove.set_tooltip_text(f"Remove .{ext}")
            remove.connect("clicked", self._remove_ext, ext)
            chip.append(remove)
            self.ext_chips.append(chip)

    def _add_ext(self, _entry):
        for ext in split_exts(self.ext_entry.get_text()):
            if ext not in self.exts:
                self.exts.append(ext)
        self.ext_entry.set_text("")
        self._rebuild_chips()
        self._preview()

    def _ext_typed(self, entry):
        text = entry.get_text()
        if any(ch in text for ch in ", \t"):
            for ext in split_exts(text):
                if ext not in self.exts:
                    self.exts.append(ext)
            entry.set_text("")
            self._rebuild_chips()
            self._preview()

    def _remove_ext(self, _button, ext):
        self.exts = [item for item in self.exts if item != ext]
        self._rebuild_chips()
        self._preview()

    # -- colour ------------------------------------------------------------
    @staticmethod
    def _hex(rgba) -> str:
        return "#{:02x}{:02x}{:02x}".format(
            round(rgba.red * 255), round(rgba.green * 255),
            round(rgba.blue * 255))

    def _on_color_picked(self, *_args):
        if self._clearing_color:
            return
        rgba = self.color_btn.get_rgba()
        self._color = self._hex(rgba) if rgba is not None else None
        self._preview()

    def _clear_color(self, _button):
        self._clearing_color = True
        try:
            self.color_btn.set_rgba(Gdk.RGBA())
        finally:
            self._clearing_color = False
        self._color = None
        self._preview()

    # -- folder picker -----------------------------------------------------
    def _browse(self, _button):
        if not hasattr(Gtk, "FileDialog"):
            self.win.toast("The folder picker needs a newer GTK")
            return
        chooser = Gtk.FileDialog()
        chooser.set_title("Choose a destination folder")
        if self.root:
            chooser.set_initial_folder(
                Gio.File.new_for_path(str(expand(self.root))))
        chooser.select_folder(self.win, None, self._browse_done)

    def _browse_done(self, chooser, result):
        try:
            gfile = chooser.select_folder_finish(result)
        except GLib.Error:
            return
        if gfile is None:
            return
        path = Path(gfile.get_path() or "")
        try:
            name = str(path.relative_to(expand(self.root)))
        except (ValueError, TypeError):
            name = path.name
        self.folder_row.set_text(safe_category_name(name) or path.name)

    # -- rule and preview --------------------------------------------------
    def _build_rule(self) -> dict:
        regex = self.regex_row.get_text().strip()
        contains = self.contains_row.get_text().strip()
        if not regex and contains:
            regex = re.escape(contains)
        rule: dict = {"name": self.folder_row.get_text().strip(),
                      "extensions": list(self.exts)}
        if regex:
            rule["match_name_regex"] = regex
        exclude = self.exclude_row.get_text().strip()
        if exclude:
            rule["exclude_name_regex"] = exclude
        for key, row in (("min_size_mb", self.min_row),
                         ("max_size_mb", self.max_row),
                         ("modified_within_days", self.days_row)):
            text = row.get_text().strip()
            if text:
                try:
                    rule[key] = float(text)
                except ValueError:
                    pass
        if not self.enabled_row.get_active():
            rule["enabled"] = False
        if self.match_all_row.get_active():
            rule["match_mode"] = "all"
        if self.original.get("icon"):
            rule["icon"] = self.original["icon"]
        if self._color:
            rule["color"] = self._color
        return rule

    def _preview(self, *_args):
        if self._building:
            return
        rule = normalize_rule(self._build_rule())
        if rule is None:
            self.preview.set_text("Give the rule a destination folder name.")
        else:
            self.preview.set_text("Reads as: " + describe_rule(rule))

    def _apply(self, _button):
        for kind, text in (("name", self.regex_row.get_text().strip()),
                           ("skip", self.exclude_row.get_text().strip())):
            if not text:
                continue
            try:
                re.compile(text)
            except re.error as exc:
                self.win.toast(f"Invalid {kind} regular expression: {exc}")
                return
        rule = self._build_rule()
        if not safe_category_name(rule.get("name") or ""):
            self.win.toast("Give the rule a destination folder name")
            return
        if not (rule.get("extensions") or rule.get("match_name_regex")):
            self.win.toast("Add at least one extension or a name match")
            return
        normalized = normalize_rule(rule)
        if normalized is None:
            self.win.toast("Give the rule a destination folder name")
            return
        if (self.old_name and self.old_name != normalized["name"]
                and self.old_name in
                {c.get("name") for c in self.controller.rules_for(self.root)}):
            self.controller.delete_rule(self.root, self.old_name)
        self.controller.save_rule(self.root, normalized)
        self.close()
