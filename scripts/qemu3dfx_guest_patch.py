"""Apply the pinned OpenGL wrapper's XP-safe diagnostic output fix."""
from pathlib import Path
import sys
from qemu3dfx_dual_output import patch_guest, patch_slots


def patch_debug_output(source: str) -> str:
    marker = "/* M90: format diagnostics without opening the XP NUL device. */"
    old = ('        FILE *f = fopen("NUL", "w"); int c = fprintf(f, fmt, ##__VA_ARGS__); fclose(f); \\\n'
           '        char *str = HeapAlloc(GetProcessHeap(), 0, ALIGNED((c+1))); \\\n')
    if marker in source:
        if 'fopen("NUL"' in source:
            raise ValueError("Partially patched wrapper diagnostic output")
        return source
    if source.count(old) != 1:
        raise ValueError("Unexpected wrapper diagnostic output revision")
    source = source.replace(old, marker + "\n" +
        '        char str[1024]; int c = snprintf(str, sizeof(str), fmt, ##__VA_ARGS__); \\\n'
        '        if (c < 0) break; \\\n'
        '        if ((size_t)c >= sizeof(str)) c = sizeof(str) - 1; \\\n')
    for formatter in ("wsprintf", "sprintf"):
        source = source.replace(f'        {formatter}(str, fmt, ##__VA_ARGS__); \\\n', "")
    source = source.replace('        HeapFree(GetProcessHeap(), 0, str); \\\n', "")
    # Keep the comment on a continued macro line.
    source = source.replace(marker + "\n        char", marker + " \\\n        char")
    return source


def apply(checkout: Path) -> None:
    paths = [checkout / "wrappers/mesa/src/wrapgl32.c",
             checkout / "wrappers/fxlib/fxhook.c"]
    updates = [(path, patch_guest(patch_debug_output(path.read_text())) if path.name == 'wrapgl32.c'
                else patch_debug_output(path.read_text())) for path in paths]
    for path, value in updates:
        path.write_text(value)
    # Guest and host must agree on the context-slot count (see patch_slots).
    header = checkout / "qemu-1/hw/mesa/mglfuncs.h"
    header.write_text(patch_slots(header.read_text()))


if __name__ == "__main__":
    apply(Path(sys.argv[1]))
