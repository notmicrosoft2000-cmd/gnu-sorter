"""Small shared GTK helpers for the Funnel shell and its dialogs.

Labels that carry structure use the CSS classes from ``brand.css``:
``funnel-eyebrow`` for captions and ``funnel-index`` for position numbers.
The string helpers live here too so every dialog formats extensions, sizes and
escaped literals the same way.
"""

from __future__ import annotations

import re

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402


# ------------------------------------------------------------------ labels ---
def heading(text: str) -> Gtk.Label:
    lbl = Gtk.Label(label=text, xalign=0)
    lbl.add_css_class("title-2")
    return lbl


def dim(text: str) -> Gtk.Label:
    lbl = Gtk.Label(label=text, xalign=0)
    lbl.add_css_class("dim-label")
    lbl.set_wrap(True)
    return lbl


def eyebrow(text: str) -> Gtk.Label:
    lbl = Gtk.Label(label=text, xalign=0)
    lbl.add_css_class("funnel-eyebrow")
    return lbl


def index_label(text: str) -> Gtk.Label:
    lbl = Gtk.Label(label=text, xalign=0, width_chars=2)
    lbl.add_css_class("funnel-index")
    lbl.set_valign(Gtk.Align.CENTER)
    return lbl


# ----------------------------------------------------------------- layout ---
def listbox(selection: Gtk.SelectionMode = Gtk.SelectionMode.NONE) -> Gtk.ListBox:
    lb = Gtk.ListBox()
    lb.add_css_class("boxed-list")
    lb.set_selection_mode(selection)
    return lb


def scroll(child: Gtk.Widget) -> Gtk.ScrolledWindow:
    sw = Gtk.ScrolledWindow()
    sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    sw.set_vexpand(True)
    sw.set_child(child)
    return sw


def pad(box: Gtk.Widget, amount: int = 18) -> Gtk.Widget:
    """Give a page container its standard margin and return it."""
    box.set_margin_top(amount)
    box.set_margin_bottom(amount)
    box.set_margin_start(amount)
    box.set_margin_end(amount)
    return box


def icon_button(icon: str, callback, tooltip: str | None = None,
                *data) -> Gtk.Button:
    """A flat icon button. ``callback`` is called with ``button, *data``."""
    btn = Gtk.Button.new_from_icon_name(icon)
    if tooltip:
        btn.set_tooltip_text(tooltip)
    btn.add_css_class("flat")
    btn.connect("clicked", callback, *data)
    return btn


def color_dot(color: str) -> Gtk.Widget:
    """A small dot painted in ``color`` (any CSS colour), for rule rows."""
    dot = Gtk.Label(label="\u25cf")
    dot.set_valign(Gtk.Align.CENTER)
    try:
        provider = Gtk.CssProvider()
        provider.load_from_string(
            f"label {{ color: {color}; font-size: 14px; }}")
        dot.get_style_context().add_provider(
            provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    except Exception:
        pass
    dot.set_tooltip_text("Accent colour")
    return dot


# ----------------------------------------------------------------- strings ---
def human_size(n) -> str:
    """Compact file size for journal and restore rows."""
    try:
        size = float(n)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def split_exts(text: str) -> list[str]:
    tokens = text.replace(",", " ").split()
    out: list[str] = []
    for token in tokens:
        token = token.strip().lower().lstrip(".")
        if token and token not in out:
            out.append(token)
    return out


def unescape_simple(pattern: str | None) -> str:
    """If ``pattern`` is exactly ``re.escape(x)``, return ``x``.

    Used to pre-fill a plain "file name contains" box when editing a rule
    whose name match was built from literal text rather than a hand-written
    regex.
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


# Old private spellings, kept so dialogs can be moved over without a rename.
_heading = heading
_dim = dim
_listbox = listbox
_scroll = scroll
_icon_button = icon_button
_color_dot = color_dot
_human_size = human_size
_split_exts = split_exts
_unescape_simple = unescape_simple
