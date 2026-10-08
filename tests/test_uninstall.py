"""Uninstall removes only the emulator's own data, never originals or sources."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from uninstall import emulator_folders, remove, working_copy_files


class UninstallTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.settings = self.root / "Roaming" / "M90 Emulator"
        self.runtime = self.root / "Local" / "M90 Emulator" / "runtime"
        for folder in (self.settings, self.runtime / "logs"):
            folder.mkdir(parents=True)
        (self.settings / "settings.json").write_text("{}")
        (self.runtime / "logs" / "database-events.log").write_text("log")

    def test_exe_removes_settings_and_runtime_including_its_parent(self):
        folders = emulator_folders(self.settings, self.runtime, frozen=True)
        self.assertEqual(folders, [self.settings, self.runtime])
        self.assertEqual(remove(folders), [])
        self.assertFalse(self.settings.exists())
        self.assertFalse(self.runtime.parent.exists())

    def test_source_tree_is_never_a_runtime_target(self):
        source = self.root / "cgos-shim"
        source.mkdir()
        self.assertEqual(emulator_folders(self.settings, source, frozen=False), [self.settings])
        self.assertEqual(emulator_folders(self.settings, source, frozen=True), [self.settings])
        foreign = self.root / "Other"
        foreign.mkdir()
        self.assertEqual(emulator_folders(foreign, self.runtime, frozen=False), [])

    def test_working_copy_with_sidecars_but_never_the_original(self):
        original = self.root / "m90img.img"
        copy = self.root / "m90imgarbeitskopie.img"
        original.write_bytes(b"original")
        copy.write_bytes(b"copy")
        (self.root / "m90imgarbeitskopie.img.touch.json").write_text("{}")
        (self.root / "m90imgarbeitskopie.img.qemu3dfx.json").write_text("{}")
        files = working_copy_files(str(copy), str(original))
        self.assertEqual([p.name for p in files], ["m90imgarbeitskopie.img",
                                                   "m90imgarbeitskopie.img.touch.json",
                                                   "m90imgarbeitskopie.img.qemu3dfx.json"])
        self.assertEqual(working_copy_files(str(original), str(original)), [])
        self.assertEqual(working_copy_files("", str(original)), [])
        self.assertEqual(remove(files), [])
        self.assertTrue(original.is_file())
        self.assertFalse(copy.exists())


if __name__ == "__main__":
    unittest.main()
