"""Log: move history, filters and reversal."""

from __future__ import annotations

import os
import re
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from ...engine import fmt  # noqa: E402
from ..widgets import heading, listbox, scroll  # noqa: E402

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmg])?\s*$", re.IGNORECASE)
_MULTIPLIERS = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}


def _parse_size(text: str) -> int | None:
    match = _SIZE_RE.match(text or "")
    if not match:
        return None
    try:
        return int(float(match.group(1)) * _MULTIPLIERS[(match.group(2) or "").lower()])
    except (ValueError, OverflowError):
        return None


def _iconed_button(label: str, icon: str) -> Gtk.Button:
    """A button that keeps its label text and shows a symbolic icon."""
    button = Gtk.Button()
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    box.append(Gtk.Image.new_from_icon_name(icon))
    text = Gtk.Label(label=label, xalign=0)
    text.set_valign(Gtk.Align.CENTER)
    box.append(text)
    button.set_child(box)
    return button


def _sorter_call(window, name: str, *args, **kwargs):
    target = getattr(window.controller, name, None)
    if target is None:
        target = getattr(getattr(window.controller, "sorter", None), name, None)
    if target is None:
        return None
    return target(*args, **kwargs)


def build_log(window) -> Gtk.Widget:
    """Build the journal view and its filter bar."""
    controller = window.controller
    state: dict = {"loading": False}

    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    outer.set_margin_top(18)
    outer.set_margin_bottom(18)
    outer.set_margin_start(18)
    outer.set_margin_end(18)

    header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    header.append(heading("Log"))
    header.append(Gtk.Box(hexpand=True))
    window.activity_search = Gtk.SearchEntry(placeholder_text="Search file name")
    window.activity_search.set_width_chars(18)
    window.activity_search.connect("search-changed", lambda *_: refresh())
    header.append(window.activity_search)
    header.append(Gtk.MenuButton(icon_name="view-more-symbolic",
                                 menu_model=_tools_menu()))
    outer.append(header)

    filters = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    filters.add_css_class("funnel-plate")
    scope_row = Adw.ActionRow(title="Scope", subtitle="Which folders to include")
    scope = Gtk.DropDown.new_from_strings(["This folder", "All folders"])
    scope.set_valign(Gtk.Align.CENTER)
    scope.set_tooltip_text("Journal scope")
    scope.connect("notify::selected", lambda *_: refresh())
    scope_row.add_suffix(scope)
    scope_list = listbox()
    scope_list.append(scope_row)
    filters.append(scope_list)

    filter_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    window.activity_rule_filter = Gtk.DropDown.new_from_strings(["All rules"])
    window.activity_rule_filter.set_tooltip_text("Filter by rule")
    window.activity_ext_filter = Gtk.DropDown.new_from_strings(["All types"])
    window.activity_ext_filter.set_tooltip_text("Filter by file type")
    for dropdown in (window.activity_rule_filter, window.activity_ext_filter):
        try:
            dropdown.set_enable_search(True)
        except AttributeError:
            pass
        dropdown.connect("notify::selected", lambda *_: refresh())
    filter_bar.append(window.activity_rule_filter)
    filter_bar.append(window.activity_ext_filter)
    window.activity_summary = Gtk.Label(label="", xalign=0, hexpand=True)
    window.activity_summary.add_css_class("dim-label")
    filter_bar.append(window.activity_summary)
    filters.append(filter_bar)

    size_expander = Adw.ExpanderRow(title="Size range",
                                    subtitle="K, M and G suffixes are accepted")
    minimum = Adw.EntryRow(title="Minimum size")
    maximum = Adw.EntryRow(title="Maximum size")
    minimum.connect("changed", lambda *_: refresh())
    maximum.connect("changed", lambda *_: refresh())
    size_expander.add_row(minimum)
    size_expander.add_row(maximum)
    size_list = listbox()
    size_list.append(size_expander)
    filters.append(size_list)
    outer.append(filters)

    moves = listbox()
    moves.set_name("log-move-list")
    move_stack = Gtk.Stack()
    move_stack.set_vexpand(True)
    move_stack.add_named(scroll(moves), "list")

    empty = Adw.StatusPage(icon_name="document-open-recent-symbolic",
                           title="Nothing sorted yet",
                           description="Once a file is moved it appears here "
                                       "with its rule and destination.")
    empty.add_css_class("funnel-empty")
    preview_button = Gtk.Button(label="Run preview")
    preview_button.set_halign(Gtk.Align.CENTER)
    preview_button.connect("clicked", lambda *_: window.navigate("rules"))
    empty.set_child(preview_button)
    move_stack.add_named(empty, "empty")
    outer.append(move_stack)

    actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    undo_move = _iconed_button("Undo last move", "edit-undo-symbolic")
    undo_move.connect("clicked", lambda *_: undo_preview("move"))
    undo_sweep = _iconed_button("Undo last sweep", "edit-undo-symbolic")
    undo_sweep.connect("clicked", lambda *_: undo_preview("sweep"))
    restore = _iconed_button("Restore selected filter", "folder-symbolic")
    restore.add_css_class("suggested-action")
    restore.connect("clicked", lambda *_: restore_selected())
    for button in (undo_move, undo_sweep):
        actions.append(button)
    actions.append(Gtk.Box(hexpand=True))
    actions.append(restore)
    outer.append(actions)

    def query() -> str | None:
        text = window.activity_search.get_text().strip()
        return text or None

    def dropdown_value(dropdown: Gtk.DropDown, first: str) -> str | None:
        if dropdown.get_selected() == 0:
            return None
        item = dropdown.get_selected_item()
        return item.get_string() if item is not None and item.get_string() != first else None

    def selected_rule() -> str | None:
        return dropdown_value(window.activity_rule_filter, "All rules")

    def selected_ext() -> str | None:
        return dropdown_value(window.activity_ext_filter, "All types")

    def active_filters() -> dict:
        result = {
            "name": query(),
            "rule": selected_rule(),
            "ext": selected_ext(),
            "min_size": _parse_size(minimum.get_text()),
            "max_size": _parse_size(maximum.get_text()),
        }
        return result

    def in_scope(rows: list[dict]) -> list[dict]:
        if scope.get_selected() != 0 or not window.current_root:
            return rows
        wanted = os.path.abspath(os.path.expanduser(str(window.current_root)))
        return [row for row in rows
                if os.path.abspath(os.path.expanduser(str(row.get("root") or "")))
                == wanted]

    def refresh():
        if state["loading"]:
            return
        state["loading"] = True
        try:
            index = controller.journal_index()
            counts = dict(index.counts() or {})
            rules = ["All rules"] + sorted(counts)
            exts_set = set()
            for _raw, record in index.entries:
                suffix = Path(record.get("src") or "").suffix.lstrip(".").lower()
                if suffix:
                    exts_set.add(suffix)
            _rebuild(window.activity_rule_filter, rules)
            _rebuild(window.activity_ext_filter, ["All types"] + sorted(exts_set))
            if scope.get_selected() == 0 and not window.current_root:
                scope.set_selected(1)

            selectors = active_filters()
            records = in_scope(list(controller.records(limit=200, **selectors) or []))
            moves.remove_all()
            for record in reversed(records):
                source = Path(record.get("src") or "")
                destination = Path(record.get("dest") or "")
                rule = record.get("rule") or "no rule"
                subtitle = (f"{rule} · {fmt(source.parent)} → "
                            f"{fmt(destination.parent)}/")
                row = Adw.ActionRow(title=source.name or "unknown file",
                                    subtitle=subtitle)
                row.set_subtitle_lines(2)
                stamp = Gtk.Label(label=str(record.get("ts") or "").split(" ")[-1])
                stamp.add_css_class("funnel-eyebrow")
                stamp.set_valign(Gtk.Align.CENTER)
                row.add_suffix(stamp)
                moves.append(row)
            move_stack.set_visible_child_name("list" if records else "empty")
            total = len(index)
            shown = len(records)
            window.activity_summary.set_label(
                f"{shown} of {total} move(s)" if shown != total
                else f"{total} move(s)")
            busy = bool(getattr(controller, "sweeping", False))
            undo_move.set_sensitive(not busy)
            undo_sweep.set_sensitive(not busy)
            restore.set_sensitive(not busy)
        finally:
            state["loading"] = False

    def undo_preview(kind: str):
        if getattr(controller, "sweeping", False):
            window.toast("Wait for the current sort to finish")
            return
        batch = None
        if kind == "sweep":
            index = controller.journal_index()
            if not index.entries:
                window.toast("Nothing to undo")
                return
            batch = index.entries[-1][1].get("batch")
            if not batch:
                window.toast("Nothing to undo")
                return
            try:
                count = controller.undo(dry_run=True, batch=batch)
            except Exception as exc:
                window.toast(f"Undo preview failed: {exc}")
                return
        else:
            try:
                count = controller.undo_last_move(dry_run=True)
            except Exception as exc:
                window.toast(f"Undo preview failed: {exc}")
                return
        if not count:
            window.toast("Nothing to undo")
            return
        first = ""
        try:
            rows = in_scope(controller.records(limit=1))
            if rows:
                first = Path(rows[0].get("src") or "").name
        except Exception:
            pass
        detail = f" First file: {first}." if first else ""
        label = "Undo last sweep?" if kind == "sweep" else "Undo last move?"

        def chosen(dialog, result):
            try:
                response = dialog.choose_finish(result)
            except GLib.Error:
                return
            if response != "undo":
                return
            try:
                if kind == "sweep":
                    undone = controller.undo_last_sweep()
                else:
                    undone = controller.undo_last_move()
            except Exception as exc:
                window.toast(f"Undo failed: {exc}")
                return
            window.toast(f"Restored {undone} file(s)" if undone else "Nothing to undo")
            refresh()

        alert = Adw.AlertDialog(
            heading=label,
            body=(f"{count} file(s) would go back to their source folders."
                  f"{detail} Clashes are renamed."))
        alert.add_response("cancel", "Cancel")
        alert.add_response("undo", "Undo")
        alert.set_default_response("cancel")
        alert.set_close_response("cancel")
        alert.choose(window, None, chosen)

    def restore_selected():
        selectors = active_filters()
        if not any(selectors.values()) and scope.get_selected() != 0:
            window.toast("Filter by a file, rule or type, then Restore")
            return
        window.open_restore(
            name=selectors["name"], rule=selectors["rule"],
            ext=selectors["ext"], min_size=selectors["min_size"],
            max_size=selectors["max_size"])

    def journal_tool(name: str, *args, label: str | None = None, **kwargs):
        try:
            result = _sorter_call(window, name, *args, **kwargs)
        except Exception as exc:
            window.toast(f"Journal tool failed: {exc}")
            return
        if result is None:
            window.toast("Journal tools are available from the command line")
            return
        refresh()
        if isinstance(result, int):
            window.toast(f"{result} {label or 'journal entry/entries affected'}")
        elif isinstance(result, Path):
            window.toast(f"Journal backup written to {fmt(result)}")

    def export_journal():
        dialog = Gtk.FileDialog()
        dialog.set_title("Export journal")
        dialog.set_initial_name("funnel-journal.jsonl")
        dialog.save(window, None, export_done)

    def export_done(dialog, result):
        try:
            gfile = dialog.save_finish(result)
        except GLib.Error:
            return
        if gfile is not None and gfile.get_path():
            journal_tool("export_journal", gfile.get_path(), **active_filters())

    def import_journal():
        dialog = Gtk.FileDialog()
        dialog.set_title("Import journal")
        dialog.open(window, None, import_done)

    def import_done(dialog, result):
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        if gfile is not None and gfile.get_path():
            journal_tool("import_journal", gfile.get_path(), merge=True)

    window._journal_export = export_journal
    window._journal_import = import_journal
    window._journal_backup = lambda: journal_tool("backup_journal")
    window._journal_repair = lambda: journal_tool(
        "repair_journal", label="malformed line(s) removed")
    window._refresh_activity = refresh
    refresh()

    clamp = Adw.Clamp()
    clamp.set_maximum_size(880)
    clamp.set_tightening_threshold(560)
    clamp.set_vexpand(True)
    clamp.set_child(outer)
    return clamp


def _rebuild(dropdown: Gtk.DropDown, items: list[str]) -> None:
    model = dropdown.get_model()
    current = [model.get_string(i) for i in range(model.get_n_items())] if model else []
    if current == items:
        return
    selected = dropdown.get_selected_item()
    wanted = selected.get_string() if selected is not None else None
    dropdown.set_model(Gtk.StringList.new(items))
    if wanted in items:
        dropdown.set_selected(items.index(wanted))


def _tools_menu() -> Gio.Menu:
    menu = Gio.Menu()
    menu.append("Export journal", "win.journal-export")
    menu.append("Backup journal", "win.journal-backup")
    menu.append("Import journal", "win.journal-import")
    menu.append("Repair journal", "win.journal-repair")
    return menu
