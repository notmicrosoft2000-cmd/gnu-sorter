"""GTK4 / libadwaita interface for Funnel.

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
import math
import re
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import (Adw, Gdk, Gio, GLib, Gtk,  # noqa: E402
                           Pango, PangoCairo)

from . import (APP_ID, APP_NAME, CREATOR, ISSUES, REPO, WEBSITE,  # noqa: E402
               __version__)
from .config import (Config, PRESETS,  # noqa: E402
                     describe_rule, duplicate_rule, move_rule,
                     name_matcher_regex, normalize_rule, safe_category_name)
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
        apply_color_scheme(self.cfg)
        if self.window is None:
            self.window = MainWindow(self, self.cfg, self.sorter)
        self.window.present()
        if self.migrated:
            self.window.toast("Imported your rules and history from download-sorter")


# ---------------------------------------------------------------- helpers ---

_SCHEMES = {"default": Adw.ColorScheme.DEFAULT,
            "light": Adw.ColorScheme.FORCE_LIGHT,
            "dark": Adw.ColorScheme.FORCE_DARK}


def apply_color_scheme(cfg: Config) -> None:
    """Apply the saved appearance choice (system / light / dark)."""
    name = str(cfg.load_settings().get("color_scheme", "default")).lower()
    try:
        Adw.StyleManager.get_default().set_color_scheme(
            _SCHEMES.get(name, Adw.ColorScheme.DEFAULT))
    except Exception:
        pass


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


def _color_dot(color: str) -> Gtk.Widget:
    """A small dot painted in ``color`` (any CSS colour), for rule rows."""
    dot = Gtk.Label(label="\u25cf")
    dot.set_valign(Gtk.Align.CENTER)
    try:
        provider = Gtk.CssProvider()
        provider.load_from_string(f"label {{ color: {color}; font-size: 14px; }}")
        dot.get_style_context().add_provider(
            provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    except Exception:
        pass
    dot.set_tooltip_text("Accent colour")
    return dot


def _dim(text: str) -> Gtk.Label:
    lbl = Gtk.Label(label=text, xalign=0)
    lbl.add_css_class("dim-label")
    lbl.set_wrap(True)
    return lbl


def _human_size(n) -> str:
    """Compact file size for journal/restore rows."""
    try:
        size = float(n)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


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

        if cat.get("color"):
            row.add_prefix(_color_dot(str(cat["color"])))

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
        self.activity_search = Gtk.SearchEntry()
        self.activity_search.set_placeholder_text("Search file name…")
        self.activity_search.set_width_chars(16)
        self.activity_search.connect("search-changed",
                                     lambda _e: self.refresh_activity())
        head.append(self.activity_search)
        self.restore_btn = Gtk.Button(label="Restore…")
        self.restore_btn.add_css_class("pill")
        self.restore_btn.connect("clicked", self.on_restore)
        head.append(self.restore_btn)
        self.undo_btn = Gtk.Button(label="Undo Last")
        self.undo_btn.add_css_class("pill")
        self.undo_btn.connect("clicked", self.on_undo)
        head.append(self.undo_btn)
        outer.append(head)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.activity_rule_filter = Gtk.DropDown.new_from_strings(["All rules"])
        self.activity_rule_filter.set_tooltip_text("Filter by rule")
        try:
            self.activity_rule_filter.set_enable_search(True)
        except AttributeError:
            pass
        self.activity_rule_filter.connect(
            "notify::selected", lambda *_a: self.refresh_activity())
        bar.append(self.activity_rule_filter)
        self.activity_ext_filter = Gtk.DropDown.new_from_strings(["All types"])
        self.activity_ext_filter.set_tooltip_text("Filter by file type")
        try:
            self.activity_ext_filter.set_enable_search(True)
        except AttributeError:
            pass
        self.activity_ext_filter.connect(
            "notify::selected", lambda *_a: self.refresh_activity())
        bar.append(self.activity_ext_filter)
        self.activity_summary = _dim("")
        self.activity_summary.set_hexpand(True)
        bar.append(self.activity_summary)
        outer.append(bar)

        self.activity_list = _listbox()
        outer.append(_scroll(self.activity_list))
        return outer

    def _activity_query(self) -> str | None:
        entry = getattr(self, "activity_search", None)
        text = entry.get_text().strip() if entry is not None else ""
        return text or None

    def _activity_rule(self) -> str | None:
        dd = getattr(self, "activity_rule_filter", None)
        if dd is None or dd.get_selected() == 0:
            return None
        item = dd.get_selected_item()
        name = item.get_string() if item is not None else ""
        return name or None

    def _activity_ext(self) -> str | None:
        dd = getattr(self, "activity_ext_filter", None)
        if dd is None or dd.get_selected() == 0:
            return None
        item = dd.get_selected_item()
        name = item.get_string() if item is not None else ""
        return name or None

    def refresh_activity(self):
        if getattr(self, "_activity_refreshing", False):
            return
        self._activity_refreshing = True
        try:
            index = self.sorter.journal_index()
            # Keep the rule filter in step with the journal without dropping
            # the user's selection (and without re-entering this method).
            dd = getattr(self, "activity_rule_filter", None)
            if dd is not None:
                wanted = self._activity_rule()
                items = ["All rules"] + sorted(index.counts())
                model = dd.get_model()
                current = ([model.get_string(i) for i in range(model.get_n_items())]
                           if model is not None else [])
                if current != items:
                    dd.set_model(Gtk.StringList.new(items))
                    if wanted in items:
                        dd.set_selected(items.index(wanted))
            ed = getattr(self, "activity_ext_filter", None)
            if ed is not None:
                wanted_ext = self._activity_ext()
                exts = sorted({Path(rec["src"]).suffix.lstrip(".").lower()
                               for _line, rec in index.entries
                               if Path(rec["src"]).suffix})
                items = ["All types"] + exts
                model = ed.get_model()
                current = ([model.get_string(i) for i in range(model.get_n_items())]
                           if model is not None else [])
                if current != items:
                    ed.set_model(Gtk.StringList.new(items))
                    if wanted_ext in items:
                        ed.set_selected(items.index(wanted_ext))
            rows = self.sorter.records(limit=200, name=self._activity_query(),
                                       rule=self._activity_rule(),
                                       ext=self._activity_ext())
            self.activity_list.remove_all()
            if not rows:
                self.activity_list.append(Adw.ActionRow(title="Nothing sorted yet"))
            else:
                for r in reversed(rows):
                    src_p, dest_p = Path(r["src"]), Path(r["dest"])
                    rule = r.get("rule") or "—"
                    row = Adw.ActionRow(
                        title=src_p.name,
                        subtitle=f"{rule} · {fmt(src_p)} → {dest_p.parent.name}/")
                    row.add_suffix(Gtk.Label(label=r["ts"].split(" ")[-1]))
                    self.activity_list.append(row)
            total = len(index)
            shown = len(rows)
            self.activity_summary.set_text(
                f"{shown} of {total} move(s)" if shown != total
                else f"{total} move(s)")
        finally:
            self._activity_refreshing = False

    def on_undo(self, _btn):
        if self.sorter.undo(limit=1, dry_run=False) == 0:
            self.toast("Nothing to undo")
        else:
            self.toast("Restored the last sorted file")
        self.refresh_activity()

    def on_restore(self, _btn):
        name, rule, ext = (self._activity_query(), self._activity_rule(),
                           self._activity_ext())
        if not (name or rule or ext):
            self.toast("Filter by a file, rule or type, then Restore")
            return
        RestoreDialog(self, name=name, rule=rule, ext=ext).open()

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

        look = Adw.PreferencesGroup(title="Appearance")
        scheme = Adw.ComboRow(
            title="Colour scheme",
            subtitle="Follow the system, or force light or dark")
        scheme.set_model(Gtk.StringList.new(["System", "Light", "Dark"]))
        scheme.set_selected({"default": 0, "light": 1, "dark": 2}.get(
            str(s.get("color_scheme", "default")).lower(), 0))
        scheme.connect("notify::selected", self._on_scheme)
        look.add(scheme)
        page.add(look)

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

        history = Adw.PreferencesGroup(
            title="History",
            description="The move journal trims itself once it grows past "
                        "these limits (a full copy is saved as history.log.bak).")
        keep = Adw.SpinRow.new_with_range(100, 100000, 100)
        keep.set_title("Entries to keep")
        keep.set_subtitle("Most recent moves kept when the journal is trimmed")
        keep.set_value(float(s.get("history_max_entries", 2500)))
        keep.connect("notify::value", self._set_setting,
                     "history_max_entries", int)
        history.add(keep)

        size = Adw.SpinRow.new_with_range(1, 500, 1)
        size.set_title("Trim after (MB)")
        size.set_subtitle("Maximum journal size before old entries are dropped")
        size.set_value(float(s.get("history_max_bytes", 1_000_000)) / 1_000_000)
        size.connect("notify::value", self._set_setting,
                     "history_max_bytes", lambda v: int(v * 1_000_000))
        history.add(size)
        page.add(history)

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

    def _on_scheme(self, row, _param):
        names = ["default", "light", "dark"]
        idx = row.get_selected()
        s = self.cfg.load_settings()
        s["color_scheme"] = names[idx] if 0 <= idx < len(names) else "default"
        self.cfg.save_settings(s)
        apply_color_scheme(self.cfg)

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

# A friendly starting point per kind of file. Each card pre-fills a sensible
# extension set; everything can still be changed in step 2.
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

WIZARD_STEPS = [
    "What are we tidying?",
    "Show me what to catch",
    "Where should they go?",
    "Fine-tune (optional)",
    "Priority & scope",
    "Review with proof",
]


class SortPreview(Gtk.DrawingArea):
    """A small, self-drawn "files flow into the folder" animation.

    Real file names are drawn on the left; the ones this rule catches travel
    into the destination folder while the rest stay put and dim. Honours the
    desktop's "reduce motion" setting by jumping straight to the final frame.
    """

    def __init__(self, files, matched, folder):
        super().__init__()
        self.files = list(files)[:8]
        self.matched = set(matched)
        self.folder = folder or "Destination"
        self.progress = 0.0
        self._tick_id = 0
        self._start = None
        self.set_content_height(170)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)
        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)

    # -- animation clock --------------------------------------------------
    @staticmethod
    def _motion_ok() -> bool:
        try:
            return bool(Gtk.Settings.get_default()
                        .get_property("gtk-enable-animations"))
        except Exception:
            return True

    def _on_map(self, *_):
        if not self._motion_ok() or not self.files:
            self.progress = 1.0
            self.queue_draw()
            return
        self._start = None
        if not self._tick_id:
            self._tick_id = self.add_tick_callback(self._tick)

    def _on_unmap(self, *_):
        if self._tick_id:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = 0

    def _tick(self, _widget, clock):
        now = clock.get_frame_time() / 1_000_000.0
        if self._start is None:
            self._start = now
        self.progress = min(1.0, (now - self._start) / 1.6)
        self.queue_draw()
        if self.progress >= 1.0:
            self._tick_id = 0
            return False
        return True

    # -- painting ---------------------------------------------------------
    def _draw(self, area, cr, width, height, *_):
        try:
            self._paint(area, cr, width, height)
        except Exception:
            pass

    def _paint(self, area, cr, width, height):
        files = self.files or ["(no files yet)"]
        chip_w, chip_h, gap = 150.0, 24.0, 6.0
        top = max(6.0, (height - len(files) * (chip_h + gap)) / 2.0)
        dest_w, dest_h = 138.0, 74.0
        dest_x = max(170.0, width - dest_w - 14.0)
        dest_y = (height - dest_h) / 2.0

        self._round_rect(cr, dest_x, dest_y + dest_h * 0.22,
                         dest_w, dest_h * 0.78, 8.0)
        cr.set_source_rgba(0.98, 0.79, 0.28, 0.95)
        cr.fill()
        self._round_rect(cr, dest_x + 12, dest_y, dest_w * 0.42,
                         dest_h * 0.34, 5.0)
        cr.set_source_rgba(0.98, 0.79, 0.28, 0.95)
        cr.fill()
        layout = area.create_pango_layout(self.folder)
        layout.set_width(int(dest_w * Pango.SCALE))
        layout.set_alignment(Pango.Alignment.CENTER)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        cr.move_to(dest_x, dest_y + dest_h + 4)
        cr.set_source_rgba(0.5, 0.5, 0.56, 1.0)
        PangoCairo.show_layout(cr, layout)

        for i, name in enumerate(files):
            y0 = top + i * (chip_h + gap)
            hit = name in self.matched
            p = self.progress if hit else 0.0
            x = 12.0 + (dest_x + 24.0 - 12.0) * p
            y = y0 + (dest_y + dest_h / 2.0 - chip_h / 2.0 - y0) * p
            if not hit:
                alpha = 0.35
            elif p > 0.85:
                alpha = max(0.0, 1.0 - (p - 0.85) / 0.15)
            else:
                alpha = 1.0
            self._chip(area, cr, x, y, chip_w, chip_h, name, alpha)

    def _chip(self, area, cr, x, y, w, h, text, alpha):
        self._round_rect(cr, x, y, w, h, 6.0)
        cr.set_source_rgba(0.30, 0.55, 0.95, 0.16 * alpha)
        cr.fill_preserve()
        cr.set_line_width(1.2)
        cr.set_source_rgba(0.30, 0.55, 0.95, 0.85 * alpha)
        cr.stroke()
        layout = area.create_pango_layout(text)
        layout.set_width(int((w - 12) * Pango.SCALE))
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        cr.move_to(x + 6, y + 4)
        cr.set_source_rgba(0.12, 0.14, 0.18, alpha)
        PangoCairo.show_layout(cr, layout)

    @staticmethod
    def _round_rect(cr, x, y, w, h, r):
        r = max(0.0, min(r, w / 2.0, h / 2.0))
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2.0, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2.0)
        cr.arc(x + r, y + h - r, r, math.pi / 2.0, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3.0 * math.pi / 2.0)
        cr.close_path()


class RuleWizard:
    """Six friendly steps, from "what are we tidying" to a proven rule.

    1 what to tidy · 2 what to catch · 3 where it goes · 4 fine-tune ·
    5 priority & scope · 6 review with proof. The last step shows the rule in
    plain English and animates the folder's real files sorting (or not).
    """

    def __init__(self, window: MainWindow, root: str):
        self.win = window
        self.cfg = window.cfg
        self.root = root
        self._files = None
        rules, _ = self.cfg.rules_for(root)
        self.existing = [c for c in rules if c.get("name")]
        self.destinations = [c.get("name", "") for c in self.existing][:12]
        self._suggest_checks = []
        self._insert_index = len(self.existing)
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
        }

        self.dialog = Adw.Dialog(title="Add a rule")
        self.dialog.set_content_width(1100)
        self.dialog.set_content_height(820)
        self.view = Adw.NavigationView()
        self.dialog.set_child(self.view)
        self._build()

    def open(self):
        self.view.push(self.page1)
        self.dialog.present(self.win)

    # -- folder scan ------------------------------------------------------
    def _scan(self) -> list[str]:
        if self._files is not None:
            return self._files
        files: list[str] = []
        try:
            for p in sorted(expand(self.root).iterdir()):
                if p.is_file() and not p.name.startswith("."):
                    files.append(p.name)
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

    # -- rule building ----------------------------------------------------
    def _name_pattern(self) -> str | None:
        return name_matcher_regex(self.state["name_mode"],
                                  self.state["name_text"])

    def _rule_dict(self) -> dict:
        s = self.state
        name = safe_category_name(s["folder"]) or s["folder"]
        rule: dict = {"name": name}
        if s["exts"]:
            rule["extensions"] = list(s["exts"])
        pattern = self._name_pattern()
        if pattern:
            rule["match_name_regex"] = pattern
        if not s["enabled"]:
            rule["enabled"] = False
        if s["exclude"]:
            rule["exclude_name_regex"] = s["exclude"]
        if s["min_mb"] is not None:
            rule["min_size_mb"] = s["min_mb"]
        if s["max_mb"] is not None:
            rule["max_size_mb"] = s["max_mb"]
        if s["days"] is not None:
            rule["modified_within_days"] = s["days"]
        if s["match_all"]:
            rule["match_mode"] = "all"
        return rule

    def _regex_ok(self) -> bool:
        if self.state["name_mode"] != "regex":
            return True
        pattern = self._name_pattern()
        if not pattern:
            return True
        try:
            re.compile(pattern)
            return True
        except re.error:
            return False

    @staticmethod
    def _has_matcher(rule: dict) -> bool:
        return bool(rule.get("extensions") or rule.get("match_name_regex")
                    or rule.get("match_all"))

    def _counts(self):
        """(caught, total) if this folder's files were sorted by this rule."""
        names = self._scan()
        if not names:
            return (0, 0)
        rule = self._rule_dict()
        folder = rule.get("name") or "Wizard"
        rule["name"] = folder
        try:
            result = self.win.sorter.classify(names, rules=[rule])
        except Exception:
            return (0, len(names))
        return (sum(1 for n in names if result.get(n) == folder), len(names))

    # -- page scaffolding -------------------------------------------------
    def _page(self, title, content, left, right, can_pop=True):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_vexpand(True)
        clamp = Adw.Clamp()
        clamp.set_maximum_size(560)
        clamp.set_tightening_threshold(420)
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
    def _heading(text):
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.add_css_class("title-3")
        lbl.set_wrap(True)
        return lbl

    @staticmethod
    def _field(label, widget):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        lbl = Gtk.Label(label=label, xalign=0)
        lbl.add_css_class("heading")
        box.append(lbl)
        box.append(widget)
        return box

    @staticmethod
    def _pad(widget):
        widget.set_margin_top(18)
        widget.set_margin_bottom(18)
        widget.set_margin_start(18)
        widget.set_margin_end(18)
        return widget

    @staticmethod
    def _clear_box(box):
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt

    def _root_name(self):
        return expand(self.root).name or str(self.root)

    # -- build ------------------------------------------------------------
    def _build(self):
        self._build_step1()
        self._build_step2()
        self._build_step3()
        self._build_step4()
        self._build_step5()
        self._build_step6()
        self._build_suggest()

    # ---- step 1: what are we tidying ------------------------------------
    def _build_step1(self):
        c = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self._pad(c)
        c.append(self._heading(WIZARD_STEPS[0]))
        c.append(_dim(f"Starting in {self._root_name()}. Pick a starting point — "
                      "you can change every detail next."))

        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(3)
        flow.set_min_children_per_line(2)
        flow.set_row_spacing(8)
        flow.set_column_spacing(8)
        flow.set_homogeneous(True)
        for key, title, sub, icon, exts in WIZARD_RECIPES:
            flow.append(self._recipe_card(key, title, sub, icon, exts))
        flow.append(self._recipe_card("custom", "Custom", "Start from scratch",
                                      "preferences-other-symbolic", []))
        c.append(flow)

        magic = _listbox()
        magic.append(self._magic_row("folder-open-symbolic",
                                     "Pick files in this folder",
                                     "Scan this folder and pick what stands out",
                                     self._magic_pick))
        magic.append(self._magic_row("starred-symbolic",
                                     "Suggest rules for me",
                                     "Learn from the files already here",
                                     self._magic_suggest))
        c.append(magic)

        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.dialog.close())
        self.page1 = self._page("Add a rule", c, [cancel], [], can_pop=False)

    def _recipe_card(self, key, title, sub, icon, exts):
        btn = Gtk.Button()
        btn.add_css_class("card")
        btn.set_size_request(170, 96)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        img = Gtk.Image.new_from_icon_name(icon)
        img.set_pixel_size(28)
        img.add_css_class("dim-label")
        box.append(img)
        lbl = Gtk.Label(label=title, xalign=0)
        lbl.add_css_class("heading")
        box.append(lbl)
        box.append(_dim(sub))
        btn.set_child(box)
        btn.connect("clicked", lambda _b: self._start_recipe(key, exts))
        return btn

    @staticmethod
    def _magic_row(icon, title, subtitle, handler):
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        row.add_prefix(Gtk.Image.new_from_icon_name(icon))
        row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        row.set_activatable(True)
        row.connect("activated", lambda _r: handler())
        return row

    def _start_recipe(self, key, exts):
        self.state["recipe"] = key
        self.state["exts"] = [] if key == "custom" else list(exts)
        self._enter_step2()

    def _magic_pick(self):
        self.state["recipe"] = "custom"
        self.state["exts"] = []
        self._enter_step2()

    # ---- step 2: what to catch ------------------------------------------
    def _build_step2(self):
        c = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self._pad(c)
        c.append(self._heading(WIZARD_STEPS[1]))
        self.count_label = _dim("")
        c.append(self.count_label)

        add_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add_lbl = Gtk.Label(label="Add extension")
        self.ext_drop = Gtk.DropDown.new_from_strings(
            ["." + e for e in self._known_exts()])
        self.ext_drop.set_enable_search(True)
        self.ext_drop.set_hexpand(True)
        self.ext_drop.connect("notify::selected", self._on_ext_picked)
        add_box.append(add_lbl)
        add_box.append(self.ext_drop)
        c.append(add_box)

        self.chip_flow = Gtk.FlowBox()
        self.chip_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.chip_flow.set_max_children_per_line(12)
        self.chip_flow.set_row_spacing(6)
        self.chip_flow.set_column_spacing(6)
        c.append(self.chip_flow)

        files_lbl = Gtk.Label(label="Files already here", xalign=0)
        files_lbl.add_css_class("heading")
        c.append(files_lbl)
        c.append(_dim("Tap a file to catch everything with that extension."))
        self.file_flow = Gtk.FlowBox()
        self.file_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.file_flow.set_max_children_per_line(2)
        self.file_flow.set_row_spacing(4)
        self.file_flow.set_column_spacing(4)
        c.append(self.file_flow)

        name_lbl = Gtk.Label(label="…or match the file name", xalign=0)
        name_lbl.add_css_class("heading")
        c.append(name_lbl)
        self.name_toggles, self.name_buttons = self._toggle_row(
            [("contains", "contains"), ("starts", "starts with"),
             ("ends", "ends with"), ("regex", "regex")])
        c.append(self.name_toggles)
        self.name_entry = Gtk.Entry(placeholder_text="screenshot")
        self.name_entry.connect("changed", self._on_name_changed)
        c.append(self.name_entry)

        back = Gtk.Button(label="Back")
        back.connect("clicked", lambda _b: self.view.pop())
        nxt = Gtk.Button(label="Next")
        nxt.add_css_class("suggested-action")
        nxt.connect("clicked", lambda _b: self._enter_step3())
        self.page2 = self._page(WIZARD_STEPS[1], c, [back], [nxt])

    def _toggle_row(self, options):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        box.add_css_class("linked")
        buttons = {}
        for key, label in options:
            btn = Gtk.ToggleButton(label=label)
            btn.connect("toggled", self._on_name_toggle, key)
            buttons[key] = btn
            box.append(btn)
        return box, buttons

    def _on_name_toggle(self, btn, key):
        if btn.get_active():
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
        idx = drop.get_selected()
        items = self._known_exts()
        if idx == Gtk.INVALID_LIST_POSITION or not (0 <= idx < len(items)):
            return
        ext = items[idx]
        if ext not in self.state["exts"]:
            self.state["exts"].append(ext)
            GLib.idle_add(self._refresh_step2)

    def _on_file_toggled(self, btn, ext):
        if not ext:
            return
        if btn.get_active() and ext not in self.state["exts"]:
            self.state["exts"].append(ext)
        elif not btn.get_active() and ext in self.state["exts"]:
            self.state["exts"].remove(ext)
        GLib.idle_add(self._refresh_step2)

    def _remove_ext(self, _btn, ext):
        if ext in self.state["exts"]:
            self.state["exts"].remove(ext)
        GLib.idle_add(self._refresh_step2)

    def _enter_step2(self):
        self._refresh_step2()
        self.view.push(self.page2)

    def _refresh_step2(self):
        self._rebuild_chips()
        self._refresh_files()
        self._refresh_count()
        return False

    def _rebuild_chips(self):
        self._clear_box(self.chip_flow)
        if not self.state["exts"]:
            self.chip_flow.append(_dim("No extensions yet — add some above."))
            return
        for ext in self.state["exts"]:
            btn = Gtk.Button(label="." + ext)
            btn.add_css_class("pill")
            btn.set_tooltip_text("Remove ." + ext)
            btn.connect("clicked", self._remove_ext, ext)
            self.chip_flow.append(btn)

    def _refresh_files(self):
        self._clear_box(self.file_flow)
        names = self._scan()
        if not names:
            self.file_flow.append(_dim("This folder is empty — add extensions "
                                       "above."))
            return
        freq = self._ext_freq()

        def rank(name):
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            return (-freq.get(ext, 0), name.lower())

        for name in sorted(names, key=rank)[:48]:
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            btn = Gtk.ToggleButton()
            btn.add_css_class("flat")
            active = bool(ext) and ext in self.state["exts"]
            btn.set_active(active)
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            box.append(Gtk.Image.new_from_icon_name("text-x-generic-symbolic"))
            lbl = Gtk.Label(label=name)
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            lbl.set_max_width_chars(28)
            box.append(lbl)
            btn.set_child(box)
            btn.connect("toggled", self._on_file_toggled, ext)
            self.file_flow.append(btn)

    def _refresh_count(self):
        caught, total = self._counts()
        if not self._regex_ok():
            self.count_label.set_text("That regex isn't valid yet — check the "
                                      "name pattern.")
            return
        if not self._has_matcher(self._rule_dict()):
            self.count_label.set_text("Nothing selected yet — pick extensions "
                                      "or a name match.")
        elif total == 0:
            self.count_label.set_text("No files here to preview, but this rule "
                                      "is ready.")
        else:
            self.count_label.set_text(
                f"This catches {caught} of {total} files in this folder.")

    # ---- step 3: where should they go -----------------------------------
    def _build_step3(self):
        c = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self._pad(c)
        c.append(self._heading(WIZARD_STEPS[2]))
        c.append(_dim("A new folder is created inside the watched folder the "
                      "first time something matches."))

        if self.destinations:
            flow = Gtk.FlowBox()
            flow.set_selection_mode(Gtk.SelectionMode.NONE)
            flow.set_max_children_per_line(6)
            for name in self.destinations:
                btn = Gtk.Button(label=f"{name}/")
                btn.add_css_class("pill")
                btn.connect("clicked", self._pick_dest, name)
                flow.append(btn)
            c.append(flow)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.folder_entry = Gtk.Entry(placeholder_text="Photos")
        self.folder_entry.set_hexpand(True)
        self.folder_entry.connect("changed", self._on_folder_changed)
        browse = Gtk.Button(label="Browse…")
        browse.connect("clicked", self._browse)
        row.append(self.folder_entry)
        row.append(browse)
        c.append(self._field("Destination folder", row))
        self.folder_hint = _dim("")
        c.append(self.folder_hint)

        tree_lbl = Gtk.Label(label="Where things live", xalign=0)
        tree_lbl.add_css_class("heading")
        c.append(tree_lbl)
        self.tree_label = _dim("")
        c.append(self.tree_label)

        back = Gtk.Button(label="Back")
        back.connect("clicked", lambda _b: self.view.pop())
        nxt = Gtk.Button(label="Next")
        nxt.add_css_class("suggested-action")
        nxt.connect("clicked", lambda _b: self._enter_step4())
        self.page3 = self._page(WIZARD_STEPS[2], c, [back], [nxt])

    def _pick_dest(self, _btn, name):
        self.folder_entry.set_text(name)

    def _on_folder_changed(self, entry):
        text = entry.get_text().strip()
        self.state["folder"] = text
        safe = safe_category_name(text) if text else None
        if not text:
            self.folder_hint.set_text("Type a name, pick a suggestion, or "
                                      "Browse…")
        elif safe is None:
            self.folder_hint.set_text("That name can't be used for a folder — "
                                      "try letters, numbers and dashes.")
        else:
            self.folder_hint.set_text(
                f"Files will go to {self._root_name()}/{safe}/")
        self._refresh_tree()

    def _refresh_tree(self):
        lines = [f"{self._root_name()}/"]
        folder = self.state["folder"]
        shown = False
        for name in self.destinations:
            marker = "  ← new" if name == folder else ""
            shown = shown or name == folder
            lines.append(f"    {name}/{marker}")
        if folder and not shown:
            lines.append(f"    {folder}/  ← new")
        self.tree_label.set_text("\n".join(lines))

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
        self.folder_entry.set_text(safe_category_name(name) or path.name)

    def _suggest_folder(self):
        exts = self.state["exts"]
        if exts:
            for cat in self.existing:
                have = [str(e).lstrip(".") for e in cat.get("extensions", [])]
                if exts[0] in have:
                    return cat.get("name", exts[0])
            for _key, title, _sub, _icon, rex in WIZARD_RECIPES:
                if exts[0] in rex:
                    return title
            return exts[0].capitalize()
        text = self.state["name_text"].strip()
        if re.search("screenshot|capture|scree", text, re.IGNORECASE):
            return "Screenshots"
        return text[:1].upper() + text[1:] if text else ""

    def _enter_step3(self):
        if not self.state["folder"]:
            self.folder_entry.set_text(self._suggest_folder())
        self._on_folder_changed(self.folder_entry)
        self._refresh_priority()
        self.view.push(self.page3)

    def _enter_step4(self):
        self.view.push(self.page4)

    def _enter_step5(self):
        self._refresh_priority()
        self.view.push(self.page5)

    # ---- step 4: fine-tune ----------------------------------------------
    def _build_step4(self):
        c = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self._pad(c)
        c.append(self._heading(WIZARD_STEPS[3]))
        c.append(_dim("All optional — skip anything you don't need."))

        box = _listbox()
        self.min_row = Adw.SpinRow.new_with_range(0, 1_000_000, 1)
        self.min_row.set_title("At least this many MB")
        self.min_row.set_digits(0)
        self.min_row.connect("notify::value", self._on_bounds)
        box.append(self.min_row)

        self.max_row = Adw.SpinRow.new_with_range(0, 1_000_000, 1)
        self.max_row.set_title("At most this many MB")
        self.max_row.set_digits(0)
        self.max_row.connect("notify::value", self._on_bounds)
        box.append(self.max_row)

        self.days_row = Adw.SpinRow.new_with_range(0, 3650, 1)
        self.days_row.set_title("Only files newer than (days)")
        self.days_row.set_digits(0)
        self.days_row.connect("notify::value", self._on_bounds)
        box.append(self.days_row)

        self.exclude_row = Adw.EntryRow(
            title="Never match names matching (regex, optional)")
        self.exclude_row.connect("changed", self._on_exclude)
        box.append(self.exclude_row)

        self.enabled_row = Adw.SwitchRow(title="Rule enabled")
        self.enabled_row.set_active(True)
        self.enabled_row.connect("notify::active", self._on_enabled)
        box.append(self.enabled_row)

        self.match_all_row = Adw.SwitchRow(
            title="Require every condition",
            subtitle="Off matches any condition (OR); on matches all (AND).")
        self.match_all_row.connect("notify::active", self._on_match_all)
        box.append(self.match_all_row)
        c.append(box)

        back = Gtk.Button(label="Back")
        back.connect("clicked", lambda _b: self.view.pop())
        nxt = Gtk.Button(label="Next")
        nxt.add_css_class("suggested-action")
        nxt.connect("clicked", lambda _b: self._enter_step5())
        self.page4 = self._page(WIZARD_STEPS[3], c, [back], [nxt])

    def _on_bounds(self, *_):
        self.state["min_mb"] = self.min_row.get_value() or None
        self.state["max_mb"] = self.max_row.get_value() or None
        self.state["days"] = int(self.days_row.get_value()) or None

    def _on_exclude(self, row):
        self.state["exclude"] = row.get_text().strip()

    def _on_enabled(self, row, _param):
        self.state["enabled"] = row.get_active()

    def _on_match_all(self, row, _param):
        self.state["match_all"] = row.get_active()

    # ---- step 5: priority & scope ---------------------------------------
    def _build_step5(self):
        c = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self._pad(c)
        c.append(self._heading(WIZARD_STEPS[4]))
        c.append(_dim("Rules are tried top to bottom; the first one that matches "
                      "wins. Move your new rule up to give it priority."))

        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        up = Gtk.Button(label="Move up")
        up.connect("clicked", lambda _b: self._move_priority(-1))
        down = Gtk.Button(label="Move down")
        down.connect("clicked", lambda _b: self._move_priority(1))
        nav.append(up)
        nav.append(down)
        c.append(nav)

        self.priority_list = _listbox()
        c.append(self.priority_list)
        self.priority_warn = _dim("")
        c.append(self.priority_warn)

        back = Gtk.Button(label="Back")
        back.connect("clicked", lambda _b: self.view.pop())
        nxt = Gtk.Button(label="Next")
        nxt.add_css_class("suggested-action")
        nxt.connect("clicked", lambda _b: self._enter_step6())
        self.page5 = self._page(WIZARD_STEPS[4], c, [back], [nxt])

    def _move_priority(self, delta):
        self._insert_index = max(0, min(self._insert_index + delta,
                                        len(self.existing)))
        self._refresh_priority()

    def _ordered_rules(self):
        idx = max(0, min(self._insert_index, len(self.existing)))
        self._insert_index = idx
        ordered = []
        for i, cat in enumerate(self.existing):
            if i == idx:
                ordered.append(("new", self._rule_dict()))
            ordered.append(("old", cat))
        if idx >= len(self.existing):
            ordered.append(("new", self._rule_dict()))
        return ordered

    def _refresh_priority(self):
        if not hasattr(self, "priority_list"):
            return
        self._clear_box(self.priority_list)
        ordered = self._ordered_rules()
        for pos, (kind, cat) in enumerate(ordered, start=1):
            row = Adw.ActionRow(
                title=f"{pos}.  {cat.get('name') or '(new rule)'}")
            row.set_subtitle(describe_rule(cat) or "no matchers")
            if kind == "new":
                row.add_suffix(Gtk.Label(label="NEW"))
            else:
                row.add_suffix(Gtk.Label(label="existing"))
            self.priority_list.append(row)
        self._update_priority_warning(ordered)

    def _update_priority_warning(self, ordered):
        names = self._scan()[:200]
        if not names:
            self.priority_warn.set_text("No files to check against yet.")
            return
        new_rule = self._rule_dict()
        folder = new_rule.get("name") or "Wizard"
        new_rule["name"] = folder
        raw = [new_rule if kind == "new" else cat for kind, cat in ordered]
        try:
            alone = self.win.sorter.classify(names, rules=[new_rule])
            together = self.win.sorter.classify(names, rules=raw)
        except Exception:
            self.priority_warn.set_text("")
            return
        mine = [n for n in names if alone.get(n) == folder]
        stolen = [n for n in mine if together.get(n) != folder]
        if not mine:
            self.priority_warn.set_text("No files in this folder match yet "
                                        "(fine if it's for files yet to come).")
        elif stolen:
            self.priority_warn.set_text(
                f"Heads up: {len(stolen)} of {len(mine)} matching files would be "
                "caught by an earlier rule first.")
        else:
            self.priority_warn.set_text(
                f"Good — all {len(mine)} matching files reach this rule.")

    # ---- step 6: review with proof --------------------------------------
    def _build_step6(self):
        c = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self._pad(c)
        c.append(self._heading(WIZARD_STEPS[5]))

        card = _listbox()
        self.review_name = Adw.ActionRow(title="Destination")
        self.review_sentence = Adw.ActionRow(title="Reads as")
        self.review_sentence.set_subtitle_lines(3)
        self.review_position = Adw.ActionRow(title="Priority")
        for row in (self.review_name, self.review_sentence, self.review_position):
            card.append(row)
        c.append(card)

        self.anim_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                   spacing=6)
        c.append(self.anim_holder)

        back = Gtk.Button(label="Back")
        back.connect("clicked", lambda _b: self.view.pop())
        again = Gtk.Button(label="Save & add another")
        again.connect("clicked", lambda _b: self._apply(True))
        save = Gtk.Button(label="Save rule")
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda _b: self._apply(False))
        self.page6 = self._page(WIZARD_STEPS[5], c, [back], [again, save])

    def _enter_step6(self):
        rule = self._rule_dict()
        folder = rule.get("name") or "(choose a folder)"
        self.review_name.set_subtitle(f"{folder}/")
        normalized = normalize_rule(rule) or rule
        self.review_sentence.set_subtitle(describe_rule(normalized) or "—")
        idx = max(0, min(self._insert_index, len(self.existing)))
        self.review_position.set_subtitle(
            f"position {idx + 1} of {len(self.existing) + 1} (first match wins)")
        self._build_animation()
        self.view.push(self.page6)

    def _build_animation(self):
        self._clear_box(self.anim_holder)
        names = self._scan()
        rule = self._rule_dict()
        folder = rule.get("name") or "Wizard"
        rule["name"] = folder
        matched = []
        if names:
            try:
                result = self.win.sorter.classify(names, rules=[rule])
                matched = [n for n in names if result.get(n) == folder]
            except Exception:
                matched = []
        shown = matched[:6]
        for name in names:
            if len(shown) >= 8:
                break
            if name not in shown:
                shown.append(name)
        self.anim_holder.append(SortPreview(shown, matched, folder))
        if names:
            caption = (f"{len(matched)} of {len(names)} files in this folder "
                       "would sort here.")
        else:
            caption = "No files to show yet."
        self.anim_holder.append(_dim(caption))

    # ---- save -----------------------------------------------------------
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
            self.win.toast("That regex isn't valid")
            return
        self.win._ensure_custom(self.root)
        rules, _ = self.cfg.rules_for(self.root)
        rules = [c for c in rules if c.get("name") != folder]
        idx = max(0, min(self._insert_index, len(rules)))
        rules.insert(idx, normalize_rule(rule))
        self.win._save_rules(self.root, rules,
                             toast=f"Saved {folder}/ to this folder")
        if save_another:
            self._reset_for_another()
        else:
            self.dialog.close()

    def _reset_for_another(self):
        self._files = None
        self.state.update({
            "recipe": None, "exts": [], "name_mode": "none", "name_text": "",
            "folder": "", "min_mb": None, "max_mb": None, "days": None,
            "exclude": "", "enabled": True, "match_all": False,
        })
        self.name_entry.set_text("")
        for btn in self.name_buttons.values():
            btn.set_active(False)
        self.folder_entry.set_text("")
        self.min_row.set_value(0)
        self.max_row.set_value(0)
        self.days_row.set_value(0)
        self.exclude_row.set_text("")
        self.enabled_row.set_active(True)
        self.match_all_row.set_active(False)
        self._insert_index = len(self.cfg.rules_for(self.root)[0])
        self.view.pop_to_page(self.page1)

    # ---- suggest rules --------------------------------------------------
    def _build_suggest(self):
        c = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self._pad(c)
        c.append(self._heading("Rules I can suggest"))
        self.suggest_intro = _dim("")
        c.append(self.suggest_intro)
        self.suggest_list = _listbox()
        c.append(self.suggest_list)

        back = Gtk.Button(label="Back")
        back.connect("clicked", lambda _b: self.view.pop())
        add = Gtk.Button(label="Add selected rules")
        add.add_css_class("suggested-action")
        add.connect("clicked", self._add_suggestions)
        self.page_suggest = self._page("Suggest rules", c, [back], [add])

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
                label=f"{folder}/   ←   " + ", ".join("." + e for e in exts))
            check.set_active(True)
            self._suggest_checks.append((check, folder, exts))
            self.suggest_list.append(check)
        self.suggest_intro.set_text(
            f"Based on {len(self._scan())} files, here are {len(groups)} rules "
            "I can add. Untick anything you don't want.")
        self.view.push(self.page_suggest)

    @staticmethod
    def _recipe_folder(ext):
        for _key, title, _sub, _icon, exts in WIZARD_RECIPES:
            if ext in exts:
                return title
        return ext.capitalize()

    def _add_suggestions(self, _btn):
        added = 0
        self.win._ensure_custom(self.root)
        rules, _ = self.cfg.rules_for(self.root)
        have = {c.get("name") for c in rules}
        for check, folder, exts in self._suggest_checks:
            if not check.get_active() or folder in have:
                continue
            rules.append(normalize_rule({"name": folder,
                                         "extensions": list(exts)}))
            have.add(folder)
            added += 1
        if not added:
            self.win.toast("Nothing new to add")
            return
        self.win._save_rules(self.root, rules, toast=f"Added {added} rule(s)")
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
        self.dialog.set_content_width(1000)
        self.dialog.set_content_height(820)

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

        self._color = self.original.get("color") or None
        self.color_row = Adw.ActionRow(
            title="Accent colour",
            subtitle="Optional \u2014 shows a coloured dot beside this rule")
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

    @staticmethod
    def _hex(rgba) -> str:
        return "#{:02x}{:02x}{:02x}".format(
            round(rgba.red * 255), round(rgba.green * 255),
            round(rgba.blue * 255))

    def _on_color_picked(self, *_):
        if getattr(self, "_clearing_color", False):
            return
        rgba = self.color_btn.get_rgba()
        self._color = self._hex(rgba) if rgba is not None else None
        self._preview()

    def _clear_color(self, _btn):
        self._clearing_color = True
        try:
            self.color_btn.set_rgba(Gdk.RGBA())
        finally:
            self._clearing_color = False
        self._color = None
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
        if self.original.get("icon"):
            rule["icon"] = self.original["icon"]
        if self._color:
            rule["color"] = self._color
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


class RestoreDialog:
    """Preview-then-commit restore for the journal entries matching a filter.

    Shows exactly what :meth:`Sorter.undo` would do — sizes, collisions and
    entries that can no longer be restored — before the user commits, so the
    dangerous action always has a dry run in front of it. The conflict
    strategy (rename / overwrite / skip) re-plans live.
    """

    _CONFLICTS = [("Rename on clash", "rename"),
                  ("Overwrite", "overwrite"),
                  ("Skip occupied", "skip")]
    _HINT = {
        "rename": "Occupied names get a “(1)” suffix — nothing is overwritten.",
        "overwrite": "Occupied names are replaced.",
        "skip": "Occupied names are left alone (entries stay in the journal).",
    }

    def __init__(self, window: MainWindow, name: str | None = None,
                 rule: str | None = None, ext: str | None = None,
                 min_size: int | None = None, max_size: int | None = None):
        self.win = window
        self.sorter = window.sorter
        self.name = name
        self.rule = rule
        self.ext = ext
        self.min_size = min_size
        self.max_size = max_size
        self.on_conflict = "rename"

        self.dialog = Adw.Dialog(title="Restore files")
        self.dialog.set_content_width(760)
        self.dialog.set_content_height(640)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for setter in (box.set_margin_top, box.set_margin_start,
                       box.set_margin_end):
            setter(18)
        box.set_margin_bottom(8)

        box.append(_heading("Restore preview"))
        self.scope_label = _dim("")
        box.append(self.scope_label)
        self.hint_label = _dim("")
        box.append(self.hint_label)

        self.listing = _listbox()
        box.append(_scroll(self.listing))

        outer.append(box)
        outer.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for setter in (footer.set_margin_top, footer.set_margin_bottom,
                       footer.set_margin_start, footer.set_margin_end):
            setter(14)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.dialog.close())
        self.conflict = Gtk.DropDown.new_from_strings(
            [label for label, _value in self._CONFLICTS])
        self.conflict.set_tooltip_text(
            "What to do when the original name is taken")
        self.conflict.connect("notify::selected",
                              lambda *_a: self._on_conflict())
        footer.append(self.conflict)
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

    def _on_conflict(self):
        self.on_conflict = self._CONFLICTS[self.conflict.get_selected()][1]
        self._build_plan()

    def _build_plan(self):
        loaded = self.sorter._read_journal()
        to_undo = self.sorter._select(
            loaded, rule=self.rule, name=self.name, ext=self.ext,
            min_size=self.min_size, max_size=self.max_size)
        self.plan = self.sorter.restore_plan(to_undo, on_conflict=self.on_conflict)
        actionable = [p for p in self.plan
                      if p["status"] in ("restore", "collision", "overwrite")]

        scope = []
        if self.name:
            scope.append(f"name like “{self.name}”")
        if self.rule:
            scope.append(f"rule “{self.rule}”")
        if self.ext:
            scope.append(f"type {self.ext}")
        self.scope_label.set_text(
            f"{len(actionable)} file(s) can be restored"
            + (f"  ·  {', '.join(scope)}" if scope else ""))
        self.hint_label.set_text(self._HINT[self.on_conflict])
        self.restore.set_label(f"Restore {len(actionable)} file(s)")
        self.restore.set_sensitive(bool(actionable))

        self.listing.remove_all()
        for item in self.plan:
            src_p, dest_p = item["src"], item["dest"]
            status = item["status"]
            if status == "missing":
                note = f"missing from {dest_p.parent.name}/"
            elif status == "already":
                note = "already back in place"
            elif status == "skip":
                note = f"kept — {item['base'].name} is occupied"
            else:
                parts = [f"from {dest_p.parent.name}/"]
                if status == "collision":
                    parts.append(f"name taken → restore as {item['target'].name}")
                elif status == "overwrite":
                    parts.append(f"replaces {item['base'].name}")
                if item["changed"]:
                    parts.append("size changed")
                note = " · ".join(parts)
            row = Adw.ActionRow(title=src_p.name, subtitle=note)
            size = item.get("size")
            if isinstance(size, int):
                row.add_suffix(Gtk.Label(label=_human_size(size)))
            self.listing.append(row)

    def open(self):
        self.dialog.present(self.win)

    def _restore(self, _btn):
        n = self.sorter.undo(name=self.name, rule=self.rule, ext=self.ext,
                             min_size=self.min_size, max_size=self.max_size,
                             on_conflict=self.on_conflict)
        self.dialog.close()
        self.win.toast(f"Restored {n} file(s)")
        self.win.refresh_activity()


def run(cfg: Config, migrated: bool = False) -> int:
    Adw.init()
    app = FunnelApp(cfg, migrated=migrated)
    return app.run([])
