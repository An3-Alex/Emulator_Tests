"""Offline checks for the safe fresh-image preparation plan."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys
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


if __name__ == "__main__":
    unittest.main()
