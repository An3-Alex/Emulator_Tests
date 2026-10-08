import hashlib
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from m68k_database_transform import transform_database
from owner_database_runtime import prepare_runtime
from m68k_database_bridge import parse_stop_address


class OwnerDatabaseRuntimeTests(unittest.TestCase):
    def test_qemu_watchpoint_stop_is_decoded(self):
        self.assertEqual(
            parse_stop_address("T05thread:01;rwatch:00fffc0d;"),
            ("rwatch", 0xFFFC0D),
        )

    def make_transport(self, directory: Path, d3: int) -> tuple[Path, str, bytes]:
        size = 0x300
        runtime = bytearray((index * 17 + 3) & 0xFF for index in range(size))
        end = 0x1000 + size - 1
        struct.pack_into(">IIIII", runtime, 0, 0, end, (~end) & 0xFFFFFFFF,
                         0x61640403, end + 1)
        struct.pack_into(">II", runtime, 0x4C, 0x1100, (~0x1100) & 0xFFFFFFFF)
        struct.pack_into(">I", runtime, 0, sum(runtime[4:]) & 0xFFFFFFFF)
        # The stream operation is symmetric, so applying it to a valid runtime
        # creates the corresponding transport representation.
        transport = transform_database(bytes(runtime), d3)
        path = directory / "database.bin"
        path.write_bytes(transport)
        return path, hashlib.sha256(transport).hexdigest(), bytes(runtime)

    def test_d3_prepares_checksum_valid_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            path, digest, expected = self.make_transport(Path(temp), 0x12345678)
            actual, report = prepare_runtime(path, digest, d3=0x12345678)
            self.assertEqual(actual, expected)
            self.assertEqual(report["entrypoint"], "00001100")
            self.assertEqual(report["d3"], "12345678")

    def test_wrong_d3_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path, digest, _ = self.make_transport(Path(temp), 0x12345678)
            with self.assertRaisesRegex(ValueError, "Schlüssel D3=00000000 nicht entschlüsseln"):
                prepare_runtime(path, digest, d3=0)

    def test_valid_runtime_dump_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            path, digest, runtime = self.make_transport(directory, 0x12345678)
            dump = directory / "runtime.bin"
            dump.write_bytes(runtime)
            actual, report = prepare_runtime(path, digest, runtime_dump_path=dump)
            self.assertEqual(actual, runtime)
            self.assertEqual(report["runtime_source"], "owner-runtime-dump")


if __name__ == "__main__":
    unittest.main()
