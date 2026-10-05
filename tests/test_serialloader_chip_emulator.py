import datetime as dt
import hashlib
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from m68k_database_transform import transform_database
from serialloader_chip_emulator import (
    ChipPhase, SYNC_WAIT, VirtualDatabaseChip, build_date_wire_frame,
)


class SerialLoaderChipTests(unittest.TestCase):
    def test_date_frame_matches_serialloader_layout(self):
        frame = build_date_wire_frame(dt.datetime(2010, 12, 31, 23, 59, 58))
        self.assertEqual(frame[:16], SYNC_WAIT)
        self.assertEqual(frame[16:23], bytes.fromhex("58 59 01 31 12 10 05"))
        self.assertEqual(frame[23], sum(frame[16:23]) & 0xFF)

    def test_programming_state_order(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            loader = bytearray(0x500)
            loader[0x0E:0x14] = bytes.fromhex("4EF900000CC8")
            loader_path = directory / "loader.bin"
            loader_path.write_bytes(loader)
            loader_hash = hashlib.sha256(loader).hexdigest()

            chip = VirtualDatabaseChip()
            self.assertEqual(chip.upload_loader(loader_path, loader_hash), bytes.fromhex("1B32"))
            with self.assertRaisesRegex(RuntimeError, "FactoryReset first"):
                chip.set_date(dt.datetime(2012, 2, 1, 22, 14))
            module_report = {
                "sha256": "A" * 64,
                "role": "database_module",
                "recognized": True,
                "header": {"module_family": "61640400"},
            }
            with patch("serialloader_chip_emulator.pinned_bytes", return_value=(b"", "A" * 64)), \
                 patch("serialloader_chip_emulator.inspect_database", return_value=module_report), \
                 patch("serialloader_chip_emulator.prepare_runtime", return_value=(
                     b"runtime", {"transport_sha256": "B" * 64, "runtime_sha256": "C" * 64}
                 )):
                self.assertEqual(
                    chip.upload_factory_module(directory / "factory.xc", "A" * 64),
                    bytes.fromhex("1B331B32"),
                )
                self.assertEqual(chip.phase, ChipPhase.FACTORY_RESET)
                with self.assertRaisesRegex(RuntimeError, "date first"):
                    chip.upload_config_module(directory / "config.bin", "A" * 64)
                _, status = chip.set_date(dt.datetime(2012, 2, 1, 22, 14))
                self.assertEqual(status, bytes.fromhex("1B311B32"))
                self.assertEqual(chip.phase, ChipPhase.DATE_SET)
                self.assertEqual(
                    chip.upload_config_module(directory / "config.bin", "A" * 64),
                    bytes.fromhex("1B331B32"),
                )
                self.assertEqual(chip.phase, ChipPhase.CONFIG_SET)
                chip.upload_database(directory / "database.bin", "B" * 64, d3=1)
            self.assertEqual(chip.phase, ChipPhase.PROGRAMMED)
            self.assertEqual(
                [event["operation"] for event in chip.events],
                ["upload-r-loader", "upload-l-factory", "set-date",
                 "upload-l-config", "upload-l-database"],
            )

    def test_database_cannot_be_programmed_before_loader(self):
        chip = VirtualDatabaseChip()
        with self.assertRaisesRegex(RuntimeError, "requires loader"):
            chip.upload_database(Path("missing"), "00", d3=0)


if __name__ == "__main__":
    unittest.main()
