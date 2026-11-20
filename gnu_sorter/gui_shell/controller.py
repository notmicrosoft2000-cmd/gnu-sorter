"""The single desk controller for the Funnel shell.

One :class:`~gnu_sorter.engine.Sorter` is owned here and every rules, settings
or journal mutation goes through this object. Views and dialogs talk to the
controller instead of reaching into the window, and listen to its signals to
refresh. All signals carry no arguments.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import gi

gi.require_version("GObject", "2.0")
from gi.repository import GObject  # noqa: E402

from .. import config, service  # noqa: E402
from ..config import Config, normalize_rule, normalize_rules  # noqa: E402
from ..engine import Sorter, expand  # noqa: E402


_SELECTORS = frozenset({
    "limit", "since", "before", "folder", "rule", "name", "batch", "ext",
    "min_size", "max_size",
})


class DeskController(GObject.Object):
    """Application state for one :class:`Config`, shared by every view."""

    __gsignals__ = {
        "roots-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "rules-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "journal-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "sweep-started": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "sweep-finished": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "settings-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "watcher-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, app, cfg: Config):
        super().__init__()
        self.app = app
        self.cfg = cfg
        self.sorter = Sorter(cfg)
        self.current_root: str | None = None
        roots = cfg.load_roots()
        if roots:
            self.current_root = roots[0].get("path")
        self.sweeping = False
        self.last_sweep: dict | None = None

    # ------------------------------------------------------------ helpers ---
    def _resolve_root(self, root: str | None) -> str | None:
        return root if root is not None else self.current_root

    def _restart_if_active(self) -> None:
        """Restart the user service after a root list change, when it runs."""
        try:
            if service.is_active():
                service.restart()
        except Exception:
            pass

    # -------------------------------------------------------------- roots ---
    def roots(self) -> list[str]:
        return [str(root.get("path")) for root in self.cfg.load_roots()
                if root.get("path")]

    def add_root(self, path: str) -> bool:
        path = str(path)
        key = self.cfg.key(path)
        for root in self.cfg.load_roots():
            if self.cfg.key(root.get("path", "")) == key:
                return False
        if not Path(expand(path)).is_dir():
            return False
        roots = self.cfg.load_roots()
        roots.append({"path": path, "enabled": True})
        self.cfg.save_roots(roots)
        if self.current_root is None:
            self.current_root = path
        self.emit("roots-changed")
        self._restart_if_active()
        return True

    def remove_root(self, path: str) -> None:
        key = self.cfg.key(path)
        roots = [root for root in self.cfg.load_roots()
                 if self.cfg.key(root.get("path", "")) != key]
        self.cfg.save_roots(roots)
        if self.current_root and self.cfg.key(self.current_root) == key:
            self.current_root = roots[0].get("path") if roots else None
        self.emit("roots-changed")
        self._restart_if_active()

    def toggle_root(self, path: str, enabled: bool) -> None:
        key = self.cfg.key(path)
        roots = self.cfg.load_roots()
        for root in roots:
            if self.cfg.key(root.get("path", "")) == key:
                root["enabled"] = bool(enabled)
                break
        else:
            return
        self.cfg.save_roots(roots)
        self.emit("roots-changed")
        self._restart_if_active()

    # -------------------------------------------------------------- rules ---
    def rules_for(self, root: str | None = None) -> list[dict]:
        root = self._resolve_root(root)
        if root is None:
            return list(self.cfg.load_rules())
        rules, _custom = self.cfg.rules_for(root)
        return rules

    def is_custom(self, root: str | None = None) -> bool:
        root = self._resolve_root(root)
        return bool(root and self.cfg.is_custom(root))

    def save_rules(self, root: str | None, rules: list[dict]) -> None:
        root = self._resolve_root(root)
        clean = normalize_rules(rules)
        if root is None:
            self.cfg.save_rules(clean)
        else:
            self.cfg.save_rules_for(root, clean)
        self.sorter.reload()
        self.emit("rules-changed")

    def save_rule(self, root: str | None, rule: dict) -> None:
        root = self._resolve_root(root)
        clean = normalize_rule(rule)
        if clean is None:
            return
        rules = self.rules_for(root)
        for index, current in enumerate(rules):
            if current.get("name") == clean["name"]:
                rules[index] = clean
                break
        else:
            rules.append(clean)
        self.save_rules(root, rules)

    def delete_rule(self, root: str | None, name: str) -> None:
        root = self._resolve_root(root)
        rules = self.rules_for(root)
        kept = [existing for existing in rules
                if existing.get("name") != name]
        if len(kept) == len(rules):
            return
        self.save_rules(root, kept)

    def reorder_rules(self, root: str | None, names: list[str]) -> None:
        root = self._resolve_root(root)
        rules = self.rules_for(root)
        wanted = set(names)
        by_name = {existing.get("name"): existing for existing in rules}
        ordered = [by_name[name] for name in names if name in by_name]
        ordered += [existing for existing in rules
                    if existing.get("name") not in wanted]
        self.save_rules(root, ordered)

    def use_shared_defaults(self, root: str) -> None:
        """Stop using a per-folder ruleset, keeping a ``.bak`` of it first."""
        if not root:
            return
        path = self.cfg.ruleset_path(root)
        if path.exists():
            try:
                shutil.copy2(path, str(path) + ".bak")
            except OSError:
                pass
        if self.cfg.attach_rules(root):
            self.sorter.reload()
            self.emit("rules-changed")

    def make_own_copy(self, root: str) -> None:
        if not root:
            return
        if self.cfg.detach_rules(root):
            self.sorter.reload()
            self.emit("rules-changed")

    def import_rules(self, root: str | None, rules: list[dict],
                     mode: str) -> None:
        incoming = normalize_rules(rules)
        if not incoming:
            return
        root = self._resolve_root(root)
        if mode == "replace":
            merged = list(incoming)
        else:
            merged = self.rules_for(root)
            for rule in incoming:
                for index, current in enumerate(merged):
                    if current.get("name") == rule["name"]:
                        merged[index] = rule
                        break
                else:
                    merged.append(rule)
        self.save_rules(root, merged)

    def export_rules(self, root: str | None, path: str) -> None:
        rules = self.rules_for(root)
        config.atomic_write_text(
            Path(path),
            json.dumps(rules, indent=2, ensure_ascii=False) + "\n")

    def preview_plan(self, root: str | None = None) -> dict[str, int]:
        root = self._resolve_root(root)
        rules = self.rules_for(root)
        roots = [Path(expand(root))] if root else self.sorter.roots()
        return self.sorter.preview_plan(roots=roots, rules=rules)

    def classify(self, root: str | None, filename: str) -> str:
        rules = self.rules_for(root)
        return self.sorter.classify([filename], rules=rules).get(
            filename, "Needs-review")

    # ------------------------------------------------------------ journal ---
    def journal_index(self):
        return self.sorter.journal_index()

    def records(self, **filters) -> list[dict]:
        return self.sorter.records(**filters)

    # ------------------------------------------------------------ settings ---
    def save_settings(self, values: dict) -> None:
        settings = self.cfg.load_settings()
        settings.update(values)
        self.cfg.save_settings(settings)
        self.sorter.reload()
        self.emit("settings-changed")
        if "color_scheme" in values or "accent" in values:
            try:
                from .theme import apply_color_scheme
                apply_color_scheme(self.cfg)
            except Exception:
                pass

    def settings(self) -> dict:
        return dict(self.cfg.load_settings())

    # -------------------------------------------------------------- sweep ---
    def run_sweep(self, dry_run: bool | None = None) -> int:
        """Sort every enabled root. ``None`` resolves to the saved default."""
        if self.sweeping:
            return 0
        if dry_run is None:
            dry_run = bool(self.settings().get("dry_run_default", False))
        self.sweeping = True
        self.emit("sweep-started")
        try:
            stats = self.sorter.sweep(dry_run=dry_run)
            self.last_sweep = stats
        finally:
            self.sweeping = False
            self.emit("sweep-finished")
        if not dry_run:
            self.emit("journal-changed")
        return int(stats.get("moved", 0))

    def undo_last_move(self, dry_run: bool = False) -> int:
        if self.sweeping:
            return 0
        moved = int(self.sorter.undo(limit=1, dry_run=dry_run))
        if not dry_run and moved:
            self.emit("journal-changed")
        return moved

    def undo_last_sweep(self, dry_run: bool = False) -> int:
        if self.sweeping:
            return 0
        recent = self.sorter.records(limit=1)
        if not recent:
            return 0
        batch = recent[0].get("batch")
        if not batch:
            return self.undo_last_move(dry_run=dry_run)
        moved = int(self.sorter.undo(batch=batch, dry_run=dry_run))
        if not dry_run and moved:
            self.emit("journal-changed")
        return moved

    def undo(self, dry_run: bool = False, **selectors) -> int:
        if self.sweeping:
            return 0
        moved = int(self.sorter.undo(dry_run=dry_run, **selectors))
        if not dry_run and moved:
            self.emit("journal-changed")
        return moved

    def restore_plan(self, **selectors):
        """Describe a filtered restore without touching the disk."""
        select = {key: value for key, value in selectors.items()
                  if key in _SELECTORS}
        on_conflict = selectors.get("on_conflict", "rename")
        target = selectors.get("target")
        to_undo = self.sorter._select(self.sorter._read_journal(), **select)
        return self.sorter.restore_plan(
            to_undo, target=target, on_conflict=on_conflict)

    def restore_commit(self, on_conflict: str, **selectors) -> int:
        if self.sweeping:
            return 0
        options = dict(selectors)
        options["on_conflict"] = on_conflict
        moved = int(self.sorter.undo(dry_run=False, **options))
        if moved:
            self.emit("journal-changed")
        return moved

    # ------------------------------------------------------------ service ---
    def watcher_status(self) -> str:
        return service.status()

    def set_watch(self, enabled: bool) -> None:
        """Enable or disable the watch unit, writing it only on first use."""
        if enabled:
            if not service.is_installed():
                service.install()
            service.enable()
        else:
            service.disable()
        self.emit("watcher-changed")

    def set_start_on_login(self, enabled: bool) -> None:
        if enabled:
            if not service.is_installed():
                service.install()
            service.enable()
        else:
            service.disable()
        self.emit("watcher-changed")
