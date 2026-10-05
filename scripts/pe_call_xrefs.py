#!/usr/bin/env python3
import argparse, struct
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument("image",type=Path); p.add_argument("target",type=lambda x:int(x,0)); a=p.parse_args()
d=a.image.read_bytes(); u16=lambda o:struct.unpack_from('<H',d,o)[0]; u32=lambda o:struct.unpack_from('<I',d,o)[0]
pe=u32(0x3c); coff=pe+4; opt=coff+20; base=u32(opt+28); sec=opt+u16(coff+16)
for n in range(u16(coff+2)):
 o=sec+n*40; name=d[o:o+8].rstrip(b'\0').decode(errors='replace'); va=u32(o+12); size=u32(o+16); raw=u32(o+20)
 if name != '.text': continue
 for i in range(raw,raw+size-4):
  if d[i] == 0xe8:
   callva=base+va+i-raw; target=callva+5+struct.unpack_from('<i',d,i+1)[0]
   if target == a.target: print(f"0x{callva:08x} file=0x{i:x}")
