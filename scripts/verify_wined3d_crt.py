"""Run the built DLL's real x86 memory primitives offline with a bounded CPU."""
from pathlib import Path
import struct
import sys


def image_and_symbols(path: Path):
    data = path.read_bytes()
    pe = struct.unpack_from("<I", data, 0x3c)[0]
    machine, count, _, table, symbols, optional_size, _ = struct.unpack_from("<HHIIIHH", data, pe + 4)
    if machine != 0x14c or data[pe:pe+4] != b"PE\0\0":
        raise ValueError("Expected a symbol-bearing x86 WineD3D DLL")
    optional = pe + 24
    base = struct.unpack_from("<I", data, optional + 28)[0]
    size = struct.unpack_from("<I", data, optional + 56)[0]
    image = bytearray(size)
    section_rvas = []
    for index in range(count):
        section = optional + optional_size + index * 40
        _, _, rva, raw_size, raw = struct.unpack_from("<8sIIII", data, section)
        image[rva:rva+raw_size] = data[raw:raw+raw_size]
        section_rvas.append(rva)
    strings = table + symbols * 18
    addresses = {}
    index = 0
    while index < symbols:
        name, value, section, _, _, aux = struct.unpack_from("<8sIhHBB", data, table + index * 18)
        if name[:4] == b"\0" * 4:
            offset = strings + struct.unpack_from("<I", name, 4)[0]
            name = data[offset:data.index(b"\0", offset)]
        else:
            name = name.rstrip(b"\0")
        if 0 < section <= count:
            addresses[name.decode("ascii", "replace")] = base + section_rvas[section-1] + value
        index += 1 + aux
    return base, image, addresses


def verify_formatter_imports(symbols: dict) -> None:
    # These DLLs have no CRT startup to apply MinGW's auto-import pseudo relocations.
    for name in symbols:
        if name.startswith('__fu') and 'InterlockedCompareExchange' in name:
            raise ValueError('Formatter uses an unresolved MinGW auto-import')
    if '___ms_vsnprintf' in symbols and '__imp__InterlockedCompareExchange@12' not in symbols:
        raise ValueError('Formatter atomic cache must call the native import table')


def verify(path: Path) -> None:
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_32
    from unicorn.x86_const import (UC_X86_REG_ESP, UC_X86_REG_EIP, UC_X86_REG_EAX,
                                  UC_X86_REG_EBX, UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP)
    base, image, symbols = image_and_symbols(path)
    verify_formatter_imports(symbols)
    cpu = Uc(UC_ARCH_X86, UC_MODE_32)
    cpu.mem_map(base, (len(image) + 4095) & ~4095)
    cpu.mem_write(base, bytes(image))
    stack, source, destination, stop = 0x20000000, 0x30000000, 0x30020000, 0xf0000000
    cpu.mem_map(stack, 0x10000); cpu.mem_map(source, 0x50000)
    saved = (UC_X86_REG_EBX, UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP)

    def call(name, *args):
        pointer = stack + 0x8000
        cpu.mem_write(pointer, struct.pack('<' + 'I' * (len(args)+1), stop, *args))
        cpu.reg_write(UC_X86_REG_ESP, pointer)
        for register in saved: cpu.reg_write(register, 0x12345678)
        cpu.emu_start(symbols[name], stop, timeout=1000000, count=1000000)
        if cpu.reg_read(UC_X86_REG_EIP) != stop:
            raise ValueError(f"{name} did not return within its instruction budget")
        if cpu.reg_read(UC_X86_REG_ESP) != pointer + 4:
            raise ValueError(f"{name} corrupted the caller stack")
        if any(cpu.reg_read(register) != 0x12345678 for register in saved):
            raise ValueError(f"{name} corrupted a callee-saved register")
        return cpu.reg_read(UC_X86_REG_EAX)

    for length in (0, 1, 3, 17, 11340, 65535):
        for offset in (0, 1):
            target = destination + offset
            cpu.mem_write(destination, b'Z' * (length + 4))
            if call('_memset', target, 0xa5, length) != target:
                raise ValueError("memset return pointer changed")
            if bytes(cpu.mem_read(target, length + 1)) != b'\xa5' * length + b'Z':
                raise ValueError("memset contents/boundary incorrect")
            payload = bytes((index % 251 + 1 for index in range(length)))
            cpu.mem_write(source + offset, payload + b'\0')
            if call('_memcpy', target, source + offset, length) != target:
                raise ValueError("memcpy return pointer changed")
            if bytes(cpu.mem_read(target, length)) != payload:
                raise ValueError("memcpy contents incorrect")
            if call('_strlen', source + offset) != length:
                raise ValueError("strlen result incorrect")
    for start, target in ((0, 5), (5, 0)):
        payload = bytes(range(200))
        cpu.mem_write(source, payload)
        expected = bytearray(payload);expected[target:target+150] = payload[start:start+150]
        call('_memmove', source + target, source + start, 150)
        if bytes(cpu.mem_read(source, 200)) != bytes(expected):
            raise ValueError("memmove overlap incorrect")


if __name__ == '__main__':
    verify(Path(sys.argv[1]))
    print('WINED3D_CRT_MACHINE_CODE_OK')
