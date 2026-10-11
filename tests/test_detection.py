"""Tests for the extra-cautious download-detection behaviour (v3.0 Phase 1).

Covers progress-suffix recognition, the size-proportional settle window, the
multi-probe stability check and the sweep's "held" (parked, still downloading)
handling. No network; sleeps are kept tiny or monkeypatched away.
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gnu_sorter import engine  # noqa: E402
from gnu_sorter.config import Config  # noqa: E402

_MB = 1024 * 1024


def _config(tmp: Path) -> Config:
    cfg = Config(tmp / "cfg")
    cfg.ensure_defaults()
    cfg.save_settings({
        "min_stable_seconds": 0,
        "stability_interval": 0.05,
        "cautious_downloads": True,
    })
    return cfg


def _old(path: Path, seconds: float = 3600) -> Path:
    t = time.time() - seconds
    os.utime(path, (t, t))
    return path


class DetectionTests(unittest.TestCase):

    def test_looks_in_progress(self):
        for name in ("movie.mp4.part", "x.crdownload", "X.CRDOWNLOAD",
                     "a.opdownload", "b.!qb", "c.ytdl", "d.un~",
                     "e.filepart", "f.partial", "g.download", "h.tmp"):
            self.assertTrue(engine.looks_in_progress(name), name)
        for name in ("movie.mp4", "photo.cr", "note.txt",
                     "backup.downloads", "report.partiality"):
            self.assertFalse(engine.looks_in_progress(name), name)

    def test_settle_window_seconds(self):
        small = 50 * _MB
        self.assertEqual(engine.settle_window_seconds(small, 3.0), 3.0)
        medium = 200 * _MB
        self.assertAlmostEqual(engine.settle_window_seconds(medium, 3.0), 7.0)
        huge = 3 * _MB * 1024  # 3 GB
        self.assertEqual(engine.settle_window_seconds(huge, 3.0), 63.0)
        self.assertEqual(engine.settle_window_seconds(huge, 0), 0.0)

    def test_is_stable_stable_file(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "done.mp4"
            p.write_bytes(b"A" * 1024)
            self.assertTrue(
                engine.is_stable(p, min_seconds=0, interval=0.02,
                                 min_probes=2))

    def test_is_stable_growing_file(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "grow.bin"
            p.write_bytes(b"A" * 1024)
            real_sleep = engine.time.sleep

            def grow(_s):
                with open(p, "ab") as f:
                    f.write(b"B" * 1024)

            engine.time.sleep = grow
            try:
                self.assertFalse(
                    engine.is_stable(p, min_seconds=0, interval=0.02,
                                     min_probes=2))
            finally:
                engine.time.sleep = real_sleep

    def test_sweep_holds_in_progress_suffix(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "Downloads"
            root.mkdir()
            ghost = root / "movie.mp4.part"
            ghost.write_bytes(b"x")
            cfg = _config(Path(td))
            sorter = engine.Sorter(cfg)
            stats = sorter.sweep(roots=[root])
            self.assertEqual(stats["moved"], 0)
            self.assertEqual(stats["held"], 1)
            self.assertIn("movie.mp4.part", stats["held_files"])
            self.assertTrue(ghost.exists())

    def test_sweep_holds_too_fresh_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "Photos"
            root.mkdir()
            p = root / "img001.jpg"
            p.write_bytes(b"x" * 100)  # mtime = now
            cfg = _config(Path(td))
            s = cfg.load_settings()
            s["min_stable_seconds"] = 5
            cfg.save_settings(s)
            sorter = engine.Sorter(cfg)
            stats = sorter.sweep(roots=[root])
            self.assertEqual(stats["moved"], 0)
            self.assertEqual(stats["held"], 1)
            self.assertTrue(p.exists())

    def test_sweep_holds_file_growing_during_confirm(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "In"
            root.mkdir()
            p = root / "archive.zip"
            p.write_bytes(b"A" * 512)
            _old(p)
            cfg = _config(Path(td))
            sorter = engine.Sorter(cfg)
            real_sleep = engine.time.sleep

            def growing(_s):
                with open(p, "ab") as f:
                    f.write(b"B")

            engine.time.sleep = growing
            try:
                stats = sorter.sweep(roots=[root])
            finally:
                engine.time.sleep = real_sleep
            self.assertEqual(stats["moved"], 0)
            self.assertEqual(stats["held"], 1)
            self.assertTrue(p.exists())

    def test_sweep_moves_stable_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "In"
            root.mkdir()
            p = root / "report.pdf"
            p.write_bytes(b"PDF" * 100)
            _old(p)
            cfg = _config(Path(td))
            sorter = engine.Sorter(cfg)
            stats = sorter.sweep(roots=[root])
            self.assertEqual(stats["moved"], 1, stats)
            self.assertFalse(p.exists())


if __name__ == "__main__":
    unittest.main()