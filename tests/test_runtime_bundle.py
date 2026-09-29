"""Offline checks for packaged runtime extraction."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))
from runtime_bundle import POWERSHELL_FILES, materialize_runtime, runtime_payload


class RuntimeBundleTests(unittest.TestCase):
    def test_copies_only_runtime_source_and_preserves_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "bundle"
            target = root / "runtime"
            (source / "scripts").mkdir(parents=True)
            for name in POWERSHELL_FILES:
                (source / name).write_text(name, encoding="utf-8")
            (source / "scripts" / "bridge.py").write_text("v1", encoding="utf-8")
            (source / "scripts" / "dump.bin").write_bytes(b"private")
            (target / "logs").mkdir(parents=True)
            (target / "logs" / "old.log").write_text("keep", encoding="utf-8")

            paths = runtime_payload(source)
            self.assertEqual(len(paths), len(POWERSHELL_FILES) + 1)
            materialize_runtime(source, target)
            self.assertEqual((target / "scripts" / "bridge.py").read_text(), "v1")
            self.assertFalse((target / "scripts" / "dump.bin").exists())
            (source / "scripts" / "bridge.py").write_text("v2", encoding="utf-8")
            materialize_runtime(source, target)
            self.assertEqual((target / "scripts" / "bridge.py").read_text(), "v2")
            self.assertEqual((target / "logs" / "old.log").read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
