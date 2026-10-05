"""Restore real shader formatting without adding CRT startup to WineD3D."""
from pathlib import Path
import re
import sys


BRIDGE = r'''/* M90: native XP CRT formatting; no local formatter or CRT startup. */
__attribute__((dllimport)) void *__stdcall LoadLibraryA(const char *name);
__attribute__((dllimport)) void *__stdcall GetProcAddress(void *module, const char *name);
__attribute__((dllimport)) int __stdcall FreeLibrary(void *module);
__attribute__((dllimport)) long __stdcall InterlockedCompareExchange(long volatile *dest, long exchange, long comparand);
static long volatile m90_format_crt;
static void *m90_format_symbol(const char *name)
{
    long module = InterlockedCompareExchange(&m90_format_crt, 0, 0);
    if (!module) {
        void *loaded = LoadLibraryA("msvcrt.dll");
        long previous;
        if (!loaded) return 0;
        previous = InterlockedCompareExchange(&m90_format_crt, (long)loaded, 0);
        if (previous) { FreeLibrary(loaded); module = previous; }
        else module = (long)loaded;
    }
    return GetProcAddress((void *)module, name);
}
static int m90_native_vsnprintf(char *buf, unsigned int size, const char *fmt, void *ap)
{
    typedef int (__cdecl *formatter)(char *, unsigned int, const char *, void *);
    formatter call = (formatter)m90_format_symbol("_vsnprintf");
    if (!call) { if (buf && size) buf[0] = 0; return -1; }
    return call(buf, size, fmt, ap);
}
static int m90_native_vscprintf(const char *fmt, void *ap)
{
    typedef int (__cdecl *formatter)(const char *, void *);
    formatter call = (formatter)m90_format_symbol("_vscprintf");
    return call ? call(fmt, ap) : -1;
}
'''


def replace_function(source: str, name: str, body: str) -> str:
    pattern = rf"\bint\s+(?:__cdecl\s+)?{re.escape(name)}\([^{{;]*\)\s*\{{"
    matches = list(re.finditer(pattern, source))
    if len(matches) != 1:
        raise ValueError(f"Unexpected formatter definition: {name}")
    start, end = matches[0].span()
    depth = 1
    while depth and end < len(source):
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    if depth:
        raise ValueError("Unterminated formatter")
    return source[:start] + body + source[end:]


def patch_source(source: str, kind: str) -> str:
    if kind not in ("heap", "math"):
        raise ValueError("Unknown formatter source kind")
    if BRIDGE in source:
        return source
    if "M90: native XP CRT formatting" in source:
        raise ValueError("Partially modified formatter bridge")
    # Preserve all heap/math behavior; replace only their formatting entries.
    source = BRIDGE + "\n" + source
    if kind == "heap":
        source = replace_function(source, "sprintf", '''int __cdecl sprintf(char *buf, const char *fmt, ...)
{
    __builtin_va_list ap;
    int result;
    __builtin_va_start(ap, fmt);
    result = m90_native_vsnprintf(buf, 0x7fffffffU, fmt, ap);
    __builtin_va_end(ap);
    return result;
}''')
    source = replace_function(source, "_vsnprintf", '''int __cdecl _vsnprintf(char *buf, unsigned int size, const char *fmt, void *ap)
{
    return m90_native_vsnprintf(buf, size, fmt, ap);
}''')
    if kind == "math":
        source = replace_function(source, "_vscprintf", '''int __cdecl _vscprintf(const char *fmt, void *ap)
{
    return m90_native_vscprintf(fmt, ap);
}''')
        source = replace_function(source, "__ms_vsnprintf", '''int __cdecl __ms_vsnprintf(char *buf, unsigned int size, const char *fmt, void *ap)
{
    return m90_native_vsnprintf(buf, size, fmt, ap);
}''')
    return source


def apply(checkout: Path) -> None:
    paths = [(checkout / "docker/heap_compat.c", "heap"),
             (checkout / "docker/mingw_matherr_stubs.c", "math")]
    updates = [(path, patch_source(path.read_text(), kind)) for path, kind in paths]
    for path, source in updates:
        path.write_text(source)


if __name__ == "__main__":
    apply(Path(sys.argv[1]))
