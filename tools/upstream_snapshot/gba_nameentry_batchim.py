"""Add two-step Hangul final-consonant (batchim) input to the name-entry screen.

Usage model:
  1. Enter a normal no-final syllable from page 1/2, e.g. 기 / 바 / 저.
  2. Switch to the second page and choose one of the first 27 final-consonant
     cells. The input handler replaces the previous syllable in-place:
         기 + ㅁ -> 김
         바 + ㄱ -> 박
         저 + ㅇ -> 정

This preserves the game's original 16-byte CP932 name buffer (8 characters).
No decomposed jamo is ever stored in the player name. The 27 token codes are
private to the 8x8 name-entry grid and are intercepted before insertion.

The patch assumes the existing Hangul name-entry pipeline has already run:
  - gba_nameentry_hangul.py
  - gba_nameentry_font8.py
  - gba_nameentry_page2.py

The existing page-2 lookup table at 0xFDE000 is reused. Its first 27 output
codes are replaced by 27 donor codes 0xEA80..0xEA9A. Those codes are existing
but unused slots in the 12x12 main MFM, validated against the source-code
usage list and the built Hangul map. The 8x8 MFM gets the same codes for the
normal grid, while the 12x12 donor glyphs are redrawn as enlarged jamo for the
focused-cell preview.

The original input function at 0x080500C4 is redirected through an 8-byte
Thumb trampoline to a new handler in the known-free ROM tail. The handler
keeps normal input behavior byte-compatible in spirit, but recognizes token
codes and maps (previous base syllable, final index) -> fully composed Hangul
using the canonical build Hangul map.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

from PIL import ImageFont

import gba_font_patch as font12
import gba_nameentry_font8 as font8

MFM = 0x00DD6808
PAGE2_TABLE = 0x00FDE000
PAGE2_COUNT = 96
TOKEN_CELLS = list(range(0, 14)) + list(range(48, 61))
INPUT_FUNC = 0x000500C4
INPUT_FUNC_ROM_ADDR = 0x080500C4
EXPECTED_INPUT_PREFIX = bytes.fromhex("30b5051ce86b2af0")

TOKEN_FIRST = 0xEA80
JONGSEONG = "ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"
TOKEN_LAST = TOKEN_FIRST + len(JONGSEONG) - 1

HOOK_CODE_AT = 0x00FDF800
PTR_TABLE_AT = 0x00FDFA00
MAP_DATA_AT = 0x00FDFA80
TAIL_END = 0x01000000

CURSOR_CHAR_FUNC = 0x0804FCFC | 1
RENDER_NAME_FUNC = 0x08071310 | 1
PLAY_SFX_FUNC = 0x08056474 | 1
MAIN_MFM = 0x00DA8DD4


class ThumbBuilder:
    """Tiny Thumb-1 assembler for the handful of instructions used here."""

    COND = {
        "eq": 0x0, "ne": 0x1, "cs": 0x2, "hs": 0x2,
        "cc": 0x3, "lo": 0x3, "mi": 0x4, "pl": 0x5,
        "vs": 0x6, "vc": 0x7, "hi": 0x8, "ls": 0x9,
        "ge": 0xA, "lt": 0xB, "gt": 0xC, "le": 0xD,
    }

    def __init__(self, base: int):
        self.base = base
        self.buf = bytearray()
        self.labels: dict[str, int] = {}
        self.branches: list[tuple[int, str, str | None]] = []
        self.lit_fixups: list[tuple[int, int, str]] = []
        self.literals: dict[str, int] = {}

    @property
    def pos(self) -> int:
        return len(self.buf)

    def hw(self, value: int) -> None:
        self.buf += struct.pack("<H", value & 0xFFFF)

    def label(self, name: str) -> None:
        if name in self.labels:
            raise ValueError(f"duplicate label {name}")
        self.labels[name] = self.pos

    def push(self, regs: list[int], lr: bool = False) -> None:
        mask = sum(1 << r for r in regs)
        self.hw(0xB400 | mask | (0x100 if lr else 0))

    def pop(self, regs: list[int], pc: bool = False) -> None:
        mask = sum(1 << r for r in regs)
        self.hw(0xBC00 | mask | (0x100 if pc else 0))

    def mov_imm(self, rd: int, imm: int) -> None:
        assert 0 <= rd < 8 and 0 <= imm <= 255
        self.hw(0x2000 | (rd << 8) | imm)

    def mov(self, rd: int, rs: int) -> None:
        assert 0 <= rd < 8 and 0 <= rs < 8
        self.hw((rs << 3) | rd)

    def lsl_imm(self, rd: int, rs: int, imm: int) -> None:
        assert 0 <= imm <= 31
        self.hw((imm << 6) | (rs << 3) | rd)

    def lsr_imm(self, rd: int, rs: int, imm: int) -> None:
        assert 1 <= imm <= 32
        enc = 0 if imm == 32 else imm
        self.hw(0x0800 | (enc << 6) | (rs << 3) | rd)

    def add_imm(self, rd: int, imm: int) -> None:
        assert 0 <= imm <= 255
        self.hw(0x3000 | (rd << 8) | imm)

    def sub_imm(self, rd: int, imm: int) -> None:
        assert 0 <= imm <= 255
        self.hw(0x3800 | (rd << 8) | imm)

    def sub_reg(self, rd: int, rs: int, rn: int) -> None:
        self.hw(0x1A00 | (rn << 6) | (rs << 3) | rd)

    def cmp_imm(self, rd: int, imm: int) -> None:
        assert 0 <= imm <= 255
        self.hw(0x2800 | (rd << 8) | imm)

    def cmp_reg(self, rd: int, rs: int) -> None:
        self.hw(0x4280 | (rs << 3) | rd)

    def orr(self, rd: int, rs: int) -> None:
        self.hw(0x4300 | (rs << 3) | rd)

    def ldr_imm(self, rd: int, rb: int, imm: int) -> None:
        assert imm % 4 == 0 and 0 <= imm <= 124
        self.hw(0x6800 | ((imm // 4) << 6) | (rb << 3) | rd)

    def ldr_reg(self, rd: int, rb: int, ro: int) -> None:
        self.hw(0x5800 | (ro << 6) | (rb << 3) | rd)

    def ldrb_reg(self, rd: int, rb: int, ro: int) -> None:
        self.hw(0x5C00 | (ro << 6) | (rb << 3) | rd)

    def strb_reg(self, rd: int, rb: int, ro: int) -> None:
        self.hw(0x5400 | (ro << 6) | (rb << 3) | rd)

    def ldrh_imm(self, rd: int, rb: int, imm: int) -> None:
        assert imm % 2 == 0 and 0 <= imm <= 62
        self.hw(0x8800 | ((imm // 2) << 6) | (rb << 3) | rd)

    def ldrsh_reg(self, rd: int, rb: int, ro: int) -> None:
        self.hw(0x5E00 | (ro << 6) | (rb << 3) | rd)

    def b(self, label: str, cond: str | None = None) -> None:
        pos = self.pos
        self.hw(0)
        self.branches.append((pos, label, cond))

    def ldr_literal(self, rd: int, name: str, value: int) -> None:
        if name in self.literals and self.literals[name] != value:
            raise ValueError(name)
        self.literals[name] = value
        pos = self.pos
        self.hw(0)
        self.lit_fixups.append((pos, rd, name))

    def indirect_call(self, name: str, target: int) -> None:
        self.ldr_literal(3, name, target)
        self.mov_imm(2, 3)
        self.hw(0x46FE)
        self.hw(0x4496)
        self.hw(0x4718)

    def finish(self) -> bytes:
        while len(self.buf) % 4:
            self.hw(0x46C0)
        lit_pos: dict[str, int] = {}
        for name, value in self.literals.items():
            lit_pos[name] = len(self.buf)
            self.buf += struct.pack("<I", value)

        for pos, label, cond in self.branches:
            if label not in self.labels:
                raise ValueError(f"unknown label {label}")
            target = self.base + self.labels[label]
            pc = self.base + pos + 4
            delta = target - pc
            if delta % 2:
                raise ValueError((label, delta))
            units = delta // 2
            if cond is None:
                if not -1024 <= units <= 1023:
                    raise ValueError(f"branch too far {label}: {units}")
                hw = 0xE000 | (units & 0x7FF)
            else:
                if not -128 <= units <= 127:
                    raise ValueError(f"conditional branch too far {label}: {units}")
                hw = 0xD000 | (self.COND[cond] << 8) | (units & 0xFF)
            struct.pack_into("<H", self.buf, pos, hw)

        for pos, rd, name in self.lit_fixups:
            ins_addr = self.base + pos
            pc_base = (ins_addr + 4) & ~3
            addr = self.base + lit_pos[name]
            delta = addr - pc_base
            if delta < 0 or delta > 1020 or delta % 4:
                raise ValueError(f"literal {name} out of range: {delta}")
            struct.pack_into("<H", self.buf, pos, 0x4800 | (rd << 8) | (delta // 4))
        return bytes(self.buf)


def build_handler() -> bytes:
    t = ThumbBuilder(0x08000000 + HOOK_CODE_AT)
    t.push([4, 5, 6, 7], lr=True)
    t.mov(5, 0)
    t.ldr_imm(6, 5, 0x3C)

    t.mov_imm(4, 0)
    t.label("len_loop")
    t.ldrb_reg(0, 6, 4)
    t.cmp_imm(0, 0)
    t.b("len_done", "eq")
    t.add_imm(4, 1)
    t.b("len_loop")
    t.label("len_done")

    t.mov(0, 5)
    t.indirect_call("cursor_char", CURSOR_CHAR_FUNC)
    t.mov(7, 0)

    t.mov_imm(1, TOKEN_FIRST >> 8)
    t.lsl_imm(1, 1, 8)
    t.add_imm(1, TOKEN_FIRST & 0xFF)
    t.cmp_reg(7, 1)
    t.b("normal", "lo")
    t.add_imm(1, len(JONGSEONG) - 1)
    t.cmp_reg(7, 1)
    t.b("normal", "hi")

    t.cmp_imm(4, 2)
    t.b("done", "lo")
    t.sub_imm(4, 2)
    t.ldrb_reg(0, 6, 4)
    t.lsl_imm(0, 0, 8)
    t.add_imm(4, 1)
    t.ldrb_reg(1, 6, 4)
    t.orr(0, 1)
    t.sub_imm(4, 1)

    t.mov_imm(1, TOKEN_FIRST >> 8)
    t.lsl_imm(1, 1, 8)
    t.add_imm(1, TOKEN_FIRST & 0xFF)
    t.sub_reg(7, 7, 1)
    t.lsl_imm(7, 7, 2)
    t.ldr_literal(1, "ptr_table", 0x08000000 + PTR_TABLE_AT)
    t.ldr_reg(3, 1, 7)

    t.label("search")
    t.ldrh_imm(1, 3, 0)
    t.cmp_imm(1, 0)
    t.b("done", "eq")
    t.cmp_reg(1, 0)
    t.b("found", "eq")
    t.add_imm(3, 4)
    t.b("search")

    t.label("found")
    t.ldrh_imm(7, 3, 2)
    t.lsr_imm(0, 7, 8)
    t.strb_reg(0, 6, 4)
    t.add_imm(4, 1)
    t.strb_reg(7, 6, 4)
    t.add_imm(4, 1)
    t.mov_imm(0, 0)
    t.strb_reg(0, 6, 4)
    t.b("refresh")

    t.label("normal")
    t.mov(0, 5)
    t.add_imm(0, 0x40)
    t.mov_imm(1, 0)
    t.ldrsh_reg(0, 0, 1)
    t.lsl_imm(0, 0, 1)
    t.cmp_reg(4, 0)
    t.b("done", "ge")
    t.lsr_imm(0, 7, 8)
    t.strb_reg(0, 6, 4)
    t.add_imm(4, 1)
    t.strb_reg(7, 6, 4)
    t.add_imm(4, 1)
    t.mov_imm(0, 0)
    t.strb_reg(0, 6, 4)

    t.label("refresh")
    t.mov(0, 5)
    t.add_imm(0, 0x80)
    t.ldr_imm(0, 0, 0)
    t.mov(1, 6)
    t.indirect_call("render_name", RENDER_NAME_FUNC)
    t.mov_imm(0, 2)
    t.indirect_call("play_sfx", PLAY_SFX_FUNC)

    t.label("done")
    t.pop([4, 5, 6, 7], pc=True)
    return t.finish()


def main_font_codes(rom: bytes) -> set[int]:
    if rom[MAIN_MFM:MAIN_MFM + 4] != b"MFM\x00":
        raise SystemExit("main 12x12 MFM missing")
    count, esize = struct.unpack_from("<HH", rom, MAIN_MFM + 8)
    ptr = struct.unpack_from("<I", rom, MAIN_MFM + 16)[0] - 0x08000000
    return {struct.unpack_from(">H", rom, ptr + i * esize)[0] for i in range(count)}


def build_mapping(hangul: dict[str, str]) -> tuple[list[bytes], dict[str, int]]:
    by_char = {ch: int(code, 16) for ch, code in hangul.items()}
    blocks: list[bytes] = []
    counts: dict[str, int] = {}
    for jong_index, token_ch in enumerate(JONGSEONG, 1):
        pairs: list[tuple[int, int]] = []
        for ch, base_code in by_char.items():
            if not ("가" <= ch <= "힣"):
                continue
            offset = ord(ch) - 0xAC00
            if offset % 28 != 0:
                continue
            result = chr(ord(ch) + jong_index)
            result_code = by_char.get(result)
            if result_code is not None:
                pairs.append((base_code, result_code))
        pairs.sort()
        block = b"".join(struct.pack("<HH", a, b) for a, b in pairs)
        block += b"\x00\x00\x00\x00"
        blocks.append(block)
        counts[token_ch] = len(pairs)
    return blocks, counts


def render_token_8(ch: str) -> bytes:
    font8_path = Path(__file__).resolve().parent.parent / font8.FONT
    font = ImageFont.truetype(str(font8_path), font8.PPEM)
    rows = font8.render(ch, font)
    if ch == "ㄳ":
        rows = bytes((0x72, 0x12, 0x12, 0x12, 0x16, 0x00, 0x00, 0x00))
    return rows


def enlarge_token_rows_12(rows8: bytes) -> list[int]:
    """Nearest-neighbour enlarge an 8x8 jamo into the main font's 11x12 ink box."""
    pts = [(x, y) for y, row in enumerate(rows8) for x in range(8) if row & (1 << (7 - x))]
    if not pts:
        raise SystemExit("empty batchim token glyph")
    min_x = min(x for x, _ in pts); max_x = max(x for x, _ in pts)
    min_y = min(y for _, y in pts); max_y = max(y for _, y in pts)
    w = max_x - min_x + 1; h = max_y - min_y + 1
    scale = min(10.0 / w, 10.0 / h)
    tw = max(w + 1, min(10, round(w * scale)))
    th = max(h + 1, min(10, round(h * scale)))
    src = [[bool(rows8[min_y + y] & (1 << (7 - (min_x + x)))) for x in range(w)] for y in range(h)]
    dst = [[False] * 11 for _ in range(12)]
    ox = (11 - tw) // 2
    oy = (11 - th) // 2
    for y in range(th):
        sy = min(h - 1, (y * h) // th)
        for x in range(tw):
            sx = min(w - 1, (x * w) // tw)
            if src[sy][sx]:
                dst[oy + y][ox + x] = True
    out = []
    for y in range(12):
        bits = 0
        for x in range(11):
            if dst[y][x]:
                bits |= 1 << (15 - (x + font12.INK_LEFT))
        out.append(bits)
    return out


def patch_main_focus_font(rom: bytearray, hangul: dict[str, str], used: set[int]) -> dict[str, object]:
    if rom[MAIN_MFM:MAIN_MFM + 4] != b"MFM\x00":
        raise SystemExit("main 12x12 MFM missing")
    count, esize = struct.unpack_from("<HH", rom, MAIN_MFM + 8)
    ptr = struct.unpack_from("<I", rom, MAIN_MFM + 16)[0] - 0x08000000
    if esize != 26:
        raise SystemExit(f"unexpected 12x12 MFM entry size {esize}")
    entries = {struct.unpack_from(">H", rom, ptr + i * esize)[0]: ptr + i * esize for i in range(count)}
    token_codes = list(range(TOKEN_FIRST, TOKEN_LAST + 1))
    missing = [code for code in token_codes if code not in entries]
    if missing:
        raise SystemExit("12x12 token donor slot missing: " + ", ".join(hex(x) for x in missing))
    collisions = [code for code in token_codes if code in used]
    if collisions:
        raise SystemExit("12x12 token donor code is used by source text: " + ", ".join(hex(x) for x in collisions))
    hangul_codes = {int(code, 16) for code in hangul.values()}
    collisions = [code for code in token_codes if code in hangul_codes]
    if collisions:
        raise SystemExit("12x12 token donor code is used by Hangul map: " + ", ".join(hex(x) for x in collisions))

    report = {}
    for i, ch in enumerate(JONGSEONG):
        code = TOKEN_FIRST + i
        entry = entries[code]
        rows = enlarge_token_rows_12(render_token_8(ch))
        if len(rows) != 12 or not any(rows):
            raise SystemExit(f"bad 12x12 batchim render for {ch}")
        before = bytes(rom[entry + 2:entry + 26])
        for y, bits in enumerate(rows):
            struct.pack_into(">H", rom, entry + 2 + y * 2, bits)
        after = bytes(rom[entry + 2:entry + 26])
        report[ch] = {"code": hex(code), "entry": hex(entry), "before": before.hex(), "after": after.hex()}
    return {"token_range": [hex(TOKEN_FIRST), hex(TOKEN_LAST)], "glyphs": report}


def patch_grid_font(rom: bytearray) -> dict[str, object]:
    if rom[MFM:MFM + 4] != b"MFM\x00":
        raise SystemExit("name-entry 8x8 MFM missing")
    count, esize = struct.unpack_from("<HH", rom, MFM + 8)
    ptr = struct.unpack_from("<I", rom, MFM + 16)[0] - 0x08000000
    if esize != 10:
        raise SystemExit(f"unexpected 8x8 MFM entry size {esize}")
    entries = {
        struct.unpack_from(">H", rom, ptr + i * esize)[0]: bytes(rom[ptr + i * esize + 2:ptr + i * esize + 10])
        for i in range(count)
    }
    collisions = [code for code in range(TOKEN_FIRST, TOKEN_LAST + 1) if code in entries]
    if collisions:
        raise SystemExit("batchim token code collision: " + ", ".join(hex(x) for x in collisions))

    token_rows: dict[str, str] = {}
    for i, ch in enumerate(JONGSEONG):
        code = TOKEN_FIRST + i
        rows = render_token_8(ch)
        entries[code] = rows
        token_rows[ch] = rows.hex()

    blob = b"".join(struct.pack(">H", code) + entries[code] for code in sorted(entries))
    font_dst = 0x00FDC000
    if font_dst + len(blob) > PAGE2_TABLE:
        raise SystemExit("expanded name-entry font overlaps page-2 table")
    old_end = ptr + count * esize
    clear_end = max(old_end, font_dst + len(blob))
    rom[font_dst:clear_end] = bytes(clear_end - font_dst)
    rom[font_dst:font_dst + len(blob)] = blob
    struct.pack_into("<H", rom, MFM + 8, len(entries))
    struct.pack_into("<I", rom, MFM + 16, 0x08000000 + font_dst)

    if len(TOKEN_CELLS) != len(JONGSEONG):
        raise SystemExit("TOKEN_CELLS/JONGSEONG size mismatch")
    for token_index, cell_index in enumerate(TOKEN_CELLS):
        off = PAGE2_TABLE + cell_index * 4
        from_code, _old_to = struct.unpack_from("<HH", rom, off)
        if from_code == 0:
            raise SystemExit(f"page-2 table ended early at cell {cell_index}")
        struct.pack_into("<H", rom, off + 2, TOKEN_FIRST + token_index)
    if rom[PAGE2_TABLE + PAGE2_COUNT * 4:PAGE2_TABLE + PAGE2_COUNT * 4 + 4] != bytes(4):
        raise SystemExit("page-2 table terminator missing")

    return {
        "font_count_before": count,
        "font_count_after": len(entries),
        "font_span": [hex(font_dst), hex(font_dst + len(blob))],
        "tokens": {ch: hex(TOKEN_FIRST + i) for i, ch in enumerate(JONGSEONG)},
        "token_rows": token_rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", type=Path, required=True)
    ap.add_argument("--map", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--used", type=Path, required=True,
                    help="JSON array of CP932 codes actually used by source text")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    src = args.rom.read_bytes()
    rom = bytearray(src)
    hangul = json.loads(args.map.read_text(encoding="utf-8"))["map"]
    used_payload = json.loads(args.used.read_text(encoding="utf-8"))
    if not isinstance(used_payload, list) or not all(isinstance(code, int) for code in used_payload):
        raise SystemExit("--used must be a JSON array of integer CP932 codes")
    used = set(used_payload)

    if rom[INPUT_FUNC:INPUT_FUNC + len(EXPECTED_INPUT_PREFIX)] != EXPECTED_INPUT_PREFIX:
        raise SystemExit("unexpected name input function prefix at 0x500C4: " + rom[INPUT_FUNC:INPUT_FUNC + 8].hex())
    if any(rom[HOOK_CODE_AT:TAIL_END]):
        first = next(i for i, b in enumerate(rom[HOOK_CODE_AT:TAIL_END]) if b)
        raise SystemExit(f"batchim tail no longer free at 0x{HOOK_CODE_AT + first:06X}")

    focus_report = patch_main_focus_font(rom, hangul, used)
    grid_report = patch_grid_font(rom)
    blocks, counts = build_mapping(hangul)
    main_codes = main_font_codes(rom)

    result_codes = {
        struct.unpack_from("<H", block, i + 2)[0]
        for block in blocks
        for i in range(0, len(block) - 4, 4)
    }
    missing_main = sorted(code for code in result_codes if code not in main_codes)
    if missing_main:
        raise SystemExit("composed batchim result missing from main font: " + ", ".join(hex(x) for x in missing_main[:16]))

    handler = build_handler()
    if len(handler) > PTR_TABLE_AT - HOOK_CODE_AT:
        raise SystemExit(f"handler too large: {len(handler)}")
    rom[HOOK_CODE_AT:HOOK_CODE_AT + len(handler)] = handler

    cursor = MAP_DATA_AT
    ptrs = []
    for block in blocks:
        ptrs.append(0x08000000 + cursor)
        rom[cursor:cursor + len(block)] = block
        cursor += len(block)
    if cursor > TAIL_END:
        raise SystemExit("batchim mapping table exceeds ROM")
    ptr_blob = b"".join(struct.pack("<I", p) for p in ptrs)
    if PTR_TABLE_AT + len(ptr_blob) > MAP_DATA_AT:
        raise SystemExit("batchim pointer table overlaps map data")
    rom[PTR_TABLE_AT:PTR_TABLE_AT + len(ptr_blob)] = ptr_blob

    trampoline = struct.pack("<HHI", 0x4B00, 0x4718, 0x08000000 + HOOK_CODE_AT + 1)
    rom[INPUT_FUNC:INPUT_FUNC + len(trampoline)] = trampoline

    args.out.write_bytes(rom)
    report = {
        "purpose": "two-step Hangul batchim composition in name entry",
        "input": str(args.rom),
        "output": str(args.out),
        "sha256": hashlib.sha256(rom).hexdigest().upper(),
        "input_handler": {
            "trampoline": [hex(INPUT_FUNC_ROM_ADDR), hex(INPUT_FUNC_ROM_ADDR + len(trampoline))],
            "hook": [hex(0x08000000 + HOOK_CODE_AT), hex(0x08000000 + HOOK_CODE_AT + len(handler))],
            "handler_bytes": len(handler),
        },
        "page2_batchim_cells": len(JONGSEONG),
        "batchim_cell_indices": TOKEN_CELLS,
        "batchim_order": JONGSEONG,
        "mapping_counts": counts,
        "mapping_pairs_total": sum(counts.values()),
        "mapping_span": [hex(MAP_DATA_AT), hex(cursor)],
        "pointer_table": hex(PTR_TABLE_AT),
        "grid_font": grid_report,
        "focused_preview_font": focus_report,
        "normal_name_capacity": "unchanged: 16 bytes / 8 CP932-style 2-byte chars",
        "backspace_policy": "unchanged: deletes the whole composed syllable",
    }
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out} sha256={report['sha256']} batchim_pairs={report['mapping_pairs_total']} focus_slots={len(report['focused_preview_font']['glyphs'])}")


if __name__ == "__main__":
    main()
