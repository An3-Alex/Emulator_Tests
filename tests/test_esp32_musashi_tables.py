"""The generated opcode tables must not consume ESP32 internal SRAM."""

import importlib.util
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / 'esp32-db' / 'place_musashi_tables.py'
spec = importlib.util.spec_from_file_location('place_musashi_tables', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MusashiTablePlacementTests(unittest.TestCase):
    def test_patches_both_large_tables(self):
        source = (
            '#include "m68kops.h"\n'
            'void  (*m68ki_instruction_jump_table[0x10000])(void);\n'
            'unsigned char m68ki_cycles[NUM_CPU_TYPES][0x10000];\n'
        )
        patched = module.patch_source(source)
        self.assertIn('DB_EXTERNAL_BSS void  (*m68ki_instruction_jump_table', patched)
        self.assertIn('DB_EXTERNAL_BSS unsigned char m68ki_cycles', patched)
        self.assertIn('EXT_RAM_BSS_ATTR', patched)

    def test_rejects_changed_generator_output(self):
        with self.assertRaises(ValueError):
            module.patch_source('#include "m68kops.h"\n')


if __name__ == '__main__':
    unittest.main()
