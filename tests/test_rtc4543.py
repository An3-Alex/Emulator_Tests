import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from rtc4543 import BITS, CE, CLK, DATA, WR, Rtc4543, calendar_bits, calendar_bytes


class Rtc4543Tests(unittest.TestCase):
    def test_calendar_layout_matches_52_bit_lsb_first_r4543_stream(self) -> None:
        when = dt.datetime(2012, 2, 1, 22, 14)
        self.assertEqual(calendar_bytes(when), bytes.fromhex("00 14 22 04 01 02 12"))
        bits = calendar_bits(when)
        self.assertEqual(len(bits), BITS)
        self.assertEqual(
            bits[:8], tuple((calendar_bytes(when)[0] >> bit) & 1 for bit in range(8))
        )

    def test_read_uses_clock_rising_edges_and_preserves_other_pins(self) -> None:
        rtc = Rtc4543(monotonic=lambda: 100.0)
        rtc.port_write(CE)
        bits = []
        for _ in range(BITS):
            output = rtc.port_write(CE | CLK | 0x03)
            bits.append(bool(output & DATA))
            self.assertEqual(output & 0x03, 0x03)
            rtc.port_write(CE | 0x03)
        self.assertEqual(tuple(int(bit) for bit in bits), calendar_bits(rtc.now()))
        self.assertEqual(rtc.completed_read, calendar_bytes(rtc.now()))
        self.assertEqual(rtc.port_write(CE | CLK) & DATA, 0)
        rtc.port_write(0)

    def test_complete_write_updates_clock_and_incomplete_write_does_not(self) -> None:
        tick = [100.0]
        rtc = Rtc4543(monotonic=lambda: tick[0])
        target = dt.datetime(2012, 2, 2, 8, 3, 4)
        rtc.port_write(CE | WR)
        for bit in calendar_bits(target):
            data = DATA if bit else 0
            rtc.port_write(CE | WR | data)
            rtc.port_write(CE | WR | CLK | data)
            rtc.port_write(CE | WR | data)
        rtc.port_write(0)
        self.assertEqual(rtc.completed_write, target)
        self.assertEqual(rtc.now(), target)
        tick[0] += 2.1
        self.assertEqual(rtc.now(), target + dt.timedelta(seconds=2))

        rtc.port_write(CE | WR)
        rtc.port_write(CE | WR | CLK | DATA)
        rtc.port_write(0)
        self.assertEqual(rtc.now(), target + dt.timedelta(seconds=2))


if __name__ == "__main__":
    unittest.main()
