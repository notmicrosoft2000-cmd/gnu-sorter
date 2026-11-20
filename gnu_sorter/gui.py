"""Compatibility facade for the Funnel GTK interface.

The implementation lives in :mod:`gnu_sorter.gui_shell`. This module owns no
behaviour: it re-exports the names used by ``cli.py``, ``tray.py`` and the GUI
smoke test. Names from other shell modules resolve on first access, so the
facade stays importable while individual shell modules are being built.
"""

from __future__ import annotations

from importlib import import_module

from .gui_shell.app import FunnelApp, run
from .gui_shell.theme import ACCENTS, ACCENT_CHOICES, apply_color_scheme

__all__ = [
    "run",
    "FunnelApp",
    "MainWindow",
    "RuleWizard",
    "AdvancedDialog",
    "RestoreDialog",
    "PreferencesDialog",
    "SortPreview",
    "WIZARD_RECIPES",
    "WIZARD_STEPS",
    "apply_color_scheme",
    "ACCENTS",
    "ACCENT_CHOICES",
]

_LAZY = {
    "MainWindow": (".gui_shell.window", "MainWindow"),
    "RuleWizard": (".gui_shell.wizard", "RuleWizard"),
    "AdvancedDialog": (".gui_shell.advanced", "AdvancedDialog"),
    "RestoreDialog": (".gui_shell.restore", "RestoreDialog"),
    "PreferencesDialog": (".gui_shell.settings", "PreferencesDialog"),
    "SortPreview": (".gui_shell.proof", "SortPreview"),
    "WIZARD_RECIPES": (".gui_shell.wizard", "WIZARD_RECIPES"),
    "WIZARD_STEPS": (".gui_shell.wizard", "WIZARD_STEPS"),
}


def __getattr__(name: str):
    try:
        module_name, attribute = _LAZY[name]
    except KeyError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(import_module(module_name, __package__), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
