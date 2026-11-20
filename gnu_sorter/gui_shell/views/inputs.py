"""Inputs: watched folders and the background watcher."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ... import service  # noqa: E402
from ...engine import expand, fmt  # noqa: E402
from ..widgets import dim, heading, listbox  # noqa: E402


def _folder_name(path: str) -> str:
    expanded = expand(path)
    return expanded.name or str(expanded)


def _iconed_button(label: str, icon: str, *css_classes: str) -> Gtk.Button:
    """A button that keeps its label text and shows a symbolic icon."""
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


def _confirm(window, title: str, body: str, action_label: str, callback) -> None:
    dialog = Adw.AlertDialog(heading=title, body=body)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("apply", action_label)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")
    dialog.choose(window, None, callback)


def build_inputs(window) -> Gtk.Widget:
    """Build the folder list and watcher controls."""
    controller = window.controller
    state: dict = {"loading": False}

    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    outer.set_margin_top(18)
    outer.set_margin_bottom(18)
    outer.set_margin_start(18)
    outer.set_margin_end(18)

    header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    header.append(heading("Inputs"))
    header.append(Gtk.Box(hexpand=True))
    add_button = _iconed_button("Add folder", "list-add-symbolic",
                                "suggested-action")
    add_button.connect("clicked", lambda *_: choose_folder())
    header.append(add_button)
    outer.append(header)
    outer.append(dim("Files are sorted into category folders inside each "
                     "watched folder. Each folder can use its own rules."))

    folders_group = Adw.PreferencesGroup(title="Watched folders")
    folders_list = listbox()
    folders_group.add(folders_list)

    empty = Adw.StatusPage(icon_name="folder-open-symbolic",
                           title="No folders to watch",
                           description="Add a folder to start sorting new files "
                                       "into category folders.")
    empty.add_css_class("funnel-empty")
    empty_button = Gtk.Button(label="Add folder")
    empty_button.add_css_class("suggested-action")
    empty_button.set_halign(Gtk.Align.CENTER)
    empty_button.connect("clicked", lambda *_: choose_folder())
    empty.set_child(empty_button)

    input_stack = Gtk.Stack()
    input_stack.add_named(folders_group, "list")
    input_stack.add_named(empty, "empty")
    outer.append(input_stack)

    watch_group = Adw.PreferencesGroup(
        title="Background watch",
        description="The switch writes a user systemd unit in "
                    "~/.config/systemd/user/gnu-sorter.service.")
    status_row = Adw.ActionRow(title="Watcher status", subtitle="")
    watch_group.add(status_row)

    watch_row = Adw.SwitchRow(title="Watch folders in the background",
                              subtitle="Sort new files while the app is closed")
    watch_group.add(watch_row)

    start_row = Adw.SwitchRow(title="Start on login",
                              subtitle="Run the background watcher automatically")
    watch_group.add(start_row)
    outer.append(watch_group)

    note = dim("Timing and download detection are in Settings.")
    note.add_css_class("caption")
    outer.append(note)

    def choose_folder():
        dialog = Gtk.FileDialog()
        dialog.set_title("Choose a folder to sort")
        dialog.select_folder(window, None, folder_chosen)

    def folder_chosen(dialog, result):
        try:
            gfile = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        if gfile is None or not gfile.get_path():
            return
        path = gfile.get_path()
        try:
            added = controller.add_root(path)
        except Exception as exc:
            window.toast(f"Could not add this folder: {exc}")
            return
        if not added:
            window.toast("Already watching that folder")
            return
        refresh()
        window.current_root = path
        window.navigate("rules")
        window.toast(f"Added {fmt(expand(path))}")

    def ask_remove(path: str):
        def chosen(dialog, result):
            try:
                response = dialog.choose_finish(result)
            except GLib.Error:
                return
            if response != "remove":
                return
            was_selected = window.current_root == path
            controller.remove_root(path)
            if was_selected:
                window.current_root = None
                window.navigate("inputs")
            refresh()
            window.toast(f"Removed {fmt(expand(path))}")

        _confirm(window, "Remove this folder?",
                 f"{_folder_name(path)} will stop being watched. Its rules stay "
                 "on disk.", "Remove", chosen)

    def install_confirm(apply):
        def chosen(dialog, result):
            try:
                response = dialog.choose_finish(result)
            except GLib.Error:
                response = "cancel"
            if response != "install":
                refresh()
                return
            apply()
            refresh()

        dialog = Adw.AlertDialog(
            heading="Install the user service?",
            body="The user systemd unit file is written when background "
                 "watching or start on login is switched on.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("install", "Install")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.choose(window, None, chosen)

    def watch_changed(row, _param):
        if state["loading"]:
            return
        wanted = row.get_active()
        if wanted and not service.is_enabled():
            install_confirm(lambda: controller.set_watch(True))
            return
        controller.set_watch(wanted)
        refresh()

    def start_changed(row, _param):
        if state["loading"]:
            return
        wanted = row.get_active()
        if wanted and not service.is_enabled():
            install_confirm(lambda: controller.set_start_on_login(True))
            return
        controller.set_start_on_login(wanted)
        refresh()

    def refresh():
        state["loading"] = True
        try:
            roots = list(controller.roots() or [])
            folders_list.remove_all()
            for path in roots:
                row = Adw.ActionRow(title=_folder_name(path),
                                    subtitle=fmt(expand(path)))
                row.set_subtitle_lines(1)
                folder_icon = Gtk.Image.new_from_icon_name("folder-symbolic")
                folder_icon.set_valign(Gtk.Align.CENTER)
                row.add_prefix(folder_icon)
                if controller.is_custom(path):
                    badge = Gtk.Label(label="Own copy", xalign=1)
                    badge.add_css_class("caption")
                    badge.add_css_class("success")
                    badge.set_valign(Gtk.Align.CENTER)
                    row.add_suffix(badge)
                enabled = True
                try:
                    for item in controller.cfg.load_roots():
                        if item.get("path") == path:
                            enabled = bool(item.get("enabled", True))
                            break
                except Exception:
                    pass
                sw = Gtk.Switch(active=enabled, valign=Gtk.Align.CENTER)
                sw.set_tooltip_text("Watch this folder")
                sw.connect("notify::active", root_toggled, path)
                row.add_suffix(sw)
                remove = Gtk.Button.new_from_icon_name("user-trash-symbolic")
                remove.add_css_class("flat")
                remove.set_tooltip_text("Remove folder")
                remove.set_valign(Gtk.Align.CENTER)
                remove.connect("clicked", lambda *_a, p=path: ask_remove(p))
                row.add_suffix(remove)
                folders_list.append(row)
            input_stack.set_visible_child_name("list" if roots else "empty")

            try:
                status_row.set_subtitle(controller.watcher_status())
            except Exception as exc:
                status_row.set_subtitle(str(exc))
            try:
                active = bool(service.is_active())
                started = bool(service.is_enabled())
            except Exception:
                active = False
                started = False
            watch_row.set_active(active)
            start_row.set_active(started)
        finally:
            state["loading"] = False

    def root_toggled(switch, _param, path: str):
        if state["loading"]:
            return
        controller.toggle_root(path, switch.get_active())

    watch_row.connect("notify::active", watch_changed)
    start_row.connect("notify::active", start_changed)
    window._refresh_inputs = refresh
    window._on_add_folder_inputs = choose_folder
    refresh()

    clamp = Adw.Clamp()
    clamp.set_maximum_size(640)
    clamp.set_tightening_threshold(480)
    clamp.set_vexpand(True)
    clamp.set_child(outer)
    return clamp
