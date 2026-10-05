"""Prevent compiler-generated self calls in WineD3D's local CRT primitives."""
from pathlib import Path
import sys


def patch_build(source: str) -> str:
    old = '$CC "${CFLAGS_LIST[@]}" -c -o "$obj_dir/_heap_compat.o"'
    new = '$CC "${CFLAGS_LIST[@]}" -fno-builtin -fno-tree-loop-distribute-patterns -c -o "$obj_dir/_heap_compat.o"'
    if new in source:
        return source
    if source.count(old) != 1:
        raise ValueError("Unknown WineD3D heap compiler command")
    return source.replace(old, new)


def apply(checkout: Path) -> None:
    from wined3d_format_patch import apply as apply_format
    apply_format(checkout)
    path = checkout / "build-ci.sh"
    path.write_text(patch_build(path.read_text()))


if __name__ == "__main__":
    apply(Path(sys.argv[1]))
