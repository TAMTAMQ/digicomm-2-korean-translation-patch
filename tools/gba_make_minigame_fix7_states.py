from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path

from gba_make_minigame_state1_fix import build_png, get_cell, mfm_entries, parse_png_chunks, put_cell

ROOT = Path(__file__).resolve().parents[1]
CURRENT_ROM = ROOT / "build" / "digicomm_nyo_kr_minigame_photoshopfix6.gba"
FIX_ROM = ROOT / "build" / "digicomm_nyo_kr_minigame_photoshopfix7.gba"

BASE_GROUPS = {
    "호객": [0x30,0x31,0x32,0x33],
    "판매": [0x34,0x35,0x36,0x37],
    "정돈": [0x38,0x39,0x3A,0x3B],
    "회원": [0x3C,0x3D,0x3E,0x3F],
}
EXTRA_GROUPS = {
    "가짜 호객": [0x40,0x41,0x42,0x43],
    "가짜 회원": [0x44,0x45,0x46,0x47],
    "가짜 판매": [0x48,0x49,0x4A,0x4B],
    "가드": [0x4C,0x4D,0x4E,0x4F],
    "가짜 호객(중복)": [0x54,0x55,0x56,0x57],
    "가짜 회원(중복)": [0x58,0x59,0x5A,0x5B],
    "가짜 판매(중복)": [0x5C,0x5D,0x5E,0x5F],
}


def convert(slot: int, current: dict[int, bytes], fixed: dict[int, bytes]):
    src = ROOT / "build" / f"digicomm_nyo_kr_minigame_photoshopfix6.ss{slot}"
    out = ROOT / "build" / f"digicomm_nyo_kr_minigame_photoshopfix7.ss{slot}"
    report_path = ROOT / "build" / f"digicomm_nyo_kr_minigame_photoshopfix7.ss{slot}.json"
    chunks = parse_png_chunks(src.read_bytes())
    si = next(i for i,(t,_) in enumerate(chunks) if t == b"gbAs")
    state = bytearray(zlib.decompress(chunks[si][1]))
    oam = state[0xC00:0x1000]
    vram = bytearray(state[0x1000:0x19000])
    patched=[]
    for i in range(128):
        a0,a1,a2,_=struct.unpack_from('<4H',oam,i*8)
        affine=bool(a0&0x100); disabled=(not affine) and bool(a0&0x200)
        if disabled or ((a0>>14)&3)!=1 or ((a1>>14)&3)!=2 or ((a2>>12)&15)!=13:
            continue
        base=a2&0x3ff
        x=a1&0x1ff; y=a0&0xff
        if x>=256:x-=512
        if y>=160:y-=256
        matched=False
        for label,codes in BASE_GROUPS.items():
            if all(get_cell(vram,base,c)==current[code] for c,code in enumerate(codes[:3])):
                for c,code in enumerate(codes[:3]):put_cell(vram,base,c,fixed[code])
                patched.append({'oam':i,'xy':[x,y],'base_tile':hex(base),'label':label,'kind':'base'})
                matched=True; break
        if matched:continue
        for label,codes in EXTRA_GROUPS.items():
            if all(get_cell(vram,base,c)==current[code] for c,code in enumerate(codes)):
                for c,code in enumerate(codes):put_cell(vram,base,c,fixed[code])
                patched.append({'oam':i,'xy':[x,y],'base_tile':hex(base),'label':label,'kind':'card_effect'})
                break
    state[0x1000:0x19000]=vram
    chunks[si]=(b'gbAs',zlib.compress(bytes(state),9))
    blob=build_png(chunks); out.write_bytes(blob)
    if zlib.decompress(next(d for t,d in parse_png_chunks(blob) if t==b'gbAs'))!=bytes(state):
        raise RuntimeError('roundtrip')
    report={'source':str(src),'output':str(out),'sha256':hashlib.sha256(blob).hexdigest().upper(),'patched_objects':patched}
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report


def main():
    current=mfm_entries(CURRENT_ROM.read_bytes()); fixed=mfm_entries(FIX_ROM.read_bytes())
    reports=[convert(i,current,fixed) for i in (1,2,3)]
    print(json.dumps(reports,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
