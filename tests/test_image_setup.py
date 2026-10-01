"""Offline checks for the safe fresh-image preparation plan."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest import mock


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))
import image_setup
from portable_launcher_model import Selection
from qxl_setup_runner import qemu_command


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ImageSetupTests(unittest.TestCase):
    def test_setup_and_runtime_use_the_same_swapped_device_names(self) -> None:
        selection = Selection(image="working.img", python=sys.executable, swap_displays=True)
        command = image_setup.guest_setup_command(selection, PROJECT, verify=True)
        self.assertIn("--swap-displays", command)
        self.assertIn("--verify", command)
        devices = qemu_command(Path("qemu.exe"), Path(selection.image), 4554, 4446,
                               swap_displays=True)
        self.assertIn("qxl-vga,id=lower,revision=2,vgamem_mb=64,xres=640,yres=480", devices)
        self.assertIn("qxl,id=upper,revision=2,vgamem_mb=64,xres=640,yres=480", devices)

    def test_standard_secondary_is_the_lower_game_display(self) -> None:
        command = qemu_command(Path("qemu.exe"), Path("working.img"), 4554, 4446)
        self.assertIn("qxl-vga,id=upper,revision=2,vgamem_mb=64,xres=640,yres=480", command)
        self.assertIn("qxl,id=lower,revision=2,vgamem_mb=64,xres=640,yres=480", command)

    def test_graphics_update_guards_original_and_keeps_space_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            original = Path(folder) / "original copy.img"
            original.write_bytes(b"source")
            selection = Selection(original_image=str(original), image="working copy.img")
            with mock.patch.object(image_setup, "wsl_path", side_effect=lambda p: "/folder with spaces/" + p.name):
                command = image_setup.graphics_update_command(selection, PROJECT)
                self.assertIn("/folder with spaces/working copy.img", command)
                self.assertIn("/folder with spaces/original copy.img", command)
                self.assertEqual(command[:5], ["wsl.exe", "--user", "root", "--exec", "bash"])
                with self.assertRaisesRegex(ValueError, "verschieden"):
                    image_setup.graphics_update_command(
                        Selection(original_image=str(original), image=str(original)), PROJECT)
        with self.assertRaisesRegex(ValueError, "Original-CF-Image"):
            image_setup.graphics_update_command(Selection(image="working.img"), PROJECT)

    def test_wsl_path_bypasses_shell_and_preserves_spaces(self) -> None:
        path = PROJECT / "folder with spaces" / "working image.img"
        converted = "/mnt/c/folder with spaces/working image.img"
        with mock.patch.object(image_setup.subprocess, "run", return_value=
                               subprocess.CompletedProcess([], 0, converted + "\n", "")) as run:
            self.assertEqual(image_setup.wsl_path(path), converted)
        self.assertEqual(run.call_args.args[0], [
            "wsl.exe", "--user", "root", "--exec", "wslpath", "-a", "-u",
            path.resolve().as_posix(),
        ])

    def test_wsl_path_failure_includes_wsl_details(self) -> None:
        with mock.patch.object(image_setup.subprocess, "run", return_value=
                               subprocess.CompletedProcess([], 1, "", "No installed distributions")):
            with self.assertRaisesRegex(RuntimeError, "No installed distributions"):
                image_setup.wsl_path(PROJECT / "working.img")

    def test_wsl_path_missing_wsl_and_timeout_are_actionable(self) -> None:
        for error in (FileNotFoundError("wsl.exe"), subprocess.TimeoutExpired("wsl.exe", 20)):
            with self.subTest(error=type(error).__name__), \
                 mock.patch.object(image_setup.subprocess, "run", side_effect=error):
                with self.assertRaisesRegex(RuntimeError, "Ubuntu-Ersteinrichtung"):
                    image_setup.wsl_path(PROJECT / "working.img")

    def test_wsl_path_rejects_empty_conversion(self) -> None:
        with mock.patch.object(image_setup.subprocess, "run", return_value=
                               subprocess.CompletedProcess([], 0, "", "")):
            with self.assertRaisesRegex(RuntimeError, "Pfad nicht umsetzen"):
                image_setup.wsl_path(PROJECT / "working.img")

    def test_preparation_rejects_original_as_output_and_existing_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original.img"
            original.write_bytes(b"image")
            swift = root / "swift.dll"
            swift.write_bytes(b"swift")
            qxl = root / "qxl"
            qxl.mkdir()
            for name in image_setup.QXL_HASHES:
                (qxl / name).write_bytes(name.encode())
            component = root / "build" / "own.dll"
            component.parent.mkdir()
            component.write_bytes(b"own")
            assets = {"own": ("build/own.dll", digest(b"own"))}
            qxl_hashes = {name: digest(name.encode()) for name in image_setup.QXL_HASHES}
            common = dict(
                original_image=str(original), swiftshader=str(swift),
                qxl_driver_dir=str(qxl), qemu_x86=sys.executable, python=sys.executable,
            )
            with mock.patch.object(image_setup, "KNOWN_CF_BYTES", 5), \
                 mock.patch.object(image_setup, "SWIFTSHADER_HASH", digest(b"swift")), \
                 mock.patch.object(image_setup, "QXL_HASHES", qxl_hashes), \
                 mock.patch.object(image_setup, "COMPONENTS", assets):
                fresh = Selection(image=str(root / "working.img"), **common)
                self.assertEqual(image_setup.check_preparation(fresh, root, require_wsl=False), [])
                same = Selection(image=str(original), **common)
                self.assertTrue(any("verschieden" in issue for issue in
                                    image_setup.check_preparation(same, root, require_wsl=False)))
                (root / "working.img").write_bytes(b"copy")
                self.assertTrue(any("existiert" in issue for issue in
                                    image_setup.check_preparation(fresh, root, require_wsl=False)))
                self.assertEqual(
                    image_setup.check_preparation(fresh, root, require_wsl=False, resume=True), [],
                )

    def test_qemu_paths_with_spaces_remain_single_arguments(self) -> None:
        command = qemu_command(
            Path(r"C:\Program Files\qemu\qemu-system-x86_64.exe"),
            Path(r"C:\My Images\m90 work.img"), 4554, 4446,
        )
        drive = command[command.index("-drive") + 1]
        self.assertIn("My Images", drive)
        self.assertTrue(drive.endswith("format=raw,if=ide,index=0,media=disk"))
        self.assertIn("-no-reboot", command)
        self.assertIn("tcp:127.0.0.1:4554,server=on,wait=off", command)

    def test_display_verification_and_retry_are_separate_setup_steps(self) -> None:
        selection = Selection(
            image=r"C:\Images\working copy.img",
            qemu_x86=r"C:\QEMU\qemu-system-x86_64.exe",
            python=sys.executable,
        )
        with mock.patch.object(image_setup, "wsl_path", side_effect=lambda path: str(path)):
            verify_guest = image_setup.guest_setup_command(selection, PROJECT, verify=True)
            stage = image_setup.stage_display_verify_command(Path(selection.image), PROJECT)
            retry = image_setup.retry_qxl_command(Path(selection.image), PROJECT)
        self.assertEqual(verify_guest[-1], "--verify")
        self.assertIn("stage_display_verify.sh", stage[5])
        self.assertIn("retry_qxl_install.sh", retry[5])
        self.assertEqual(stage[:5], ["wsl.exe", "--user", "root", "--exec", "bash"])
        self.assertEqual(retry[:5], stage[:5])

    def test_all_image_steps_preserve_linux_paths_as_arguments(self) -> None:
        selection = Selection(
            original_image="source image.img", image="working image.img",
            swiftshader="shader folder/d3d9.dll", qxl_driver_dir="driver folder",
        )
        with mock.patch.object(image_setup, "wsl_path", side_effect=lambda path: "/folder with spaces/" + path.name):
            commands = [
                image_setup.stage_command(selection, PROJECT),
                image_setup.stage_check_command(Path(selection.image), PROJECT),
                image_setup.finalize_command(Path(selection.image), PROJECT),
            ]
        for command in commands:
            self.assertEqual(command[:5], ["wsl.exe", "--user", "root", "--exec", "bash"])
            self.assertIn("/folder with spaces/working image.img", command)


if __name__ == "__main__":
    unittest.main()
