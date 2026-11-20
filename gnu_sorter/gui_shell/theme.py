"""Colour scheme and accent handling for the Funnel shell.

The stylesheet still lives next to ``gnu_sorter/gui.py``. Resolving it from the
package parent keeps the CSS path stable now that the implementation has moved
into ``gui_shell/``. Only the selectable accent variants inject literal hex
values; ``system`` leaves libadwaita's own accent alone.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..config import Config

BRAND_CSS_PATH: Path = Path(__file__).resolve().parents[1] / "brand.css"

_SCHEMES = {
    "default": Adw.ColorScheme.DEFAULT,
    "light": Adw.ColorScheme.FORCE_LIGHT,
    "dark": Adw.ColorScheme.FORCE_DARK,
}

# Every accent ships a light and a dark variant so contrast holds on either
# background. The default mirrors the project website: lime on dark, olive on
# light.
ACCENTS: dict[str, dict[str, tuple[str, str]]] = {
    "green": {"light": ("#466f0d", "#ffffff"), "dark": ("#a3e635", "#0b0d08")},
    "lime": {"light": ("#65a30d", "#ffffff"), "dark": ("#bef264", "#0b0d08")},
    "teal": {"light": ("#0f766e", "#ffffff"), "dark": ("#2dd4bf", "#04201d")},
    "cyan": {"light": ("#0e7490", "#ffffff"), "dark": ("#67e8f9", "#04222b")},
    "blue": {"light": ("#1c71d8", "#ffffff"), "dark": ("#78aeff", "#0a1f3d")},
    "purple": {"light": ("#7c3aed", "#ffffff"), "dark": ("#c4a0ff", "#23123f")},
    "pink": {"light": ("#d6336c", "#ffffff"), "dark": ("#ff8fbe", "#3d0a20")},
    "red": {"light": ("#e01b24", "#ffffff"), "dark": ("#ff7b72", "#360c0a")},
    "orange": {"light": ("#c64600", "#ffffff"), "dark": ("#ffb45c", "#3a1e00")},
    "yellow": {"light": ("#a1660a", "#ffffff"), "dark": ("#f5c211", "#332600")},
    "slate": {"light": ("#5e6b78", "#ffffff"), "dark": ("#9fb0c0", "#131a20")},
}

# Follow the desktop first, then the brand green, then the rest.
ACCENT_CHOICES: list[tuple[str, str]] = [
    ("system", "Follow the desktop"),
    ("green", "Green"),
]
ACCENT_CHOICES += [(key, key.capitalize()) for key in
                   ("lime", "teal", "cyan", "blue", "purple", "pink",
                    "red", "orange", "yellow", "slate")]

_brand_provider: Gtk.CssProvider | None = None
_style_hooked = False


def _brand_css(accent: str, dark: bool) -> str:
    """The stylesheet with the chosen accent appended as named colours.

    ``accent == "system"`` injects nothing, so libadwaita keeps the desktop's
    own accent colour.
    """
    try:
        css = BRAND_CSS_PATH.read_text(encoding="utf-8")
    except OSError:
        css = ""
    if accent == "system":
        return css
    entry = ACCENTS.get(accent)
    if entry is not None:
        bg, fg = entry["dark" if dark else "light"]
        css += ("\n@define-color accent_bg_color %s;\n"
                "@define-color accent_color %s;\n"
                "@define-color accent_fg_color %s;\n" % (bg, bg, fg))
    return css


def _hook_style_manager(cfg: Config) -> None:
    """Re-apply the theme when the desktop flips between light and dark."""
    global _style_hooked
    if _style_hooked:
        return
    try:
        Adw.StyleManager.get_default().connect(
            "notify::dark", lambda *_a: apply_color_scheme(cfg))
        _style_hooked = True
    except Exception:
        pass


def apply_color_scheme(cfg: Config) -> None:
    """Apply the saved appearance: colour scheme and accent colour."""
    global _brand_provider
    settings = cfg.load_settings()
    name = str(settings.get("color_scheme", "default")).lower()
    accent = str(settings.get("accent", "green")).lower()
    manager = Adw.StyleManager.get_default()
    try:
        manager.set_color_scheme(_SCHEMES.get(name, Adw.ColorScheme.DEFAULT))
    except Exception:
        pass
    if name == "dark":
        dark = True
    elif name == "light":
        dark = False
    else:
        try:
            dark = bool(manager.get_dark())
        except Exception:
            dark = False
    display = Gdk.Display.get_default()
    if display is None:
        return
    if _brand_provider is None:
        _brand_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            display, _brand_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    try:
        _brand_provider.load_from_string(_brand_css(accent, dark))
    except Exception:
        pass
    _hook_style_manager(cfg)


def _hex_to_rgb(value: str) -> tuple[float, float, float]:
    """A CSS hex colour as three 0..1 floats, for Cairo painting."""
    text = str(value).lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    try:
        return tuple(int(text[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except (ValueError, IndexError):
        return (0.639, 0.902, 0.208)


def accent_colors(cfg: Config | None = None) -> dict:
    """Resolve the active accent to Cairo floats for hand-painted widgets.

    Picks the light or dark variant for the active colour scheme. When the
    accent follows the desktop the resolved system colour is not exposed, so
    the brand green stands in rather than a hardcoded value.
    """
    scheme, accent, dark = "default", "green", False
    if cfg is not None:
        try:
            settings = cfg.load_settings()
            scheme = str(settings.get("color_scheme", "default")).lower()
            accent = str(settings.get("accent", "green")).lower()
        except Exception:
            pass
    if scheme == "dark":
        dark = True
    elif scheme == "light":
        dark = False
    else:
        try:
            dark = bool(Adw.StyleManager.get_default().get_dark())
        except Exception:
            dark = False
    entry = ACCENTS.get(accent) or ACCENTS["green"]
    bg, fg = entry["dark" if dark else "light"]
    return {"bg": _hex_to_rgb(bg), "fg": _hex_to_rgb(fg),
            "muted": (0.62, 0.64, 0.70) if dark else (0.30, 0.31, 0.36),
            "dark": dark}


# Legacy spelling used by the old SortPreview implementation.
_accent_colors = accent_colors
