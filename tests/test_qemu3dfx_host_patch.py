import unittest
from qemu3dfx_host_patch import patch_whpx_cpu_brand


class WhpxCpuBrandTests(unittest.TestCase):
    source = ("UINT32 cpuidExitList[] = {1, 0x80000001};\n"
              "UINT32 cpuidExitList[] = {1, 0x80000001, 0x40000000, 0x40000010};\n")

    def test_both_initial_and_frequency_lists_keep_brand_leaves(self):
        patched = patch_whpx_cpu_brand(self.source)
        for leaf in ("0x80000002", "0x80000003", "0x80000004"):
            self.assertEqual(patched.count(leaf), 2)
        self.assertIn("0x40000000, 0x40000010", patched)
        self.assertEqual(patch_whpx_cpu_brand(patched), patched)

    def test_unknown_or_partial_source_is_rejected(self):
        with self.assertRaises(ValueError):
            patch_whpx_cpu_brand("unknown")
        partial = self.source.replace("0x80000001};", "0x80000001, 0x80000002, 0x80000003, 0x80000004};")
        with self.assertRaises(ValueError):
            patch_whpx_cpu_brand(partial)
