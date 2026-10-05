import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "m68k_database_transform.py"
SPEC = importlib.util.spec_from_file_location("m68k_database_transform", MODULE_PATH)
assert SPEC and SPEC.loader
TRANSFORM = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TRANSFORM)


class DatabaseTransformTests(unittest.TestCase):
    def test_stream_transform_is_symmetric_with_fresh_state(self):
        prefix = bytes(range(256))
        payload = b"database transport payload"
        state, i, j = TRANSFORM.initialize_state(prefix, 0x12345678)
        encrypted = TRANSFORM.transform_payload(payload, state, i, j)
        state, i, j = TRANSFORM.initialize_state(prefix, 0x12345678)
        self.assertEqual(
            TRANSFORM.transform_payload(encrypted, state, i, j), payload
        )

    def test_raw_prefix_is_not_transformed(self):
        data = bytes(range(256)) + b"opaque"
        transformed = TRANSFORM.transform_database(data, 0)
        self.assertEqual(transformed[:0x100], data[:0x100])
        self.assertNotEqual(transformed[0x100:], data[0x100:])

    def test_requires_exact_prefix(self):
        with self.assertRaisesRegex(ValueError, "exactly 0x100"):
            TRANSFORM.initialize_state(b"short", 0)


if __name__ == "__main__":
    unittest.main()
