import hashlib
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from m68k_database_transform import transform_database
from owner_config_runtime import (
    CONFIG_CLEAR_START, CONFIG_COPY_START, CONFIG_ENTRY_PREFIX,
    IDENTITY_COPY_START, prepare_config_writes,
)


class OwnerConfigRuntimeTests(unittest.TestCase):
    def test_verified_module_copies_only_its_two_proven_sram_ranges(self) -> None:
        decoded = bytearray(1920)
        struct.pack_into(">I", decoded, 4, 0x1000 + len(decoded) - 1)
        struct.pack_into(">I", decoded, 12, 0x61640403)
        struct.pack_into(">I", decoded, 0x4C, 0x1500)
        decoded[0x500:0x508] = CONFIG_ENTRY_PREFIX
        decoded[0x562:0x57A] = b"A" * 0x18
        decoded[0x578:0x778] = bytes(range(256)) * 2
        struct.pack_into(">I", decoded, 0, sum(decoded[4:]) & 0xFFFFFFFF)
        raw = transform_database(bytes(decoded), 0xD27B7159)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.bin"
            path.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            writes, actual = prepare_config_writes(path, digest, 0xD27B7159)
        self.assertEqual(actual, digest.upper())
        self.assertEqual([address for address, _ in writes], [
            CONFIG_CLEAR_START, CONFIG_COPY_START, IDENTITY_COPY_START
        ])
        self.assertEqual(writes[0][1], bytes(0x400))
        self.assertEqual(writes[1][1], bytes(range(256)) * 2)
        self.assertEqual(writes[2][1], bytes(decoded[0x562:0x57A]))

    def test_wrong_config_hash_is_rejected_before_transform(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.bin"
            path.write_bytes(bytes(1920))
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                prepare_config_writes(path, "0" * 64, 0xD27B7159)


if __name__ == "__main__":
    unittest.main()
