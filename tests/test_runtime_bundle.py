"""Offline checks for packaged runtime extraction."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))
from runtime_bundle import (
    OWN_BINARIES, POWERSHELL_FILES, REQUIRED_PYTHON_FILES, SHELL_FILES,
    RETIRED_PYTHON_FILES, materialize_runtime, runtime_payload,
)


class RuntimeBundleTests(unittest.TestCase):
    def test_copies_only_runtime_source_and_preserves_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "bundle"
            target = root / "runtime"
            (source / "scripts").mkdir(parents=True)
            for name in POWERSHELL_FILES:
                (source / name).write_text(name, encoding="utf-8")
            for name in REQUIRED_PYTHON_FILES:
                (source / "scripts" / name).write_text(name, encoding="utf-8")
            (source / "scripts" / "bridge.py").write_text("v1", encoding="utf-8")
            for name in RETIRED_PYTHON_FILES:
                (source / "scripts" / name).write_text("retired installer", encoding="utf-8")
            for name in SHELL_FILES:
                (source / "scripts" / name).write_text(name, encoding="utf-8")
            for relative in OWN_BINARIES:
                binary = source / relative
                binary.parent.mkdir(parents=True, exist_ok=True)
                binary.write_bytes(b"own binary")
            (source / "scripts" / "dump.bin").write_bytes(b"private")
            (target / "logs").mkdir(parents=True)
            (target / "logs" / "old.log").write_text("keep", encoding="utf-8")

            paths = runtime_payload(source)
            self.assertEqual(
                len(paths), len(POWERSHELL_FILES) + len(SHELL_FILES)
                + len(OWN_BINARIES) + len(REQUIRED_PYTHON_FILES) + 1,
            )
            materialize_runtime(source, target)
            self.assertEqual((target / "scripts" / "bridge.py").read_text(), "v1")
            self.assertFalse((target / "scripts" / "dump.bin").exists())
            for name in RETIRED_PYTHON_FILES:
                self.assertFalse((target / "scripts" / name).exists())
            self.assertFalse(any("audio-" in str(name) for name in OWN_BINARIES))
            (source / "scripts" / "bridge.py").write_text("v2", encoding="utf-8")
            materialize_runtime(source, target)
            self.assertEqual((target / "scripts" / "bridge.py").read_text(), "v2")
            self.assertEqual((target / "logs" / "old.log").read_text(), "keep")

    def test_missing_required_script_fails_before_materializing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(FileNotFoundError, "Laufzeit-Skript fehlt"):
                runtime_payload(Path(directory))


if __name__ == "__main__":
    unittest.main()
