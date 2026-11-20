"""Static count proof for the Plan page.

The old preview drew a moving Cairo scene. This panel reports the same
information as a small table: how many files would land in each category.
Nothing here paints on a tick callback or moves on its own.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .widgets import eyebrow, index_label  # noqa: E402


def _as_count(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


class ProofPanel(Gtk.Box):
    """A read-only table of destination counts for one context."""

    def __init__(self, root, counts, controller):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.root = root
        self.counts = {str(name): _as_count(count)
                       for name, count in dict(counts or {}).items()
                       if _as_count(count) > 0}
        self.controller = controller
        self.add_css_class("funnel-proof")
        self.set_margin_top(12)
        self.set_margin_bottom(6)
        self._build()

    def _build(self) -> None:
        total = sum(self.counts.values())
        if not total:
            empty = Gtk.Label(label="Nothing to move in this folder right now.",
                              xalign=0)
            empty.add_css_class("dim-label")
            empty.add_css_class("caption")
            empty.set_wrap(True)
            self.append(empty)
            return

        total_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        total_row.append(index_label("TOTAL"))
        number = Gtk.Label(label=str(total), xalign=0)
        number.add_css_class("funnel-proof-count")
        total_row.append(number)
        unit = Gtk.Label(label="file(s) would move", xalign=0)
        unit.add_css_class("dim-label")
        total_row.append(unit)
        self.append(total_row)

        bar = Gtk.ProgressBar()
        bar.set_fraction(1.0)
        bar.set_show_text(False)
        bar.add_css_class("funnel-proof-bar")
        self.append(bar)

        self.append(eyebrow("By destination"))
        for name, count in sorted(self.counts.items(), key=lambda item: (-item[1], item[0])):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            label = Gtk.Label(label=f"{name}/", xalign=0, hexpand=True,
                              ellipsize=3)
            row.append(label)
            value = Gtk.Label(label=str(count), xalign=1)
            value.add_css_class("funnel-proof-count")
            value.set_width_chars(4)
            row.append(value)
            meter = Gtk.ProgressBar()
            meter.set_fraction(count / float(total))
            meter.set_show_text(False)
            meter.set_size_request(64, -1)
            meter.add_css_class("funnel-proof-bar")
            row.append(meter)
            self.append(row)


class SortPreview(ProofPanel):
    """Compatibility constructor for the retired animated preview.

    Old callers pass ``(files, matched, folder, colors=None)``. The file lists
    are used only to compute counts; colours and timing are ignored.
    """

    def __init__(self, files, matched, folder, colors=None):
        matched = list(matched or [])
        counts = {folder: len(matched)} if matched else {}
        super().__init__(folder, counts, None)

    def _build(self) -> None:
        super()._build()
        note = Gtk.Label(label="Static count proof", xalign=0)
        note.add_css_class("dim-label")
        note.add_css_class("caption")
        self.append(note)
