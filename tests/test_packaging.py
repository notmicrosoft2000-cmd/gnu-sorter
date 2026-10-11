"""Phase 4.5 packaging guards (Flatpak + AppStream + desktop entry).

These run without network and skip the parts that need optional tools
(``appstreamcli``, ``desktop-file-validate``, PyYAML) so the normal test job
stays green on a bare runner.
"""

import os
import re
import shutil
import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

METAINFO = ROOT / "data" / "io.neptunepc.Funnel.metainfo.xml"
DESKTOP = ROOT / "data" / "gnu-sorter.desktop"
MANIFEST = ROOT / "packaging" / "flatpak" / "io.neptunepc.Funnel.yml"
LAUNCHER = ROOT / "packaging" / "flatpak" / "funnel-launcher.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "flatpak.yml"


def _try_yaml():
    try:
        import yaml  # noqa: F401
        return yaml
    except Exception:
        return None


class AppStreamTests(unittest.TestCase):
    def test_metainfo_parses_and_has_required_fields(self):
        root = ET.parse(METAINFO).getroot()
        self.assertEqual(root.tag, "component")
        self.assertEqual(root.get("type"), "desktop-application")
        get = lambda tag: (root.findtext(tag) or "").strip()  # noqa: E731
        self.assertEqual(get("id"), "io.neptunepc.Funnel")
        self.assertTrue(get("name"))
        self.assertTrue(get("summary"))
        self.assertTrue(get("metadata_license"))
        self.assertTrue(get("project_license"))
        self.assertTrue(root.find("description"))
        launchable = root.find("launchable")
        self.assertIsNotNone(launchable)
        self.assertEqual(launchable.get("type"), "desktop-id")
        self.assertEqual(launchable.text.strip(), "io.neptunepc.Funnel.desktop")

    def test_metainfo_passes_appstreamcli_when_available(self):
        if not shutil.which("appstreamcli"):
            self.skipTest("appstreamcli not installed")
        res = subprocess.run(["appstreamcli", "validate", str(METAINFO)],
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)


class DesktopEntryTests(unittest.TestCase):
    def test_desktop_entry_matches_app_id(self):
        text = DESKTOP.read_text()
        self.assertIn("Name=Funnel", text)
        self.assertIn("StartupWMClass=io.neptunepc.Funnel", text)
        # The Flatpak workflow/sed rewrites this to the app id at build time.
        self.assertIn("Icon=gnu-sorter", text)

    def test_desktop_file_validates_when_available(self):
        if not shutil.which("desktop-file-validate"):
            self.skipTest("desktop-file-validate not installed")
        res = subprocess.run(["desktop-file-validate", str(DESKTOP)],
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)


class FlatpakManifestTests(unittest.TestCase):
    def setUp(self):
        self.yaml = _try_yaml()
        if self.yaml is None:
            self.skipTest("PyYAML not installed")
        self.manifest = self.yaml.safe_load(MANIFEST.read_text())

    def test_identity_and_runtime(self):
        m = self.manifest
        self.assertEqual(m["app-id"], "io.neptunepc.Funnel")
        self.assertEqual(m["runtime"], "org.gnome.Platform")
        self.assertEqual(m["command"], "gnu-sorter")
        # runtime-version must be a string so Flatpak accepts it.
        self.assertIsInstance(m["runtime-version"], str)

    def test_command_installed_by_a_build_step(self):
        steps = "\n".join(self.manifest["modules"][0]["build-commands"])
        self.assertIn("/app/bin/gnu-sorter", steps)

    def test_referenced_source_files_exist(self):
        missing = []
        for step in self.manifest["modules"][0]["build-commands"]:
            for token in re.findall(r"\bdata/\S+|\bpackaging/\S+", step):
                if "${" in token:            # templated inside a shell loop
                    continue
                if not (ROOT / token).exists():
                    missing.append(token)
        self.assertEqual(missing, [], f"manifest references missing files: {missing}")

    def test_metainfo_and_icons_are_installed(self):
        steps = "\n".join(self.manifest["modules"][0]["build-commands"])
        self.assertIn("io.neptunepc.Funnel.metainfo.xml", steps)
        self.assertIn("io.neptunepc.Funnel.png", steps)


class FlatpakWorkflowTests(unittest.TestCase):
    def test_workflow_publishes_to_pages_dir(self):
        text = WORKFLOW.read_text()
        self.assertIn("docs/flatpak", text)
        self.assertIn("build-update-repo", text)
        self.assertIn("org.gnome.Sdk", text)

    def test_launcher_runs_the_app(self):
        text = LAUNCHER.read_text()
        self.assertTrue(text.startswith("#!/bin/sh"))
        self.assertIn("/app/share/gnu-sorter/main.py", text)


class NoAppImageTests(unittest.TestCase):
    def test_appimage_was_dropped(self):
        self.assertFalse((ROOT / "packaging" / "appimage").exists())
        self.assertFalse((ROOT / ".github" / "workflows" / "appimage.yml").exists())


if __name__ == "__main__":
    unittest.main()
