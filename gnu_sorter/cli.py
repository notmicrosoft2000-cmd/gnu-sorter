"""Command-line interface for GNU Sorter."""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

from . import __version__
from .config import Config, DEFAULT_RULES
from .engine import Sorter, expand, fmt
from . import service


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gnu-sorter",
        description="Sort any folder (or a group of folders) automatically.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--version", action="version", version=f"GNU Sorter {__version__}")

    mode = p.add_argument_group("modes")
    mode.add_argument("--gui", action="store_true", help="open the graphical interface")
    mode.add_argument("--tray", action="store_true", help="run in system tray (minimised background)")
    mode.add_argument("--sweep", action="store_true", help="sort folders now")
    mode.add_argument("--watch", action="store_true", help="watch folders and sort continuously")
    mode.add_argument("--undo", action="store_true", help="reverse the most recent moves")
    mode.add_argument("--dry-run", action="store_true", help="preview only, change nothing")

    mode.add_argument("--root", action="append", metavar="PATH",
                      help="limit the action to this folder (repeatable); for rules "
                           "commands, the folder whose rules to edit")
    mode.add_argument("--undo-last", type=int, metavar="N",
                      help="only reverse the last N moves")

    p.add_argument("--config", metavar="DIR", help="use a different config directory")

    grp = p.add_argument_group("folders")
    grp.add_argument("--add-root", action="append", metavar="PATH", help="add a folder to watch")
    grp.add_argument("--remove-root", metavar="PATH", help="remove a watched folder")
    grp.add_argument("--list-roots", action="store_true", help="list watched folders")

    rgrp = p.add_argument_group("rules")
    rgrp.add_argument("--add-rule", metavar="EXT",
                      help="send files with this extension to a folder (e.g. quant)")
    rgrp.add_argument("--folder", metavar="NAME",
                      help="destination folder for --add-rule (default: the extension)")
    rgrp.add_argument("--remove-rule", metavar="NAME", help="delete a category by name")
    rgrp.add_argument("--list-rules", action="store_true",
                      help="list rules (use --root to show one folder's rules)")
    rgrp.add_argument("--detach-rules", action="store_true",
                      help="give a folder (--root) its own copy of the rules")
    rgrp.add_argument("--attach-rules", action="store_true",
                      help="make a folder (--root) inherit the default rules again")

    sgrp = p.add_argument_group("service")
    sgrp.add_argument("--install-service", action="store_true", help="install the systemd unit")
    sgrp.add_argument("--enable-service", action="store_true", help="enable + start auto-watch")
    sgrp.add_argument("--disable-service", action="store_true", help="stop + disable auto-watch")
    sgrp.add_argument("--service-status", action="store_true", help="show watcher status")
    sgrp.add_argument("--finish-migration", action="store_true",
                      help="retire download-sorter and take over watching")
    return p


def _cmd_list_roots(cfg: Config) -> int:
    roots = cfg.load_roots()
    if not roots:
        print("(no folders configured)")
    for r in roots:
        mark = "●" if r["enabled"] else "○"
        print(f"  {mark} {r['path']}")
    return 0


def _rules_scope(cfg: Config, root: str | None):
    """Resolve where rules commands should act.

    Returns (label, rules, save). With no --root the default template is
    edited; with --root the folder is detached from the template if needed
    and its own ruleset is edited.
    """
    if not root:
        return ("default template", cfg.load_rules(), cfg.save_rules)
    target = expand(root)
    cfg.detach_rules(target)                 # no-op if already custom
    rules, _ = cfg.rules_for(target)
    return (fmt(target), rules, lambda r: cfg.save_rules_for(target, r))


def _cmd_list_rules(cfg: Config, root: str | None = None) -> int:
    if not root:
        print("Rules — default template")
        rules = cfg.load_rules()
    else:
        target = expand(root)
        state = "own rules" if cfg.is_custom(target) else "inherits the default template"
        print(f"Rules — {fmt(target)}  ({state})")
        rules, _ = cfg.rules_for(target)
    for cat in rules:
        exts = ", ".join("." + str(e).lstrip(".") for e in cat.get("extensions", []))
        regex = cat.get("match_name_regex")
        detail = exts or (f"name ≈ /{regex}/" if regex else "—")
        print(f"  {cat.get('name', '?'):<18} {detail}")
    return 0


def _cmd_add_root(cfg: Config, path: str) -> int:
    target = expand(path)
    if not target.is_dir():
        print(f"!! not a folder: {target}", file=sys.stderr)
        return 1
    roots = cfg.load_roots()
    if any(expand(r["path"]) == target for r in roots):
        print(f"already watching {fmt(target)}")
        return 0
    roots.append({"path": str(target), "enabled": True})
    cfg.save_roots(roots)
    print(f"added {fmt(target)}")
    return 0


def _cmd_remove_root(cfg: Config, path: str) -> int:
    target = expand(path)
    roots = cfg.load_roots()
    kept = [r for r in roots if expand(r["path"]) != target]
    if len(kept) == len(roots):
        print(f"not found: {target}", file=sys.stderr)
        return 1
    cfg.save_roots(kept)
    print(f"removed {fmt(target)}")
    return 0


def _cmd_add_rule(cfg: Config, ext: str, folder: str | None,
                  root: str | None = None) -> int:
    ext = ext.strip().lower().lstrip(".")
    folder = (folder or ext).strip()
    if not ext:
        print("!! missing extension", file=sys.stderr)
        return 1
    label, rules, save = _rules_scope(cfg, root)
    for cat in rules:
        if cat.get("name") == folder:
            if ext not in [str(e).lstrip(".") for e in cat.get("extensions", [])]:
                cat.setdefault("extensions", []).append(ext)
            save(rules)
            print(f"added .{ext} → {folder}/  ({label})")
            return 0
    rules.append({"name": folder, "extensions": [ext]})
    save(rules)
    print(f"added .{ext} → {folder}/  ({label}; folder is created on first match)")
    return 0


def _cmd_remove_rule(cfg: Config, name: str, root: str | None = None) -> int:
    label, rules, save = _rules_scope(cfg, root)
    kept = [c for c in rules if c.get("name") != name]
    if len(kept) == len(rules):
        print(f"no rule named '{name}' in {label}", file=sys.stderr)
        return 1
    save(kept)
    print(f"removed rule '{name}'  ({label})")
    return 0


def _cmd_detach_rules(cfg: Config, root: str | None) -> int:
    if not root:
        print("!! --detach-rules needs --root PATH", file=sys.stderr)
        return 1
    target = expand(root)
    if cfg.detach_rules(target):
        print(f"{fmt(target)} now has its own rules (copied from the template)")
    else:
        print(f"{fmt(target)} already has its own rules")
    return 0


def _cmd_attach_rules(cfg: Config, root: str | None) -> int:
    if not root:
        print("!! --attach-rules needs --root PATH", file=sys.stderr)
        return 1
    target = expand(root)
    if cfg.attach_rules(target):
        print(f"{fmt(target)} now inherits the default template")
    else:
        print(f"{fmt(target)} already inherits the default template")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config(args.config)
    migrated = cfg.ensure_defaults()

    # ---- config-only commands (no GUI) --------------------------------
    if args.list_roots:
        return _cmd_list_roots(cfg)
    rule_root = args.root[0] if args.root else None
    if args.list_rules:
        return _cmd_list_rules(cfg, rule_root)
    if args.add_root:
        for p in args.add_root:
            _cmd_add_root(cfg, p)
        return 0
    if args.remove_root:
        return _cmd_remove_root(cfg, args.remove_root)
    if args.add_rule:
        return _cmd_add_rule(cfg, args.add_rule, args.folder, rule_root)
    if args.remove_rule:
        return _cmd_remove_rule(cfg, args.remove_rule, rule_root)
    if args.detach_rules:
        return _cmd_detach_rules(cfg, rule_root)
    if args.attach_rules:
        return _cmd_attach_rules(cfg, rule_root)

    # ---- service management -------------------------------------------
    if args.install_service:
        path = service.install()
        print(f"installed {path}")
        return 0
    if args.enable_service:
        service.enable()
        print(f"watcher: {service.status()}")
        return 0
    if args.disable_service:
        service.disable()
        print(f"watcher: {service.status()}")
        return 0
    if args.service_status:
        print(f"watcher: {service.status()}")
        return 0
    if args.finish_migration:
        service.migrate_from_download_sorter()
        service.install()
        service.enable()
        print(f"download-sorter retired — GNU Sorter watcher: {service.status()}")
        return 0

    sorter = Sorter(cfg)
    dry = args.dry_run or bool(sorter.settings.get("dry_run_default"))

    roots = [expand(r) for r in args.root] if args.root else None

    if args.undo:
        sorter.undo(dry_run=dry, limit=args.undo_last)
        return 0

    if args.watch:
        from .watcher import Watcher
        watcher = Watcher(sorter)
        try:
            watcher.run(dry_run=dry)
        except KeyboardInterrupt:
            watcher.stop()
        return 0

    if getattr(args, 'tray', False):
        try:
            from .tray import Tray
            from .gui import GNUSorterApp
            import gi
            gi.require_version("Gtk", "4.0")
            gi.require_version("Adw", "1")
            from gi.repository import Adw, GLib
            Adw.init()
            app = GNUSorterApp(cfg, migrated=migrated)
            tray = Tray(
                on_open_gui=lambda: GLib.idle_add(
                    lambda: app.window.present() if app.window else app.activate()),
                on_sort_now=lambda: GLib.idle_add(
                    lambda: app.window.on_sort_now(None) if app.window else None),
                on_start_watch=lambda: service.start(),
                on_stop_watch=lambda: service.stop(),
                on_quit=lambda: GLib.idle_add(app.quit),
            )
            # GTK must own the main thread; pystray runs on its own thread.
            app._tray = tray  # keep a reference alive for the app's lifetime
            threading.Thread(target=tray.start, daemon=True).start()
            return app.run([])
        except Exception as e:
            print(f"Tray unavailable: {e}", file=sys.stderr)
            return 1

    if args.sweep:
        stats = sorter.sweep(roots=roots, dry_run=dry)
        verb = "would sort" if dry else "sorted"
        print(f"\n{verb} {stats['moved']} file(s); {stats['needs_review']} → Needs-review/"
              f" ({stats['skipped']} skipped) ({'preview' if dry else 'live'})")
        return 0

    # ---- default: GUI --------------------------------------------------
    try:
        from .gui import run as run_gui
    except ImportError as exc:
        print(f"GUI unavailable ({exc}); use --sweep / --watch / --undo.", file=sys.stderr)
        return 1
    return run_gui(cfg, migrated=migrated)


if __name__ == "__main__":
    sys.exit(main())
