"""Four step rule draft for Funnel.

The draft keeps every rule field from the old six step flow, but drops the
recipe cards, the priority page and the animated proof. Steps are Catch,
Destination, Conditions and Proof. The public names pinned by the GUI smoke
script stay in place as aliases into the shorter flow.
"""

from __future__ import annotations

import re
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from ..config import (describe_rule, name_matcher_regex, normalize_rule,  # noqa: E402
                      safe_category_name)
from ..engine import Sorter, expand  # noqa: E402
from .widgets import dim, eyebrow, index_label, listbox, scroll  # noqa: E402

# A starting point per kind of file. Each entry pre-fills an extension set;
# every field remains editable after the starting point is picked. The tuple
# shape and order are kept for the external smoke script.
WIZARD_RECIPES = [
    ("images", "Images", "Photos, art and pictures",
     "image-x-generic-symbolic",
     ["jpg", "jpeg", "png", "gif", "webp", "heic", "bmp", "tiff", "svg", "raw"]),
    ("video", "Video", "Movies and clips",
     "video-x-generic-symbolic",
     ["mp4", "mkv", "mov", "avi", "webm", "wmv", "flv", "m4v", "mpg"]),
    ("audio", "Audio", "Music and sound",
     "audio-x-generic-symbolic",
     ["mp3", "wav", "flac", "ogg", "m4a", "aac", "wma", "opus"]),
    ("documents", "Documents", "Docs, PDFs and notes",
     "x-office-document-symbolic",
     ["pdf", "doc", "docx", "txt", "rtf", "odt", "md", "tex",
      "xls", "xlsx", "csv", "ppt", "pptx"]),
    ("archives", "Archives", "Zips and bundles",
     "package-x-generic-symbolic",
     ["zip", "tar", "gz", "bz2", "xz", "7z", "rar", "tgz"]),
    ("code", "Code", "Source and scripts",
     "text-x-generic-symbolic",
     ["py", "js", "ts", "java", "c", "cpp", "h", "cs", "go", "rs", "rb",
      "sh", "ps1", "html", "css", "json", "yaml", "yml", "sql"]),
]

WIZARD_STEPS = ["Catch", "Destination", "Conditions", "Proof"]


class RuleWizard:
    """Build one rule over four pages and save it through the controller."""

    def __init__(self, window, root: str):
        self.win = window
        self.controller = window.controller
        self.root = root
        self._files = None
        rules = self.controller.rules_for(self.root)
        self.existing = [c for c in rules if c.get("name")]
        self.destinations = [c.get("name", "") for c in self.existing][:12]
        self._suggest_checks = []
        self.state = {
            "recipe": None,
            "exts": [],
            "name_mode": "none",
            "name_text": "",
            "folder": "",
            "min_mb": None,
            "max_mb": None,
            "days": None,
            "exclude": "",
            "enabled": True,
            "match_all": False,
            "color": None,
        }
        self._clearing_color = False

        self.dialog = Adw.Dialog(title="Add a rule")
        self.dialog.set_content_width(760)
        self.dialog.set_content_height(760)
        self.view = Adw.NavigationView()
        self.dialog.set_child(self.view)
        self._build()

    def open(self):
        if self.view.get_visible_page() is None:
            self.view.push(self.page_catch)
        else:
            self._refresh_catch()
        self.dialog.present(self.win)

    def open_suggest(self):
        """Open the suggest sheet directly from the Plan overflow action."""
        if self.view.get_visible_page() is None:
            self.view.push(self.page_catch)
        self.dialog.present(self.win)
        self._magic_suggest()

    def close(self):
        self.dialog.close()

    # -- folder scan and rule building -----------------------------------
    def _scan(self) -> list[str]:
        if self._files is not None:
            return self._files
        files: list[str] = []
        if self.root:
            try:
                for path in sorted(expand(self.root).iterdir()):
                    if path.is_file() and not path.name.startswith("."):
                        files.append(path.name)
            except OSError:
                pass
        self._files = files
        return files

    def _ext_freq(self) -> dict[str, int]:
        freq: dict[str, int] = {}
        for name in self._scan():
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext:
                freq[ext] = freq.get(ext, 0) + 1
        return freq

    def _known_exts(self) -> list[str]:
        present = set(self._ext_freq())
        library = {e for _k, _t, _s, _i, exts in WIZARD_RECIPES for e in exts}
        return sorted(present | library)

    def _sorter(self):
        sorter = getattr(self.controller, "sorter", None)
        if sorter is not None:
            return sorter
        cfg = getattr(self.controller, "cfg", None)
        return Sorter(cfg) if cfg is not None else None

    def _classify(self, names, rules=None) -> dict[str, str]:
        sorter = self._sorter()
        if sorter is None:
            return {}
        try:
            if rules is None:
                return sorter.classify(names)
            return sorter.classify(names, rules=rules)
        except Exception:
            return {}

    def _name_pattern(self) -> str | None:
        return name_matcher_regex(self.state["name_mode"],
                                  self.state["name_text"])

    def _rule_dict(self) -> dict:
        state = self.state
        name = safe_category_name(state["folder"]) or state["folder"]
        rule: dict = {"name": name}
        if state["exts"]:
            rule["extensions"] = list(state["exts"])
        pattern = self._name_pattern()
        if pattern:
            rule["match_name_regex"] = pattern
        if not state["enabled"]:
            rule["enabled"] = False
        if state["exclude"]:
            rule["exclude_name_regex"] = state["exclude"]
        if state["min_mb"] is not None:
            rule["min_size_mb"] = state["min_mb"]
        if state["max_mb"] is not None:
            rule["max_size_mb"] = state["max_mb"]
        if state["days"] is not None:
            rule["modified_within_days"] = state["days"]
        if state["match_all"]:
            rule["match_mode"] = "all"
        if state.get("color"):
            rule["color"] = state["color"]
        return rule

    def _regex_ok(self) -> bool:
        patterns = [self.state.get("exclude") or ""]
        if self.state["name_mode"] == "regex":
            patterns.append(self._name_pattern() or "")
        for pattern in patterns:
            if not pattern:
                continue
            try:
                re.compile(pattern)
            except re.error:
                return False
        return True

    @staticmethod
    def _has_matcher(rule: dict) -> bool:
        return bool(rule.get("extensions") or rule.get("match_name_regex")
                    or rule.get("match_all"))

    def _counts(self):
        names = self._scan()
        if not names:
            return (0, 0)
        rule = self._rule_dict()
        folder = rule.get("name") or "Wizard"
        rule["name"] = folder
        result = self._classify(names, rules=[rule])
        return (sum(1 for name in names if result.get(name) == folder),
                len(names))

    # -- page scaffolding -------------------------------------------------
    def _page(self, step: int, content, left, right, can_pop=True):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_vexpand(True)
        clamp = Adw.Clamp()
        clamp.set_maximum_size(640)
        clamp.set_tightening_threshold(520)
        clamp.set_child(content)
        box.append(scroll(clamp))
        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for setter in (footer.set_margin_start, footer.set_margin_end,
                       footer.set_margin_top, footer.set_margin_bottom):
            setter(18)
        for widget in left:
            footer.append(widget)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        footer.append(spacer)
        for widget in right:
            footer.append(widget)
        box.append(footer)
        title = f"{step:02d} / 04   {WIZARD_STEPS[step - 1]}"
        page = Adw.NavigationPage(title=title, child=box)
        page.set_can_pop(can_pop)
        return page

    @staticmethod
    def _title(text, style="title-3"):
        label = Gtk.Label(label=text, xalign=0, wrap=True)
        label.add_css_class(style)
        return label

    def _step_header(self, index, text):
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.append(index_label(f"{index:02d}"))
        header.append(self._title(text))
        return header

    @staticmethod
    def _field(label, widget):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(eyebrow(label))
        box.append(widget)
        return box

    @staticmethod
    def _pad(widget):
        for setter in (widget.set_margin_top, widget.set_margin_bottom,
                       widget.set_margin_start, widget.set_margin_end):
            setter(18)
        return widget

    @staticmethod
    def _clear_box(box):
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt

    def _root_name(self):
        if not self.root:
            return "No folder selected"
        return expand(self.root).name or str(self.root)

    def _page_in_stack(self, page) -> bool:
        stack = self.view.get_navigation_stack()
        for i in range(stack.get_n_items()):
            if stack.get_item(i) is page:
                return True
        return False

    def _show_page(self, page, refresh=None):
        if refresh is not None:
            refresh()
        if self.view.get_visible_page() is page:
            return
        if self._page_in_stack(page):
            self.view.pop_to_page(page)
        else:
            self.view.push(page)

    def _back_button(self):
        button = Gtk.Button(label="Back")
        button.connect("clicked", lambda _b: self.view.pop())
        return button

    def _next_button(self, handler):
        button = Gtk.Button(label="Next")
        button.add_css_class("suggested-action")
        button.connect("clicked", handler)
        return button

    def _build(self):
        self._build_catch()
        self._build_destination()
        self._build_conditions()
        self._build_proof_page()
        self._build_suggest()

    # ---- step 1: Catch ---------------------------------------------------
    def _build_catch(self):
        content = self._pad(Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                    spacing=12))
        content.append(self._step_header(1, WIZARD_STEPS[0]))
        content.append(dim(f"Starting in {self._root_name()}. "
                           "Pick a starting point or add files by hand."))

        content.append(eyebrow("Starting point"))
        starts = Gtk.FlowBox()
        starts.set_selection_mode(Gtk.SelectionMode.NONE)
        starts.set_max_children_per_line(3)
        starts.set_min_children_per_line(2)
        starts.set_row_spacing(6)
        starts.set_column_spacing(6)
        for key, title, subtitle, icon, exts in WIZARD_RECIPES:
            starts.append(self._start_button(key, title, subtitle, icon, exts))
        starts.append(self._start_button("custom", "Custom",
                                         "Start with an empty extension set",
                                         "preferences-other-symbolic", []))
        content.append(starts)

        content.append(eyebrow("Add extension"))
        self.ext_drop = Gtk.DropDown.new_from_strings(
            ["." + ext for ext in self._known_exts()])
        self.ext_drop.set_enable_search(True)
        self.ext_drop.set_hexpand(True)
        self.ext_drop.connect("notify::selected", self._on_ext_picked)
        content.append(self.ext_drop)

        self.chip_flow = Gtk.FlowBox()
        self.chip_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.chip_flow.set_max_children_per_line(12)
        self.chip_flow.set_row_spacing(6)
        self.chip_flow.set_column_spacing(6)
        content.append(self.chip_flow)

        content.append(eyebrow("Files already here"))
        content.append(dim("Pick a file to catch everything with that "
                           "extension."))
        self.file_flow = Gtk.FlowBox()
        self.file_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.file_flow.set_max_children_per_line(2)
        self.file_flow.set_row_spacing(4)
        self.file_flow.set_column_spacing(4)
        content.append(self.file_flow)

        content.append(eyebrow("Match the file name"))
        self.name_toggles = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.name_toggles.add_css_class("linked")
        self.name_buttons = {}
        for key, label in (("contains", "contains"), ("starts", "starts with"),
                           ("ends", "ends with"), ("regex", "regex")):
            button = Gtk.ToggleButton(label=label)
            button.connect("toggled", self._on_name_toggle, key)
            self.name_buttons[key] = button
            self.name_toggles.append(button)
        content.append(self.name_toggles)
        self.name_entry = Gtk.Entry(placeholder_text="screenshot")
        self.name_entry.connect("changed", self._on_name_changed)
        content.append(self.name_entry)

        self.count_label = dim("")
        content.append(self.count_label)

        actions = listbox()
        suggest = Adw.ActionRow(title="Suggest rules for me",
                                subtitle="Learn from the files already here")
        suggest.add_prefix(Gtk.Image.new_from_icon_name("starred-symbolic"))
        suggest.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        suggest.set_activatable(True)
        suggest.connect("activated", lambda _r: self._magic_suggest())
        actions.append(suggest)
        content.append(actions)

        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.close())
        self.page_catch = self._page(1, content, [cancel],
                                     [self._next_button(
                                         lambda _b: self._enter_destination())],
                                     can_pop=False)

    def _start_button(self, key, title, subtitle, icon, exts):
        button = Gtk.Button()
        button.add_css_class("funnel-card")
        button.set_size_request(148, 68)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        image = Gtk.Image.new_from_icon_name(icon)
        image.set_pixel_size(20)
        image.add_css_class("dim-label")
        box.append(image)
        label = Gtk.Label(label=title, xalign=0)
        label.add_css_class("heading")
        box.append(label)
        box.append(dim(subtitle))
        button.set_child(box)
        button.connect("clicked", lambda _b: self._start_recipe(key, exts))
        return button

    def _start_recipe(self, key, exts):
        self.state["recipe"] = key
        self.state["exts"] = ([] if key == "custom"
                              else [str(e).lstrip(".") for e in exts])
        self._show_page(self.page_catch, refresh=self._refresh_catch)

    def _on_name_toggle(self, button, key):
        if button.get_active():
            self.state["name_mode"] = key
            for other_key, other in self.name_buttons.items():
                if other_key != key and other.get_active():
                    other.set_active(False)
        elif self.state["name_mode"] == key:
            self.state["name_mode"] = "none"
        self._refresh_count()

    def _on_name_changed(self, entry):
        self.state["name_text"] = entry.get_text()
        self._refresh_count()

    def _on_ext_picked(self, drop, _param):
        index = drop.get_selected()
        items = self._known_exts()
        if index == Gtk.INVALID_LIST_POSITION or not (0 <= index < len(items)):
            return
        ext = items[index]
        if ext not in self.state["exts"]:
            self.state["exts"].append(ext)
            GLib.idle_add(self._refresh_catch)

    def _on_file_toggled(self, button, ext):
        if not ext:
            return
        if button.get_active() and ext not in self.state["exts"]:
            self.state["exts"].append(ext)
        elif not button.get_active() and ext in self.state["exts"]:
            self.state["exts"].remove(ext)
        GLib.idle_add(self._refresh_catch)

    def _remove_ext(self, _button, ext):
        if ext in self.state["exts"]:
            self.state["exts"].remove(ext)
        GLib.idle_add(self._refresh_catch)

    def _refresh_catch(self):
        self._rebuild_chips()
        self._refresh_files()
        self._refresh_count()
        return False

    def _rebuild_chips(self):
        self._clear_box(self.chip_flow)
        if not self.state["exts"]:
            self.chip_flow.append(dim("No extensions yet. Add some above."))
            return
        for ext in self.state["exts"]:
            button = Gtk.Button(label="." + ext)
            button.add_css_class("pill")
            button.set_tooltip_text("Remove ." + ext)
            button.connect("clicked", self._remove_ext, ext)
            self.chip_flow.append(button)

    def _refresh_files(self):
        self._clear_box(self.file_flow)
        names = self._scan()
        if not names:
            self.file_flow.append(dim("No files here to preview, but this "
                                      "rule is ready."))
            return
        freq = self._ext_freq()

        def rank(name):
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            return (-freq.get(ext, 0), name.lower())

        for name in sorted(names, key=rank)[:48]:
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            button = Gtk.ToggleButton()
            button.add_css_class("flat")
            button.set_active(bool(ext) and ext in self.state["exts"])
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            box.append(Gtk.Image.new_from_icon_name("text-x-generic-symbolic"))
            label = Gtk.Label(label=name)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(28)
            box.append(label)
            button.set_child(box)
            button.connect("toggled", self._on_file_toggled, ext)
            self.file_flow.append(button)

    def _refresh_count(self):
        caught, total = self._counts()
        if not self._regex_ok():
            self.count_label.set_text("That regex isn't valid yet. Check the "
                                      "name pattern.")
        elif not self._has_matcher(self._rule_dict()):
            self.count_label.set_text("Nothing selected yet. Pick extensions "
                                      "or a name match.")
        elif total == 0:
            self.count_label.set_text("No files here to preview, but this rule "
                                      "is ready.")
        else:
            self.count_label.set_text(
                f"This catches {caught} of {total} files in this folder.")

    # ---- step 2: Destination ---------------------------------------------
    def _build_destination(self):
        content = self._pad(Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                    spacing=12))
        content.append(self._step_header(2, WIZARD_STEPS[1]))
        content.append(dim("A new folder is created inside the watched folder "
                           "the first time something matches."))

        if self.destinations:
            suggestions = Gtk.FlowBox()
            suggestions.set_selection_mode(Gtk.SelectionMode.NONE)
            suggestions.set_max_children_per_line(6)
            suggestions.set_row_spacing(6)
            suggestions.set_column_spacing(6)
            for name in self.destinations:
                button = Gtk.Button(label=f"{name}/")
                button.add_css_class("pill")
                button.connect("clicked", self._pick_dest, name)
                suggestions.append(button)
            content.append(suggestions)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.folder_entry = Gtk.Entry(placeholder_text="Photos")
        self.folder_entry.set_hexpand(True)
        self.folder_entry.connect("changed", self._on_folder_changed)
        browse = Gtk.Button(label="Browse")
        browse.connect("clicked", self._browse)
        row.append(self.folder_entry)
        row.append(browse)
        content.append(self._field("Destination folder", row))
        self.folder_hint = dim("")
        content.append(self.folder_hint)
        content.append(eyebrow("Where things live"))
        self.tree_label = dim("")
        content.append(self.tree_label)

        self.page_destination = self._page(
            2, content, [self._back_button()],
            [self._next_button(lambda _b: self._enter_conditions())])

    def _pick_dest(self, _button, name):
        self.folder_entry.set_text(name)

    def _on_folder_changed(self, entry):
        text = entry.get_text().strip()
        self.state["folder"] = text
        safe = safe_category_name(text) if text else None
        if not text:
            self.folder_hint.set_text("Type a name, pick a suggestion, or "
                                      "Browse.")
        elif safe is None:
            self.folder_hint.set_text("That name can't be used for a folder. "
                                      "Try letters, numbers and dashes.")
        else:
            self.folder_hint.set_text(
                f"Files will go to {self._root_name()}/{safe}/")
        self._refresh_tree()

    def _refresh_tree(self):
        lines = [f"{self._root_name()}/"]
        folder = self.state["folder"]
        shown = False
        for name in self.destinations:
            marker = "  new" if name == folder else ""
            shown = shown or name == folder
            lines.append(f"    {name}/{marker}")
        if folder and not shown:
            lines.append(f"    {folder}/  new")
        self.tree_label.set_text("\n".join(lines))

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
        self.folder_entry.set_text(safe_category_name(name) or path.name)

    def _suggest_folder(self):
        exts = self.state["exts"]
        if exts:
            for rule in self.existing:
                have = [str(e).lstrip(".") for e in rule.get("extensions", [])]
                if exts[0] in have:
                    return rule.get("name", exts[0])
            for _key, title, _sub, _icon, recipe_exts in WIZARD_RECIPES:
                if exts[0] in recipe_exts:
                    return title
            return exts[0].capitalize()
        text = self.state["name_text"].strip()
        if re.search("screenshot|capture|scree", text, re.IGNORECASE):
            return "Screenshots"
        return text[:1].upper() + text[1:] if text else ""

    def _enter_destination(self):
        if not self.state["folder"]:
            self.folder_entry.set_text(self._suggest_folder())
        self._on_folder_changed(self.folder_entry)
        self._show_page(self.page_destination)

    # ---- step 3: Conditions ---------------------------------------------
    def _build_conditions(self):
        content = self._pad(Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                    spacing=12))
        content.append(self._step_header(3, WIZARD_STEPS[2]))
        content.append(dim("Every control here is optional."))

        rows = listbox()
        self.min_row = Adw.SpinRow.new_with_range(0, 1_000_000, 1)
        self.min_row.set_title("At least this many MB")
        self.min_row.set_digits(0)
        self.min_row.connect("notify::value", self._on_bounds)
        rows.append(self.min_row)

        self.max_row = Adw.SpinRow.new_with_range(0, 1_000_000, 1)
        self.max_row.set_title("At most this many MB")
        self.max_row.set_digits(0)
        self.max_row.connect("notify::value", self._on_bounds)
        rows.append(self.max_row)

        self.days_row = Adw.SpinRow.new_with_range(0, 3650, 1)
        self.days_row.set_title("Only files newer than (days)")
        self.days_row.set_digits(0)
        self.days_row.connect("notify::value", self._on_bounds)
        rows.append(self.days_row)

        self.exclude_row = Adw.EntryRow(
            title="Never match names matching (regex, optional)")
        self.exclude_row.connect("changed", self._on_exclude)
        rows.append(self.exclude_row)

        self.enabled_row = Adw.SwitchRow(title="Rule enabled")
        self.enabled_row.set_active(True)
        self.enabled_row.connect("notify::active", self._on_enabled)
        rows.append(self.enabled_row)

        self.match_all_row = Adw.SwitchRow(
            title="Require every condition",
            subtitle="Off matches any condition, on matches all conditions.")
        self.match_all_row.connect("notify::active", self._on_match_all)
        rows.append(self.match_all_row)

        self.color_row = Adw.ActionRow(
            title="Accent colour",
            subtitle="Optional, shows a coloured dot beside this rule")
        self.color_btn = Gtk.ColorDialogButton()
        self.color_btn.set_valign(Gtk.Align.CENTER)
        color_dialog = Gtk.ColorDialog()
        color_dialog.set_with_alpha(False)
        self.color_btn.set_dialog(color_dialog)
        self.color_btn.connect("notify::rgba", self._on_color_picked)
        clear = Gtk.Button(icon_name="edit-clear-symbolic")
        clear.add_css_class("flat")
        clear.set_tooltip_text("No accent colour")
        clear.set_valign(Gtk.Align.CENTER)
        clear.connect("clicked", self._clear_color)
        self.color_row.add_suffix(self.color_btn)
        self.color_row.add_suffix(clear)
        rows.append(self.color_row)
        content.append(rows)

        self.page_conditions = self._page(
            3, content, [self._back_button()],
            [self._next_button(lambda _b: self._enter_proof())])

    def _on_bounds(self, *_args):
        self.state["min_mb"] = self.min_row.get_value() or None
        self.state["max_mb"] = self.max_row.get_value() or None
        self.state["days"] = int(self.days_row.get_value()) or None

    def _on_exclude(self, row):
        self.state["exclude"] = row.get_text().strip()

    def _on_enabled(self, row, _param):
        self.state["enabled"] = row.get_active()

    def _on_match_all(self, row, _param):
        self.state["match_all"] = row.get_active()

    @staticmethod
    def _hex(rgba) -> str:
        return "#{:02x}{:02x}{:02x}".format(
            round(rgba.red * 255), round(rgba.green * 255),
            round(rgba.blue * 255))

    def _on_color_picked(self, *_args):
        if self._clearing_color:
            return
        rgba = self.color_btn.get_rgba()
        self.state["color"] = self._hex(rgba) if rgba is not None else None

    def _clear_color(self, _button):
        self._clearing_color = True
        try:
            self.color_btn.set_rgba(Gdk.RGBA())
        finally:
            self._clearing_color = False
        self.state["color"] = None

    def _enter_conditions(self):
        self._show_page(self.page_conditions)

    # ---- step 4: Proof ---------------------------------------------------
    def _build_proof_page(self):
        content = self._pad(Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                    spacing=12))
        content.append(self._step_header(4, WIZARD_STEPS[3]))
        card = listbox()
        self.review_name = Adw.ActionRow(title="Destination")
        self.review_sentence = Adw.ActionRow(title="Reads as")
        self.review_sentence.set_subtitle_lines(3)
        self.review_position = Adw.ActionRow(title="Position")
        for row in (self.review_name, self.review_sentence,
                    self.review_position):
            card.append(row)
        content.append(card)
        self.proof_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                    spacing=6)
        content.append(self.proof_holder)

        again = Gtk.Button(label="Save another")
        again.connect("clicked", lambda _b: self._apply(True))
        save = Gtk.Button(label="Save rule")
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda _b: self._apply(False))
        self.page_proof = self._page(4, content, [self._back_button()],
                                     [again, save])

    def _refresh_proof(self):
        rule = self._rule_dict()
        folder = rule.get("name") or "(choose a folder)"
        self.review_name.set_subtitle(f"{folder}/")
        normalized = normalize_rule(rule) or rule
        self.review_sentence.set_subtitle(
            describe_rule(normalized) or "Nothing yet")
        total = len(self.existing) + 1
        self.review_position.set_subtitle(
            f"position {total} of {total}, first match wins")

        self._clear_box(self.proof_holder)
        names = self._scan()
        if not names:
            self.proof_holder.append(dim("No files to show yet. The rule is "
                                         "ready for future files."))
            return
        rule["name"] = folder
        result = self._classify(names, rules=[rule])
        matched = [name for name in names if result.get(name) == folder]
        others = [name for name in names if result.get(name) != folder]

        proof = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        proof.add_css_class("funnel-proof")
        bar = Gtk.ProgressBar()
        bar.set_fraction(len(matched) / len(names))
        proof.append(bar)
        proof.append(dim(f"{len(matched)} of {len(names)} files in this folder "
                         "would sort here."))
        columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        columns.append(self._example_column(f"Would go to {folder}/",
                                            matched))
        columns.append(self._example_column("Would stay put", others))
        proof.append(columns)
        self.proof_holder.append(proof)

    def _example_column(self, caption, names):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        box.set_hexpand(True)
        box.append(eyebrow(caption))
        if not names:
            box.append(dim("None"))
            return box
        for name in names[:6]:
            label = Gtk.Label(label=name, xalign=0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.add_css_class("caption")
            box.append(label)
        if len(names) > 6:
            box.append(dim(f"and {len(names) - 6} more"))
        return box

    def _enter_proof(self):
        self._show_page(self.page_proof, refresh=self._refresh_proof)

    # Four aliases pinned by the smoke script. All of them land on Proof.
    def _enter_step3(self):
        self._enter_destination()

    def _enter_step4(self):
        self._enter_conditions()

    def _enter_step5(self):
        self._enter_proof()

    def _enter_step6(self):
        self._enter_proof()

    # -- save --------------------------------------------------------------
    def _apply(self, save_another):
        rule = self._rule_dict()
        folder = safe_category_name(rule.get("name") or "")
        if not folder:
            self.win.toast("Give the rule a destination folder name")
            return
        rule["name"] = folder
        if not self._has_matcher(rule):
            self.win.toast("Add at least one extension or a name match")
            return
        if not self._regex_ok():
            self.win.toast("That name or exclusion regex isn't valid")
            return
        normalized = normalize_rule(rule)
        if normalized is None:
            self.win.toast("Give the rule a destination folder name")
            return
        self.controller.save_rule(self.root, normalized)
        self.existing = [c for c in self.controller.rules_for(self.root)
                         if c.get("name")]
        self.destinations = [c.get("name", "") for c in self.existing][:12]
        self.win.toast(f"Saved {folder}/ to this folder")
        if save_another:
            self._reset_for_another()
        else:
            self.close()

    def _reset_for_another(self):
        self._files = None
        self.state.update({
            "recipe": None, "exts": [], "name_mode": "none", "name_text": "",
            "folder": "", "min_mb": None, "max_mb": None, "days": None,
            "exclude": "", "enabled": True, "match_all": False, "color": None,
        })
        self.name_entry.set_text("")
        for button in self.name_buttons.values():
            button.set_active(False)
        self.folder_entry.set_text("")
        self.min_row.set_value(0)
        self.max_row.set_value(0)
        self.days_row.set_value(0)
        self.exclude_row.set_text("")
        self.enabled_row.set_active(True)
        self.match_all_row.set_active(False)
        self._clearing_color = True
        try:
            self.color_btn.set_rgba(Gdk.RGBA())
        finally:
            self._clearing_color = False
        self._show_page(self.page_catch, refresh=self._refresh_catch)

    # -- suggest sheet -----------------------------------------------------
    def _build_suggest(self):
        content = self._pad(Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                    spacing=12))
        content.append(self._step_header(1, "Rules I can suggest"))
        self.suggest_intro = dim("")
        content.append(self.suggest_intro)
        self.suggest_list = listbox()
        content.append(self.suggest_list)
        add = Gtk.Button(label="Add selected rules")
        add.add_css_class("suggested-action")
        add.connect("clicked", self._add_suggestions)
        self.page_suggest = self._page(1, content, [self._back_button()],
                                       [add])
        self.page_suggest.set_title("Suggest rules")

    def _magic_suggest(self):
        freq = self._ext_freq()
        if not freq:
            self.win.toast("No files in this folder to learn from yet")
            return
        groups: dict[str, list[str]] = {}
        for ext, _count in sorted(freq.items(),
                                  key=lambda kv: (-kv[1], kv[0])):
            groups.setdefault(self._recipe_folder(ext), []).append(ext)
        self._clear_box(self.suggest_list)
        self._suggest_checks = []
        for folder, exts in groups.items():
            check = Gtk.CheckButton(
                label=f"{folder}/   " + ", ".join("." + e for e in exts))
            check.set_active(True)
            self._suggest_checks.append((check, folder, exts))
            self.suggest_list.append(check)
        self.suggest_intro.set_text(
            f"Based on {len(self._scan())} files, here are {len(groups)} rules "
            "I can add. Untick anything you don't want.")
        self._show_page(self.page_suggest)

    @staticmethod
    def _recipe_folder(ext):
        for _key, title, _sub, _icon, exts in WIZARD_RECIPES:
            if ext in exts:
                return title
        return ext.capitalize()

    def _add_suggestions(self, _button):
        have = {c.get("name") for c in self.controller.rules_for(self.root)}
        added = 0
        for check, folder, exts in self._suggest_checks:
            if not check.get_active() or folder in have:
                continue
            rule = normalize_rule({"name": folder, "extensions": list(exts)})
            if rule is None:
                continue
            self.controller.save_rule(self.root, rule)
            have.add(folder)
            added += 1
        if not added:
            self.win.toast("Nothing new to add")
            return
        self.win.toast(f"Added {added} rule(s)")
        self.close()
