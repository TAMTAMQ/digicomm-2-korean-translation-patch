from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path

from gba_make_minigame_state1_fix import (
    build_png,
    get_cell,
    mfm_entries,
    parse_png_chunks,
    put_cell,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE_STATE = ROOT / "build" / "digicomm_nyo_kr_minigame_photoshopfix6.ss2"
CURRENT_ROM = ROOT / "build" / "digicomm_nyo_kr_minigame_photoshopfix6.gba"
FIX_ROM = ROOT / "build" / "digicomm_nyo_kr_minigame_photoshopfix7.gba"
OUT_STATE = ROOT / "build" / "digicomm_nyo_kr_minigame_photoshopfix7.ss2"
REPORT = ROOT / "build" / "digicomm_nyo_kr_minigame_photoshopfix7.ss2.json"

BASE_GROUPS = {
    "호객": [0x30, 0x31, 0x32, 0x33],
    "판매": [0x34, 0x35, 0x36, 0x37],
    "정돈": [0x38, 0x39, 0x3A, 0x3B],
    "회원": [0x3C, 0x3D, 0x3E, 0x3F],
}
EXTRA_GROUPS = {
    "가짜 호객": [0x40, 0x41, 0x42, 0x43],
    "가짜 회원": [0x44, 0x45, 0x46, 0x47],
    "가짜 판매": [0x48, 0x49, 0x4A, 0x4B],
    "가드": [0x4C, 0x4D, 0x4E, 0x4F],
    "가짜 호객(중복)": [0x54, 0x55, 0x56, 0x57],
    "가짜 회원(중복)": [0x58, 0x59, 0x5A, 0x5B],
    "가짜 판매(중복)": [0x5C, 0x5D, 0x5E, 0x5F],
}


def main() -> None:
    chunks = parse_png_chunks(SOURCE_STATE.read_bytes())
    state_index = next(i for i, (typ, _) in enumerate(chunks) if typ == b"gbAs")
    state = bytearray(zlib.decompress(chunks[state_index][1]))
    if len(state) < 0x19000:
        raise RuntimeError(f"short gbAs: {len(state)}")

    current = mfm_entries(CURRENT_ROM.read_bytes())
    fixed = mfm_entries(FIX_ROM.read_bytes())
    oam = state[0xC00:0x1000]
    vram = bytearray(state[0x1000:0x19000])

    patched = []
    for i in range(128):
        a0, a1, a2, _ = struct.unpack_from("<4H", oam, i * 8)
        affine = bool(a0 & 0x100)
        disabled = (not affine) and bool(a0 & 0x200)
        shape = (a0 >> 14) & 3
        size = (a1 >> 14) & 3
        pal = (a2 >> 12) & 15
        if disabled or shape != 1 or size != 2 or pal != 13:
            continue
        base = a2 & 0x3FF
        x = a1 & 0x1FF
        y = a0 & 0xFF
        if x >= 256:
            x -= 512
        if y >= 160:
            y -= 256

        matched = False
        # Base states: only first three cells are static; fourth contains number.
        for label, codes in BASE_GROUPS.items():
            if all(get_cell(vram, base, col) == current[code] for col, code in enumerate(codes[:3])):
                for col, code in enumerate(codes[:3]):
                    put_cell(vram, base, col, fixed[code])
                patched.append({"oam": i, "xy": [x, y], "base_tile": hex(base), "label": label, "kind": "base"})
                matched = True
                break
        if matched:
            continue

        # Card effects: all four cells are static artwork and can be replaced.
        for label, codes in EXTRA_GROUPS.items():
            if all(get_cell(vram, base, col) == current[code] for col, code in enumerate(codes)):
                for col, code in enumerate(codes):
                    put_cell(vram, base, col, fixed[code])
                patched.append({
                    "oam": i,
                    "xy": [x, y],
                    "base_tile": hex(base),
                    "label": label,
                    "kind": "card_effect",
                    "codes": [hex(c) for c in codes],
                })
                matched = True
                break

    state[0x1000:0x19000] = vram
    chunks[state_index] = (b"gbAs", zlib.compress(bytes(state), 9))
    output = build_png(chunks)
    OUT_STATE.write_bytes(output)

    verify = zlib.decompress(next(data for typ, data in parse_png_chunks(output) if typ == b"gbAs"))
    if verify != bytes(state):
        raise RuntimeError("state round-trip mismatch")
    if not any(item["kind"] == "card_effect" for item in patched):
        raise RuntimeError("no active card-effect label found in state2")

    report = {
        "source": str(SOURCE_STATE),
        "output": str(OUT_STATE),
        "sha256": hashlib.sha256(output).hexdigest().upper(),
        "rom": str(FIX_ROM),
        "policy": "patch base statuses and every active card-effect 32x16 cache to fix7 MFM art",
        "patched_objects": patched,
        "gbAs_size": len(state),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
