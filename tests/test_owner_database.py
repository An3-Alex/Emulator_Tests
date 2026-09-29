import importlib.util
import struct
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "inspect_owner_database.py"
SPEC = importlib.util.spec_from_file_location("inspect_owner_database", MODULE_PATH)
assert SPEC and SPEC.loader
INSPECTOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSPECTOR)


class OwnerDatabaseInspectionTests(unittest.TestCase):
    def test_recognizes_observed_clear_text_header_without_modifying_dump(self):
        data = bytearray(b"\0" * 0x200)
        end_address = INSPECTOR.DATABASE_LOAD_BASE + len(data) - 1
        entrypoint = INSPECTOR.DATABASE_LOAD_BASE + 0x100
        struct.pack_into(">IIIII", data, 0, 0x12345678, end_address,
                         (~end_address) & 0xFFFFFFFF, 0x61640403,
                         end_address + 1)
        struct.pack_into(">II", data, 0x4C, entrypoint,
                         (~entrypoint) & 0xFFFFFFFF)
        for offset, value in (
            (0x18, b" COPYRIGHT BY ADP LUEBBECKE GERMANY 2012"),
            (0x60, b"MERKUR MAGIE  90 G   CC4"),
            (0x79, b"1Aa/"),
            (0x7E, b"151008"),
            (0x8C, b"070101"),
        ):
            data[offset:offset + len(value)] = value
        data[0xE0:] = bytes(range(256)) + bytes(range(32))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owner.bin"
            path.write_bytes(data)
            before = path.read_bytes()
            report = INSPECTOR.inspect_dump(path)
            self.assertEqual(path.read_bytes(), before)

        self.assertTrue(report["recognized"])
        self.assertEqual(report["header"]["product"], "MERKUR MAGIE  90 G   CC4")
        self.assertEqual(report["header"]["edition"], "1Aa/")
        self.assertEqual(report["header"]["build_id"], "151008")
        self.assertEqual(report["header"]["release_id"], "070101")
        self.assertEqual(report["header"]["module_id"], "61640403")
        self.assertEqual(report["header"]["entrypoint_file_offset"], 0x100)
        self.assertTrue(report["validations"]["load_extent_matches_file_size"])
        self.assertTrue(report["validations"]["end_exclusive_valid"])

    def test_recognizes_small_structural_database_module(self):
        data = bytearray(b"\0" * 0x780)
        end_address = INSPECTOR.DATABASE_LOAD_BASE + len(data) - 1
        entrypoint = INSPECTOR.DATABASE_LOAD_BASE + 0x500
        struct.pack_into(">IIIII", data, 0, 0x5F14, end_address,
                         (~end_address) & 0xFFFFFFFF, 0x61640403, 0)
        struct.pack_into(">II", data, 0x4C, entrypoint,
                         (~entrypoint) & 0xFFFFFFFF)
        data[0xE0:] = (bytes(range(256)) * 8)[:len(data) - 0xE0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "module.bin"
            path.write_bytes(data)
            report = INSPECTOR.inspect_dump(path)
        self.assertTrue(report["recognized"])
        self.assertEqual(report["role"], "database_module")
        self.assertEqual(report["header"]["module_id"], "61640403")
        self.assertTrue(report["validations"]["end_exclusive_valid"])

    def test_rejects_database_with_broken_complement_pair(self):
        data = bytearray(b"\0" * 0x200)
        end_address = INSPECTOR.DATABASE_LOAD_BASE + len(data) - 1
        struct.pack_into(">IIIII", data, 0, 0, end_address, 0,
                         0x61640403, end_address + 1)
        struct.pack_into(">II", data, 0x4C, 0x1100, (~0x1100) & 0xFFFFFFFF)
        copyright_text = b" COPYRIGHT BY ADP LUEBBECKE GERMANY 2012"
        product = b"MERKUR MAGIE  90 G   CC4"
        data[0x18:0x18 + len(copyright_text)] = copyright_text
        data[0x60:0x60 + len(product)] = product
        data[0xE0] = 1
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.bin"
            path.write_bytes(data)
            report = INSPECTOR.inspect_dump(path)
        self.assertFalse(report["recognized"])
        self.assertFalse(report["validations"]["end_address_complement"])

    def test_rejects_unrelated_header(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unknown.bin"
            path.write_bytes(b"\0" * 0x200)
            report = INSPECTOR.inspect_dump(path)
        self.assertFalse(report["recognized"])

    def test_recognizes_observed_loader_header_as_separate_role(self):
        data = bytearray(b"\0" * 0xA00)
        data[:5] = b"|load"
        version = b" L 5.0b"
        copyright_text = b"  COPYRIGHT BY ADP LUEBBECKE GERMANY 2009"
        data[0x14:0x14 + len(version)] = version
        data[0x1C:0x1C + len(copyright_text)] = copyright_text
        data[0x0E:0x14] = b"\x4E\xF9\x00\x00\x0C\xC8"
        data[0x2D4:0x2D8] = b"\x4E\x7B\x08\x01"
        data[0x8B0:0x8C8] = bytes.fromhex(
            "7B 6A 68 6B 6B FC C3 54 1A 52 58 4D 42 52 58 4D "
            "42 56 40 48 53 46 4E 09"
        )
        payload_size = len(data) - 0x58
        data[0x58:] = (bytes(range(256)) * ((payload_size + 255) // 256))[:payload_size]
        # Restore the structural bytes overwritten by the synthetic payload.
        data[0x2D4:0x2D8] = b"\x4E\x7B\x08\x01"
        data[0x8B0:0x8C8] = bytes.fromhex(
            "7B 6A 68 6B 6B FC C3 54 1A 52 58 4D 42 52 58 4D "
            "42 56 40 48 53 46 4E 09"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "loader.bin"
            path.write_bytes(data)
            report = INSPECTOR.inspect_dump(path)
        self.assertTrue(report["recognized"])
        self.assertEqual(report["role"], "loader")
        self.assertEqual(report["header"]["signature"], "|load")
        self.assertEqual(report["header"]["version"], "L 5.0b")
        self.assertEqual(report["header"]["entry_target"], 0xCC8)
        self.assertEqual(report["header"]["entry_target_file_offset"], 0x8C8)
        self.assertEqual(report["header"]["sync_wait_ascii"], "SYNCSYNCWAITGO")

    def test_rejects_truncated_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "short.bin"
            path.write_bytes(b"short")
            with self.assertRaisesRegex(ValueError, "shorter"):
                INSPECTOR.inspect_dump(path)


if __name__ == "__main__":
    unittest.main()
