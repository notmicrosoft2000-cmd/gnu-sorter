"""GTK4 / libadwaita interface for Funnel (v2.0).

Layout notes:
  * Rules page is a split view: watched folders on the left, that folder's
    rules on the right.
  * "Add Rule" opens a step-by-step wizard (Adw.NavigationView in an
    Adw.Dialog); power users get a single "Advanced rule" form instead.
  * Any edit automatically gives the folder its own ruleset if it was still
    inheriting the shared default template.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import (APP_ID, APP_NAME, CREATOR, ISSUES, REPO, WEBSITE,  # noqa: E402
               __version__)
from .config import (Config, PRESETS,  # noqa: E402
                     describe_rule, duplicate_rule, move_rule,
                     normalize_rule, safe_category_name)
from .engine import Sorter, expand, fmt  # noqa: E402
from . import service  # noqa: E402


# --------------------------------------------------------------------- app ---

class FunnelApp(Adw.Application):
    def __init__(self, cfg: Config, migrated: bool = False):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.cfg = cfg
        self.sorter = Sorter(cfg)
        self.migrated = migrated
        self.window: MainWindow | None = None
        self.connect("activate", self.on_activate)
        self._add_actions()

    def _add_actions(self) -> None:
        handlers = {
            "about": lambda *_: self.window and self.window.show_about(),
            "sort-now": lambda *_: self.window and self.window.on_sort_now(None),
            "update": lambda *_: self.window and self.window.on_update_clicked(),
            "quit": lambda *_: self.quit(),
        }
        for name, handler in handlers.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def on_activate(self, _app):
        if self.window is None:
            self.window = MainWindow(self, self.cfg, self.sorter)
        self.window.present()
        if self.migrated:
            self.window.toast("Imported your rules and history from download-sorter")


# ---------------------------------------------------------------- helpers ---

def _heading(text: str) -> Gtk.Label:
    lbl = Gtk.Label(label=text, xalign=0)
    lbl.add_css_class("title-2")
    return lbl


def _icon_button(icon: str, tooltip: str, callback, *data) -> Gtk.Button:
    btn = Gtk.Button.new_from_icon_name(icon)
    btn.set_tooltip_text(tooltip)
    btn.add_css_class("flat")
    btn.connect("clicked", callback, *data)
    return btn


def _listbox(selection=Gtk.SelectionMode.NONE) -> Gtk.ListBox:
    lb = Gtk.ListBox()
    lb.add_css_class("boxed-list")
    lb.set_selection_mode(selection)
    return lb


def _scroll(child: Gtk.Widget) -> Gtk.ScrolledWindow:
    sw = Gtk.ScrolledWindow()
    sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    sw.set_vexpand(True)
    sw.set_child(child)
    return sw


def _dim(text: str) -> Gtk.Label:
    lbl = Gtk.Label(label=text, xalign=0)
    lbl.add_css_class("dim-label")
    lbl.set_wrap(True)
    return lbl


def _ext_summary(exts) -> str:
    items = ["." + str(e).lstrip(".") for e in exts]
    if not items:
        return "no extensions"
    shown = ", ".join(items[:4])
    extra = len(items) - 4
    return shown + (f", +{extra} more" if extra else "")


def _split_exts(text: str) -> list[str]:
    tokens = text.replace(",", " ").split()
    out = []
    for t in tokens:
        t = t.strip().lower().lstrip(".")
        if t and t not in out:
            out.append(t)
    return out


def _unescape_simple(pattern: str | None) -> str:
    """If `pattern` is exactly an escaped literal (``re.escape(x)``), return x.

    Used to pre-fill the "file name contains" box when editing a rule whose
    name match was created from plain text rather than a hand-written regex.
    """
    if not pattern:
        return ""
    candidate = re.sub(r"\\(.)", r"\1", pattern)
    try:
        if re.escape(candidate) == pattern:
            return candidate
    except re.error:
        pass
    return ""


# ------------------------------------------------------------- main window ---

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: FunnelApp, cfg: Config, sorter: Sorter):
        super().__init__(application=app)
        self.app = app
        self.cfg = cfg
        self.sorter = sorter
        self._loading = False
        self._ctx_pop: Gtk.PopoverMenu | None = None
        self.current_root: str | None = None

        self._add_window_actions()

        self.set_title(APP_NAME)
        try:
            self.set_icon_name("gnu-sorter")
        except Exception:
            pass
        self.set_default_size(940, 640)

        header = Adw.HeaderBar()
        header.set_title_widget(
            Adw.WindowTitle(title=APP_NAME, subtitle="Automatic folder sorting"))

        self.sort_btn = Gtk.Button(label="Sort Now")
        self.sort_btn.add_css_class("suggested-action")
        self.sort_btn.connect("clicked", self.on_sort_now)
        header.pack_start(self.sort_btn)

        menu = Gio.Menu()
        quick = Gio.Menu()
        quick.append("Sort now", "app.sort-now")
        menu.append_section(None, quick)
        tail = Gio.Menu()
        tail.append("Check for updates…", "app.update")
        tail.append("About Funnel", "app.about")
        tail.append("Quit", "app.quit")
        menu.append_section(None, tail)
        header.pack_end(Gtk.MenuButton(menu_model=menu, icon_name="open-menu-symbolic"))

        self.watch_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.watch_switch.set_tooltip_text("Background auto-sort (systemd service)")
        self.watch_switch.connect("notify::active", self.on_watch_toggled)
        watch_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        watch_box.set_margin_end(6)
        watch_box.append(Gtk.Label(label="Watch"))
        watch_box.append(self.watch_switch)
        header.pack_end(watch_box)

        self.toolbar = Adw.ToolbarView()
        self.toolbar.add_top_bar(header)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        body.append(self._build_sidebar())
        body.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        body.append(self._build_stack())
        self.toolbar.set_content(body)

        self.toasts = Adw.ToastOverlay()
        self.toasts.set_child(self.toolbar)
        self.set_content(self.toasts)

        # right-click menu (About / Sort now)
        self.ctx_menu = Gio.Menu()
        self.ctx_menu.append("Sort now", "app.sort-now")
        self.ctx_menu.append("About Funnel", "app.about")
        click = Gtk.GestureClick.new()
        click.set_button(3)
        click.connect("pressed", self._on_context_press)
        self.toasts.add_controller(click)

        self.sidebar.select_row(self.sidebar.get_row_at_index(0))
        self.refresh_folders()
        self.refresh_rules_folders()
        self.refresh_activity()
        self.refresh_watch_switch()

        # Quietly check for updates a few seconds after launch; only the menu
        # item produces a status toast.
        GLib.timeout_add_seconds(3, self._background_update_check)

    # ---------------------------------------------------------- window menu --
    def _add_window_actions(self) -> None:
        action = Gio.SimpleAction.new("advanced", None)
        action.connect("activate", lambda *_: self.on_advanced())
        self.add_action(action)
        for i, (label, _rules) in enumerate(PRESETS):
            act = Gio.SimpleAction.new(f"preset-{i}", None)
            act.connect("activate", lambda *_a, idx=i: self.on_preset(idx))
            self.add_action(act)

    # ------------------------------------------------------- self-update --
    def on_update_clicked(self, *_args) -> None:
        self.toast("Checking for updates…")
        threading.Thread(target=lambda: self._update_worker(notify=True),
                         daemon=True).start()

    def _background_update_check(self) -> bool:
        """One-shot startup check (silent unless an update is installed)."""
        threading.Thread(target=lambda: self._update_worker(notify=False),
                         daemon=True).start()
        return False

    def _update_worker(self, notify: bool) -> None:
        try:
            from . import updater
            current, latest, needed = updater.check()
            if not needed:
                if notify:
                    GLib.idle_add(self.toast, f"Up to date — version {current}")
                return
            GLib.idle_add(self.toast, f"Downloading {latest}…")
            ok, msg = updater.update(dry_run=False)
            GLib.idle_add(self.toast, msg)
        except Exception as exc:
            if notify:
                GLib.idle_add(self.toast, f"Update check failed: {exc}")

    def _on_context_press(self, _gesture, _n_press, x, y):
        if self._ctx_pop is not None:
            return
        pop = Gtk.PopoverMenu.new_from_model(self.ctx_menu)
        pop.set_parent(self.toasts)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        pop.connect("closed", self._on_context_closed)
        self._ctx_pop = pop
        pop.popup()

    def _on_context_closed(self, pop):
        pop.unparent()
        self._ctx_pop = None

    def show_about(self):
        about = Adw.AboutDialog()
        about.set_application_name(APP_NAME)
        about.set_application_icon("gnu-sorter")
        try:
            about.set_logo_icon_name("gnu-sorter")
        except Exception:
            pass
        about.set_developer_name(CREATOR)
        about.set_developers([CREATOR])
        about.set_version(__version__)
        about.set_website(WEBSITE)
        about.set_issue_url(ISSUES)
        about.set_license_type(Gtk.License.GPL_3_0)
        about.set_copyright(f"© 2026 {CREATOR}")
        about.add_link("Source code", REPO)
        about.present(self)

    # ------------------------------------------------------------ sidebar ---
    def _build_sidebar(self) -> Gtk.Widget:
        self.sidebar = Gtk.ListBox()
        self.sidebar.add_css_class("navigation-sidebar")
        self.sidebar.set_size_request(190, -1)
        for name, icon in [("Folders", "folder-symbolic"),
                           ("Rules", "emblem-documents-symbolic"),
                           ("Activity", "document-open-recent-symbolic"),
                           ("Settings", "emblem-system-symbolic")]:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            for setter in (box.set_margin_top, box.set_margin_bottom):
                setter(8)
            box.set_margin_start(10)
            box.set_margin_end(10)
            box.append(Gtk.Image.new_from_icon_name(icon))
            box.append(Gtk.Label(label=name, xalign=0))
            row.set_child(box)
            row.page = name.lower()
            self.sidebar.append(row)
        self.sidebar.connect("row-selected", self._on_nav)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(self.sidebar)
        return scroll

    def _build_stack(self) -> Gtk.Widget:
        self.stack = Gtk.Stack()
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.add_named(self._page_folders(), "folders")
        self.stack.add_named(self._page_rules(), "rules")
        self.stack.add_named(self._page_activity(), "activity")
        self.stack.add_named(self._page_settings(), "settings")
        return self.stack

    def _on_nav(self, _listbox, row):
        if row is not None and hasattr(self, "stack"):
            self.stack.set_visible_child_name(row.page)

    # ------------------------------------------------------------ folders ---
    def _page_folders(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for setter in (outer.set_margin_top, outer.set_margin_bottom,
                       outer.set_margin_start, outer.set_margin_end):
            setter(18)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        head.append(_heading("Folders to sort"))
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        head.append(spacer)
        add = Gtk.Button(label="Add Folder")
        add.add_css_class("pill")
        add.connect("clicked", self.on_add_folder)
        head.append(add)
        outer.append(head)

        outer.append(_dim("Files are sorted into category folders inside each "
                          "watched folder. Every folder can have its own rules."))

        self.roots_list = _listbox()
        outer.append(_scroll(self.roots_list))
        return outer

    def refresh_folders(self):
        self.roots_list.remove_all()
        roots = self.cfg.load_roots()
        if not roots:
            self.roots_list.append(
                Adw.ActionRow(title="No folders yet",
                              subtitle="Use “Add Folder” to get started"))
            return
        for r in roots:
            path = expand(r["path"])
            row = Adw.ActionRow(title=path.name or str(path), subtitle=fmt(path))
            row.set_subtitle_lines(1)
            if self.cfg.is_custom(r["path"]):
                badge = Gtk.Label(label="own rules")
                badge.add_css_class("caption")
                badge.add_css_class("success")
                badge.set_valign(Gtk.Align.CENTER)
                row.add_suffix(badge)
            sw = Gtk.Switch(valign=Gtk.Align.CENTER, active=r["enabled"])
            sw.connect("notify::active", self.on_root_toggled, r["path"])
            row.add_suffix(sw)
            row.set_activatable_widget(sw)
            row.add_suffix(_icon_button("user-trash-symbolic", "Remove",
                                        self.on_remove_root, r["path"]))
            self.roots_list.append(row)

    def on_root_toggled(self, switch, _param, path):
        roots = self.cfg.load_roots()
        for r in roots:
            if r["path"] == path:
                r["enabled"] = switch.get_active()
        self.cfg.save_roots(roots)
        self._restart_if_active()

    def on_remove_root(self, _btn, path):
        roots = [r for r in self.cfg.load_roots() if r["path"] != path]
        self.cfg.save_roots(roots)
        if self.current_root == path:
            self.current_root = None
        self.refresh_folders()
        self.refresh_rules_folders()
        self._restart_if_active()
        self.toast(f"Removed {fmt(expand(path))}")

    def on_add_folder(self, _btn):
        dialog = Gtk.FileDialog()
        dialog.set_title("Choose a folder to sort")
        dialog.select_folder(self, None, self._on_folder_chosen)

    def _on_folder_chosen(self, dialog, result):
        try:
            gfile = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        if gfile is None or not gfile.get_path():
            return
        path = gfile.get_path()
        roots = self.cfg.load_roots()
        if any(expand(r["path"]) == Path(path) for r in roots):
            self.toast("Already watching that folder")
            return
        roots.append({"path": path, "enabled": True})
        self.cfg.save_roots(roots)
        self.refresh_folders()
        self.refresh_rules_folders()
        self._restart_if_active()
        self.toast(f"Added {fmt(Path(path))}")

    # -------------------------------------------------------------- rules ---
    def _page_rules(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for setter in (outer.set_margin_top, outer.set_margin_bottom,
                       outer.set_margin_start, outer.set_margin_end):
            setter(18)

        # --- header: title, search, primary action, overflow menu
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        head.append(_heading("Rules"))
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        head.append(spacer)

        self.rules_search = Gtk.SearchEntry(placeholder_text="Search rules")
        self.rules_search.connect("search-changed", self._on_rule_search)
        head.append(self.rules_search)

        self.add_rule_btn = Gtk.Button(label="Add Rule")
        self.add_rule_btn.add_css_class("suggested-action")
        self.add_rule_btn.add_css_class("pill")
        self.add_rule_btn.connect("clicked", self.on_add_rule)
        head.append(self.add_rule_btn)

        rules_menu = Gio.Menu()
        rules_menu.append("Advanced rule editor…", "win.advanced")
        presets_menu = Gio.Menu()
        for i, (label, _rules) in enumerate(PRESETS):
            presets_menu.append(label, f"win.preset-{i}")
        rules_menu.append_submenu("Apply preset", presets_menu)
        head.append(Gtk.MenuButton(menu_model=rules_menu,
                                   icon_name="view-more-symbolic"))
        outer.append(head)

        # --- split view: folders | rules
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(250)
        paned.set_resize_start_child(False)
        paned.set_shrink_start_child(False)

        self.rules_folders = _listbox(Gtk.SelectionMode.SINGLE)
        self.rules_folders.connect("row-selected", self._on_folder_pick)
        paned.set_start_child(_scroll(self.rules_folders))

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        inherit_box = _listbox()
        self.rules_inherit = Adw.SwitchRow(
            title="Use default rules",
            subtitle="Turn off to give this folder its own rules")
        self.rules_inherit.connect("notify::active", self._on_inherit)
        inherit_box.append(self.rules_inherit)
        right.append(inherit_box)

        hint = Gtk.Label(
            label="First matching rule wins \u2014 reorder with \u25b2 \u25bc.",
            xalign=0)
        hint.add_css_class("dim-label")
        hint.add_css_class("caption")
        right.append(hint)

        self.rules_list = _listbox()
        right.append(_scroll(self.rules_list))

        tester_box = _listbox()
        self.rules_tester = Adw.EntryRow(title="Test a file name")
        self.rules_tester.connect("changed", self._on_tester)
        tester_box.append(self.rules_tester)
        self.rules_tester_result = Adw.ActionRow(title="\u2026",
                                                 subtitle="Type a name to preview")
        tester_box.append(self.rules_tester_result)
        right.append(tester_box)

        paned.set_end_child(right)
        paned.set_vexpand(True)
        outer.append(paned)
        return outer

    def refresh_rules_folders(self):
        """Rebuild the folder list on the left side of the Rules page."""
        self._loading = True
        try:
            self.rules_folders.remove_all()
            roots = self.cfg.load_roots()
            if not roots:
                row = Gtk.ListBoxRow()
                row.set_sensitive(False)
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                box.set_margin_top(8)
                box.set_margin_bottom(8)
                box.set_margin_start(10)
                box.set_margin_end(10)
                box.append(Gtk.Label(label="No folders yet", xalign=0))
                row.set_child(box)
                row._path = None
                self.rules_folders.append(row)
                self.current_root = None
                self.refresh_rules()
                return
            paths = [r["path"] for r in roots]
            if self.current_root not in paths:
                self.current_root = paths[0]
            for r in roots:
                path = expand(r["path"])
                row = Gtk.ListBoxRow()
                row._path = r["path"]
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                for setter in (box.set_margin_top, box.set_margin_bottom):
                    setter(8)
                box.set_margin_start(10)
                box.set_margin_end(10)
                top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                top.append(Gtk.Label(label=path.name or str(path), xalign=0,
                                     hexpand=True, ellipsize=3))
                if self.cfg.is_custom(r["path"]):
                    badge = Gtk.Label(label="own rules")
                    badge.add_css_class("caption")
                    badge.add_css_class("success")
                    top.append(badge)
                box.append(top)
                box.append(Gtk.Label(label=fmt(path), xalign=0, ellipsize=3))
                box.get_last_child().add_css_class("dim-label")
                box.get_last_child().add_css_class("caption")
                row.set_child(box)
                self.rules_folders.append(row)
                if r["path"] == self.current_root:
                    self.rules_folders.select_row(row)
        finally:
            self._loading = False
        self.refresh_rules()

    def _on_folder_pick(self, _listbox, row):
        if self._loading or row is None or not getattr(row, "_path", None):
            return
        self.current_root = row._path
        self.refresh_rules()

    def refresh_rules(self):
        """Fill the right pane for the selected folder."""
        self.rules_list.remove_all()
        root = self.current_root
        self._loading = True
        try:
            self.rules_inherit.set_sensitive(root is not None)
            if root is None:
                self.rules_inherit.set_active(True)
                self.rules_inherit.set_subtitle("Add a folder to edit its rules")
                return
            rules, custom = self.cfg.rules_for(root)
            self.rules_inherit.set_active(not custom)
            self.rules_inherit.set_subtitle(
                "This folder has its own rules" if custom else
                "Turn off to give this folder its own rules")
        finally:
            self._loading = False

        template = {json.dumps(c, sort_keys=True)
                    for c in self.cfg.load_rules()}
        try:
            counts = self.sorter.rule_counts()
        except OSError:
            counts = {}
        total = len(rules)
        for i, cat in enumerate(rules):
            self.rules_list.append(self._rule_row(
                cat, template, i, total, counts.get(cat.get("name", ""), 0)))
        if not rules:
            self.rules_list.append(
                Adw.ActionRow(title="No rules yet",
                              subtitle="Use \u201cAdd Rule\u201d to create one"))
        self._on_rule_search(self.rules_search)

    def _rule_row(self, cat: dict, template: set, index: int,
                  total: int, count: int = 0) -> Adw.ActionRow:
        name = cat.get("name", "?")
        row = Adw.ActionRow(title=f"{name}/")
        parts = [describe_rule(cat)]
        if count:
            parts.append(f"{count} sorted")
        if cat.get("enabled") is False:
            parts.append("paused")
        row.set_subtitle("  \u00b7  ".join(parts))
        row.set_subtitle_lines(2)
        row._haystack = (name + " " + describe_rule(cat) + " "
                         + " ".join(str(e) for e in cat.get("extensions", []))
                         + " " + (cat.get("match_name_regex") or "")).lower()

        num = Gtk.Label(label=str(index + 1))
        num.add_css_class("dim-label")
        num.add_css_class("caption")
        num.set_size_request(16, -1)
        num.set_valign(Gtk.Align.CENTER)
        row.add_prefix(num)

        switch = Gtk.Switch(active=cat.get("enabled") is not False,
                            valign=Gtk.Align.CENTER)
        switch.set_tooltip_text("Include this rule")
        switch.connect("notify::active", self._on_rule_enabled, cat)
        row.add_prefix(switch)

        if json.dumps(cat, sort_keys=True) not in template:
            badge = Gtk.Label(label="custom")
            badge.add_css_class("caption")
            badge.add_css_class("success")
            badge.set_valign(Gtk.Align.CENTER)
            row.add_suffix(badge)

        up = _icon_button("go-up-symbolic", "Move up", self._move_rule, cat, -1)
        down = _icon_button("go-down-symbolic", "Move down",
                            self._move_rule, cat, +1)
        up.set_sensitive(index > 0)
        down.set_sensitive(index < total - 1)
        row.add_suffix(up)
        row.add_suffix(down)
        row.add_suffix(self._rule_menu(cat))
        return row

    def _rule_menu(self, cat: dict) -> Gtk.MenuButton:
        menu = Gtk.MenuButton(icon_name="view-more-symbolic")
        menu.add_css_class("flat")
        menu.set_valign(Gtk.Align.CENTER)
        pop = Gtk.Popover()
        pbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for setter in (pbox.set_margin_top, pbox.set_margin_bottom,
                       pbox.set_margin_start, pbox.set_margin_end):
            setter(6)
        for label, callback in (("Edit\u2026", self._edit_rule),
                                ("Duplicate", self._duplicate_rule),
                                ("Remove", self._delete_rule)):
            btn = Gtk.Button(label=label)
            btn.add_css_class("flat")
            btn.set_halign(Gtk.Align.FILL)
            btn.connect("clicked", self._pop_action, pop, callback, cat)
            pbox.append(btn)
        pop.set_child(pbox)
        menu.set_popover(pop)
        return menu

    def _pop_action(self, _btn, pop, callback, cat):
        pop.popdown()
        callback(_btn, cat)

    def _on_rule_enabled(self, switch, _param, cat: dict):
        if self._loading or not self.current_root:
            return
        root = self.current_root
        self._ensure_custom(root)
        rules, _ = self.cfg.rules_for(root)
        for c in rules:
            if c.get("name") == cat.get("name"):
                if switch.get_active():
                    c.pop("enabled", None)
                else:
                    c["enabled"] = False
                break
        else:
            return
        self._save_rules(root, rules)

    def _move_rule(self, _btn, cat: dict, delta: int):
        if not self.current_root:
            return
        root = self.current_root
        self._ensure_custom(root)
        rules, _ = self.cfg.rules_for(root)
        name = cat.get("name")
        idx = next((i for i, c in enumerate(rules)
                    if c.get("name") == name), None)
        if idx is None:
            return
        target = max(0, min(idx + delta, len(rules) - 1))
        self._save_rules(root, move_rule(rules, name, target))

    def _edit_rule(self, _btn, cat: dict):
        if not self.current_root:
            return
        AdvancedDialog(self, self.current_root, rule=cat).open()

    def _duplicate_rule(self, _btn, cat: dict):
        if not self.current_root:
            return
        root = self.current_root
        self._ensure_custom(root)
        rules, _ = self.cfg.rules_for(root)
        self._save_rules(root, duplicate_rule(rules, cat.get("name")),
                         toast=f"Duplicated {cat.get('name')}/")

    def _save_rules(self, root, rules, toast: str | None = None):
        self.cfg.save_rules_for(root, rules)
        self.sorter.reload()
        self.refresh_rules_folders()
        self.refresh_folders()
        if toast:
            self.toast(toast)

    def _delete_rule(self, _btn, cat: dict):
        root = self.current_root
        if not root:
            return
        self._ensure_custom(root)
        rules, _ = self.cfg.rules_for(root)
        kept = [c for c in rules if c.get("name") != cat.get("name")]
        if len(kept) == len(rules):
            self.toast("Rule not found")
            return
        self._save_rules(root, kept,
                         toast=f"Removed {cat.get('name')}/ from this folder")

    def _on_inherit(self, row, _param):
        if self._loading or not self.current_root:
            return
        root = self.current_root
        if row.get_active():
            if self.cfg.attach_rules(root):
                self.toast("Reverted to the default rules")
        else:
            if self.cfg.detach_rules(root):
                self.toast("This folder now has its own rules")
        self.sorter.reload()
        self.refresh_rules_folders()
        self.refresh_folders()

    def _on_rule_search(self, entry):
        text = entry.get_text().strip().lower()
        i = 0
        while True:
            row = self.rules_list.get_row_at_index(i)
            if row is None:
                break
            hay = getattr(row, "_haystack", "")
            row.set_visible(not text or text in hay)
            i += 1

    def _on_tester(self, entry):
        if not self.current_root:
            return
        name = entry.get_text().strip()
        if not name:
            self.rules_tester_result.set_title("\u2026")
            self.rules_tester_result.set_subtitle("Type a name to preview")
            return
        rules, _ = self.cfg.rules_for(self.current_root)
        dest = self.sorter.classify([name], rules=rules)[name]
        if dest == "Needs-review":
            self.rules_tester_result.set_title(f"{name} \u2192 Needs-review")
            self.rules_tester_result.set_subtitle(
                "No enabled rule matches \u2014 it stays put")
            return
        idx = next((i for i, c in enumerate(rules)
                    if c.get("name") == dest), None)
        pos = f" (rule #{idx + 1})" if idx is not None else ""
        self.rules_tester_result.set_title(f"{name} \u2192 {dest}/")
        self.rules_tester_result.set_subtitle(f"Matched by {dest}{pos}")

    # -- rule creation ----------------------------------------------------
    def _ensure_custom(self, root: str) -> bool:
        if self.cfg.detach_rules(root):
            self.sorter.reload()
            self.toast("This folder now has its own rules")
            return True
        return False

    def save_rule(self, root: str, rule: dict, old_name: str | None = None,
                  merge: bool = False) -> bool:
        """Add or replace one rule (advanced editor + wizard).

        With ``merge`` and no ``old_name`` an existing rule of the same name
        gains the new extensions / name match instead of being replaced.
        """
        rule = normalize_rule(rule)
        if rule is None:
            self.toast("Give the rule a destination folder name")
            return False
        self._ensure_custom(root)
        rules, _ = self.cfg.rules_for(root)
        if merge and old_name is None:
            for cat in rules:
                if cat.get("name") == rule["name"]:
                    have = [str(e).lstrip(".")
                            for e in cat.get("extensions", [])]
                    for e in rule.get("extensions", []):
                        if e not in have:
                            cat.setdefault("extensions", []).append(e)
                    if (rule.get("match_name_regex")
                            and not cat.get("match_name_regex")):
                        cat["match_name_regex"] = rule["match_name_regex"]
                    break
            else:
                rules.append(rule)
        else:
            target = old_name or rule["name"]
            for i, cat in enumerate(rules):
                if cat.get("name") == target:
                    rules[i] = rule
                    break
            else:
                rules.append(rule)
        self._save_rules(root, rules,
                         toast=f"Saved {rule['name']}/ to this folder")
        return True

    def apply_new_rule(self, root: str, folder: str, extensions: list[str],
                       regex: str | None) -> bool:
        folder = safe_category_name(folder)
        if not folder:
            self.toast("Destination folder must be a name inside the watched folder")
            return False
        new = {"name": folder, "extensions": list(extensions)}
        if regex:
            new["match_name_regex"] = regex
        return self.save_rule(root, new, merge=True)

    def on_add_rule(self, _btn):
        if not self.current_root:
            self.toast("Add a folder first (Folders → Add Folder)")
            return
        RuleWizard(self, self.current_root).open()

    def on_advanced(self):
        if not self.current_root:
            self.toast("Add a folder first (Folders → Add Folder)")
            return
        AdvancedDialog(self, self.current_root).open()

    def on_preset(self, index: int):
        root = self.current_root
        if not root:
            return
        label, rules = PRESETS[index]
        self._ensure_custom(root)
        self.cfg.save_rules_for(root, json.loads(json.dumps(rules)))
        self.sorter.reload()
        self.refresh_rules_folders()
        self.refresh_folders()
        self.toast(f"Applied “{label}” to this folder")

    # ----------------------------------------------------------- activity ---
    def _page_activity(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for setter in (outer.set_margin_top, outer.set_margin_bottom,
                       outer.set_margin_start, outer.set_margin_end):
            setter(18)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        head.append(_heading("Activity"))
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        head.append(spacer)
        self.undo_btn = Gtk.Button(label="Undo Last")
        self.undo_btn.add_css_class("pill")
        self.undo_btn.connect("clicked", self.on_undo)
        head.append(self.undo_btn)
        outer.append(head)

        self.activity_list = _listbox()
        outer.append(_scroll(self.activity_list))
        return outer

    def refresh_activity(self):
        self.activity_list.remove_all()
        rows = self.sorter.history(limit=100)
        if not rows:
            self.activity_list.append(Adw.ActionRow(title="Nothing sorted yet"))
            return
        for ts, src, dest in reversed(rows):
            src_p, dest_p = Path(src), Path(dest)
            row = Adw.ActionRow(title=src_p.name,
                                subtitle=f"{fmt(src_p)} → {dest_p.parent.name}/")
            row.add_suffix(Gtk.Label(label=ts.split(" ")[-1]))
            self.activity_list.append(row)

    def on_undo(self, _btn):
        if self.sorter.undo(limit=1, dry_run=False) == 0:
            self.toast("Nothing to undo")
        else:
            self.toast("Restored the last sorted file")
        self.refresh_activity()

    # ----------------------------------------------------------- settings ---
    def _page_settings(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()
        s = self.sorter.settings

        about_group = Adw.PreferencesGroup(title="About")
        about_row = Adw.ActionRow(
            title=APP_NAME,
            subtitle=f"Version {__version__} · by {CREATOR}")
        about_btn = Gtk.Button(label="Details", valign=Gtk.Align.CENTER)
        about_btn.connect("clicked", lambda _b: self.show_about())
        about_row.add_suffix(about_btn)
        about_group.add(about_row)
        self.about_status = Adw.ActionRow(title="Watcher", subtitle=service.status())
        about_group.add(self.about_status)
        page.add(about_group)

        behaviour = Adw.PreferencesGroup(title="Behaviour")
        stable = Adw.SpinRow.new_with_range(0, 30, 1)
        stable.set_title("Stability window")
        stable.set_subtitle("Seconds a file must stop changing before it is moved")
        stable.set_value(float(s.get("min_stable_seconds", 3)))
        stable.connect("notify::value", self._set_setting, "min_stable_seconds", float)
        behaviour.add(stable)

        check = Adw.SpinRow.new_with_range(0.0, 10.0, 0.5)
        check.set_title("Stability check interval")
        check.set_subtitle("Seconds between size checks while a file settles")
        check.set_value(float(s.get("stability_interval", 0.5)))
        check.connect("notify::value", self._set_setting, "stability_interval", float)
        behaviour.add(check)

        interval = Adw.SpinRow.new_with_range(1, 60, 1)
        interval.set_title("Poll interval")
        interval.set_subtitle("Seconds between checks when inotify is unavailable")
        interval.set_value(float(s.get("watch_interval", 2)))
        interval.connect("notify::value", self._set_setting, "watch_interval", float)
        behaviour.add(interval)
        page.add(behaviour)

        extras = Adw.PreferencesGroup(title="Extras")
        notify = Adw.SwitchRow(title="Notifications",
                               subtitle="Desktop notification after each sort",
                               active=bool(s.get("notifications", True)))
        notify.connect("notify::active", self._set_setting, "notifications", bool)
        extras.add(notify)

        dry = Adw.SwitchRow(title="Dry-run by default",
                            subtitle="Preview sorting without moving anything",
                            active=bool(s.get("dry_run_default", False)))
        dry.connect("notify::active", self._set_setting, "dry_run_default", bool)
        extras.add(dry)

        cautious = Adw.SwitchRow(
            title="Extra-cautious download detection",
            subtitle="Wait for downloads to fully settle before sorting them",
            active=bool(s.get("cautious_downloads", True)))
        cautious.connect("notify::active", self._set_setting,
                         "cautious_downloads", bool)
        extras.add(cautious)
        page.add(extras)

        startup = Adw.PreferencesGroup(title="Startup")
        self.start_row = Adw.SwitchRow(title="Start on login",
                                      subtitle="Run the background watcher automatically",
                                      active=service.is_enabled() and service.is_active())
        self.start_row.connect("notify::active", self.on_start_toggled)
        startup.add(self.start_row)
        page.add(startup)
        return page

    def _set_setting(self, row, _param, key, caster):
        value = row.get_active() if isinstance(row, Adw.SwitchRow) else row.get_value()
        s = self.cfg.load_settings()
        s[key] = caster(value)
        self.cfg.save_settings(s)
        self.sorter.reload()

    def on_start_toggled(self, row, _param):
        if row.get_active():
            service.install()
            service.enable()
        else:
            service.disable()
        self.refresh_watch_switch()
        if getattr(self, "about_status", None):
            self.about_status.set_subtitle(service.status())
        self.toast(f"Watch service {service.status()}")

    # --------------------------------------------------------------- misc ---
    def refresh_watch_switch(self):
        self.watch_switch.handler_block_by_func(self.on_watch_toggled)
        self.watch_switch.set_active(service.is_active())
        self.watch_switch.handler_unblock_by_func(self.on_watch_toggled)

    def on_watch_toggled(self, switch, _param):
        if switch.get_active():
            service.install()
            service.enable()
        else:
            service.disable()
        if getattr(self, "about_status", None):
            self.about_status.set_subtitle(service.status())
        self.toast(f"Watch service {service.status()}")

    def _restart_if_active(self):
        if service.is_active():
            service.restart()

    def toast(self, message: str):
        self.toasts.add_toast(Adw.Toast.new(message))

    def on_sort_now(self, _btn):
        self.sort_btn.set_sensitive(False)
        self.toast("Sorting…")

        def work():
            try:
                stats = self.sorter.sweep()
            except Exception as exc:              # never leave the button stuck
                GLib.idle_add(self._on_sweep_failed, str(exc))
                return
            GLib.idle_add(self._on_sweep_done, stats)

        threading.Thread(target=work, daemon=True).start()

    def _on_sweep_done(self, stats):
        self.sort_btn.set_sensitive(True)
        self.refresh_activity()
        verb = "Sorted" if stats["moved"] else "Nothing to sort"
        message = (f"{verb}: {stats['moved']} file(s), "
                   f"{stats['needs_review']} → Needs-review/")
        if stats.get("failed"):
            message += f", {stats['failed']} failed"
        self.toast(message)
        return False

    def _on_sweep_failed(self, message):
        self.sort_btn.set_sensitive(True)
        self.toast(f"Sorting failed: {message}")
        return False


# ------------------------------------------------------------- wizard -------

class RuleWizard:
    """Step-by-step "Add Rule" flow: which files → where → review."""

    def __init__(self, window: MainWindow, root: str):
        self.win = window
        self.cfg = window.cfg
        self.root = root
        self.state = {"mode": "ext"}
        rules, _ = self.cfg.rules_for(root)
        self.destinations = [c.get("name", "") for c in rules
                             if c.get("name")][:12]

        self.dialog = Adw.Dialog(title="Add a rule")
        self.dialog.set_content_width(640)
        self.dialog.set_content_height(600)
        self.view = Adw.NavigationView()
        self.dialog.set_child(self.view)
        self._build()

    def open(self):
        self.view.push(self.page1)
        # Let the dialog layout properly before presenting; show a little larger
        self.dialog.present(self.win)


    # -- page construction ------------------------------------------------
    def _page(self, title: str, content: Gtk.Widget, left, right,
              can_pop: bool) -> Adw.NavigationPage:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_vexpand(True)
        clamp = Adw.Clamp()
        clamp.set_maximum_size(520)
        clamp.set_tightening_threshold(400)
        clamp.set_child(content)
        box.append(_scroll(clamp))
        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.set_margin_start(18)
        footer.set_margin_end(18)
        footer.set_margin_top(14)
        footer.set_margin_bottom(14)
        for w in left:
            footer.append(w)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        footer.append(spacer)
        for w in right:
            footer.append(w)
        box.append(footer)
        page = Adw.NavigationPage(title=title, child=box)
        page.set_can_pop(can_pop)
        return page

    @staticmethod
    def _field(label: str, widget: Gtk.Widget) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        lbl = Gtk.Label(label=label, xalign=0)
        lbl.add_css_class("heading")
        box.append(lbl)
        box.append(widget)
        return box

    @staticmethod
    def _pad(widget: Gtk.Widget) -> Gtk.Widget:
        widget.set_margin_top(18)
        widget.set_margin_bottom(18)
        widget.set_margin_start(18)
        widget.set_margin_end(18)
        return widget

    def _build(self) -> None:
        # ---------------- page 1: which files?
        c1 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self._pad(c1)
        c1.append(Gtk.Label(label="Which files should this rule catch?",
                            xalign=0))
        c1.get_last_child().add_css_class("title-3")

        self.mode_list = _listbox(Gtk.SelectionMode.SINGLE)
        for mode, title, sub in (
                ("ext", "File extension", ".jpg, .quant, .pdf, .cbz"),
                ("name", "File name", "matches text — e.g. “screenshot”")):
            row = Gtk.ListBoxRow()
            row._mode = mode
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.set_margin_top(8)
            box.set_margin_bottom(8)
            box.set_margin_start(10)
            box.set_margin_end(10)
            lbl = Gtk.Label(label=title, xalign=0)
            sub_lbl = Gtk.Label(label=sub, xalign=0)
            sub_lbl.add_css_class("dim-label")
            sub_lbl.add_css_class("caption")
            box.append(lbl)
            box.append(sub_lbl)
            row.set_child(box)
            self.mode_list.append(row)
        self.mode_list.connect("row-selected", self._on_mode)
        c1.append(self.mode_list)

        self.entry_stack = Gtk.Stack()
        self.entry_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.ext_entry = Gtk.Entry(placeholder_text="jpg, png, quant")
        self.ext_entry.connect("changed", self._on_input)
        self.name_entry = Gtk.Entry(placeholder_text="screenshot")
        self.name_entry.connect("changed", self._on_input)
        self.entry_stack.add_named(self.ext_entry, "ext")
        self.entry_stack.add_named(self.name_entry, "name")
        c1.append(self._field("Extensions", self.entry_stack))
        c1.append(_dim("Separate multiple extensions with commas. The folder is "
                       "created automatically on the first matching file."))

        self.btn_cancel = Gtk.Button(label="Cancel")
        self.btn_cancel.connect("clicked", lambda _b: self.dialog.close())
        self.btn_next1 = Gtk.Button(label="Next")
        self.btn_next1.add_css_class("suggested-action")
        self.btn_next1.set_sensitive(False)
        self.btn_next1.connect("clicked", self._go_dest)
        self.page1 = self._page("Which files?", c1,
                                [self.btn_cancel], [self.btn_next1], can_pop=False)

        # ---------------- page 2: where do they go?
        c2 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self._pad(c2)
        c2.append(Gtk.Label(label="Where should they go?", xalign=0))
        c2.get_last_child().add_css_class("title-3")
        c2.append(_dim("Pick an existing destination or type your own — "
                       "Funnel creates the folder for you."))

        if self.destinations:
            flow = Gtk.FlowBox()
            flow.set_selection_mode(Gtk.SelectionMode.NONE)
            flow.set_max_children_per_line(6)
            flow.set_homogeneous(False)
            for name in self.destinations:
                btn = Gtk.Button(label=f"{name}/", valign=Gtk.Align.CENTER)
                btn.add_css_class("pill")
                btn.connect("clicked", self._pick_dest, name)
                flow.append(btn)
            c2.append(flow)

        self.folder_entry = Gtk.Entry(placeholder_text="Photos")
        self.folder_entry.connect("changed", self._on_folder_changed)
        c2.append(self._field("Destination folder", self.folder_entry))
        c2.append(_dim("Examples: Photos, quant, Comics, Reports"))

        self.btn_back2 = Gtk.Button(label="Back")
        self.btn_back2.connect("clicked", lambda _b: self.view.pop())
        self.btn_next2 = Gtk.Button(label="Next")
        self.btn_next2.add_css_class("suggested-action")
        self.btn_next2.set_sensitive(False)
        self.btn_next2.connect("clicked", self._go_review)
        self.page2 = self._page("Where should they go?", c2,
                                [self.btn_back2], [self.btn_next2], can_pop=True)

        # ---------------- page 3: review
        c3 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self._pad(c3)
        c3.append(Gtk.Label(label="Ready to add", xalign=0))
        c3.get_last_child().add_css_class("title-3")

        summary = _listbox()
        self.review_match = Adw.ActionRow(title="Matches")
        self.review_dest = Adw.ActionRow(title="Destination")
        self.review_scope = Adw.ActionRow(title="Applies to")
        for row in (self.review_match, self.review_dest, self.review_scope):
            row.set_subtitle_lines(2)
            summary.append(row)
        c3.append(summary)
        c3.append(_dim("This folder will get its own rules, separate from the "
                       "shared defaults."))

        self.btn_back3 = Gtk.Button(label="Back")
        self.btn_back3.connect("clicked", lambda _b: self.view.pop())
        self.btn_apply = Gtk.Button(label="Add rule")
        self.btn_apply.add_css_class("suggested-action")
        self.btn_apply.connect("clicked", self._apply)
        self.page3 = self._page("Review", c3,
                                [self.btn_back3], [self.btn_apply], can_pop=True)

        # initial selection (after all widgets exist)
        self.mode_list.select_row(self.mode_list.get_row_at_index(0))

    # -- behaviour --------------------------------------------------------
    def _on_mode(self, _listbox, row):
        if row is None:
            return
        self.state["mode"] = row._mode
        self.entry_stack.set_visible_child_name(row._mode)
        self._on_input()

    def _on_input(self, *_):
        if self.state["mode"] == "ext":
            ok = bool(_split_exts(self.ext_entry.get_text()))
        else:
            ok = bool(self.name_entry.get_text().strip())
        self.btn_next1.set_sensitive(ok)

    def _suggest_folder(self) -> str:
        if self.state["mode"] == "ext":
            exts = _split_exts(self.ext_entry.get_text())
            if exts:
                rules, _ = self.cfg.rules_for(self.root)
                for cat in rules:
                    have = [str(e).lstrip(".") for e in cat.get("extensions", [])]
                    if exts[0] in have:
                        return cat.get("name", exts[0])
                return exts[0]
            return ""
        text = self.name_entry.get_text().strip()
        if re.search("screenshot|capture|scree", text, re.IGNORECASE):
            return "Screenshots"
        return text[:1].upper() + text[1:] if text else ""

    def _go_dest(self, _btn):
        self.folder_entry.set_text(self._suggest_folder())
        self.view.push(self.page2)

    def _pick_dest(self, _btn, name: str):
        self.folder_entry.set_text(name)

    def _on_folder_changed(self, entry):
        self.btn_next2.set_sensitive(bool(entry.get_text().strip()))

    def _go_review(self, _btn):
        folder = self.folder_entry.get_text().strip()
        if not folder:
            return
        if self.state["mode"] == "ext":
            exts = _split_exts(self.ext_entry.get_text())
            match = "extension " + " ".join(f".{e}" for e in exts)
        else:
            match = f"file name contains “{self.name_entry.get_text().strip()}”"
        root_name = expand(self.root).name or str(self.root)
        self.review_match.set_subtitle(match)
        self.review_dest.set_subtitle(f"{folder}/")
        self.review_scope.set_subtitle(f"{root_name}  (own rules)")
        self.view.push(self.page3)

    def _apply(self, _btn):
        folder = self.folder_entry.get_text().strip()
        if not folder:
            return
        if self.state["mode"] == "ext":
            extensions, regex = _split_exts(self.ext_entry.get_text()), None
        else:
            extensions = []
            regex = re.escape(self.name_entry.get_text().strip())
        if self.win.apply_new_rule(self.root, folder, extensions, regex):
            self.dialog.close()


# ------------------------------------------------------ advanced editor ------

class AdvancedDialog:
    """Power-user form: everything in one place, applied to one folder."""

    def __init__(self, window: MainWindow, root: str, rule: dict | None = None):
        self.win = window
        self.cfg = window.cfg
        self.root = root
        self.original = dict(rule or {})
        self.old_name = self.original.get("name")
        self.exts = [str(e).lstrip(".")
                     for e in self.original.get("extensions", [])]
        self._building = True

        self.dialog = Adw.Dialog(
            title="Edit rule" if self.old_name else "Advanced rule")
        self.dialog.set_content_width(600)
        self.dialog.set_content_height(680)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for setter in (box.set_margin_top, box.set_margin_start, box.set_margin_end):
            setter(18)
        box.set_margin_bottom(8)

        # --- where ---------------------------------------------------
        box.append(self._section("Where should they go?"))
        where = _listbox()
        self.folder_row = Adw.EntryRow(title="Destination folder")
        self.folder_row.set_text(str(self.original.get("name", "")))
        self.folder_row.connect("changed", self._preview)
        browse = Gtk.Button(icon_name="folder-open-symbolic")
        browse.add_css_class("flat")
        browse.set_tooltip_text("Pick a folder\u2026")
        browse.set_valign(Gtk.Align.CENTER)
        browse.connect("clicked", self._browse)
        self.folder_row.add_suffix(browse)
        where.append(self.folder_row)
        box.append(where)

        # --- match ---------------------------------------------------
        box.append(self._section("What should it catch?"))
        match = _listbox()
        self.ext_entry = Adw.EntryRow(title="Add an extension, then press Enter")
        self.ext_entry.connect("entry-activated", self._add_ext)
        self.ext_entry.connect("changed", self._ext_typed)
        match.append(self.ext_entry)
        literal = _unescape_simple(self.original.get("match_name_regex"))
        self.contains_row = Adw.EntryRow(title="File name contains (optional)")
        self.contains_row.set_text(literal)
        self.contains_row.connect("changed", self._preview)
        self.regex_row = Adw.EntryRow(title="\u2026or matches regex (optional)")
        self.regex_row.set_text(
            "" if literal else self.original.get("match_name_regex", ""))
        self.regex_row.connect("changed", self._preview)
        self.exclude_row = Adw.EntryRow(
            title="Skip if name matches regex (optional)")
        self.exclude_row.set_text(self.original.get("exclude_name_regex", ""))
        self.exclude_row.connect("changed", self._preview)
        match.append(self.contains_row)
        match.append(self.regex_row)
        match.append(self.exclude_row)
        box.append(match)

        self.ext_chips = Gtk.FlowBox()
        self.ext_chips.set_selection_mode(Gtk.SelectionMode.NONE)
        self.ext_chips.set_max_children_per_line(14)
        self.ext_chips.set_row_spacing(6)
        self.ext_chips.set_column_spacing(6)
        box.append(self.ext_chips)

        mode_box = _listbox()
        self.match_all_row = Adw.SwitchRow(
            title="Require every condition",
            subtitle="Off matches any condition (OR); on matches all (AND)")
        self.match_all_row.set_active(self.original.get("match_mode") == "all")
        self.match_all_row.connect("notify::active", self._preview)
        mode_box.append(self.match_all_row)
        box.append(mode_box)

        # --- refine --------------------------------------------------
        box.append(self._section("Fine-tune (optional)"))
        refine = _listbox()
        self.min_row = Adw.EntryRow(title="Minimum size in MB")
        self.max_row = Adw.EntryRow(title="Maximum size in MB")
        self.days_row = Adw.EntryRow(title="Only files modified in the last N days")
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
        box.append(refine)

        self.preview = Gtk.Label(xalign=0, wrap=True)
        self.preview.add_css_class("dim-label")
        box.append(self.preview)

        outer.append(_scroll(box))
        outer.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for setter in (footer.set_margin_top, footer.set_margin_bottom,
                       footer.set_margin_start, footer.set_margin_end):
            setter(14)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.dialog.close())
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

    # -- helpers ------------------------------------------------------
    @staticmethod
    def _section(text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.add_css_class("heading")
        return lbl

    def open(self):
        self.dialog.present(self.win)

    def _rebuild_chips(self):
        while (child := self.ext_chips.get_first_child()) is not None:
            self.ext_chips.remove(child)
        for ext in self.exts:
            chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            chip.add_css_class("pill")
            chip.add_css_class("card")
            chip.append(Gtk.Label(label=f".{ext}"))
            remove = Gtk.Button(icon_name="window-close-symbolic")
            remove.add_css_class("flat")
            remove.add_css_class("circular")
            remove.set_tooltip_text(f"Remove .{ext}")
            remove.connect("clicked", self._remove_ext, ext)
            chip.append(remove)
            self.ext_chips.append(chip)

    def _add_ext(self, _entry):
        for ext in _split_exts(self.ext_entry.get_text()):
            if ext not in self.exts:
                self.exts.append(ext)
        self.ext_entry.set_text("")
        self._rebuild_chips()
        self._preview()

    def _ext_typed(self, entry):
        # Commit on a comma/space so typing "jpg, png" still works, but typing
        # a single extension only becomes a chip on Enter.
        text = entry.get_text()
        if any(ch in text for ch in ", \t"):
            for ext in _split_exts(text):
                if ext not in self.exts:
                    self.exts.append(ext)
            entry.set_text("")
            self._rebuild_chips()
            self._preview()

    def _remove_ext(self, _btn, ext: str):
        self.exts = [e for e in self.exts if e != ext]
        self._rebuild_chips()
        self._preview()

    def _browse(self, _btn):
        if not hasattr(Gtk, "FileDialog"):
            self.win.toast("The folder picker needs a newer GTK")
            return
        dialog = Gtk.FileDialog()
        dialog.set_title("Choose a destination folder")
        dialog.set_initial_folder(Gio.File.new_for_path(str(expand(self.root))))
        dialog.select_folder(self.win, None, self._browse_done)

    def _browse_done(self, dialog, result):
        try:
            gfile = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        if gfile is None:
            return
        path = Path(gfile.get_path() or "")
        try:
            name = str(path.relative_to(expand(self.root)))
        except ValueError:
            name = path.name
        self.folder_row.set_text(safe_category_name(name) or path.name)

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
        for key in ("icon", "color"):
            if self.original.get(key):
                rule[key] = self.original[key]
        return rule

    def _preview(self, *_):
        if getattr(self, "_building", False):
            return
        normalized = normalize_rule(self._build_rule())
        if normalized is None:
            self.preview.set_text("Give the rule a destination folder name.")
        else:
            self.preview.set_text("Reads as: " + describe_rule(normalized))

    def _apply(self, _btn):
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
        if not (rule.get("extensions") or rule.get("match_name_regex")):
            self.win.toast("Add at least one extension or a name match")
            return
        if self.win.save_rule(self.root, rule, old_name=self.old_name):
            self.dialog.close()


def run(cfg: Config, migrated: bool = False) -> int:
    Adw.init()
    app = FunnelApp(cfg, migrated=migrated)
    return app.run([])
