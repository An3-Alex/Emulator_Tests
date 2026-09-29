import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "admission_card.py"
spec = importlib.util.spec_from_file_location("admission_card", SCRIPT)
card = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = card
spec.loader.exec_module(card)


class AdmissionCardTests(unittest.TestCase):
    def test_ergoline_m90_layout_matches_programmer(self) -> None:
        template = bytes([0xA5]) * card.EEPROM_SIZE
        image = card.build_m90_eeprom(template, "123456789")
        self.assertEqual(image[40:49], b"123456789")
        self.assertEqual(image[64:73], bytes.fromhex("01 23 45 67 89 06 32 11 55"))
        self.assertEqual(card.inspect_eeprom(image), ("123456789", card.ERGO_M90_ID))
        self.assertEqual(image[:40], template[:40])
        self.assertEqual(image[49:64], template[49:64])
        self.assertEqual(image[73:], template[73:])

    def test_leading_zero_is_preserved(self) -> None:
        image = card.build_m90_eeprom(bytes(256), "000000001")
        self.assertEqual(image[40:49], b"000000001")
        self.assertEqual(image[64:69], bytes.fromhex("00 00 00 00 01"))

    def test_rejects_invalid_images_and_numbers(self) -> None:
        for number in ("12345678", "1234567890", "1234x6789", "１２３４５６７８９"):
            with self.subTest(number=number), self.assertRaises(ValueError):
                card.build_m90_eeprom(bytes(256), number)
        with self.assertRaises(ValueError):
            card.build_m90_eeprom(bytes(255), "123456789")
        broken = bytearray(card.build_m90_eeprom(bytes(256), "123456789"))
        broken[64] ^= 1
        with self.assertRaisesRegex(ValueError, "disagree"):
            card.inspect_eeprom(bytes(broken))


if __name__ == "__main__":
    unittest.main()
