"""Offline checks for the portable user's file-selection launch plan."""

from __future__ import annotations

from dataclasses import replace
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))
from portable_launcher_model import Selection, check_runtime, launch_command, validate_selection
from admission_card import build_m90_eeprom
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
        self.assertNotIn("-SwapDisplays", command)
        self.assertIn("-SwapDisplays", launch_command(
            replace(selection, swap_displays=True), PROJECT
        ))

    def test_other_package_files_and_cf_size_are_selectable_without_changing_them(self) -> None:
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
            card.write_bytes(build_m90_eeprom(bytes(256), "123456789"))
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
            self.assertEqual(validate_selection(selection), [])
            self.assertEqual(Path(selection.database).read_bytes(), b"database")

    def test_other_card_model_is_accepted_but_number_copies_are_checked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrong = root / "wrong-card.bin"
            image = bytearray(build_m90_eeprom(bytes(256), "123456789"))
            image[69:73] = bytes.fromhex("06 32 02 87")
            wrong.write_bytes(image)
            for name in ("database.bin", "loader.bin", "factory.bin", "config.bin",
                         "qemu-system-x86_64.exe", "qemu-system-m68k.exe", "python.exe"):
                (root / name).write_bytes(b"test")
            cf = root / "image.img"
            cf.write_bytes(b"")
            selection = Selection(
                image=str(cf), admission_eeprom=str(wrong),
                database=str(root / "database.bin"), loader=str(root / "loader.bin"),
                factory=str(root / "factory.bin"), config=str(root / "config.bin"),
                qemu_x86=str(root / "qemu-system-x86_64.exe"),
                qemu_m68k=str(root / "qemu-system-m68k.exe"),
                python=str(root / "python.exe"),
            )
            self.assertEqual(validate_selection(selection), [])
            image[40] = ord("9")
            wrong.write_bytes(image)
            issues = validate_selection(selection)
            self.assertTrue(any("copies disagree" in issue for issue in issues))

    def test_selected_hashes_are_used_in_programmer_and_runtime_plans(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {}
            for key in ("database", "loader", "factory", "config"):
                file = root / f"different package {key}.bin"
                file.write_bytes(key.encode())
                files[key] = str(file)
            selection = Selection(image="unused.img", admission_eeprom="card.bin",
                                  qemu_x86="qemu-system-x86_64.exe",
                                  qemu_m68k="qemu-system-m68k.exe", python="python.exe", **files)
            completed = subprocess.run([*launch_command(selection, PROJECT), "-DryRun"],
                                       capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            plan = json.loads(completed.stdout)
            programmer = plan["virtual_programming"]["arguments"]
            bridge = plan["runtime"]["database_bridge"]["arguments"]
            for key, path in files.items():
                flag = f"--expected-{key}-sha256"
                expected = model.file_sha256(Path(path))
                self.assertEqual(programmer[programmer.index(flag) + 1], expected)
                if key != "factory":
                    self.assertEqual(bridge[bridge.index(flag) + 1], expected)

    def test_settings_round_trip_ignores_unknown_future_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            source = Selection(image=r"D:\CF\M90.img", show_live_log=True, swap_displays=True)
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

    @unittest.skipUnless(shutil.which("powershell.exe"), "Windows PowerShell required")
    def test_python_to_powershell_dry_run_preserves_spaced_paths(self) -> None:
        selection = Selection(
            image=r"D:\CF Images\M90 original copy.img",
            database=r"D:\DB Files\Magie 90.bin",
            loader=r"D:\DB Files\Loader.bin",
            factory=r"D:\DB Files\Factory.xc",
            config=r"D:\DB Files\Config.bin",
            admission_eeprom=r"D:\Cards\M90 card.bin",
            qemu_x86=r"C:\Program Files\qemu\qemu-system-x86_64.exe",
            qemu_m68k=r"C:\Program Files\qemu\qemu-system-m68k.exe",
            python=r"C:\Python 3.14\python.exe",
            swap_displays=True,
        )
        completed = subprocess.run(
            [*launch_command(selection, PROJECT), "-DryRun"],
            cwd=PROJECT, capture_output=True, text=True, check=True,
        )
        plan = json.loads(completed.stdout)
        self.assertEqual(plan["runtime"]["visible_qemu"]["image"], selection.image)
        self.assertEqual(plan["runtime"]["visible_qemu"]["qemu"], selection.qemu_x86)
        self.assertTrue(plan["runtime"]["visible_qemu"]["swap_displays"])
        self.assertFalse(plan["runtime"]["event_window"]["visible"])
        self.assertIn(selection.admission_eeprom,
                      plan["runtime"]["database_bridge"]["arguments"])


if __name__ == "__main__":
    unittest.main()
