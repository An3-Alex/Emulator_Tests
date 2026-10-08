"""Size-limited diagnostic logs: fixed file, one old copy, reuse after the limit."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from capped_log import LOG_LIMIT, CappedTextLog, previous_path, retire_previous


class CappedLogTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "database-events.log"

    def test_limit_is_ten_megabytes(self):
        self.assertEqual(LOG_LIMIT, 10 * 1024 * 1024)

    def test_previous_run_becomes_the_single_old_copy(self):
        self.assertEqual(previous_path(self.path).name, "database-events.old.log")
        retire_previous(self.path)  # nothing to keep yet
        for run in ("first", "second", "third"):
            retire_previous(self.path)
            with CappedTextLog(self.path) as log:
                log.write(f"{run}\n")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "third\n")
        self.assertEqual(previous_path(self.path).read_text(encoding="utf-8"), "second\n")
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()),
                         ["database-events.log", "database-events.old.log"])

    def test_log_restarts_in_place_after_limit(self):
        with CappedTextLog(self.path, limit=100) as log:
            for index in range(30):
                print(f"line {index:02d}", file=log, flush=True)
            # The same file object stays valid; the file never exceeds the limit.
            self.assertLessEqual(self.path.stat().st_size, 100 + 200)
        text = self.path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("--- Log nach"))
        self.assertIn("line 29", text)
        old = previous_path(self.path).read_text(encoding="utf-8")
        self.assertIn("line", old)
        self.assertNotIn("line 29", old)

    def test_reader_holding_the_log_open_does_not_block_rollover(self):
        with CappedTextLog(self.path, limit=300) as log, self.path.open("r", encoding="utf-8") as reader:
            log.write("a" * 250 + "\n")
            self.assertEqual(reader.read(), "a" * 250 + "\n")
            log.write("b" * 60 + "\n")
            # The viewer sees the shrunken file and starts over from the beginning.
            self.assertLess(self.path.stat().st_size, reader.tell())
            reader.seek(0)
            self.assertIn("b" * 60, reader.read())

    @unittest.skipUnless(os.name == "nt", "Windows refuses to rename open files")
    def test_log_held_open_by_an_old_viewer_does_not_block_the_start(self):
        self.path.write_text("previous run\n", encoding="utf-8")
        with self.path.open("r", encoding="utf-8") as viewer:
            retire_previous(self.path)  # may be refused while open; never raises
            with CappedTextLog(self.path) as log:
                log.write("new run\n")
            viewer.seek(0)
            self.assertEqual(viewer.read(), "new run\n")

    def test_traceback_output_is_accepted(self):
        import traceback
        with CappedTextLog(self.path) as log:
            try:
                raise RuntimeError("bridge failed")
            except RuntimeError:
                traceback.print_exc(file=log)
        self.assertIn("RuntimeError: bridge failed", self.path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
