import hashlib
import importlib.util
import struct
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "validate_runtime_database_dump.py"
SPEC = importlib.util.spec_from_file_location("validate_runtime_database_dump", MODULE_PATH)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VALIDATOR
SPEC.loader.exec_module(VALIDATOR)


def make_runtime() -> bytes:
    data = bytearray((index * 17) & 0xFF for index in range(0x200))
    end = 0x1000 + len(data) - 1
    struct.pack_into(">IIIII", data, 0, 0, end, (~end) & 0xFFFFFFFF,
                     0x61640403, end + 1)
    struct.pack_into(">II", data, 0x4C, 0x1100, (~0x1100) & 0xFFFFFFFF)
    for offset, value in (
        (0x18, b" COPYRIGHT BY ADP TEST"),
        (0x60, b"MERKUR MAGIE TEST CC4"),
    ):
        data[offset:offset + len(value)] = value
    struct.pack_into(">I", data, 0, sum(data[4:]) & 0xFFFFFFFF)
    return bytes(data)


class RuntimeDumpTests(unittest.TestCase):
    def test_accepts_native_valid_post_transform_dump(self):
        runtime = make_runtime()
        transport = runtime[:0x100] + bytes(value ^ 0x55 for value in runtime[0x100:])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transport_path = root / "transport.bin"
            runtime_path = root / "runtime.bin"
            transport_path.write_bytes(transport)
            runtime_path.write_bytes(runtime)
            report = VALIDATOR.validate_runtime_dump(
                transport_path, hashlib.sha256(transport).hexdigest(), runtime_path
            )
        self.assertTrue(report["recognized_runtime_dump"])
        self.assertTrue(report["validations"]["native_additive_checksum"])

    def test_rejects_bad_runtime_checksum(self):
        runtime = bytearray(make_runtime())
        transport = bytes(runtime[:0x100]) + bytes(value ^ 0x55 for value in runtime[0x100:])
        runtime[-1] ^= 1
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transport_path = root / "transport.bin"
            runtime_path = root / "runtime.bin"
            transport_path.write_bytes(transport)
            runtime_path.write_bytes(runtime)
            report = VALIDATOR.validate_runtime_dump(
                transport_path, hashlib.sha256(transport).hexdigest(), runtime_path
            )
        self.assertFalse(report["recognized_runtime_dump"])
        self.assertFalse(report["validations"]["native_additive_checksum"])


if __name__ == "__main__":
    unittest.main()
