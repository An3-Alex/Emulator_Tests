import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_esp32_seed import (  # noqa: E402
    LOADER_ADDRESS, RAM_SIZE, SECTOR_SIZE, make_ram_image, pack_image, unpack_image,
)


class Esp32SeedTests(unittest.TestCase):
    def test_memory_layout_and_roundtrip(self):
        ram = make_ram_image(b"LOAD", b"DB", [(RAM_SIZE - 4, b"CFG!")])
        self.assertEqual(ram[:4], RAM_SIZE.to_bytes(4, "big"))
        self.assertEqual(ram[LOADER_ADDRESS:LOADER_ADDRESS + 4], b"LOAD")
        self.assertEqual(ram[0x1000:0x1002], b"DB")
        self.assertEqual(ram[-4:], b"CFG!")
        image = pack_image(ram, 7)
        self.assertEqual(len(image), SECTOR_SIZE + RAM_SIZE)
        self.assertEqual(unpack_image(image), (ram, 7))

    def test_corruption_and_overlap_rejected(self):
        ram = make_ram_image(b"L", b"D", [])
        image = bytearray(pack_image(ram))
        image[-1] ^= 1
        with self.assertRaisesRegex(ValueError, "CRC32"):
            unpack_image(bytes(image))
        with self.assertRaisesRegex(ValueError, "loader"):
            make_ram_image(bytes(0xC01), b"D", [])
        with self.assertRaisesRegex(ValueError, "config write"):
            make_ram_image(b"L", b"D", [(RAM_SIZE - 1, b"AB")])


if __name__ == "__main__":
    unittest.main()
