"""Tests for the release helper's version model (tools/release.py).

The script is not a package, so it is loaded from its path. These tests cover
the pure functions only — nothing here touches git or the working tree.
"""

import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "gs_release", ROOT / "tools" / "release.py")
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)


def _on_indev_trunk() -> bool:
    """True when the working copy is the ``main`` (indev) trunk.

    CI exports ``GITHUB_REF``; local runs fall back to the checked-out branch.
    Release tags and pull-request merge refs are not the trunk, so the guard
    below must not fire there — a tagged build legitimately carries its channel.
    """
    ref = os.environ.get("GITHUB_REF")
    if ref:
        return ref == "refs/heads/main"
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return False
    return branch == "main"


class VersionModelTests(unittest.TestCase):
    def test_parse_channels(self):
        self.assertEqual(release.parse("3.0.0.dev0"), (3, 0, 0, "indev", 0))
        self.assertEqual(release.parse("3.0.0a1"), (3, 0, 0, "alpha", 1))
        self.assertEqual(release.parse("3.0.0b2"), (3, 0, 0, "beta", 2))
        self.assertEqual(release.parse("3.0.0rc3"), (3, 0, 0, "rc", 3))
        self.assertEqual(release.parse("3.0.0"), (3, 0, 0, "release", 0))

    def test_render_roundtrip(self):
        for v in ("3.0.0.dev4", "3.0.0a1", "2.1.3b9", "1.0.0rc1", "4.2.0"):
            self.assertEqual(release.render(*release.parse(v)), v)

    def test_tag_name(self):
        self.assertEqual(release.tag_name("3.0.0.dev0"), "v3.0.0-indev.0")
        self.assertEqual(release.tag_name("3.0.0a1"), "v3.0.0-alpha.1")
        self.assertEqual(release.tag_name("3.0.0b1"), "v3.0.0-beta.1")
        self.assertEqual(release.tag_name("3.0.0rc1"), "v3.0.0-rc.1")
        self.assertEqual(release.tag_name("3.0.0"), "v3.0.0")

    def test_bump_resets_to_indev(self):
        self.assertEqual(release.bump("3.0.0a2", "patch"), "3.0.1.dev0")
        self.assertEqual(release.bump("3.0.0", "minor"), "3.1.0.dev0")
        self.assertEqual(release.bump("3.4.2rc1", "major"), "4.0.0.dev0")

    def test_cut_advances_within_channel(self):
        self.assertEqual(release.cut("3.0.0.dev0", "alpha"), "3.0.0a1")
        self.assertEqual(release.cut("3.0.0a1", "alpha"), "3.0.0a2")
        self.assertEqual(release.cut("3.0.0a2", "beta"), "3.0.0b1")
        self.assertEqual(release.cut("3.0.0b3", "rc"), "3.0.0rc1")
        self.assertEqual(release.cut("3.0.0rc2", "release"), "3.0.0")

    def test_repo_version_is_indev(self):
        # main is the indev trunk; a release branch or tag legitimately carries
        # a non-indev channel, so only guard the trunk itself.
        if not _on_indev_trunk():
            self.skipTest("not on the indev trunk (main)")
        self.assertEqual(release.parse(release.read_version())[3], "indev")


if __name__ == "__main__":
    unittest.main()
