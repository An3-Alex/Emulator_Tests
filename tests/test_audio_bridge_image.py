from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audio_bridge_image import configure, FLAG


class ImageBridgeTests(unittest.TestCase):
    def test_switch_changes_only_our_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'NVRAM').mkdir()
            (root / 'NVRAM/m90_setup_stage.txt').write_text('stage=ready')
            original = root / 'sthda.sys'
            original.write_bytes(b'original driver')
            configure(root, True)
            self.assertTrue((root / FLAG).is_file())
            configure(root, False)
            self.assertFalse((root / FLAG).exists())
            self.assertEqual(original.read_bytes(), b'original driver')

    def test_unprepared_image_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'NVRAM').mkdir()
            with self.assertRaises(ValueError):
                configure(root, True)
            self.assertFalse((root / FLAG).exists())

    def test_stale_temporary_file_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'NVRAM').mkdir()
            (root / 'NVRAM/m90_setup_stage.txt').write_text('stage=ready')
            (root / (FLAG + '.new')).write_text('unfinished')
            with self.assertRaises(ValueError):
                configure(root, True)
