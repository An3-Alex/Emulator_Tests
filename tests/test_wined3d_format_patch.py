import unittest
from wined3d_format_patch import BRIDGE, patch_source
from verify_wined3d_crt import verify_formatter_imports


class ShaderFormatterPatchTests(unittest.TestCase):
    def test_native_atomic_import_does_not_need_crt_pseudo_relocations(self):
        self.assertIn('__attribute__((dllimport)) long __stdcall InterlockedCompareExchange', BRIDGE)
        verify_formatter_imports({'___ms_vsnprintf': 1, '__imp__InterlockedCompareExchange@12': 2})
        for symbols in ({'___ms_vsnprintf': 1}, {'__fu10__InterlockedCompareExchange@12': 2}):
            with self.assertRaises(ValueError):
                verify_formatter_imports(symbols)

    def test_math_formatters_no_longer_return_fake_success(self):
        source = '''int _vsnprintf(char *buf,unsigned int size,const char *fmt,void *ap) { return 0; }
int _vscprintf(const char *fmt,void *ap) { return 0; }
int __ms_vsnprintf(char *buf,unsigned int size,const char *fmt,void *ap) { return 0; }
double unchanged(double v) { return v; }
'''
        fixed = patch_source(source, "math")
        self.assertNotIn("return 0; }", fixed)
        self.assertIn("return m90_native_vsnprintf(buf, size, fmt, ap);", fixed)
        self.assertIn("return m90_native_vscprintf(fmt, ap);", fixed)
        self.assertIn("double unchanged(double v) { return v; }", fixed)
        self.assertEqual(patch_source(fixed, "math"), fixed)

    def test_heap_preserved_and_variadic_call_uses_real_va_list(self):
        source = '''int __cdecl sprintf(char *buf,const char *fmt,...) { if (1) { return 0; } }
int __cdecl _vsnprintf(char *buf,unsigned int count,const char *fmt,void *ap) { return 0; }
void *malloc(unsigned int size) { return custom_heap(size); }
'''
        fixed = patch_source(source, "heap")
        self.assertIn("__builtin_va_start(ap, fmt)", fixed)
        self.assertIn('custom_heap(size)', fixed)
        self.assertEqual(fixed.count(BRIDGE), 1)

    def test_unknown_or_partial_source_refused(self):
        for source in ("unknown", "/* M90: native XP CRT formatting */"):
            with self.assertRaises(ValueError):
                patch_source(source, "math")
