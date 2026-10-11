"""Journal v2 tests: rich entries, undo/restore semantics, selectors, repair
and rotation. No network; sweeps use interval 0 so nothing sleeps."""

import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gnu_sorter import engine  # noqa: E402
from gnu_sorter.config import Config  # noqa: E402
from gnu_sorter.engine import Sorter  # noqa: E402

_TS = "%Y-%m-%d %H:%M:%S"


def _sorter(tmp: Path) -> tuple[Sorter, Config]:
    cfg = Config(tmp / "cfg")
    cfg.ensure_defaults()
    cfg.save_settings({"min_stable_seconds": 0, "stability_interval": 0,
                       "cautious_downloads": False})
    return Sorter(cfg), cfg


def _old(path: Path) -> Path:
    t = time.time() - 3600
    path.write_bytes(b"PDF" * 100)
    os.utime(path, (t, t))
    return path


def _write_history(cfg: Config, lines: list[str]) -> None:
    cfg.history_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.history_path.write_text("".join(lines), encoding="utf-8")


class JournalTests(unittest.TestCase):

    def test_rich_entry_fields(self):
        with tempfile.TemporaryDirectory() as td:
            sorter, _cfg = _sorter(Path(td))
            root = Path(td) / "In"
            root.mkdir()
            _old(root / "report.pdf")
            stats = sorter.sweep(roots=[root])
            self.assertEqual(stats["moved"], 1)
            recs = sorter.records()
            self.assertEqual(len(recs), 1)
            r = recs[0]
            self.assertEqual(r["v"], 2)
            self.assertTrue(r["id"])
            self.assertTrue(r["batch"])
            self.assertEqual(r["rule"], "Documents")
            self.assertEqual(r["root"], str(root))
            self.assertGreater(r["size"], 0)
            self.assertGreater(r["mtime"], 0)
            self.assertNotEqual(r["ts"], "")

    def test_sweep_one_batch_all_entries(self):
        with tempfile.TemporaryDirectory() as td:
            sorter, _cfg = _sorter(Path(td))
            root = Path(td) / "In"
            root.mkdir()
            _old(root / "a.pdf")
            _old(root / "b.jpg")
            sorter.sweep(roots=[root])
            recs = sorter.records()
            self.assertEqual(len(recs), 2)
            self.assertEqual(len({r["batch"] for r in recs}), 1)

    def test_undo_restores_and_empties(self):
        with tempfile.TemporaryDirectory() as td:
            sorter, _cfg = _sorter(Path(td))
            root = Path(td) / "In"
            root.mkdir()
            src = _old(root / "notes.txt")
            sorter.sweep(roots=[root])
            self.assertFalse(src.exists())
            self.assertEqual(sorter.undo(), 1)
            self.assertTrue(src.exists())
            self.assertEqual(sorter.records(), [])

    def test_undo_collision_uses_unique_path(self):
        with tempfile.TemporaryDirectory() as td:
            sorter, _cfg = _sorter(Path(td))
            root = Path(td) / "In"
            root.mkdir()
            src = _old(root / "song.mp3")
            sorter.sweep(roots=[root])
            src.write_bytes(b"different file")       # newcomer at the old spot
            self.assertEqual(sorter.undo(), 1)
            self.assertEqual(src.read_bytes(), b"different file")
            self.assertTrue((root / "song (1).mp3").exists())

    def test_undo_skips_and_drops_already_back(self):
        with tempfile.TemporaryDirectory() as td:
            sorter, cfg = _sorter(Path(td))
            tmp = Path(td)
            src = tmp / "a.txt"
            dest = tmp / "done" / "b.txt"
            dest.parent.mkdir()
            src.write_bytes(b"x" * 10)
            dest.write_bytes(b"x" * 10)
            st = src.stat()
            rec = engine._journal_record(
                "2026-10-11 00:00:00", str(src), str(dest), size=st.st_size,
                mtime=st.st_mtime, rule="X", root=str(tmp), batch="b1")
            _write_history(cfg, [engine._journal_line_v2(rec)])
            self.assertEqual(sorter.undo(), 0)        # already back → skipped
            self.assertEqual(sorter.records(), [])    # and dropped from journal

    def test_records_selectors(self):
        with tempfile.TemporaryDirectory() as td:
            sorter, cfg = _sorter(Path(td))
            tmp = Path(td)
            now = datetime.now()
            dest_a = tmp / "In" / "Documents" / "a.pdf"
            dest_b = tmp / "In" / "Videos" / "b.mp4"
            dest_a.parent.mkdir(parents=True)
            dest_b.parent.mkdir(parents=True)
            src_a, src_b = tmp / "a.pdf", tmp / "b.mp4"
            rec_a = engine._journal_record(
                (now - timedelta(minutes=10)).strftime(_TS), str(src_a),
                str(dest_a), rule="Documents", root=str(tmp / "In"),
                batch="ba")
            rec_b = engine._journal_record(
                (now - timedelta(minutes=1)).strftime(_TS), str(src_b),
                str(dest_b), rule="Videos", root=str(tmp / "In"), batch="bb")
            _write_history(cfg, [engine._journal_line_v2(rec_a),
                                 engine._journal_line_v2(rec_b)])

            by_rule = sorter.records(rule="videos")
            self.assertEqual([r["dest"] for r in by_rule], [str(dest_b)])

            by_folder = sorter.records(folder=str(tmp / "In" / "Documents"))
            self.assertEqual([r["dest"] for r in by_folder], [str(dest_a)])

            since_recent = sorter.records(since="5m")
            self.assertEqual([r["dest"] for r in since_recent], [str(dest_b)])

            before_cut = sorter.records(before=(now - timedelta(minutes=5))
                                        .strftime(_TS))
            self.assertEqual([r["dest"] for r in before_cut], [str(dest_a)])

            limited = sorter.records(limit=1)
            self.assertEqual([r["dest"] for r in limited], [str(dest_b)])

    def test_undo_dry_run_counts_only(self):
        with tempfile.TemporaryDirectory() as td:
            sorter, cfg = _sorter(Path(td))
            tmp = Path(td)
            dest = tmp / "In" / "Documents" / "a.pdf"
            dest.parent.mkdir(parents=True)
            dest.write_bytes(b"x")
            rec = engine._journal_record(
                "2026-10-11 00:00:00", str(tmp / "a.pdf"), str(dest),
                rule="Documents", root=str(tmp / "In"), batch="b1")
            _write_history(cfg, [engine._journal_line_v2(rec)])
            self.assertEqual(sorter.undo(dry_run=True, rule="documents"), 1)
            self.assertTrue(dest.exists())
            self.assertEqual(len(sorter.records()), 1)  # dry-run touched nothing

    def test_legacy_v1_and_tab_lines(self):
        with tempfile.TemporaryDirectory() as td:
            sorter, cfg = _sorter(Path(td))
            lines = [
                "2026-10-10 00:00:00\t/tmp/src1.txt\t/tmp/d1.txt\n",
                "{\"ts\": \"2026-10-11 00:00:00\", "
                "\"src\": \"/tmp/src2.txt\", \"dest\": \"/tmp/d2.txt\"}\n",
            ]
            _write_history(cfg, lines)
            self.assertEqual(len(sorter.records()), 2)
            self.assertEqual(len(sorter.history()), 2)
            self.assertEqual(sorter.history()[0][0], "2026-10-10 00:00:00")

    def test_repair_drops_malformed(self):
        with tempfile.TemporaryDirectory() as td:
            sorter, cfg = _sorter(Path(td))
            lines = [
                "this is not json\n",
                "{\"ts\": \"x\"}\n",                        # no src/dest
                "2026-10-11 00:00:00\t/tmp/s\t/tmp/d\n",    # valid legacy
                "{\"v\": 2, \"id\": \"i\", \"ts\": \"2026-10-11 00:00:01\", "
                "\"src\": \"/a\", \"dest\": \"/b\"}\n",     # valid v2
            ]
            _write_history(cfg, lines)
            self.assertEqual(sorter.repair_journal(), 2)
            self.assertEqual(len(sorter.records()), 2)

    def test_journal_index_counts_and_buckets(self):
        with tempfile.TemporaryDirectory() as td:
            sorter, cfg = _sorter(Path(td))
            tmp, root = Path(td), Path(td) / "In"
            (root / "Documents").mkdir(parents=True)
            (root / "Videos").mkdir(parents=True)
            now = datetime.now().strftime(_TS)
            recs = [
                engine._journal_record(now, str(tmp / "a.pdf"),
                                       str(root / "Documents" / "a.pdf"),
                                       rule="Documents", root=str(root),
                                       batch="b1"),
                engine._journal_record(now, str(tmp / "b.pdf"),
                                       str(root / "Documents" / "b.pdf"),
                                       rule="Documents", root=str(root),
                                       batch="b1"),
                engine._journal_record(now, str(tmp / "c.mp4"),
                                       str(root / "Videos" / "c.mp4"),
                                       rule="Videos", root=str(root),
                                       batch="b2"),
            ]
            _write_history(cfg, [engine._journal_line_v2(r) for r in recs])
            idx = sorter.journal_index()
            self.assertEqual(len(idx), 3)
            self.assertEqual(idx.counts(), {"Documents": 2, "Videos": 1})
            self.assertEqual(len(idx.by_batch["b1"]), 2)
            self.assertEqual(len(idx.by_folder[str(root / "Documents")]), 2)
            self.assertEqual(sorter.rule_counts(), {"Documents": 2, "Videos": 1})

    def test_search_by_name_and_batch(self):
        with tempfile.TemporaryDirectory() as td:
            sorter, cfg = _sorter(Path(td))
            tmp, root = Path(td), Path(td) / "In"
            (root / "Documents").mkdir(parents=True)
            (root / "Photos").mkdir(parents=True)
            now = datetime.now().strftime(_TS)
            recs = [
                engine._journal_record(now, str(tmp / "a.pdf"),
                                       str(root / "Documents" / "a.pdf"),
                                       rule="Documents", batch="b1"),
                engine._journal_record(now, str(tmp / "screenshot_2026.png"),
                                       str(root / "Photos" / "screenshot_2026.png"),
                                       rule="Photos", batch="b2"),
                engine._journal_record(now, str(tmp / "holiday.png"),
                                       str(root / "Photos" / "holiday.png"),
                                       rule="Photos", batch="b2"),
            ]
            _write_history(cfg, [engine._journal_line_v2(r) for r in recs])
            self.assertEqual([r["src"] for r in sorter.records(name="*.pdf")],
                             [str(tmp / "a.pdf")])
            self.assertEqual(len(sorter.records(name="screenshot")), 1)
            self.assertEqual(len(sorter.records(batch="b2")), 2)
            self.assertEqual(len(sorter.records(name="*.png", batch="b2")), 2)

    def test_backup_journal(self):
        with tempfile.TemporaryDirectory() as td:
            sorter, cfg = _sorter(Path(td))
            rec = engine._journal_record(
                "2026-10-11 00:00:00", "/a", "/b", rule="X", batch="b")
            _write_history(cfg, [engine._journal_line_v2(rec)])
            bak = sorter.backup_journal()
            self.assertIsNotNone(bak)
            self.assertEqual(bak.name, "history.log.bak")
            self.assertEqual(bak.read_text(), cfg.history_path.read_text())
            # nothing to back up on a clean config directory
            empty, _ = _sorter(Path(td) / "empty")
            self.assertIsNone(empty.backup_journal())

    def test_export_import_journal(self):
        with tempfile.TemporaryDirectory() as td:
            sorter, cfg = _sorter(Path(td))
            tmp = Path(td)
            now = datetime.now().strftime(_TS)
            recs = [
                engine._journal_record(now, "/a.pdf", "/doc/a.pdf",
                                       rule="Documents", batch="b1"),
                engine._journal_record(now, "/b.mp4", "/vid/b.mp4",
                                       rule="Videos", batch="b1"),
            ]
            _write_history(cfg, [engine._journal_line_v2(r) for r in recs])
            out = tmp / "export.log"
            self.assertEqual(sorter.export_journal(out, rule="Documents"), 1)
            self.assertEqual(sorter.export_journal(tmp / "all.log"), 2)
            # re-importing the same entries is a no-op (ids dedupe)
            self.assertEqual(sorter.import_journal(out), 0)
            # a fresh sorter picks them up
            fresh, _ = _sorter(tmp / "fresh")
            self.assertEqual(fresh.import_journal(out), 1)
            self.assertEqual(len(fresh.records()), 1)
            self.assertEqual(fresh.records()[0]["rule"], "Documents")

    def test_restore_plan_and_target(self):
        with tempfile.TemporaryDirectory() as td:
            sorter, cfg = _sorter(Path(td))
            tmp, root = Path(td), Path(td) / "In"
            (root / "Documents").mkdir(parents=True)
            now = datetime.now().strftime(_TS)
            dest_a = root / "Documents" / "a.txt"
            dest_b = root / "Documents" / "b.txt"
            dest_a.write_bytes(b"x" * 5)
            dest_b.write_bytes(b"x" * 5)
            recs = [
                engine._journal_record(now, str(tmp / "a.txt"), str(dest_a),
                                       size=5, rule="Documents", batch="b1"),
                engine._journal_record(now, str(tmp / "b.txt"), str(dest_b),
                                       size=5, rule="Documents", batch="b1"),
            ]
            _write_history(cfg, [engine._journal_line_v2(r) for r in recs])

            target = tmp / "Back"
            target.mkdir()
            (target / "a.txt").write_bytes(b"older")     # force a collision
            plan = sorter.restore_plan(sorter._read_journal(), target=str(target))
            self.assertEqual(len(plan), 2)
            by_name = {p["src"].name: p for p in plan}
            self.assertEqual(by_name["a.txt"]["status"], "collision")
            self.assertEqual(by_name["b.txt"]["status"], "restore")

            restored = sorter.undo(rule="Documents", target=str(target))
            self.assertEqual(restored, 2)
            self.assertEqual((target / "a.txt").read_bytes(), b"older")
            self.assertTrue((target / "a (1).txt").exists())
            self.assertTrue((target / "b.txt").exists())
            self.assertEqual(sorter.records(), [])

    def test_rotation_trims(self):
        with tempfile.TemporaryDirectory() as td:
            sorter, _cfg = _sorter(Path(td))
            tmp = Path(td)
            orig_b, orig_k = engine._ROTATE_BYTES, engine._ROTATE_KEEP
            engine._ROTATE_BYTES, engine._ROTATE_KEEP = 200, 5
            try:
                for i in range(8):
                    sorter._journal(tmp / f"f{i}.txt", tmp / f"d{i}.txt",
                                    "X", str(tmp), "batch")
                rows = sorter.records()
                self.assertLessEqual(len(rows), 7)
                self.assertEqual(rows[-1]["src"], str(tmp / "f7.txt"))
            finally:
                engine._ROTATE_BYTES, engine._ROTATE_KEEP = orig_b, orig_k


if __name__ == "__main__":
    unittest.main()