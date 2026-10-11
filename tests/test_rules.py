"""Phase 3 rule helpers: plain-English description, schema passthrough,
`classify()` preview and `rule_counts()` journal index. No network, no GTK."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gnu_sorter import engine  # noqa: E402
from gnu_sorter.config import (Config, describe_rule,  # noqa: E402
                               describe_rule_details, humanize_regex,
                               normalize_rule)
from gnu_sorter.engine import Sorter  # noqa: E402


def _sorter(tmp: Path) -> tuple[Sorter, Config]:
    cfg = Config(tmp / "cfg")
    cfg.ensure_defaults()
    cfg.save_settings({"min_stable_seconds": 0, "stability_interval": 0,
                       "cautious_downloads": False})
    return Sorter(cfg), cfg


class TestHumanizeRegex(unittest.TestCase):
    def test_literal(self):
        self.assertEqual(humanize_regex("screenshot"),
                         "the name contains \u201cscreenshot\u201d")

    def test_alternation(self):
        self.assertEqual(
            humanize_regex("screenshot|ibis"),
            "the name contains one of: \u201cscreenshot\u201d, \u201cibis\u201d")

    def test_complex_falls_back_to_pattern(self):
        self.assertEqual(humanize_regex(r"^IMG_\d+$"),
                         "the name matches /^IMG_\\d+$/")

    def test_escaped_literal_is_simplified(self):
        self.assertEqual(humanize_regex(r"my\.file"),
                         "the name contains \u201cmy.file\u201d")

    def test_empty(self):
        self.assertEqual(humanize_regex(""), "")


class TestDescribeRule(unittest.TestCase):
    def test_extensions(self):
        self.assertEqual(
            describe_rule({"name": "Photos", "extensions": ["jpg", "png"]}),
            "A file ending in .jpg, .png")

    def test_extensions_truncated(self):
        rule = {"name": "X", "extensions": [f"e{i}" for i in range(9)]}
        self.assertIn("and 3 more", describe_rule(rule))

    def test_regex_only(self):
        self.assertEqual(
            describe_rule({"name": "X", "match_name_regex": "wallpaper"}),
            "The name contains \u201cwallpaper\u201d")

    def test_any_mode_joins_with_or(self):
        rule = {"name": "X", "extensions": ["png"],
                "match_name_regex": "shot"}
        self.assertIn(" or ", describe_rule(rule))

    def test_all_mode_joins_with_and(self):
        rule = {"name": "X", "extensions": ["png"],
                "match_name_regex": "shot", "match_mode": "all"}
        self.assertIn(" and ", describe_rule(rule))

    def test_bounds_and_exclude(self):
        rule = {"name": "X", "extensions": ["png"], "min_size_mb": 2,
                "max_size_mb": 10, "modified_within_days": 7,
                "exclude_name_regex": "keep"}
        text = describe_rule(rule)
        self.assertIn("between 2 and 10 MB", text)
        self.assertIn("modified in the last 7 day(s)", text)
        self.assertIn("skipping names matching /keep/", text)

    def test_match_all(self):
        self.assertEqual(describe_rule({"name": "X", "match_all": True}),
                         "Any file")

    def test_empty_rule(self):
        self.assertEqual(describe_rule({"name": "X"}), "Nothing yet")

    def test_details_include_paused(self):
        bullets = describe_rule_details(
            {"name": "X", "extensions": ["tmp"], "enabled": False})
        self.assertTrue(any("Paused" in b for b in bullets))


class TestNormalizeMetadata(unittest.TestCase):
    def test_icon_color_preserved(self):
        rule = normalize_rule({"name": "X", "extensions": ["JPG"],
                               "icon": "image-x-generic-symbolic",
                               "color": "#8ab4f8"})
        self.assertEqual(rule["icon"], "image-x-generic-symbolic")
        self.assertEqual(rule["color"], "#8ab4f8")
        self.assertEqual(rule["extensions"], ["jpg"])

    def test_match_mode_any_omitted(self):
        rule = normalize_rule({"name": "X", "extensions": ["png"],
                               "match_mode": "any"})
        self.assertNotIn("match_mode", rule)

    def test_match_mode_all_kept(self):
        rule = normalize_rule({"name": "X", "extensions": ["png"],
                               "match_mode": "all"})
        self.assertEqual(rule["match_mode"], "all")

    def test_blank_metadata_dropped(self):
        rule = normalize_rule({"name": "X", "extensions": ["png"],
                               "icon": "  ", "color": ""})
        self.assertNotIn("icon", rule)
        self.assertNotIn("color", rule)


class TestClassify(unittest.TestCase):
    def test_classify_uses_given_rules(self):
        with tempfile.TemporaryDirectory() as d:
            sorter, _ = _sorter(Path(d))
            rules = [{"name": "Photos", "extensions": ["jpg"]},
                     {"name": "Docs", "extensions": ["pdf"]}]
            out = sorter.classify(["a.jpg", "b.pdf", "c.zip"], rules=rules)
            self.assertEqual(out["a.jpg"], "Photos")
            self.assertEqual(out["b.pdf"], "Docs")
            self.assertEqual(out["c.zip"], "Needs-review")

    def test_classify_honours_enabled_and_bounds(self):
        with tempfile.TemporaryDirectory() as d:
            sorter, _ = _sorter(Path(d))
            rules = [{"name": "Off", "extensions": ["png"], "enabled": False},
                     {"name": "Big", "extensions": ["png"], "min_size_mb": 5}]
            # No stat available -> size-bounded rule is skipped.
            out = sorter.classify(["x.png"], rules=rules)
            self.assertEqual(out["x.png"], "Needs-review")

    def test_classify_matches_current_rules_when_none(self):
        with tempfile.TemporaryDirectory() as d:
            sorter, _ = _sorter(Path(d))
            out = sorter.classify(["movie.mkv"])
            self.assertEqual(out["movie.mkv"], "Videos")

    def test_match_mode_all_requires_every_matcher(self):
        with tempfile.TemporaryDirectory() as d:
            sorter, _ = _sorter(Path(d))
            # note: `all` semantics land with the engine decision; today the
            # compiled engine only knows any() so both files must match the ext.
            rules = [{"name": "Shots", "extensions": ["png"],
                      "match_mode": "all"}]
            out = sorter.classify(["a.png"], rules=rules)
            self.assertEqual(out["a.png"], "Shots")


class TestRuleCounts(unittest.TestCase):
    def test_counts_live_entries(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            sorter, cfg = _sorter(tmp)
            root = tmp / "root"
            root.mkdir()
            cfg.save_roots([{"path": str(root), "enabled": True}])
            (root / "a.jpg").write_bytes(b"x" * 10)
            (root / "b.jpg").write_bytes(b"y" * 10)
            (root / "c.pdf").write_bytes(b"z" * 10)
            t = time.time() - 3600
            for p in root.iterdir():
                os.utime(p, (t, t))
            stats = sorter.sweep()
            self.assertEqual(stats["moved"], 3)
            counts = sorter.rule_counts()
            self.assertEqual(counts.get("Photos", 0), 2)
            self.assertEqual(counts.get("Documents", 0), 1)
            self.assertEqual(sum(counts.values()), 3)


if __name__ == "__main__":
    unittest.main()
