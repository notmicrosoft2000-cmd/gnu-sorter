"""The Funnel desk: context rail, three sections and shared actions."""

from __future__ import annotations

import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

from .. import APP_NAME, CREATOR, ISSUES, REPO, WEBSITE, __version__  # noqa: E402
from ..config import PRESETS  # noqa: E402
from ..engine import expand  # noqa: E402
from .views import build_inputs, build_log, build_plan  # noqa: E402
from .widgets import dim, eyebrow  # noqa: E402

_SECTIONS = (
    ("rules", "Plan"),
    ("inputs", "Inputs"),
    ("activity", "Log"),
)
_SECTION_ALIASES = {
    "plan": "rules", "rules": "rules", "sheet": "rules",
    "inputs": "inputs", "folders": "inputs",
    "log": "activity", "activity": "activity", "history": "activity",
}
_SECTION_ICONS = {
    "rules": "folder-symbolic",
    "inputs": "folder-open-symbolic",
    "activity": "document-open-recent-symbolic",
}


def _root_name(root: str | None) -> str:
    if not root:
        return "Shared defaults"
    path = expand(root)
    return path.name or str(path)


def _iconed_button(label: str, icon: str, *css_classes: str) -> Gtk.Button:
    """A flat button that keeps its label text and shows a symbolic icon."""
    button = Gtk.Button()
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    box.append(Gtk.Image.new_from_icon_name(icon))
    text = Gtk.Label(label=label, xalign=0)
    text.set_valign(Gtk.Align.CENTER)
    box.append(text)
    button.set_child(box)
    for css_class in css_classes:
        button.add_css_class(css_class)
    return button


class MainWindow(Adw.ApplicationWindow):
    """One window with two independent axes: context and section."""

    def __init__(self, app, cfg_or_controller=None, sorter=None, controller=None):
        super().__init__(application=app)
        self.app = app
        if controller is None:
            controller = getattr(app, "controller", None)
        if controller is None:
            from .controller import DeskController
            cfg = cfg_or_controller if hasattr(cfg_or_controller, "load_settings") \
                else getattr(app, "cfg", None)
            controller = DeskController(app, cfg)
        self.controller = controller
        self.cfg = getattr(controller, "cfg", None) or cfg_or_controller or getattr(app, "cfg", None)
        self.sorter = getattr(controller, "sorter", None) or sorter

        self._loading = False
        self._sweeping = False
        self._prefs = None
        self._refresh_plan = None
        self._refresh_inputs = None
        self._refresh_activity = None
        self.activity_search = None
        self.page_banners: dict[str, Adw.Banner] = {}

        self.set_title(APP_NAME)
        try:
            self.set_icon_name("gnu-sorter")
        except Exception:
            pass
        self.set_default_size(1040, 720)

        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)

        self._add_window_actions()
        self._build_banners()

        self.stack = Gtk.Stack()
        self.stack.set_name("desk-stack")
        self.stack.set_vexpand(True)
        self.stack.set_hexpand(True)
        self.stack.connect("notify::visible-child-name", self._on_stack_changed)
        for name, builder in (("rules", build_plan), ("inputs", build_inputs),
                              ("activity", build_log)):
            page = builder(self)
            page.set_name(name)
            self.stack.add_named(page, name)

        rail = self._build_rail()
        content = self._build_content()
        shell = self._assemble(rail, content)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.banner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for banner in self.page_banners.values():
            self.banner_box.append(banner)
        body.append(self.banner_box)
        body.append(shell)
        self.toasts.set_child(body)

        self._connect_controller()
        if self.controller.roots():
            self.current_root = self.controller.roots()[0]
            self.navigate("rules")
        else:
            self.current_root = None
            self.navigate("inputs")
        self._refresh_all()
        self._on_stack_changed()

    # ------------------------------------------------------------ context --
    @property
    def current_root(self) -> str | None:
        return getattr(self.controller, "current_root", None)

    @current_root.setter
    def current_root(self, value: str | None) -> None:
        try:
            self.controller.current_root = value
        except Exception:
            pass
        self._after_root_changed()

    # --------------------------------------------------------------- shell --
    def _build_banners(self) -> None:
        for name in ("rules", "inputs", "activity"):
            banner = Adw.Banner()
            banner.set_revealed(False)
            self.page_banners[name] = banner

    def _rail_button(self, label: str,
                     icon: str | None = None) -> Gtk.Button:
        button = Gtk.Button()
        button.add_css_class("flat")
        button.set_halign(Gtk.Align.FILL)
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        inner.set_margin_top(6)
        inner.set_margin_bottom(6)
        inner.set_margin_start(6)
        inner.set_margin_end(6)
        if icon is not None:
            image = Gtk.Image.new_from_icon_name(icon)
            image.set_valign(Gtk.Align.CENTER)
            inner.append(image)
        name_label = Gtk.Label(label=label, xalign=0, hexpand=True, ellipsize=3)
        inner.append(name_label)
        button.set_child(inner)
        return button

    def _build_rail(self) -> Gtk.Widget:
        rail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        rail.add_css_class("funnel-rail")
        rail.set_size_request(240, -1)
        rail.set_margin_top(12)
        rail.set_margin_bottom(12)
        rail.set_margin_start(12)
        rail.set_margin_end(12)

        rail.append(eyebrow("Contexts"))
        self.shared_button = self._rail_button(
            "Shared defaults", icon="emblem-default-symbolic")
        self.shared_button.connect("clicked", self._on_shared_clicked)
        rail.append(self.shared_button)

        self.rules_folders = Gtk.ListBox()
        self.rules_folders.set_name("watched-folders")
        self.rules_folders.add_css_class("boxed-list")
        self.rules_folders.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.rules_folders.connect("row-selected", self._on_folder_selected)
        rail.append(self._rail_scroll(self.rules_folders))

        add = _iconed_button("+ Add folder", "list-add-symbolic", "flat")
        add.connect("clicked", lambda *_: self._add_folder())
        rail.append(add)

        rail.append(Gtk.Label(label="", hexpand=True, vexpand=True))
        rail.append(eyebrow("Sections"))
        self.section_buttons: dict[str, Gtk.Button] = {}
        for name, label in _SECTIONS:
            button = self._rail_button(label, icon=_SECTION_ICONS[name])
            button.connect("clicked", lambda *_a, section=name: self.navigate(section))
            self.section_buttons[name] = button
            rail.append(button)

        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        footer.set_margin_top(12)
        settings = _iconed_button("Settings", "preferences-system-symbolic", "flat")
        settings.connect("clicked", lambda *_: self.show_preferences())
        footer.append(settings)
        self.update_status = dim("Automatic update check is on")
        self.update_status.add_css_class("caption")
        footer.append(self.update_status)
        rail.append(footer)
        return rail

    @staticmethod
    def _rail_scroll(child: Gtk.Widget) -> Gtk.Widget:
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(child)
        return scroll

    def _build_content(self) -> Gtk.Widget:
        self.context_name = Gtk.Label(label="Shared defaults", xalign=0,
                                      ellipsize=3, hexpand=True)
        title = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title.append(self.context_name)

        header = Adw.HeaderBar()
        header.set_title_widget(title)

        sort_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        sort_box.append(Gtk.Image.new_from_icon_name("emblem-documents-symbolic"))
        self.sort_label = Gtk.Label(label="Sort", xalign=0)
        sort_box.append(self.sort_label)
        self.sort_btn = Gtk.Button()
        self.sort_btn.set_child(sort_box)
        self.sort_btn.add_css_class("suggested-action")
        self.sort_btn.set_tooltip_text("Sort every watched folder now")
        self.sort_btn.connect("clicked", self.on_sort_now)
        header.pack_start(self.sort_btn)
        self.sort_spinner = Gtk.Spinner(valign=Gtk.Align.CENTER)
        self.sort_spinner.set_visible(False)
        header.pack_start(self.sort_spinner)

        menu = Gio.Menu()
        quick = Gio.Menu()
        quick.append("Sort now", "app.sort-now")
        menu.append_section(None, quick)
        tail = Gio.Menu()
        tail.append("Preferences", "win.preferences")
        tail.append("Keyboard shortcuts", "win.shortcuts")
        tail.append("Check for updates", "app.update")
        tail.append("About Funnel", "app.about")
        tail.append("Quit", "app.quit")
        menu.append_section(None, tail)
        header.pack_end(Gtk.MenuButton(menu_model=menu,
                                       icon_name="open-menu-symbolic",
                                       tooltip_text="Main menu"))

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(self.stack)
        return toolbar

    def _assemble(self, rail: Gtk.Widget, content: Gtk.Widget) -> Gtk.Widget:
        if hasattr(Adw, "NavigationSplitView"):
            try:
                split = Adw.NavigationSplitView()
                sidebar_page = Adw.NavigationPage(title="Desk")
                sidebar_page.set_child(rail)
                content_page = Adw.NavigationPage(title=APP_NAME)
                content_page.set_child(content)
                split.set_sidebar(sidebar_page)
                split.set_content(content_page)
                split.set_min_sidebar_width(220)
                split.set_max_sidebar_width(280)
                self.split_view = split
                return split
            except Exception:
                pass
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_start_child(rail)
        paned.set_end_child(content)
        paned.set_position(240)
        paned.set_resize_start_child(False)
        self.split_view = paned
        return paned

    # ------------------------------------------------------------- actions --
    def _dispatch(self, attribute: str, *args):
        callback = getattr(self, attribute, None)
        if callable(callback):
            callback(*args)
        else:
            self.toast("This action is not available for the current context")

    def _add_window_actions(self) -> None:
        handlers = {
            "advanced": lambda *_: self.open_advanced(),
            "preferences": lambda *_: self.show_preferences(),
            "shortcuts": lambda *_: self.show_shortcuts(),
            "suggest-rules": lambda *_: self._dispatch("_on_suggest_rules"),
            "import-rules": lambda *_: self._dispatch("_on_import_rules"),
            "export-rules": lambda *_: self._dispatch("_on_export_rules"),
            "use-shared-defaults": lambda *_: self._dispatch("_on_use_shared_defaults"),
            "reset-rules": lambda *_: self._dispatch("_on_use_shared_defaults"),
            "journal-export": lambda *_: self._dispatch("_journal_export"),
            "journal-import": lambda *_: self._dispatch("_journal_import"),
            "journal-backup": lambda *_: self._dispatch("_journal_backup"),
            "journal-repair": lambda *_: self._dispatch("_journal_repair"),
        }
        for i, (_label, _rules) in enumerate(PRESETS):
            handlers[f"preset-{i}"] = lambda *_a, index=i: self._dispatch("_on_preset", index)
        for name, handler in handlers.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)
        for name, accel in (("win.preferences", "<Control>comma"),
                            ("win.shortcuts", "<Control>question"),
                            ("app.sort-now", "<Control>n")):
            try:
                self.app.set_accels_for_action(name, [accel])
            except Exception:
                pass

    def _connect_controller(self) -> None:
        if not hasattr(self.controller, "connect"):
            return
        gtype = getattr(type(self.controller), "__gtype__", None)
        if gtype is None:
            return
        handlers = {
            "roots-changed": self._on_roots_changed,
            "rules-changed": self._on_rules_changed,
            "journal-changed": self._controller_journal_changed,
            "sweep-started": self._controller_sweep_started,
            "sweep-finished": self._controller_sweep_finished,
            "settings-changed": self._controller_settings_changed,
            "watcher-changed": self._on_watcher_changed,
        }
        for signal, handler in handlers.items():
            try:
                if GObject.signal_lookup(signal, gtype):
                    self.controller.connect(signal, handler)
            except Exception:
                pass

    def _on_roots_changed(self, *_args):
        roots = list(self.controller.roots() or [])
        if self.current_root not in roots:
            self.current_root = roots[0] if roots else None
        self._refresh_all()

    def _on_rules_changed(self, *_args):
        self._refresh_rail()
        for callback in (self._refresh_plan, self._refresh_inputs):
            if callable(callback):
                callback()

    def _controller_journal_changed(self, *_args):
        GLib.idle_add(self._refresh_log)

    def _controller_sweep_started(self, *_args):
        GLib.idle_add(self._set_sorting, True)

    def _controller_sweep_finished(self, *_args):
        GLib.idle_add(self._set_sorting, False)
        GLib.idle_add(self._refresh_log)

    def _controller_settings_changed(self, *_args):
        GLib.idle_add(self._refresh_header)

    def _on_watcher_changed(self, *_args):
        GLib.idle_add(self._refresh_inputs)

    def _refresh_log(self):
        if callable(self._refresh_activity):
            self._refresh_activity()

    def _refresh_all(self):
        self._refresh_rail()
        self._refresh_header()
        for callback in (self._refresh_plan, self._refresh_inputs, self._refresh_activity):
            if callable(callback):
                callback()

    def _after_root_changed(self):
        if not hasattr(self, "rules_folders"):
            return
        self._refresh_rail()
        self._refresh_header()
        for callback in (self._refresh_plan, self._refresh_activity):
            if callable(callback):
                callback()

    def _refresh_rail(self) -> None:
        if not hasattr(self, "rules_folders"):
            return
        self._loading = True
        try:
            roots = list(self.controller.roots() or [])
            self.rules_folders.remove_all()
            for path in roots:
                row = Gtk.ListBoxRow()
                row._path = path
                line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                line.set_margin_top(6)
                line.set_margin_bottom(6)
                line.set_margin_start(6)
                line.set_margin_end(6)
                name = Gtk.Label(label=_root_name(path), xalign=0,
                                 hexpand=True, ellipsize=3)
                line.append(name)
                if self.controller.is_custom(path):
                    badge = Gtk.Label(label="Own", xalign=1)
                    badge.add_css_class("caption")
                    line.append(badge)
                row.set_child(line)
                self.rules_folders.append(row)
                if path == self.current_root:
                    self.rules_folders.select_row(row)
            if self.current_root not in roots:
                self.rules_folders.select_row(None)
        finally:
            self._loading = False
        self._refresh_section_marker()

    def _refresh_header(self) -> None:
        if not hasattr(self, "context_name"):
            return
        root = self.current_root
        self.context_name.set_label(_root_name(root))
        try:
            settings = self.controller.settings() or {}
            dry = bool(settings.get("dry_run_default", False))
            checking = bool(settings.get("check_for_updates", True))
        except Exception:
            dry, checking = False, True
        self.sort_label.set_label("Preview" if dry else "Sort")
        self.update_status.set_label(
            "Automatic update check is on" if checking
            else "Automatic update check is off")

    def _refresh_section_marker(self) -> None:
        if not hasattr(self, "section_buttons"):
            return
        for name, button in self.section_buttons.items():
            if name == self.stack.get_visible_child_name():
                button.add_css_class("funnel-rail-current")
            else:
                button.remove_css_class("funnel-rail-current")
        if self.current_root is None:
            self.shared_button.add_css_class("funnel-rail-current")
        else:
            self.shared_button.remove_css_class("funnel-rail-current")

    def _on_stack_changed(self, *_args):
        current = self.stack.get_visible_child_name() or "rules"
        for name, banner in self.page_banners.items():
            banner.set_visible(name == current)
        self._refresh_section_marker()

    # ---------------------------------------------------------- navigation --
    def navigate(self, section: str) -> None:
        name = _SECTION_ALIASES.get(str(section).lower(), "rules")
        self.stack.set_visible_child_name(name)
        self._on_stack_changed()

    def _on_shared_clicked(self, *_args):
        self.current_root = None
        self.navigate("rules")

    def _on_folder_selected(self, _listbox, row):
        if self._loading or row is None:
            return
        path = getattr(row, "_path", None)
        if not path:
            return
        self.current_root = path
        self.navigate("rules")

    def _add_folder(self):
        self._dispatch("_on_add_folder_inputs")

    # ------------------------------------------------------------ actions --
    def toast(self, message: str) -> None:
        self.toasts.add_toast(Adw.Toast.new(str(message)))

    def show_banner(self, section: str, message: str) -> None:
        banner = self.page_banners.get(section)
        if banner is not None:
            banner.set_title(message)
            banner.set_revealed(True)

    def _set_sorting(self, active: bool) -> None:
        self.sort_btn.set_sensitive(not active)
        self.sort_label.set_label("Sorting" if active else
                                  ("Preview" if self._sort_is_preview() else "Sort"))
        self.sort_spinner.set_visible(active)
        if active:
            self.sort_spinner.start()
        else:
            self.sort_spinner.stop()

    def _sort_is_preview(self) -> bool:
        try:
            return bool((self.controller.settings() or {}).get("dry_run_default", False))
        except Exception:
            return False

    def on_sort_now(self, _button=None) -> None:
        if self._sweeping or getattr(self.controller, "sweeping", False):
            return
        self._sweeping = True
        self._set_sorting(True)
        self.toast("Sorting" if not self._sort_is_preview() else "Previewing")

        def work():
            try:
                moved = self.controller.run_sweep()
            except Exception as exc:
                GLib.idle_add(self._sweep_finished, None, str(exc))
                return
            GLib.idle_add(self._sweep_finished, moved, None)

        threading.Thread(target=work, daemon=True).start()

    def _sweep_finished(self, moved, error):
        self._sweeping = False
        self._set_sorting(False)
        self._refresh_log()
        if error is not None:
            self.toast(f"Sorting failed: {error}")
            return False
        extra = ""
        stats = (getattr(self.controller, "last_sweep", None)
                 or getattr(self.controller, "last_sweep_stats", None)
                 or getattr(self.controller, "last_stats", None))
        if isinstance(stats, dict) and stats.get("held"):
            extra = f", {stats['held']} file(s) held"
        count = moved or 0
        self.toast(f"Sorted {count} file(s){extra}" if count
                   else f"Nothing to sort right now{extra}")
        return False

    # ------------------------------------------------------------- dialogs --
    def show_preferences(self):
        if self._prefs is None:
            from .settings import PreferencesDialog
            self._prefs = PreferencesDialog(self)
        dialog = getattr(self._prefs, "dialog", None) or self._prefs
        dialog.present(self)

    def open_settings(self):
        self.show_preferences()

    def show_shortcuts(self):
        try:
            dialog = Adw.ShortcutsDialog()
            section = Adw.ShortcutsSection(title="General")
            for title, accel in (("Sort now", "<Control>n"),
                                 ("Preferences", "<Control>comma"),
                                 ("Keyboard shortcuts", "<Control>question"),
                                 ("Quit", "<Control>q")):
                section.add(Adw.ShortcutsItem(title=title, accelerator=accel))
            dialog.add(section)
            dialog.present(self)
        except Exception:
            self.toast("Shortcuts: Ctrl+N sort, Ctrl+comma preferences, Ctrl+Q quit")

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
        about.set_copyright(f"2026 {CREATOR}")
        about.add_link("Source code", REPO)
        about.present(self)

    def open_wizard(self, root=None):
        from .wizard import RuleWizard
        wizard = RuleWizard(self, self.current_root if root is None else root)
        wizard.open()
        return wizard

    def open_advanced(self, root=None, rule=None):
        from .advanced import AdvancedDialog
        dialog = AdvancedDialog(self, self.current_root if root is None else root,
                                rule=rule)
        dialog.open()
        return dialog

    def open_restore(self, **filters):
        from .restore import RestoreDialog
        dialog = RestoreDialog(self, **filters)
        dialog.open()
        return dialog

    def open_update_result(self, current=None, latest=None, needed=False,
                           kind=None, **kwargs):
        from . import updater
        info = kwargs.pop("info", None)
        if isinstance(info, dict):
            current = info.get("current", current)
            latest = info.get("latest", latest)
            needed = info.get("needed", needed)
            kind = info.get("kind", kind)
        if current is not None and isinstance(current, dict):
            info, current = current, None
            latest = info.get("latest", latest)
            needed = info.get("needed", needed)
            kind = info.get("kind", kind)
        if latest is None:
            latest = kwargs.get("version")
        flatpak = (kind == "flatpak" or updater.install_kind() == "flatpak")
        if not needed:
            self.toast(f"Up to date, version {current or __version__}")
            return
        if flatpak:
            message = (f"{latest} is available. Run "
                       f"`flatpak update {updater.FLATPAK_ID}`")
            dialog = Adw.AlertDialog(heading="Update available", body=message)
            dialog.add_response("close", "Close")
            dialog.set_default_response("close")
            dialog.choose(self, None, lambda *_a: None)
            return

        def chosen(dialog_, result):
            try:
                response = dialog_.choose_finish(result)
            except GLib.Error:
                return
            if response != "install":
                return

            def install():
                try:
                    ok, message = updater.update(dry_run=False)
                except Exception as exc:
                    ok, message = False, f"Update failed: {exc}"
                GLib.idle_add(self.toast, message)

            threading.Thread(target=install, daemon=True).start()

        dialog = Adw.AlertDialog(
            heading="Update available",
            body=f"{latest} is available. Install this build now?")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("install", "Install")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.choose(self, None, chosen)

    # ---------------------------------------------------- compatibility ---
    def refresh_folders(self):
        if callable(self._refresh_inputs):
            self._refresh_inputs()

    def refresh_rules_folders(self):
        self._refresh_rail()

    def refresh_rules(self):
        if callable(self._refresh_plan):
            self._refresh_plan()

    def refresh_activity(self):
        self._refresh_log()
