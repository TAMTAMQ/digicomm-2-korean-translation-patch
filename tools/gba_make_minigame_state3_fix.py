from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path

from gba_make_minigame_state1_fix import (
    build_png,
    decode_number_col,
    encode_number_col,
    get_cell,
    mfm_entries,
    parse_png_chunks,
    put_cell,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE_STATE = ROOT / "build" / "digicomm_nyo_kr_minigame_photoshopfix5.ss3"
CURRENT_ROM = ROOT / "build" / "digicomm_nyo_kr_minigame_photoshopfix5.gba"
FIX_ROM = ROOT / "build" / "digicomm_nyo_kr_minigame_photoshopfix6.gba"
OUT_STATE = ROOT / "build" / "digicomm_nyo_kr_minigame_photoshopfix6.ss3"
REPORT = ROOT / "build" / "digicomm_nyo_kr_minigame_photoshopfix6.ss3.json"

# Actual runtime 32x16 groups: 3 label cells + 1 numeric cell.
GROUPS = {
    "호객": [0x30, 0x31, 0x32, 0x33],
    "판매": [0x34, 0x35, 0x36, 0x37],
    "정돈": [0x38, 0x39, 0x3A, 0x3B],
    "회원": [0x3C, 0x3D, 0x3E, 0x3F],
}


def unpack_cell(data: bytes):
    px = []
    for value in data:
        px.extend((value & 15, value >> 4))
    return [px[y * 8:(y + 1) * 8] for y in range(16)]


def clean_numeric_overlay(vram: bytearray, base_tile: int, underlying: bytes):
    """Remove the old fourth-cell artwork while preserving the drawn number.

    The engine draws the number over the fourth MFM cell without clearing the
    whole 8x16 destination first.  In fix5, code 0x33 held the first 판매 tile
    and code 0x3B held the last 정돈 tile, so their old pixels remained under
    the number.  A pixel that still equals the underlying MFM cell is stale;
    pixels changed by the number renderer are retained.  Runtime number glyphs
    occupy rows 0..9 in every clean reference state, so rows 10..15 are always
    cleared.
    """
    current = decode_number_col(vram, base_tile)
    under = unpack_cell(underlying)
    before_nonzero = sum(bool(q) for row in current for q in row)
    removed = 0
    for y in range(16):
        for x in range(8):
            q = current[y][x]
            if not q:
                continue
            if y >= 10 or (under[y][x] and q == under[y][x]):
                current[y][x] = 0
                removed += 1
    encode_number_col(vram, base_tile, current)
    after_nonzero = sum(bool(q) for row in current for q in row)
    return {
        "before_nonzero": before_nonzero,
        "after_nonzero": after_nonzero,
        "removed_pixels": removed,
        "rows_10_15_nonzero": sum(bool(current[y][x]) for y in range(10, 16) for x in range(8)),
    }


def main() -> None:
    chunks = parse_png_chunks(SOURCE_STATE.read_bytes())
    state_index = next(i for i, (typ, _) in enumerate(chunks) if typ == b"gbAs")
    state = bytearray(zlib.decompress(chunks[state_index][1]))
    if len(state) < 0x19000:
        raise RuntimeError(f"short gbAs: {len(state)}")

    current_entries = mfm_entries(CURRENT_ROM.read_bytes())
    fixed_entries = mfm_entries(FIX_ROM.read_bytes())
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

        base_tile = a2 & 0x3FF
        matched = None
        for label, codes in GROUPS.items():
            if all(get_cell(vram, base_tile, col) == current_entries[code] for col, code in enumerate(codes[:3])):
                matched = (label, codes)
                break
        if matched is None:
            continue

        label, codes = matched
        for col, code in enumerate(codes[:3]):
            put_cell(vram, base_tile, col, fixed_entries[code])
        cleanup = clean_numeric_overlay(vram, base_tile, current_entries[codes[3]])

        x = a1 & 0x1FF
        y = a0 & 0xFF
        if x >= 256:
            x -= 512
        if y >= 160:
            y -= 256
        patched.append({
            "oam": i,
            "xy": [x, y],
            "base_tile": hex(base_tile),
            "label": label,
            "codes": [hex(code) for code in codes],
            "numeric_cleanup": cleanup,
        })

    state[0x1000:0x19000] = vram
    chunks[state_index] = (b"gbAs", zlib.compress(bytes(state), 9))
    output = build_png(chunks)
    OUT_STATE.write_bytes(output)

    verify_chunks = parse_png_chunks(output)
    verify_state = zlib.decompress(next(data for typ, data in verify_chunks if typ == b"gbAs"))
    if verify_state != bytes(state):
        raise RuntimeError("state round-trip mismatch")
    if not patched:
        raise RuntimeError("no active status objects matched state3")

    report = {
        "source": str(SOURCE_STATE),
        "output": str(OUT_STATE),
        "sha256": hashlib.sha256(output).hexdigest().upper(),
        "rom": str(FIX_ROM),
        "policy": "correct 4-cell group mapping; photoshopfix3 label shape; remove old fourth-cell art under number",
        "patched_objects": patched,
        "gbAs_size": len(state),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
