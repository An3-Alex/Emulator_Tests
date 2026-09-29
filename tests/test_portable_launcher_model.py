"""Offline checks for the portable user's file-selection launch plan."""

from __future__ import annotations

from dataclasses import replace
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))
from portable_launcher_model import Selection, check_runtime, launch_command, validate_selection
import portable_launcher_model as model


class PortableLauncherTests(unittest.TestCase):
    def test_selected_paths_remain_separate_arguments_and_log_is_optional(self) -> None:
        selection = Selection(
            image=r"D:\CF Images\clean M90.img",
            database=r"D:\DB Dateien\Magie 90.bin",
            loader=r"D:\DB Dateien\Loader.bin",
            factory=r"D:\DB Dateien\Factory.xc",
            config=r"D:\DB Dateien\Config.bin",
            admission_eeprom=r"D:\Karte\card.eeprom.bin",
            qemu_x86=r"C:\Program Files\qemu\qemu-system-x86_64.exe",
            qemu_m68k=r"C:\Program Files\qemu\qemu-system-m68k.exe",
            python=r"C:\Program Files\Python\python.exe",
        )
        command = launch_command(selection, PROJECT)
        self.assertEqual(command[command.index("-Image") + 1], selection.image)
        self.assertEqual(command[command.index("-Database") + 1], selection.database)
        self.assertIn("-NoEventWindow", command)
        self.assertNotIn("-NoEventWindow", launch_command(
            replace(selection, show_live_log=True), PROJECT
        ))

    def test_unknown_dump_is_rejected_without_changing_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {}
            for key in ("database", "loader", "factory", "config"):
                path = root / f"{key}.bin"
                path.write_bytes(key.encode())
                paths[key] = str(path)
            image = root / "fresh.img"
            with image.open("wb") as output:
                output.truncate(64 * 1024 * 1024)
            card = root / "card.bin"
            card.write_bytes(bytes(256))
            for name in (
                "qemu-system-x86_64.exe", "qemu-system-m68k.exe", "python.exe"
            ):
                (root / name).write_bytes(b"test")
            selection = Selection(
                image=str(image), admission_eeprom=str(card),
                qemu_x86=str(root / "qemu-system-x86_64.exe"),
                qemu_m68k=str(root / "qemu-system-m68k.exe"),
                python=str(root / "python.exe"), **paths,
            )
            with patch.object(model, "KNOWN_CF_BYTES", 64 * 1024 * 1024), \
                 patch.dict(model.KNOWN_SHA256, {
                key: model.file_sha256(Path(value)) for key, value in paths.items()
            }):
                self.assertEqual(validate_selection(selection), [])
            self.assertEqual(len([issue for issue in validate_selection(selection)
                                  if "nicht als M90-kompatibel" in issue]), 4)
            self.assertEqual(Path(selection.database).read_bytes(), b"database")

    def test_settings_round_trip_ignores_unknown_future_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            source = Selection(image=r"D:\CF\M90.img", show_live_log=True)
            source.save(path)
            self.assertEqual(Selection.from_json(path), source)

    def test_runtime_rejects_python_older_than_310(self) -> None:
        selection = Selection(
            qemu_x86=r"C:\qemu\qemu-system-x86_64.exe",
            qemu_m68k=r"C:\qemu\qemu-system-m68k.exe",
            python=r"C:\Python39\python.exe",
        )
        results = [
            model.subprocess.CompletedProcess([], 0),
            model.subprocess.CompletedProcess([], 0),
            model.subprocess.CompletedProcess([], 1),
        ]
        with patch.object(model, "validate_selection", return_value=[]), \
             patch.object(model.subprocess, "run", side_effect=results) as run:
            issues = check_runtime(selection)
        self.assertTrue(any("Python 3.10+" in issue for issue in issues))
        self.assertEqual(run.call_args_list[-1].args[0][1], "-c")


if __name__ == "__main__":
    unittest.main()
