"""Offline flash-plan tests. No serial device or esptool is invoked."""

import json
import hashlib
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "esp32-db"))

from build_esp32_seed import pack_image  # noqa: E402
from usb_flash_core import (  # noqa: E402
    SEED_OFFSET, check_probe_output, firmware_files, flash_command,
    gui_sdkconfig, idf_command, make_flash_plan, read_partitions,
    record_firmware_profile,
    seed_command, seed_destination, source_files, temporary_seed_path,
    validate_firmware_profile, validate_seed,
)


class UsbFlashTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.seed = self.root / "seed.bin"
        self.seed.write_bytes(pack_image(bytes(2 * 1024 * 1024)))

    def test_seed_crc_and_partition(self):
        generation, _ = validate_seed(self.seed)
        self.assertEqual(generation, 0)
        self.assertEqual(read_partitions()["db_seed"][0], SEED_OFFSET)
        self.assertEqual(make_flash_plan(self.seed, False)[0].offset, SEED_OFFSET)
        with self.seed.open("r+b") as stream:
            stream.seek(-1, 2)
            stream.write(b"X")
        with self.assertRaisesRegex(ValueError, "CRC32"):
            validate_seed(self.seed)

    def test_firmware_plan_only_expected_offsets(self):
        build = self.root / "idf-build"
        (build / "bootloader").mkdir(parents=True)
        (build / "partition_table").mkdir()
        (build / "bootloader" / "bootloader.bin").write_bytes(b"BOOT")
        partition_bytes = bytearray()
        for name, (offset, size) in read_partitions().items():
            partition_bytes.extend(struct.pack(
                "<2sBBII16sI", b"\xaa\x50", 0 if name == "factory" else 1,
                0, offset, size, name.encode(), 0,
            ))
        partition_bytes.extend(b"\xff" * (0xC00 - len(partition_bytes)))
        partition_file = build / "partition_table" / "partition-table.bin"
        partition_file.write_bytes(partition_bytes)
        (build / "database.bin").write_bytes(b"APP")
        metadata = {
            "extra_esptool_args": {"chip": "esp32s3"},
            "flash_settings": {"flash_size": "16MB"},
            "flash_files": {
                "0x0": "bootloader/bootloader.bin",
                "0x8000": "partition_table/partition-table.bin",
                "0x10000": "database.bin",
            },
        }
        (build / "flasher_args.json").write_text(json.dumps(metadata), encoding="utf-8")
        files = make_flash_plan(self.seed, True, build)
        self.assertEqual([item.offset for item in files],
                         [0, 0x8000, 0x10000, SEED_OFFSET])
        command = flash_command("COM7", files)
        self.assertIn("write-flash", command)
        self.assertIn(hex(SEED_OFFSET), command)
        (build / "config").mkdir()
        (build / "config" / "sdkconfig.json").write_text(json.dumps({
            "M90_DB_UART_TEST": True, "M90_DB_UART_TX_GPIO": 17,
            "M90_DB_UART_RX_GPIO": 18,
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Modus"):
            record_firmware_profile(False, 17, 18, build)
        record_firmware_profile(True, 17, 18, build)
        self.assertTrue(validate_firmware_profile(build)["uart_enabled"])
        (build / "config" / "sdkconfig.json").write_text(json.dumps({
            "M90_DB_UART_TEST": False, "M90_DB_USB_BRIDGE": True,
            "ESP_CONSOLE_SECONDARY_NONE": False,
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "USB-Brückenmodus"):
            record_firmware_profile(False, 17, 18, build)
        with self.assertRaisesRegex(ValueError, "Log-Konsole"):
            record_firmware_profile(False, 17, 18, build, usb_bridge=True)
        (build / "config" / "sdkconfig.json").write_text(json.dumps({
            "M90_DB_UART_TEST": False, "M90_DB_USB_BRIDGE": True,
            "ESP_CONSOLE_SECONDARY_NONE": True,
        }), encoding="utf-8")
        record_firmware_profile(False, 17, 18, build, usb_bridge=True)
        self.assertTrue(validate_firmware_profile(build)["usb_bridge"])
        (build / "database.bin").write_bytes(b"CHANGED")
        with self.assertRaisesRegex(ValueError, "verändert"):
            validate_firmware_profile(build)
        (build / "database.bin").write_bytes(b"APP")
        partition_file.write_bytes(b"\xff" * 0xC00)
        with self.assertRaisesRegex(ValueError, "passt nicht"):
            firmware_files(build)
        partition_file.write_bytes(partition_bytes)
        metadata["flash_files"]["0x520000"] = "database.bin"
        (build / "flasher_args.json").write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Unerwarteter"):
            firmware_files(build)

    def test_probe_requires_16_mb(self):
        check_probe_output("Detected flash size: 16MB")
        with self.assertRaisesRegex(ValueError, "16 MB"):
            check_probe_output("Detected flash size: 8MB")

    def test_source_folder_hashes_all_four_originals_and_builds_command(self):
        names = ("Magie_90_CC4.bin", "Loader_61640403_L5.0b_2MB.bin",
                 "M90_Las_Vegas.bin", "FactoryReset_61640403.xc")
        members = []
        for index, name in enumerate(names):
            content = bytes([index + 1]) * (index + 3)
            (self.root / name).write_bytes(content)
            members.append({"source_name": name, "size": len(content),
                            "sha256": hashlib.sha256(content).hexdigest().upper()})
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"members": members}), encoding="utf-8")
        with patch("usb_flash_core.MANIFEST", manifest):
            files = source_files(self.root)
            self.assertEqual(set(files), {"database", "loader", "config", "factory"})
            destination = seed_destination(files)
            self.assertIn("esp32-seed-", destination.name)
            command = seed_command(files, temporary_seed_path(destination))
            self.assertIn(str(self.root / names[0]), command)
            (self.root / names[3]).write_bytes(b"wrong")
            with self.assertRaisesRegex(ValueError, "SHA-256|Größe"):
                source_files(self.root)

    def test_gui_uart_config_rejects_reserved_pins(self):
        config = gui_sdkconfig(True, 17, 18)
        self.assertIn("CONFIG_M90_DB_UART_TEST=y", config)
        self.assertIn("CONFIG_M90_DB_UART_TX_GPIO=17", config)
        self.assertIn("CONFIG_IDF_TARGET=\"esp32s3\"", config)
        self.assertIn("not set", gui_sdkconfig(False, 17, 18))
        for pins in ((17, 17), (43, 44), (19, 18), (36, 18)):
            with self.assertRaisesRegex(ValueError, "GPIO"):
                gui_sdkconfig(True, *pins)

    def test_usb_bridge_config_is_separate_and_disables_usb_console(self):
        config = gui_sdkconfig(False, 17, 18, usb_bridge=True)
        self.assertIn("CONFIG_M90_DB_USB_BRIDGE=y", config)
        self.assertIn("CONFIG_ESP_CONSOLE_SECONDARY_NONE=y", config)
        self.assertIn("# CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG is not set", config)
        with self.assertRaisesRegex(ValueError, "getrennte Modi"):
            gui_sdkconfig(True, 17, 18, usb_bridge=True)

    def test_idf_wrapper_only_accepts_known_actions(self):
        self.assertEqual(idf_command("build")[-2:], ["-Action", "build"])
        with self.assertRaisesRegex(ValueError, "Aktion"):
            idf_command("erase-flash")


if __name__ == "__main__":
    unittest.main()
