"""Move generated Musashi opcode tables to ESP32-S3 PSRAM BSS.

Run after m68kmake. The generated source is also built unchanged on Windows;
the attribute only takes effect in an ESP-IDF build.
"""

from pathlib import Path
import sys


PREFIX = (
    '#ifdef ESP_PLATFORM\n'
    '#include "esp_attr.h"\n'
    '#define DB_EXTERNAL_BSS EXT_RAM_BSS_ATTR\n'
    '#else\n'
    '#define DB_EXTERNAL_BSS\n'
    '#endif\n'
)
DECLARATIONS = (
    ('void  (*m68ki_instruction_jump_table[0x10000])(void);',
     'DB_EXTERNAL_BSS void  (*m68ki_instruction_jump_table[0x10000])(void);'),
    ('unsigned char m68ki_cycles[NUM_CPU_TYPES][0x10000];',
     'DB_EXTERNAL_BSS unsigned char m68ki_cycles[NUM_CPU_TYPES][0x10000];'),
)


def patch_source(source: str) -> str:
    if 'DB_EXTERNAL_BSS' in source:
        raise ValueError('generated source was already patched')
    for before, after in DECLARATIONS:
        if source.count(before) != 1:
            raise ValueError(f'expected exactly one declaration: {before}')
        source = source.replace(before, after, 1)
    marker = '#include "m68kops.h"\n'
    if source.count(marker) != 1:
        raise ValueError('generated source does not have expected include')
    return source.replace(marker, marker + PREFIX, 1)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit('usage: place_musashi_tables.py <generated-m68kops.c>')
    path = Path(sys.argv[1])
    source = path.read_text(encoding='utf-8')
    path.write_text(patch_source(source), encoding='utf-8', newline='')


if __name__ == '__main__':
    main()
