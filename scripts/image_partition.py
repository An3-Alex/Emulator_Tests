"""Find an unambiguous NTFS volume in a raw CF image, without mounting it."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import struct

SECTOR = 512
EXTENDED_TYPES = {0x05, 0x0F, 0x85}


@dataclass(frozen=True)
class ImageVolume:
    offset: int
    length: int


def locate_ntfs(path: Path) -> ImageVolume:
    size = path.stat().st_size
    with path.open("rb") as stream:
        def sector(offset: int) -> bytes:
            if offset < 0 or offset + SECTOR > size:
                raise ValueError("CF-Partitionsgrenze liegt außerhalb des Images")
            stream.seek(offset)
            return stream.read(SECTOR)

        def ntfs(offset: int, length: int) -> bool:
            boot = sector(offset)
            if boot[3:11] != b"NTFS    ":
                return False
            bytes_per_sector = struct.unpack_from("<H", boot, 11)[0]
            sectors = struct.unpack_from("<Q", boot, 40)[0]
            if (boot[510:512] != b"\x55\xaa" or bytes_per_sector not in (512, 1024, 2048, 4096)
                    or not boot[13] or boot[13] & (boot[13] - 1)
                    or not sectors or sectors * bytes_per_sector > length):
                raise ValueError("Ungültiger oder abgeschnittener NTFS-Bootsektor im CF-Image")
            return True

        first = sector(0)
        if first[3:11] == b"NTFS    " and ntfs(0, size):
            return ImageVolume(0, size)
        if first[510:512] != b"\x55\xaa":
            raise ValueError("CF-Image hat weder eine MBR-Tabelle noch einen NTFS-Bootsektor")

        candidates = []
        def entries(table: bytes):
            return [struct.unpack_from("<B3sB3sII", table, 446 + index * 16)
                    for index in range(4)]

        def candidate(kind: int, lba: int, count: int):
            offset, length = lba * SECTOR, count * SECTOR
            if not count or offset < SECTOR or offset + length > size:
                raise ValueError("Ungültige oder abgeschnittene CF-Partition")
            if kind == 0x07 and ntfs(offset, length):
                candidates.append(ImageVolume(offset, length))

        extents = []
        for _active, _chs, kind, _end_chs, lba, count in entries(first):
            if not kind:
                continue
            if kind == 0xEE:
                raise ValueError("GPT-CF-Images sind im XP-Profil noch nicht unterstützt")
            if not count or lba < 1 or (lba + count) * SECTOR > size:
                raise ValueError("Ungültige oder abgeschnittene CF-Partition")
            if any(lba < end and start < lba + count for start, end in extents):
                raise ValueError("Überlappende CF-Partitionen")
            extents.append((lba, lba + count))
        for _active, _chs, kind, _end_chs, lba, count in entries(first):
            if not kind:
                continue
            if kind == 0xEE:
                raise ValueError("GPT-CF-Images sind im XP-Profil noch nicht unterstützt")
            if kind not in EXTENDED_TYPES:
                candidate(kind, lba, count)
                continue
            base, end = lba, lba + count
            if not count or base < 1 or end * SECTOR > size:
                raise ValueError("Ungültige erweiterte CF-Partition")
            current, visited, logical_extents = base, set(), []
            while current:
                if current in visited or len(visited) >= 128 or not base <= current < end:
                    raise ValueError("Ungültige EBR-Kette im CF-Image")
                visited.add(current)
                table = sector(current * SECTOR)
                if table[510:512] != b"\x55\xaa":
                    raise ValueError("Ungültige EBR-Signatur im CF-Image")
                logical, link, *extra = entries(table)
                if any(entry[2] for entry in extra):
                    raise ValueError("Mehrdeutige EBR-Tabelle im CF-Image")
                if logical[2]:
                    start, stop = current + logical[4], current + logical[4] + logical[5]
                    if logical[2] in EXTENDED_TYPES or not logical[4] or stop > end:
                        raise ValueError("Ungültige logische CF-Partition")
                    if any(start < previous_end and previous_start < stop
                           for previous_start, previous_end in logical_extents):
                        raise ValueError("Überlappende logische CF-Partitionen")
                    logical_extents.append((start, stop))
                    candidate(logical[2], current + logical[4], logical[5])
                if not link[2]:
                    break
                if (link[2] not in EXTENDED_TYPES or not link[4] or not link[5]
                        or base + link[4] + link[5] > end):
                    raise ValueError("Ungültiger EBR-Verweis im CF-Image")
                current = base + link[4]
        if len(candidates) != 1:
            raise ValueError(f"CF-Image braucht eine eindeutige NTFS-Partition; gefunden: {len(candidates)}")
        return candidates[0]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    args = parser.parse_args()
    try:
        volume = locate_ntfs(args.image)
    except (OSError, ValueError) as exc:
        parser.exit(3, f"CF-Layout: {exc}\n")
    print(f"{volume.offset} {volume.length}")
