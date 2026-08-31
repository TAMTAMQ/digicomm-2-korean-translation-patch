from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_STATE = ROOT / "build" / "digicomm_nyo_kr_minigame_state1fix.ss1"
SOURCE_ROM = Path(
    r"D:\game\gba\DigiCommunication Nyo - Datou! Black Gemagema Dan (Japan)"
    r"\DigiCommunication Nyo - Datou! Black Gemagema Dan (Japan).gba"
)
ART_ROM = ROOT / "build" / "digicomm_nyo_kr_minigame_photoshopfix3.gba"
OUT_STATE = ROOT / "build" / "digicomm_nyo_kr_minigame_photoshopfix5.ss1"
REPORT = ROOT / "build" / "digicomm_nyo_kr_minigame_photoshopfix5.ss1.json"

MFM = 0xD81758
GROUPS = {
    "호객": [0x30, 0x31, 0x32],
    "판매": [0x33, 0x34, 0x35],
    "정돈": [0x39, 0x3A, 0x3B],
    "회원": [0x3C, 0x3D, 0x3E],
}


def parse_png_chunks(blob: bytes):
    if blob[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError("not PNG/mGBA ss1")
    chunks = []
    p = 8
    while p + 12 <= len(blob):
        n = struct.unpack_from(">I", blob, p)[0]
        typ = blob[p + 4:p + 8]
        data = blob[p + 8:p + 8 + n]
        crc = struct.unpack_from(">I", blob, p + 8 + n)[0]
        if (zlib.crc32(typ + data) & 0xFFFFFFFF) != crc:
            raise RuntimeError(f"bad chunk CRC {typ!r}")
        chunks.append((typ, data))
        p += 12 + n
        if typ == b"IEND":
            break
    return chunks


def build_png(chunks) -> bytes:
    out = bytearray(b"\x89PNG\r\n\x1a\n")
    for typ, data in chunks:
        out += struct.pack(">I", len(data))
        out += typ
        out += data
        out += struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
    return bytes(out)


def mfm_entries(rom: bytes):
    count, entry_size = struct.unpack_from("<HH", rom, MFM + 8)
    ptr = struct.unpack_from("<I", rom, MFM + 16)[0] - 0x08000000
    out = {}
    for i in range(count):
        at = ptr + i * entry_size
        code = struct.unpack_from(">H", rom, at)[0]
        out[code] = bytes(rom[at + 2:at + entry_size])
    return out


def get_cell(vram: bytearray, base_tile: int, col: int) -> bytes:
    top = 0x10000 + (base_tile + col) * 32
    bot = 0x10000 + (base_tile + 4 + col) * 32
    return bytes(vram[top:top + 32] + vram[bot:bot + 32])


def put_cell(vram: bytearray, base_tile: int, col: int, data: bytes) -> None:
    if len(data) != 64:
        raise ValueError(len(data))
    top = 0x10000 + (base_tile + col) * 32
    bot = 0x10000 + (base_tile + 4 + col) * 32
    vram[top:top + 32] = data[:32]
    vram[bot:bot + 32] = data[32:]


def decode_number_col(vram: bytearray, base_tile: int):
    rows = [[0] * 8 for _ in range(16)]
    for y in range(16):
        ti = base_tile + 3 + (4 if y >= 8 else 0)
        raw = vram[0x10000 + ti * 32:0x10000 + (ti + 1) * 32]
        yy = y & 7
        for x in range(8):
            z = raw[yy * 4 + x // 2]
            rows[y][x] = (z >> (4 * (x & 1))) & 15
    return rows


def encode_number_col(vram: bytearray, base_tile: int, rows) -> None:
    for half in range(2):
        raw = bytearray(32)
        for y in range(8):
            for x in range(8):
                q = rows[half * 8 + y][x] & 15
                idx = y * 4 + x // 2
                raw[idx] |= q << (4 * (x & 1))
        ti = base_tile + 3 + half * 4
        vram[0x10000 + ti * 32:0x10000 + (ti + 1) * 32] = raw


def clean_detached_number_pixels(vram: bytearray, base_tile: int):
    rows = decode_number_col(vram, base_tile)
    points = {(x, y) for y in range(16) for x in range(8) if rows[y][x]}
    components = []
    while points:
        seed = points.pop()
        stack = [seed]
        comp = {seed}
        while stack:
            x, y = stack.pop()
            for nb in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if nb in points:
                    points.remove(nb)
                    comp.add(nb)
                    stack.append(nb)
        components.append(comp)
    if len(components) <= 1:
        return {"components_before": [len(c) for c in components], "removed_pixels": 0}
    components.sort(key=len, reverse=True)
    keep = components[0]
    removed = sum(len(c) for c in components[1:])
    for y in range(16):
        for x in range(8):
            if rows[y][x] and (x, y) not in keep:
                rows[y][x] = 0
    encode_number_col(vram, base_tile, rows)
    return {"components_before": [len(c) for c in components], "removed_pixels": removed}


def main() -> None:
    source_blob = SOURCE_STATE.read_bytes()
    chunks = parse_png_chunks(source_blob)
    source_rom = SOURCE_ROM.read_bytes()
    art_rom = ART_ROM.read_bytes()
    src_entries = mfm_entries(source_rom)
    art_entries = mfm_entries(art_rom)

    state_index = next(i for i, (typ, _) in enumerate(chunks) if typ == b"gbAs")
    state = bytearray(zlib.decompress(chunks[state_index][1]))
    if len(state) < 0x19000:
        raise RuntimeError(f"short gbAs {len(state)}")
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
            if all(get_cell(vram, base_tile, col) == src_entries[code] for col, code in enumerate(codes)):
                matched = (label, codes)
                break
        if matched is None:
            continue
        label, codes = matched
        for col, code in enumerate(codes):
            put_cell(vram, base_tile, col, art_entries[code])
        number_cleanup = clean_detached_number_pixels(vram, base_tile)
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
            "number_cleanup": number_cleanup,
        })

    state[0x1000:0x19000] = vram
    chunks[state_index] = (b"gbAs", zlib.compress(bytes(state), 9))
    output = build_png(chunks)
    OUT_STATE.write_bytes(output)

    # Reparse the generated state to make sure the PNG chunks and gbAs survive.
    verify_chunks = parse_png_chunks(output)
    verify_state = zlib.decompress(next(data for typ, data in verify_chunks if typ == b"gbAs"))
    if verify_state != bytes(state):
        raise RuntimeError("state round-trip mismatch")
    if not patched:
        raise RuntimeError("no active Japanese status cache found in state1")

    report = {
        "source": str(SOURCE_STATE),
        "output": str(OUT_STATE),
        "sha256": hashlib.sha256(output).hexdigest().upper(),
        "art_source": str(ART_ROM),
        "policy": "replace only exact active Japanese MFM status caches with photoshopfix3 art; keep largest connected numeric component",
        "patched_objects": patched,
        "gbAs_size": len(state),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
