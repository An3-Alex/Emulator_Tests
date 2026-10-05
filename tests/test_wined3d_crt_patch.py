import unittest
from wined3d_crt_patch import patch_build


class WineCrtBuildTests(unittest.TestCase):
    def test_flags_are_targeted_and_idempotent(self):
        source = ('$CC "${CFLAGS_LIST[@]}" -c -o "$obj_dir/_heap_compat.o" src\n'
                  '$CC "${CFLAGS_LIST[@]}" -c -o "$obj_dir/other.o" other\n')
        fixed = patch_build(source)
        self.assertIn('-fno-builtin -fno-tree-loop-distribute-patterns', fixed)
        self.assertIn('$CC "${CFLAGS_LIST[@]}" -c -o "$obj_dir/other.o"', fixed)
        self.assertEqual(patch_build(fixed), fixed)

    def test_unknown_build_fails_closed(self):
        with self.assertRaises(ValueError):
            patch_build("unknown")
