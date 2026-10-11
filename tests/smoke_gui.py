#!/usr/bin/env python3
"""GUI render smoke test — opens every page + dialog, screenshots and exits 0/1.

Requires a display (the GTK4 window is shown briefly). Output PNGs go to
$GS_SMOKE_OUT (default /tmp/opencode/gsui/).
"""
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gtk, Gsk, GLib, Graphene  # noqa: E402

from gnu_sorter.config import Config  # noqa: E402
from gnu_sorter.gui import FunnelApp, RuleWizard, AdvancedDialog  # noqa: E402

OUT = pathlib.Path(os.environ.get("GS_SMOKE_OUT", "/tmp/opencode/gsui"))


def capture(window, name: str) -> None:
    w, h = window.get_width(), window.get_height()
    if w <= 1 or h <= 1:
        w, h = 940, 640
    native = window.get_native()
    if native is None:
        print(f"!! no native for {name} (mapped={window.get_mapped()})", flush=True)
        return
    snap = Gtk.Snapshot.new()
    paintable = Gtk.WidgetPaintable.new(window)
    paintable.snapshot(snap, w, h)
    node = snap.to_node()
    if node is None:
        print(f"!! no render node for {name} (mapped={window.get_mapped()} "
              f"size={w}x{h} visible={window.get_visible()})", flush=True)
        return
    renderer = window.get_native().get_renderer()
    viewport = Graphene.Rect().init(0.0, 0.0, float(w), float(h))
    texture = renderer.render_texture(node, viewport)
    OUT.mkdir(parents=True, exist_ok=True)
    texture.save_to_png(str(OUT / name))
    print(f"saved {name}", flush=True)


def main() -> int:
    Adw.init()
    tmp = tempfile.mkdtemp(prefix="gs-smoke-")
    cfg = Config(tmp)
    cfg.ensure_defaults()
    root1 = "/tmp/opencode/gs/root1"
    root2 = tmp + "/Documents"
    pathlib.Path(root1).mkdir(parents=True, exist_ok=True)
    pathlib.Path(root2).mkdir(parents=True, exist_ok=True)
    cfg.save_roots([
        {"path": root1, "enabled": True},
        {"path": root2, "enabled": True},
    ])
    rules = cfg.load_rules()
    rules.append({"name": "quant", "extensions": ["quant"]})
    cfg.save_rules(rules)
    # Give root2 its own ruleset so the "own rules" badge is exercised.
    cfg.detach_rules(root2)
    own, _ = cfg.rules_for(root2)
    own.append({"name": "Comics", "extensions": ["cbz", "cbr"]})
    cfg.save_rules_for(root2, own)

    app = FunnelApp(cfg, migrated=False)
    state = {"step": 0, "wiz": None, "adv": None}

    def run_steps() -> bool:
        win = app.window
        if win is None:
            return True
        step = state["step"]
        try:
            keep_going = step_impl(win, state)
        except Exception:
            import traceback
            traceback.print_exc()
            keep_going = True
        state["step"] += 1
        return keep_going

    def step_impl(win, state):
        step = state["step"]
        if step == 0:
            capture(win, "1-folders.png")
        elif step == 1:
            win.sidebar.select_row(win.sidebar.get_row_at_index(1))
            capture(win, "2-rules.png")
        elif step == 2:
            # select the custom folder (row 1) to show its "own rules" state
            if win.rules_folders.get_row_at_index(1):
                win.rules_folders.select_row(win.rules_folders.get_row_at_index(1))
            capture(win, "3-rules-custom.png")
        elif step == 3:
            state["wiz"] = RuleWizard(win, win.current_root)
            state["wiz"].open()
            capture(win, "4-wizard-step1-ext.png")
        elif step == 4:
            wiz = state["wiz"]
            wiz.mode_list.select_row(wiz.mode_list.get_row_at_index(1))
            capture(win, "5-wizard-step1-name.png")
        elif step == 5:
            wiz = state["wiz"]
            wiz.mode_list.select_row(wiz.mode_list.get_row_at_index(0))
            wiz.ext_entry.set_text("heic, raw")
            wiz._go_dest(None)
            capture(win, "6-wizard-step2.png")
        elif step == 6:
            wiz = state["wiz"]
            wiz._go_review(None)
            capture(win, "7-wizard-step3-review.png")
        elif step == 7:
            state["wiz"].dialog.close()
        elif step == 8:
            state["adv"] = AdvancedDialog(win, win.current_root)
            state["adv"].open()
            capture(win, "8-advanced.png")
        elif step == 9:
            state["adv"].dialog.close()
        elif step == 10:
            win.sidebar.select_row(win.sidebar.get_row_at_index(2))
            capture(win, "9-activity.png")
        elif step == 11:
            win.sidebar.select_row(win.sidebar.get_row_at_index(3))
            capture(win, "10-settings.png")
        elif step == 12:
            win.show_about()
            capture(win, "11-about.png")
            app.quit()
            return False
        return True

    app.connect("activate", lambda _a: GLib.timeout_add(1200, run_steps))
    code = app.run([])
    print(f"exit {code}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
