"""01 Plan: the ordered rule sheet for the current context."""

from __future__ import annotations

import json

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from ...config import PRESETS, describe_rule, duplicate_rule, normalize_rule, rule_overlaps  # noqa: E402
from ...engine import expand  # noqa: E402
from ..proof import ProofPanel  # noqa: E402
from ..widgets import color_dot, dim, heading, index_label, listbox, scroll  # noqa: E402


def _root_name(root: str | None) -> str:
    if not root:
        return "00 Shared defaults"
    path = expand(root)
    return path.name or str(path)


def _alert(window, heading_text: str, body: str, responses, callback,
           default: str | None = None) -> None:
    dialog = Adw.AlertDialog(heading=heading_text, body=body)
    for key, label in responses:
        dialog.add_response(key, label)
    dialog.set_default_response(default or responses[0][0])
    dialog.set_close_response(responses[0][0])
    dialog.choose(window, None, callback)


def _button(icon: str, tooltip: str, callback, *args) -> Gtk.Button:
    button = Gtk.Button.new_from_icon_name(icon)
    button.add_css_class("flat")
    button.set_tooltip_text(tooltip)
    button.set_valign(Gtk.Align.CENTER)
    button.connect("clicked", callback, *args)
    return button


def _attach_drag(window, rules_list: Gtk.ListBox, state: dict, refresh) -> None:
    def prepare(_source, _x, _y, name: str):
        try:
            value = GObject.Value()
            value.init(GObject.TYPE_STRING)
            value.set_string(name)
            return Gdk.ContentProvider.new_for_value(value)
        except Exception:
            return None

    def dropped(_target, value, _x, y):
        root = window.current_root
        if not isinstance(value, str) or not value:
            return False
        names = [str(rule.get("name")) for rule in state["rules"]]
        if value not in names:
            return False
        hit = rules_list.get_row_at_y(int(y))
        index = hit.get_index() if hit is not None else len(names) - 1
        names.remove(value)
        names.insert(max(0, min(index, len(names))), value)
        window.controller.reorder_rules(root, names)
        refresh()
        return True

    try:
        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        target.connect("drop", dropped)
        rules_list.add_controller(target)
    except Exception:
        return
    state["drag_prepare"] = prepare


def _make_rule_row(window, state: dict, rule: dict, index: int, total: int,
                   counts: dict, refresh) -> Adw.ActionRow:
    controller = window.controller
    root = window.current_root
    name = str(rule.get("name") or "?")
    description = describe_rule(rule)
    parts = [description] if description else []
    shown = counts.get(name, 0)
    if shown:
        parts.append(f"{shown} sorted")
    if rule.get("enabled") is False:
        parts.append("paused")
    row = Adw.ActionRow(title=f"{name}/", subtitle=" · ".join(parts) or "No match yet")
    row.set_subtitle_lines(2)
    row.add_css_class("funnel-rule")
    row._haystack = " ".join([
        name, description,
        " ".join(str(ext) for ext in rule.get("extensions", [])),
        str(rule.get("match_name_regex") or ""),
    ]).lower()

    number = index_label(f"{index + 1:02d}")
    number.set_valign(Gtk.Align.CENTER)
    row.add_prefix(number)
    if rule.get("color"):
        row.add_prefix(color_dot(str(rule["color"])))

    def enabled_changed(switch, _param, target: str):
        if state["loading"]:
            return
        updated = []
        for current in state["rules"]:
            entry = dict(current)
            if entry.get("name") == target:
                if switch.get_active():
                    entry.pop("enabled", None)
                else:
                    entry["enabled"] = False
            updated.append(entry)
        controller.save_rules(root, updated)
        refresh()

    switch = Gtk.Switch(active=rule.get("enabled") is not False,
                        valign=Gtk.Align.CENTER)
    switch.set_tooltip_text("Include this rule")
    switch.connect("notify::active", enabled_changed, name)
    row.add_prefix(switch)

    def reorder(target: int):
        names = [str(item.get("name")) for item in state["rules"]]
        if name not in names:
            return
        source = names.index(name)
        place = max(0, min(target, len(names) - 1))
        names.pop(source)
        names.insert(place, name)
        controller.reorder_rules(root, names)
        refresh()

    up = _button("go-up-symbolic", "Move up", lambda *_: reorder(index - 1))
    down = _button("go-down-symbolic", "Move down", lambda *_: reorder(index + 1))
    up.set_sensitive(index > 0)
    down.set_sensitive(index < total - 1)
    row.add_suffix(up)
    row.add_suffix(down)

    def see_moves(*_args):
        window.navigate("activity")
        dropdown = getattr(window, "activity_rule_filter", None)
        if dropdown is not None:
            model = dropdown.get_model()
            if model is not None:
                for i in range(model.get_n_items()):
                    if model.get_string(i) == name:
                        dropdown.set_selected(i)
                        break

    def duplicate_rule_here(*_args):
        controller.save_rules(root, duplicate_rule(state["rules"], name))
        window.toast(f"Duplicated {name}/")
        refresh()

    def remove_rule_here(*_args):
        saved = dict(rule)
        where = _root_name(root)

        def chosen(dialog, result):
            try:
                response = dialog.choose_finish(result)
            except GLib.Error:
                return
            if response != "remove":
                return
            controller.delete_rule(root, name)
            refresh()

            def undo(*_a):
                controller.save_rule(root, saved)
                refresh()

            toast = Adw.Toast.new(f"Removed {name}/ from {where}")
            toast.set_button_label("Undo")
            toast.connect("button-clicked", undo)
            window.toasts.add_toast(toast)

        _alert(window, "Remove this rule?",
               f"{name}/ will be removed from {where}. Its files stay in place.",
               [("cancel", "Cancel"), ("remove", "Remove")], chosen)

    popover = Gtk.Popover()
    popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    popover_box.set_margin_top(6)
    popover_box.set_margin_bottom(6)
    popover_box.set_margin_start(6)
    popover_box.set_margin_end(6)
    for label, action in (("Edit", lambda: window.open_advanced(root, rule)),
                          ("Duplicate", duplicate_rule_here),
                          ("Move up", lambda: reorder(index - 1)),
                          ("Move down", lambda: reorder(index + 1)),
                          ("See moves", see_moves),
                          ("Remove", remove_rule_here)):
        item = Gtk.Button(label=label)
        item.add_css_class("flat")
        item.set_halign(Gtk.Align.FILL)
        item.connect("clicked", lambda *_a, act=action: (popover.popdown(), act()))
        popover_box.append(item)
    popover.set_child(popover_box)
    menu = Gtk.MenuButton(icon_name="view-more-symbolic")
    menu.add_css_class("flat")
    menu.set_popover(popover)
    row.add_suffix(menu)

    try:
        source = Gtk.DragSource.new()
        source.set_actions(Gdk.DragAction.MOVE)
        source.connect("prepare", state["drag_prepare"], name)
        row.add_controller(source)
    except Exception:
        pass
    return row


def build_plan(window) -> Gtk.Widget:
    """Build the rule sheet, tester and static proof for the desk."""
    controller = window.controller
    state: dict = {"rules": [], "loading": False, "drag_prepare": None}

    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    outer.set_margin_top(18)
    outer.set_margin_bottom(18)
    outer.set_margin_start(18)
    outer.set_margin_end(18)

    header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    header.append(heading("01 Plan"))
    header.append(Gtk.Box(hexpand=True))
    window.rules_search = Gtk.SearchEntry(placeholder_text="Search rules")
    window.rules_search.connect("search-changed", lambda *_: apply_search())
    header.append(window.rules_search)

    presets_menu = Gio.Menu()
    for i, (label, _rules) in enumerate(PRESETS):
        presets_menu.append(label, f"win.preset-{i}")
    add = Adw.SplitButton()
    add.set_label("Add Rule")
    add.set_icon_name("list-add-symbolic")
    add.set_menu_model(presets_menu)
    add.add_css_class("suggested-action")
    add.connect("clicked", lambda *_: window.open_wizard(window.current_root))
    header.append(add)

    overflow = Gio.Menu()
    overflow.append("Advanced rule editor", "win.advanced")
    overflow.append("Suggest rules", "win.suggest-rules")
    overflow.append("Import rules", "win.import-rules")
    overflow.append("Export rules", "win.export-rules")
    overflow.append("Use shared defaults", "win.use-shared-defaults")
    header.append(Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=overflow))
    outer.append(header)

    context_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    context_label = Gtk.Label(xalign=0, hexpand=True, wrap=True)
    context_label.add_css_class("dim-label")
    context_badge = Gtk.Label(xalign=1)
    context_badge.add_css_class("caption")
    context_box.append(context_label)
    context_box.append(context_badge)
    outer.append(context_box)

    overlap_banner = Adw.Banner()
    overlap_banner.set_revealed(False)
    outer.append(overlap_banner)

    inherit_banner = window.page_banners["rules"]
    inherit_banner.set_button_label("Make own copy")

    def make_own_copy(*_args):
        root = window.current_root
        if not root:
            return
        try:
            controller.make_own_copy(root)
        except Exception as exc:
            window.toast(f"Could not make an own copy: {exc}")
            return
        window.toast("This folder now has its own rules")
        refresh()

    inherit_banner.connect("button-clicked", make_own_copy)

    rule_stack = Gtk.Stack()
    rule_stack.set_vexpand(True)
    sheet = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

    hint = dim("Rules are checked top to bottom. The first match wins. "
               "Reorder with the arrows or by dragging.")
    hint.add_css_class("caption")
    sheet.append(hint)

    rules_list = listbox()
    rules_list.set_name("plan-rule-sheet")
    _attach_drag(window, rules_list, state, lambda: refresh())
    sheet.append(scroll(rules_list))

    tester = Adw.ExpanderRow(title="Try a file name",
                             subtitle="See which rule would catch it")
    name_entry = Adw.EntryRow(title="File name")
    result = Adw.ActionRow(title="Waiting for a name",
                           subtitle="Type a name to preview the match")
    tester.add_row(name_entry)
    tester.add_row(result)
    tester_list = listbox()
    tester_list.append(tester)
    sheet.append(tester_list)

    def entry_changed(_entry):
        root = window.current_root
        filename = name_entry.get_text().strip()
        if not filename:
            result.set_title("Waiting for a name")
            result.set_subtitle("Type a name to preview the match")
            return
        try:
            destination = controller.classify(root, filename)
        except Exception as exc:
            result.set_title("Could not test this name")
            result.set_subtitle(str(exc))
            return
        if destination == "Needs-review":
            result.set_title(f"{filename} → Needs-review")
            result.set_subtitle("No enabled rule matches. It stays put.")
            return
        rules = state["rules"]
        position = next((i + 1 for i, item in enumerate(rules)
                         if item.get("name") == destination), None)
        suffix = f" (rule #{position})" if position else ""
        result.set_title(f"{filename} → {destination}/")
        result.set_subtitle(f"Matched by {destination}{suffix}")

    name_entry.connect("changed", entry_changed)

    preview_row = Adw.ActionRow(title="Count what would move",
                                subtitle="Static proof for this context")
    preview_button = Gtk.Button(label="Preview")
    preview_button.set_valign(Gtk.Align.CENTER)
    preview_button.add_css_class("flat")
    preview_row.add_suffix(preview_button)
    preview_list = listbox()
    preview_list.append(preview_row)
    sheet.append(preview_list)
    proof_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    sheet.append(proof_holder)

    def show_proof(*_args):
        root = window.current_root
        try:
            counts = controller.preview_plan(root) or {}
        except Exception as exc:
            window.toast(f"Preview failed: {exc}")
            return
        while (child := proof_holder.get_first_child()) is not None:
            proof_holder.remove(child)
        proof_holder.append(ProofPanel(root, counts, controller))

    preview_button.connect("clicked", show_proof)

    empty = Adw.StatusPage(
        icon_name="emblem-documents-symbolic",
        title="No rules in this sheet",
        description="Add a rule, or start from a preset. Rules run in order.")
    empty.add_css_class("funnel-empty")
    empty_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    empty_actions.set_halign(Gtk.Align.CENTER)
    empty_add = Gtk.Button(label="Add rule")
    empty_add.add_css_class("suggested-action")
    empty_add.connect("clicked", lambda *_: window.open_wizard(window.current_root))
    preset_button = Gtk.MenuButton(label="Choose a preset")
    preset_button.set_menu_model(presets_menu)
    empty_actions.append(empty_add)
    empty_actions.append(preset_button)
    empty.set_child(empty_actions)

    rule_stack.add_named(sheet, "sheet")
    rule_stack.add_named(empty, "empty")
    outer.append(rule_stack)

    def apply_search():
        text = window.rules_search.get_text().strip().lower()
        i = 0
        while (row := rules_list.get_row_at_index(i)) is not None:
            haystack = getattr(row, "_haystack", "")
            row.set_visible(not text or text in haystack)
            i += 1

    def refresh():
        state["loading"] = True
        try:
            root = window.current_root
            rules = list(controller.rules_for(root) or [])
            state["rules"] = rules
            custom = bool(root and controller.is_custom(root))
            if root:
                context_label.set_label(_root_name(root))
                context_badge.set_label("Own copy" if custom else "Uses 00 Shared defaults")
            else:
                context_label.set_label("00 Shared defaults")
                context_badge.set_label("Shared rules")
            if root and not custom:
                inherit_banner.set_title("This folder uses 00 Shared defaults.")
                inherit_banner.set_revealed(True)
            else:
                inherit_banner.set_revealed(False)

            try:
                warnings = rule_overlaps(rules)
            except Exception:
                warnings = []
            if warnings:
                text = str(warnings[0][1] or "Rules overlap")
                if len(warnings) > 1:
                    text = f"{len(warnings)} rules need a look: {text}"
                overlap_banner.set_title(text)
                overlap_banner.set_revealed(True)
            else:
                overlap_banner.set_revealed(False)

            try:
                counts = dict(controller.preview_plan(root) or {})
            except Exception:
                counts = {}
            rules_list.remove_all()
            for i, rule in enumerate(rules):
                rules_list.append(_make_rule_row(
                    window, state, rule, i, len(rules), counts, refresh))
            apply_search()
            while (child := proof_holder.get_first_child()) is not None:
                proof_holder.remove(child)
            result.set_title("Waiting for a name")
            result.set_subtitle("Type a name to preview the match")
            if rules:
                rule_stack.set_visible_child_name("sheet")
            else:
                rule_stack.set_visible_child_name("empty")
                if root is None:
                    empty.set_description(
                        "No rules in 00 Shared defaults yet. Start from a preset "
                        "or add a rule.")
                else:
                    empty.set_description(
                        "Add a rule, or start from a preset. Rules run in order.")
        finally:
            state["loading"] = False

    def apply_preset(index: int):
        root = window.current_root
        label, rules = PRESETS[index]
        where = _root_name(root)
        copied = json.loads(json.dumps(rules))
        count = len(copied)
        body = (f"{label} will replace the rules in {where} with {count} rule(s)."
                if count else f"{label} will remove every rule in {where}."
                if root else f"{label} will replace 00 Shared defaults with {count} rule(s).")

        def chosen(dialog, result_):
            try:
                response = dialog.choose_finish(result_)
            except GLib.Error:
                return
            if response != "apply":
                return
            controller.save_rules(root, copied)
            window.toast(f"Applied {label} to {where}")
            refresh()

        _alert(window, "Replace this rule sheet?", body,
               [("cancel", "Cancel"), ("apply", "Apply")], chosen)

    def import_rules():
        dialog = Gtk.FileDialog()
        dialog.set_title("Import rules")
        dialog.open(window, None, import_finished)

    def import_finished(dialog, result_):
        try:
            gfile = dialog.open_finish(result_)
        except GLib.Error:
            return
        if gfile is None or not gfile.get_path():
            return
        try:
            with open(gfile.get_path(), encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            window.toast(f"Import failed: {exc}")
            return
        if isinstance(data, dict) and isinstance(data.get("rules"), list):
            data = data["rules"]
        if not isinstance(data, list):
            window.toast("That file does not contain a list of rules")
            return
        imported = [rule for rule in (normalize_rule(item) for item in data)
                    if rule is not None]
        if not imported:
            window.toast("No usable rules found in that file")
            return

        def chosen(alert, result__):
            try:
                mode = alert.choose_finish(result__)
            except GLib.Error:
                return
            if mode not in ("append", "replace"):
                return
            controller.import_rules(window.current_root, imported, mode)
            window.toast(f"Imported {len(imported)} rule(s)")
            refresh()

        _alert(window, "Import rules",
               f"{len(imported)} rule(s) found. Append them, or replace the "
               "rules in this sheet? Same-named rules are overwritten on append.",
               [("cancel", "Cancel"), ("append", "Append"), ("replace", "Replace")],
               chosen, default="append")

    def export_rules():
        dialog = Gtk.FileDialog()
        dialog.set_title("Export rules")
        dialog.set_initial_name("funnel-rules.json")
        dialog.save(window, None, export_finished)

    def export_finished(dialog, result_):
        try:
            gfile = dialog.save_finish(result_)
        except GLib.Error:
            return
        if gfile is None or not gfile.get_path():
            return
        try:
            controller.export_rules(window.current_root, gfile.get_path())
        except Exception as exc:
            window.toast(f"Export failed: {exc}")
            return
        window.toast("Exported this rule sheet")

    def use_shared_defaults():
        root = window.current_root
        if not root:
            window.toast("Shared defaults are already in use")
            return

        def chosen(dialog, result_):
            try:
                response = dialog.choose_finish(result_)
            except GLib.Error:
                return
            if response != "apply":
                return
            controller.use_shared_defaults(root)
            window.toast("This folder now uses 00 Shared defaults")
            refresh()

        _alert(window, "Use shared defaults?",
               f"{_root_name(root)} will stop using its own rules. A backup "
               "of that ruleset stays on disk.",
               [("cancel", "Cancel"), ("apply", "Use shared defaults")], chosen)

    def suggest_rules():
        wizard = window.open_wizard(window.current_root)
        if wizard is not None and hasattr(wizard, "open_suggest"):
            wizard.open_suggest()

    window._refresh_plan = refresh
    window._apply_rule_search = apply_search
    window._on_preset = apply_preset
    window._on_import_rules = import_rules
    window._on_export_rules = export_rules
    window._on_use_shared_defaults = use_shared_defaults
    window._on_suggest_rules = suggest_rules

    clamp = Adw.Clamp()
    clamp.set_maximum_size(880)
    clamp.set_tightening_threshold(560)
    clamp.set_vexpand(True)
    clamp.set_child(outer)
    refresh()
    return clamp
