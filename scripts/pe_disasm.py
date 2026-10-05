#!/usr/bin/env python3
import argparse, struct, sys
from pathlib import Path

dependency_dir = Path(__file__).parents[1] / ".deps" / "capstone"
if dependency_dir.is_dir():
    sys.path.insert(0, str(dependency_dir))
from capstone import Cs, CS_ARCH_X86, CS_MODE_32

p = argparse.ArgumentParser()
p.add_argument("image", type=Path)
p.add_argument("start", type=lambda x: int(x, 0))
p.add_argument("end", type=lambda x: int(x, 0))
a = p.parse_args()
d = a.image.read_bytes()
u16 = lambda o: struct.unpack_from("<H", d, o)[0]
u32 = lambda o: struct.unpack_from("<I", d, o)[0]
pe = u32(0x3c)
coff = pe + 4
opt = coff + 20
base = u32(opt + 28)
sections = []
sec = opt + u16(coff + 16)
for i in range(u16(coff + 2)):
    o = sec + i * 40
    sections.append((u32(o + 12), max(u32(o + 8), u32(o + 16)), u32(o + 20)))
def off(va):
    rva = va - base
    for sva, size, raw in sections:
        if sva <= rva < sva + size:
            return raw + rva - sva
    raise ValueError(hex(va))
code = d[off(a.start):off(a.end)]
md = Cs(CS_ARCH_X86, CS_MODE_32)
for ins in md.disasm(code, a.start):
    print(f"{ins.address:08x}  {ins.mnemonic:8} {ins.op_str}")
