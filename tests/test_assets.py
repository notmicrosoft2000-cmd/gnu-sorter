"""Guards the shipped icon/desktop assets (Phase 4.2).

The tray and the shell icon both resolve from files in ``data/icons``; the
desktop entry is the single source of truth the installer copies. These tests
fail if the ladder or the entry drifts.
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ICONS = ROOT / "data" / "icons"
SIZES = [16, 22, 24, 32, 48, 64, 128, 256, 512]


class AssetTests(unittest.TestCase):
    def test_master_icon_present(self):
        self.assertTrue((ICONS / "gnu-sorter.svg").is_file())
        # Raster fallback used by the system tray.
        self.assertTrue((ICONS / "gnu-sorter.png").is_file())

    def test_hicolor_ladder_present(self):
        self.assertTrue((ICONS / "hicolor" / "scalable" / "apps"
                         / "gnu-sorter.svg").is_file())
        for sz in SIZES:
            png = ICONS / "hicolor" / f"{sz}x{sz}" / "apps" / "gnu-sorter.png"
            self.assertTrue(png.is_file(), f"missing {png}")

    def test_desktop_entry_is_single_source(self):
        text = (ROOT / "data" / "gnu-sorter.desktop").read_text()
        self.assertIn("Name=Funnel", text)
        self.assertIn("Icon=gnu-sorter", text)
        self.assertIn("StartupWMClass=io.neptunepc.Funnel", text)

    def test_installer_copies_committed_assets(self):
        install = (ROOT / "install.sh").read_text()
        self.assertIn("data/icons/hicolor", install)
        self.assertIn("data/gnu-sorter.desktop", install)


if __name__ == "__main__":
    unittest.main()
