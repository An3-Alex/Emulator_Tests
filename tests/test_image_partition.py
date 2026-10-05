"""Synthetic raw CF layouts only; no VM, loop device or filesystem mount."""
from pathlib import Path
import struct
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from image_partition import ImageVolume, locate_ntfs


def table(*entries):
    data = bytearray(512)
    data[510:512] = b"\x55\xaa"
    for index, (kind, lba, count) in enumerate(entries):
        struct.pack_into("<B3sB3sII", data, 446 + index * 16,
                         0, bytes(3), kind, bytes(3), lba, count)
    return data


def boot(sectors):
    data = bytearray(512)
    data[3:11] = b"NTFS    "
    struct.pack_into("<H", data, 11, 512)
    data[13] = 8
    struct.pack_into("<Q", data, 40, sectors)
    data[510:512] = b"\x55\xaa"
    return data


class PartitionTests(unittest.TestCase):
    def fixture(self, root, sectors, records):
        file = root / "CF with spaces.img"
        with file.open("wb") as stream:
            stream.truncate(sectors * 512)
            for lba, data in records:
                stream.seek(lba * 512)
                stream.write(data)
        return file

    def test_other_cf_sizes_and_partition_offsets(self):
        with tempfile.TemporaryDirectory() as directory:
            for total, start, count in ((16384, 63, 8000), (32768, 2048, 16000)):
                file = self.fixture(Path(directory), total, [(0, table((7, start, count))),
                                                             (start, boot(count))])
                self.assertEqual(locate_ntfs(file), ImageVolume(start * 512, count * 512))

    def test_ntfs_whole_volume(self):
        with tempfile.TemporaryDirectory() as directory:
            file = self.fixture(Path(directory), 4096, [(0, boot(4096))])
            self.assertEqual(locate_ntfs(file), ImageVolume(0, 4096 * 512))

    def test_logical_ntfs_partition(self):
        with tempfile.TemporaryDirectory() as directory:
            file = self.fixture(Path(directory), 16384, [
                (0, table((0x0F, 100, 9000))),
                (100, table((7, 63, 8000))), (163, boot(8000))])
            self.assertEqual(locate_ntfs(file), ImageVolume(163 * 512, 8000 * 512))

    def test_truncated_partition_and_ntfs_extent_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for table_count, boot_count in ((9999, 100), (3000, 4000)):
                file = self.fixture(root, 4096, [(0, table((7, 63, table_count))), (63, boot(boot_count))])
                with self.assertRaisesRegex(ValueError, "abgeschnitten"):
                    locate_ntfs(file)

    def test_multiple_ntfs_volumes_are_not_silently_guessed(self):
        with tempfile.TemporaryDirectory() as directory:
            file = self.fixture(Path(directory), 4096, [
                (0, table((7, 63, 1000), (7, 2000, 1000))), (63, boot(1000)), (2000, boot(1000))])
            with self.assertRaisesRegex(ValueError, "eindeutige NTFS"):
                locate_ntfs(file)

    def test_cyclic_ebr_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            file = self.fixture(Path(directory), 4096, [
                (0, table((0x0F, 100, 3000))), (100, table((0, 0, 0), (0x0F, 100, 2000))),
                (200, table((0, 0, 0), (0x0F, 100, 2000)))])
            with self.assertRaisesRegex(ValueError, "EBR-Kette"):
                locate_ntfs(file)

    def test_overlapping_partitions_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            file = self.fixture(Path(directory), 4096, [
                (0, table((7, 63, 1000), (0x0B, 1000, 1000))), (63, boot(1000))])
            with self.assertRaisesRegex(ValueError, "Überlappende"):
                locate_ntfs(file)

    def test_empty_and_gpt_images_have_clear_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            file = self.fixture(Path(directory), 4096, [(0, table((0xEE, 1, 4095)))])
            with self.assertRaisesRegex(ValueError, "GPT"):
                locate_ntfs(file)
            file.write_bytes(b"")
            with self.assertRaises(ValueError):
                locate_ntfs(file)

    def test_all_shell_image_tools_use_shared_layout_and_no_fixed_cf_extent(self):
        scripts = Path(__file__).resolve().parents[1] / "scripts"
        for file in scripts.glob("*.sh"):
            source = file.read_text()
            with self.subTest(file=file.name):
                self.assertNotIn("16139354112", source)
                self.assertNotIn("16021151744", source)
                if "image_loop_device " in source and file.name != "image_partition.sh":
                    self.assertIn('source "$(dirname -- "$0")/image_partition.sh"', source)

    @unittest.skipUnless(shutil.which("wsl.exe"), "WSL bash required")
    def test_shell_helper_derives_bounds_and_preserves_readonly_flag(self):
        with tempfile.TemporaryDirectory(prefix="CF layout ") as directory:
            image = self.fixture(Path(directory), 4096, [(0, table((7, 63, 3000))), (63, boot(3000))])
            helper = Path(__file__).resolve().parents[1] / "scripts/image_partition.sh"
            linux = []
            for path in (helper, image):
                result = subprocess.run(["wsl.exe", "--exec", "wslpath", "-a", str(path)],
                                        capture_output=True, text=True, timeout=15, check=True)
                linux.append(result.stdout.strip())
            code = 'set -eu; source "$1"; losetup() { printf "%s\\n" "$@"; }; image_loop_device "$2" ro'
            result = subprocess.run(["wsl.exe", "--exec", "bash", "-c", code, "fixture", *linux],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), ["--find", "--show", "--read-only",
                             "--offset", str(63 * 512), "--sizelimit", str(3000 * 512), linux[1]])
