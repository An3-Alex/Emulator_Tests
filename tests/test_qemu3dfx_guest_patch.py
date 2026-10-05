import unittest
from qemu3dfx_guest_patch import patch_debug_output


class GuestDebugOutputTests(unittest.TestCase):
    def test_both_upstream_macros_and_idempotence(self):
        for formatter in ("wsprintf", "sprintf"):
            with self.subTest(formatter=formatter):
                source = ('#define OHST_DMESG(fmt, ...) \\\n'
                    '        FILE *f = fopen("NUL", "w"); int c = fprintf(f, fmt, ##__VA_ARGS__); fclose(f); \\\n'
                    '        char *str = HeapAlloc(GetProcessHeap(), 0, ALIGNED((c+1))); \\\n'
                    f'        {formatter}(str, fmt, ##__VA_ARGS__); \\\n'
                    '        send(str, c+1); \\\n'
                    '        HeapFree(GetProcessHeap(), 0, str); \\\n')
                patched = patch_debug_output(source)
                self.assertNotIn('fopen("NUL"', patched)
                self.assertNotIn('HeapAlloc', patched)
                self.assertIn('snprintf(str, sizeof(str)', patched)
                self.assertIn('if (c < 0) break;', patched)
                self.assertIn('c = sizeof(str) - 1', patched)
                self.assertEqual(patch_debug_output(patched), patched)

    def test_reject_unknown_revision(self):
        with self.assertRaises(ValueError):
            patch_debug_output("unknown source")

    def test_reject_partial_patch(self):
        with self.assertRaises(ValueError):
            patch_debug_output('/* M90: format diagnostics without opening the XP NUL device. */\nfopen("NUL", "w");')
