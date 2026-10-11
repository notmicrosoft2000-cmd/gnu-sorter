"""Guards the "one version source" invariant introduced in Phase 4.

The version lives only in ``gnu_sorter/__init__.py``; ``pyproject.toml`` reads
it dynamically. These tests fail loudly if someone re-introduces a second
literal or an unparseable version string.
"""

import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tomllib  # noqa: E402

import gnu_sorter  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# PEP 440-ish: MAJOR.MINOR.PATCH with an optional pre-release/dev suffix.
_VERSION_RE = re.compile(
    r"^\d+\.\d+\.\d+((a|b|rc)\d+|\.dev\d+|\.post\d+)?$")


class VersionTests(unittest.TestCase):
    def test_version_is_pep440ish(self):
        self.assertRegex(gnu_sorter.__version__, _VERSION_RE)

    def test_pyproject_has_no_static_version(self):
        data = tomllib.loads((ROOT / "pyproject.toml").read_text())
        project = data.get("project", {})
        self.assertNotIn(
            "version", project,
            "pyproject [project] must not hard-code a version; use dynamic.")
        self.assertIn("version", project.get("dynamic", []))

    def test_pyproject_reads_the_single_source(self):
        data = tomllib.loads((ROOT / "pyproject.toml").read_text())
        attr = data["tool"]["setuptools"]["dynamic"]["version"]["attr"]
        self.assertEqual(attr, "gnu_sorter.__version__")


if __name__ == "__main__":
    unittest.main()
