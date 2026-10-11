"""Tests for the self-updater. No network: the GitHub API and download
calls are replaced with fakes, so these run in CI without a connection."""

import os
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


def _release(tag: str, draft: bool = False) -> dict:
    return {"tag_name": tag, "tarball_url": "https://example.invalid/tarball",
            "draft": draft}


class VersionLogicTests(unittest.TestCase):
    def test_version_tuple(self):
        self.assertEqual(updater.version_tuple("v2.5.0"), (2, 5, 0))
        self.assertEqual(updater.version_tuple("2.5.0-rc1"), (2, 5, 0))
        self.assertEqual(updater.version_tuple("junk"), (0, 0, 0))

    def test_channel_of_accepts_both_spellings(self):
        cases = {
            "3.0.0.dev0": "indev", "v3.0.0-indev.5": "indev",
            "3.0.0a1": "alpha", "v3.0.0-alpha.1": "alpha",
            "3.0.0b2": "beta", "v3.0.0-beta.2": "beta",
            "3.0.0rc1": "rc", "v3.0.0-rc.1": "rc",
            "3.0.0": "release", "v3.0.0": "release",
        }
        for version, channel in cases.items():
            self.assertEqual(updater.channel_of(version), channel, version)

    def test_version_key_orders_within_a_release(self):
        order = ["3.0.0.dev0", "3.0.0a1", "3.0.0a2", "3.0.0b1",
                 "3.0.0rc1", "3.0.0"]
        keys = [updater.version_key(v) for v in order]
        self.assertEqual(keys, sorted(keys))
        self.assertLess(updater.version_key("3.0.0"), updater.version_key("3.0.1"))

    def test_version_key_handles_dashed_tags(self):
        self.assertEqual(updater.version_key("v3.0.0-rc.1"),
                         updater.version_key("3.0.0rc1"))
        self.assertLess(updater.version_key("v3.0.0-beta.1"),
                        updater.version_key("v3.0.0-rc.1"))


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self._real_json = updater._http_json
        self._real_download = updater._download
        self._real_install_dir = updater.install_dir
        self._real_install_kind = updater.install_kind
        self._tmp = Path(tempfile.mkdtemp(prefix="gs-update-test-"))

    def tearDown(self):
        updater._http_json = self._real_json
        updater._download = self._real_download
        updater.install_dir = self._real_install_dir
        updater.install_kind = self._real_install_kind

    def fake_releases(self, tags) -> None:
        releases = [_release(t) for t in tags]
        updater._http_json = lambda url, timeout=15: releases

    def next_tag(self, offset: int = 1) -> str:
        """A tag strictly newer than the running version, same channel."""
        major = updater.version_tuple(updater.__version__)[0] + offset
        channel = updater.channel_of(updater.__version__)
        suffix = {"indev": "-indev.1", "alpha": "-alpha.1",
                  "beta": "-beta.1", "rc": "-rc.1", "release": ""}[channel]
        return f"v{major}.0.0{suffix}"

    # -- checking -------------------------------------------------------

    def test_check_no_network(self):
        updater._http_json = lambda url, timeout=15: None
        current, latest, needed = updater.check()
        self.assertEqual(latest, None)
        self.assertFalse(needed)
        self.assertEqual(current, updater.__version__)

    def test_check_finds_update_in_channel(self):
        tag = self.next_tag()
        self.fake_releases([tag])
        _, latest, needed = updater.check()
        self.assertEqual(latest, tag)
        self.assertTrue(needed)

    def test_check_ignores_other_channels(self):
        # Only *other* channels are present, so nothing should be offered.
        my_channel = updater.channel_of(updater.__version__)
        other = [f"v99.0.0-{ch}.1" for ch in ("indev", "alpha", "beta", "rc")
                 if ch != my_channel]
        if my_channel != "release":
            other.append("v99.0.0")   # stable is a different channel too
        self.fake_releases(other)
        _, latest, needed = updater.check()
        self.assertIsNone(latest)
        self.assertFalse(needed)

    def test_check_ignores_drafts(self):
        updater._http_json = lambda url, timeout=15: [
            _release(self.next_tag(), draft=True)]
        _, latest, needed = updater.check()
        self.assertIsNone(latest)
        self.assertFalse(needed)

    def test_check_picks_newest_within_channel(self):
        channel = updater.channel_of(updater.__version__)
        suffix = {"indev": "-indev", "alpha": "-alpha",
                  "beta": "-beta", "rc": "-rc", "release": ""}[channel]
        older, newer = f"v90.0.0{suffix}.1", f"v91.0.0{suffix}.1"
        self.fake_releases([older, newer])
        _, latest, _needed = updater.check()
        self.assertEqual(latest, newer)

    # -- installing -----------------------------------------------------

    def test_dry_run(self):
        tag = self.next_tag()
        self.fake_releases([tag])
        updater.install_dir = lambda: self._tmp
        ok, msg = updater.update(dry_run=True)
        self.assertTrue(ok)
        self.assertIn(f"→ {tag}", msg)

    def test_already_latest(self):
        self.fake_releases([f"v{updater.__version__}"])
        ok, msg = updater.update(dry_run=True)
        self.assertTrue(ok)
        self.assertIn("already up to date", msg)

    def test_flatpak_defers_to_flatpak_update(self):
        tag = self.next_tag()
        self.fake_releases([tag])
        updater.install_kind = lambda: "flatpak"
        ok, msg = updater.update()
        self.assertFalse(ok)
        self.assertIn("flatpak update", msg)
        self.assertIn(updater.FLATPAK_ID, msg)

    def test_full_update(self):
        newer = self.next_tag()
        archive = self._tmp / "release.tar.gz"
        _make_tarball(archive, newer)
        self.fake_releases([newer])
        updater.install_dir = lambda: self._tmp
        updater.install_kind = lambda: "script"
        updater._download = lambda url, dest, timeout=60: shutil.copy2(
            archive, dest)
        # pretend there is an existing install with stale bytecode
        (self._tmp / "gnu_sorter").mkdir(exist_ok=True)
        (self._tmp / "gnu_sorter" / "__pycache__").mkdir(exist_ok=True)
        (self._tmp / "gnu_sorter" / "__pycache__" / "x.pyc").write_bytes(b"x")
        (self._tmp / "old_thing.txt").write_text("gone")
        ok, msg = updater.update()
        self.assertTrue(ok, msg)
        self.assertEqual((self._tmp / "README.md").read_text(), f"# {newer}\n")
        self.assertTrue((self._tmp / "main.py").exists())
        self.assertFalse((self._tmp / "gnu_sorter" / "__pycache__").exists())
        self.assertFalse((self._tmp / "old_thing.txt").exists())

    # -- install detection ---------------------------------------------

    def test_install_dir_detection(self):
        root = Path(updater.install_dir() or "")
        self.assertTrue((root / "gnu_sorter").is_dir())
        self.assertTrue((root / "main.py").is_file())

    def test_install_kind_flatpak_env(self):
        with mock.patch.dict(os.environ, {"FLATPAK_ID": updater.FLATPAK_ID}):
            self.assertEqual(updater.install_kind(), "flatpak")

    def test_install_kind_script_for_a_checkout(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FLATPAK_ID", None)
            self.assertEqual(updater.install_kind(), "script")


if __name__ == "__main__":
    unittest.main()
