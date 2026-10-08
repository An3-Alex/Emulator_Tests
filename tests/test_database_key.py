"""Database key (D3) check, cache and automatic search with synthetic databases."""
import hashlib
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from database_key import DEFAULT_KEY, KeyCache, key_matches, parse_key, resolve_key
from m68k_database_transform import transform_database

TOOL = ROOT / "build/recover-d3/recover_database_d3.exe"


def make_database(directory: Path, d3: int, name: str = "database.bin") -> Path:
    size = 0x300
    runtime = bytearray((index * 17 + 3) & 0xFF for index in range(size))
    end = 0x1000 + size - 1
    struct.pack_into(">IIIII", runtime, 0, 0, end, (~end) & 0xFFFFFFFF, 0x61640403, end + 1)
    struct.pack_into(">II", runtime, 0x4C, 0x1100, (~0x1100) & 0xFFFFFFFF)
    struct.pack_into(">II", runtime, 0x100, 0x1100, 0x1104)  # first two 68020 vectors
    struct.pack_into(">I", runtime, 0, sum(runtime[4:]) & 0xFFFFFFFF)
    path = directory / name
    # The stream transform is symmetric.
    path.write_bytes(transform_database(bytes(runtime), d3))
    return path


def fake_tool(directory: Path, lines: list[str]) -> Path:
    tool = directory / "fake_search.cmd"
    tool.write_text("@echo off\r\n" + "".join(f"echo {line}\r\n" for line in lines), encoding="ascii")
    return tool


class DatabaseKeyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.cache = KeyCache(self.directory / "keys.json")
        self.messages, self.progress = [], []

    def resolve(self, database, setting="auto", tool=None):
        return resolve_key(database, setting, self.cache, tool or self.directory / "missing.exe",
                           self.progress.append, self.messages.append)

    def test_parse_key_accepts_auto_and_eight_hex_digits(self):
        self.assertIsNone(parse_key("auto"))
        self.assertEqual(parse_key("d27b7159"), 0xD27B7159)
        self.assertEqual(parse_key(" 0xD27B7159 "), 0xD27B7159)
        for text in ("12", "D27B71590", "xyz"):
            with self.assertRaises(ValueError):
                parse_key(text)

    def test_only_the_right_key_matches(self):
        data = make_database(self.directory, 0x12345678).read_bytes()
        self.assertTrue(key_matches(data, 0x12345678))
        self.assertFalse(key_matches(data, 0x12345679))
        self.assertFalse(key_matches(data[:-1], 0x12345678))

    def test_explicit_key_is_verified(self):
        database = make_database(self.directory, 0x12345678)
        self.assertEqual(self.resolve(database, "12345678"), 0x12345678)
        with self.assertRaisesRegex(ValueError, "D3=87654321 nicht entschlüsseln"):
            self.resolve(database, "87654321")

    def test_known_default_key_needs_no_search(self):
        database = make_database(self.directory, DEFAULT_KEY)
        self.assertEqual(self.resolve(database), DEFAULT_KEY)
        self.assertEqual(self.messages, [])

    def test_search_result_is_cached_per_database(self):
        database = make_database(self.directory, 0x0BADC0DE)
        tool = fake_tool(self.directory, ["D3_PROGRESS tested=2147483648 rate=1 filtered=0",
                                          "D3_FOUND=0x0BADC0DE"])
        self.assertEqual(self.resolve(database, tool=tool), 0x0BADC0DE)
        self.assertEqual(self.progress, [0.5])
        digest = hashlib.sha256(database.read_bytes()).hexdigest().upper()
        self.assertEqual(self.cache.get(digest), 0x0BADC0DE)
        # The second start uses the stored key without searching again.
        self.messages.clear()
        self.assertEqual(self.resolve(database), 0x0BADC0DE)
        self.assertEqual(self.messages, [])

    def test_wrong_or_missing_search_result_is_refused(self):
        database = make_database(self.directory, 0x0BADC0DE)
        for lines in (["D3_FOUND=0x12345678"], ["D3_SCAN_DONE tested=4294967296"]):
            with self.subTest(lines=lines):
                with self.assertRaisesRegex(ValueError, "kein passender Schlüssel"):
                    self.resolve(database, tool=fake_tool(self.directory, lines))

    def test_missing_search_tool_is_reported(self):
        database = make_database(self.directory, 0x0BADC0DE)
        with self.assertRaisesRegex(ValueError, "Schlüsselsuche nicht verfügbar"):
            self.resolve(database)

    def test_foreign_file_is_not_searched(self):
        foreign = self.directory / "foreign.bin"
        foreign.write_bytes(bytes(0x400))
        with self.assertRaisesRegex(ValueError, "keine Datenbankdatei"):
            self.resolve(foreign, tool=fake_tool(self.directory, ["D3_FOUND=0x00000000"]))

    def test_corrupt_cache_is_ignored(self):
        self.cache.path.write_text("{not json", encoding="utf-8")
        database = make_database(self.directory, DEFAULT_KEY)
        self.assertEqual(self.resolve(database), DEFAULT_KEY)

    @unittest.skipUnless(TOOL.is_file(), "native key search tool not built")
    def test_native_search_finds_key_in_range(self):
        database = make_database(self.directory, 0x00ABCDEF)
        completed = subprocess.run([str(TOOL), str(database), "0x00AB0000", "0x20000", "2"],
                                   capture_output=True, text=True, timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertIn("D3_FOUND=0x00ABCDEF", completed.stdout)


if __name__ == "__main__":
    unittest.main()
