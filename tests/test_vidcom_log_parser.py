import importlib.util
import struct
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "vidcom_log_parser.py"
SPEC = importlib.util.spec_from_file_location("vidcom_log_parser", MODULE_PATH)
assert SPEC and SPEC.loader
PARSER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PARSER
SPEC.loader.exec_module(PARSER)


def record(tick: int, command: int, payload: bytes) -> bytes:
    return struct.pack("<IHH", tick, command, len(payload)) + payload + b"\r\n"


class VidComLogParserTests(unittest.TestCase):
    def test_parses_newest_first_records_without_reordering(self) -> None:
        data = record(50234, 73, b"\x00") + record(50156, 79, b"\x09\x00")
        parsed = PARSER.parse_records(data)
        self.assertEqual([item.tick for item in parsed], [50234, 50156])
        self.assertEqual([item.command for item in parsed], [73, 79])
        self.assertEqual(parsed[1].payload_hex, "09 00")

    def test_rejects_truncated_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "truncated payload"):
            PARSER.parse_records(struct.pack("<IHH", 1, 34, 34) + b"\x00")

    def test_rejects_missing_record_terminator(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing CRLF"):
            PARSER.parse_records(struct.pack("<IHH", 1, 73, 1) + b"\x00XX")

    def test_summary_reports_tick_span_gap_and_command_counts(self) -> None:
        records = [
            PARSER.VidComRecord(0, 300, 64, 0, ""),
            PARSER.VidComRecord(10, 120, 57, 0, ""),
            PARSER.VidComRecord(20, 100, 57, 0, ""),
        ]
        summary = PARSER.summarize_records(records)
        self.assertEqual(summary["first_tick"], 100)
        self.assertEqual(summary["last_tick"], 300)
        self.assertEqual(summary["largest_forward_gap"]["milliseconds"], 180)
        self.assertEqual(summary["command_counts"], {"57": 2, "64": 1})


if __name__ == "__main__":
    unittest.main()
