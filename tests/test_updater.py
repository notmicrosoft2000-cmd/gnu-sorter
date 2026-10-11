"""Tests for the self-updater. No network: the GitHub API and download
calls are replaced with fakes, so these run in CI without a connection."""

import os
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gnu_sorter import updater  # noqa: E402


def _make_tarball(dest: Path, tag: str) -> None:
    """Build a fake release tarball shaped like GitHub's gnu-sorter-<tag>/."""
    root = Path(tempfile.mkdtemp(prefix="gs-rel-src-"))
    top = root / f"gnu-sorter-{tag.lstrip('v')}"
    (top / "gnu_sorter").mkdir(parents=True)
    (top / "main.py").write_text("#!/usr/bin/env python3\nprint('new')\n")
    (top / "README.md").write_text(f"# {tag}\n")
    with tarfile.open(dest, "w:gz") as tf:
        tf.add(top, arcname=top.name)


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self._real_json = updater._http_json
        self._real_download = updater._download
        self._real_install_dir = updater.install_dir
        self._tmp = Path(tempfile.mkdtemp(prefix="gs-update-test-"))

    def tearDown(self):
        updater._http_json = self._real_json
        updater._download = self._real_download
        updater.install_dir = self._real_install_dir

    def fake_latest(self, tag: str) -> None:
        updater._http_json = lambda url, timeout=15: {
            "tag_name": tag,
            "tarball_url": "https://example.invalid/tarball",
        }

    def test_version_tuple(self):
        self.assertEqual(updater.version_tuple("v2.5.0"), (2, 5, 0))
        self.assertEqual(updater.version_tuple("2.5.0-rc1"), (2, 5, 0))
        self.assertEqual(updater.version_tuple("junk"), (0, 0, 0))

    def test_check_no_network(self):
        updater._http_json = lambda url, timeout=15: None
        current, latest, needed = updater.check()
        self.assertEqual(latest, None)
        self.assertFalse(needed)
        self.assertEqual(current, updater.__version__)

    def test_check_finds_update(self):
        self.fake_latest("v99.0.0")
        _, latest, needed = updater.check()
        self.assertEqual(latest, "v99.0.0")
        self.assertTrue(needed)

    def test_dry_run(self):
        self.fake_latest("v99.0.0")
        updater.install_dir = lambda: self._tmp
        ok, msg = updater.update(dry_run=True)
        self.assertTrue(ok)
        self.assertIn("→ v99.0.0", msg)

    def test_already_latest(self):
        self.fake_latest(f"v{updater.__version__}")
        ok, msg = updater.update(dry_run=True)
        self.assertTrue(ok)
        self.assertIn("already up to date", msg)

    def test_full_update(self):
        archive = self._tmp / "release.tar.gz"
        _make_tarball(archive, "v2.6.0")
        self.fake_latest("v2.6.0")
        updater.install_dir = lambda: self._tmp
        updater._download = lambda url, dest, timeout=60: shutil.copy2(
            archive, dest)
        # pretend there is an existing install with stale bytecode
        (self._tmp / "gnu_sorter").mkdir(exist_ok=True)
        (self._tmp / "gnu_sorter" / "__pycache__").mkdir(exist_ok=True)
        (self._tmp / "gnu_sorter" / "__pycache__" / "x.pyc").write_bytes(b"x")
        (self._tmp / "old_thing.txt").write_text("gone")
        ok, msg = updater.update()
        self.assertTrue(ok, msg)
        self.assertEqual((self._tmp / "README.md").read_text(), "# v2.6.0\n")
        self.assertTrue((self._tmp / "main.py").exists())
        self.assertFalse((self._tmp / "gnu_sorter" / "__pycache__").exists())
        self.assertFalse((self._tmp / "old_thing.txt").exists())

    def test_install_dir_detection(self):
        root = Path(updater.install_dir() or "")
        self.assertTrue((root / "gnu_sorter").is_dir())
        self.assertTrue((root / "main.py").is_file())


if __name__ == "__main__":
    unittest.main()