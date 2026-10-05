"""Keep the configured guest CPU brand visible under WHPX, not the host brand."""
from pathlib import Path
import sys


def patch_whpx_cpu_brand(source: str) -> str:
    lists = (
        "UINT32 cpuidExitList[] = {1, 0x80000001};",
        "UINT32 cpuidExitList[] = {1, 0x80000001, 0x40000000, 0x40000010};",
    )
    updates = []
    for original in lists:
        replacement = original.replace("0x80000001", "0x80000001, 0x80000002, 0x80000003, 0x80000004")
        if source.count(original) == 1 and replacement not in source:
            updates.append((original, replacement))
        elif original not in source and source.count(replacement) == 1:
            continue
        else:
            raise ValueError("Unexpected WHPX CPUID exit-list revision")
    if updates and len(updates) != len(lists):
        raise ValueError("Partially patched WHPX CPU brand")
    for original, replacement in updates:
        source = source.replace(original, replacement)
    return source


def apply(checkout: Path) -> None:
    path = checkout / "qemu-9.2.2/target/i386/whpx/whpx-all.c"
    path.write_text(patch_whpx_cpu_brand(path.read_text()))


if __name__ == "__main__":
    apply(Path(sys.argv[1]))
