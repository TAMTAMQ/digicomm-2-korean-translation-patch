from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from mbm import read_mbm

ROOT = Path(__file__).resolve().parents[1]
# Latest consolidated build (messages + translated cards + exit button).
# Keep this as the default base so rerunning the minigame UI patch cannot
# silently regress later integrated work by starting from an older stage ROM.
BASE = ROOT / "build" / "digicomm_nyo_kr_cards_messages_exit.gba"
GOOD = ROOT / "build" / "digicomm_nyo_kr_minigame_state1_objfix.gba"
META = ROOT / "build" / "digicomm_nyo_kr_minigame_state1_objfix.json"
ART = ROOT / "build" / "digicomm_nyo_kr_minigame_photoshopfix3.gba"
OUT = ROOT / "build" / "digicomm_nyo_kr_cards_messages_exit_final.gba"
REPORT = ROOT / "build" / "digicomm_nyo_kr_cards_messages_exit_final.json"

# 0xB36C60 MBM physical ownership ends exactly where the next MBM starts.
# The previous recovery scripts stopped at 0xB38384, which omitted the tail
# of the third stream plus the 64-colour palette at 0xB38874..0xB388F3.
# That explains why the decoded structure looked valid while the title/main
# screen still rendered as rainbow garbage in mGBA.
B36_START = 0xB36C60
B36_END = 0xB388F4
B36_PTRS = [0xB36C98, 0xB374E4, 0xB37F14]

HUD_START = 0xD7CBC4
HUD_END = 0xD81758

STATUS_FONT = {
    "호": 0xFDC370,
    "객": 0xFDC29E,
    "정": 0xFDCDD4,
    "돈": 0xFDC230,
    "판": 0xFDD144,
    "매": 0xFDD09A,
    "회": 0xFDC80C,
    "원": 0xFDC776,
}
# The game also has clean 10x10 Hangul strikes in the general UI font.  These
# are much clearer for the 24x16 overhead label than expanding the tiny 8x8
# status font.  Each entry is code(2) + ten big-endian 16-bit scanlines.
STATUS_FONT10 = {
    "호": 0xD97D24,
    "객": 0xD86DAA,
    "정": 0xD934DE,
    "돈": 0xD8A888,
    "판": 0xD97014,
    "매": 0xD8CF34,
    "회": 0xD97EF2,
    "원": 0xD92902,
}
DATE_FONT = {"일": 0xDBA722, "차": 0xDBDD2A}
# Extra Hangul needed by card-effect labels.  These are clean 10x10 strikes in
# the same general UI font used as the source for the approved status style.
EXTRA_FONT10 = {
    "가": 0xD86BB0,
    "짜": 0xD93F9C,
    "드": 0xD8AF3C,
}
# The 2-line card-effect labels need the same crisp small-pixel skeleton used
# by the one-line overhead status text.  The old renderer shrank 10x10 glyphs
# with area-OR resampling, which filled internal holes/strokes (notably 호/객)
# and made both rows look like solid blobs.  Use the game's already-patched
# 8x8 status font instead; 가/짜 are the matching entries in that same table.
EXTRA_STATUS_FONT8 = {
    "가": 0xFDC6F4,
    "짜": 0xFDD248,
}
# Explicit row selection is a pixel-art decision, not generic image scaling.
# Every compact glyph keeps five authored rows from its 6/7-row 8x8 strike so
# the two complete navy contours fit in 16px with a truly blank separator row.
COMPACT_STATUS_ROWS = {
    "가": [0, 1, 3, 5, 6],
    "짜": [0, 1, 3, 5, 6],
    "호": [0, 1, 2, 3, 5],
    "객": [0, 1, 3, 5, 6],
    "회": [0, 2, 4, 5, 6],
    "원": [0, 1, 3, 4, 6],
    "판": [0, 1, 3, 5, 6],
    "매": [0, 1, 3, 5, 6],
}
# Card-effect labels occupy full 32x16 / four-cell MFM groups.  0x54..0x5F
# duplicate the three fake-job labels and must be patched too or Japanese will
# reappear for another card/path.
EXTRA_STATUS_GROUPS = {
    "가짜 호객": [(0x40, 0x43), (0x54, 0x57)],
    "가짜 회원": [(0x44, 0x47), (0x58, 0x5B)],
    "가짜 판매": [(0x48, 0x4B), (0x5C, 0x5F)],
    "가드": [(0x4C, 0x4F)],
}
MFM_ENTRY0 = 0xD8178C  # code 0x30
MFM_ENTRY_SIZE = 0x42


def mfm_entry_offset(code: int) -> int:
    if not 0x30 <= code <= 0x5F:
        raise ValueError(hex(code))
    return MFM_ENTRY0 + (code - 0x30) * MFM_ENTRY_SIZE

# The runtime status OBJ is a 32x16 sprite made from FOUR consecutive MFM
# cells.  The first three cells hold the label and the fourth is the numeric
# cell.  Earlier patches accidentally shifted 판매 and 정돈 by one cell.
#
# Keep the user-approved photoshopfix3 24x16 shapes byte-for-byte, but move
# their three payload cells to the actual runtime groups and blank the fourth
# cell before the number renderer draws into it.
STATUS_LAYOUT = {
    "호객": {
        "dst": [0xD8178C, 0xD817CE, 0xD81810],  # 0x30..0x32
        "art": [0xD8178C, 0xD817CE, 0xD81810],
        "numeric": 0xD81852,                    # 0x33
    },
    "판매": {
        "dst": [0xD81894, 0xD818D6, 0xD81918],  # 0x34..0x36
        "art": [0xD81852, 0xD81894, 0xD818D6],  # fix3 drew it at 0x33..0x35
        "numeric": 0xD8195A,                    # 0x37
    },
    "정돈": {
        "dst": [0xD8199C, 0xD819DE, 0xD81A20],  # 0x38..0x3A
        "art": [0xD819DE, 0xD81A20, 0xD81A62],  # fix3 drew it at 0x39..0x3B
        "numeric": 0xD81A62,                    # 0x3B
    },
    "회원": {
        "dst": [0xD81AA4, 0xD81AE6, 0xD81B28],  # 0x3C..0x3E
        "art": [0xD81AA4, 0xD81AE6, 0xD81B28],
        "numeric": 0xD81B6A,                    # 0x3F
    },
}
DATE_TILES = {"일": 0xE3D228, "차": 0xE3D248}
ORIGINAL_STATUS_BBOX = {
    # Measured directly from the Japanese ROM's three MFM label cells.
    # Keep these exact extents so the separate numeric cell stays at the
    # original engine position for every state.
    "호객": (0, 3, 23, 12),   # よび
    "판매": (0, 3, 23, 12),   # 販売
    "정돈": (0, 4, 16, 12),   # 整と
    "회원": (0, 3, 22, 12),   # 会員
}
RAW_STATUS = {
    # Preserve the source format structure exactly: Hangul(4 bytes) + '+' +
    # '%d' + NUL.  The Japanese game does not visibly show the '+', so do not
    # reinterpret or delete it; let the original renderer handle it.
    "호객": (0x143370, bytes.fromhex("99bc88b62b2564")),
    "정돈": (0x143378, bytes.fromhex("94f48c722b2564")),
    "판매": (0x143380, bytes.fromhex("98e08ebd2b2564")),
    "회원": (0x143388, bytes.fromhex("99d194692b2564")),
}


def unpack_1bpp_8(data: bytes) -> list[list[bool]]:
    """Decode the font strike for a left-to-right 4bpp OBJ redraw.

    The runtime 1bpp font reader consumes the byte in the opposite horizontal
    bit order from the simple 4bpp row writer below.  Treating bit 0 as x=0
    therefore mirrored every Hangul syllable in photoshopfix1; the detached
    right-hand jamo then looked like an extra Japanese/hanja glyph.  Keep the
    runtime font bytes untouched and reverse only while converting them into
    the 24x16 pre-rendered status OBJ.
    """
    if len(data) != 8:
        raise ValueError(len(data))
    return [[bool(data[y] & (1 << (7 - x))) for x in range(8)] for y in range(8)]


def unpack_1bpp_10(data: bytes) -> list[list[bool]]:
    """Decode the game's 10x10 big-endian font strike, MSB at screen-left."""
    if len(data) != 20:
        raise ValueError(len(data))
    rows: list[list[bool]] = []
    for y in range(10):
        value = int.from_bytes(data[y * 2:y * 2 + 2], "big")
        rows.append([bool(value & (1 << (15 - x))) for x in range(10)])
    return rows


def unpack_4bpp(data: bytes, width: int, height: int) -> list[list[int]]:
    px: list[int] = []
    for value in data:
        px.extend((value & 0x0F, value >> 4))
    if len(px) != width * height:
        raise ValueError((len(px), width, height))
    return [px[y * width:(y + 1) * width] for y in range(height)]


def pack_4bpp(rows: list[list[int]]) -> bytes:
    flat = [value for row in rows for value in row]
    if len(flat) % 2:
        raise ValueError("odd 4bpp pixel count")
    return bytes(flat[i] | (flat[i + 1] << 4) for i in range(0, len(flat), 2))


def split_status(rows: list[list[int]]) -> list[bytes]:
    return [pack_4bpp([row[x0:x0 + 8] for row in rows]) for x0 in (0, 8, 16)]


def split_effect(rows: list[list[int]]) -> list[bytes]:
    if len(rows) != 16 or any(len(row) != 32 for row in rows):
        raise ValueError("effect label must be 32x16")
    return [pack_4bpp([row[x0:x0 + 8] for row in rows]) for x0 in (0, 8, 16, 24)]


def put_if_empty(rows: list[list[int]], x: int, y: int, value: int) -> None:
    if 0 <= y < len(rows) and 0 <= x < len(rows[0]) and rows[y][x] == 0:
        rows[y][x] = value


def bbox(rows: list[list[int | bool]]) -> tuple[int, int, int, int] | None:
    pts = [(x, y) for y, row in enumerate(rows) for x, value in enumerate(row) if value]
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def scale_cropped_mask(src: list[list[bool]], dst_w: int, dst_h: int) -> list[list[bool]]:
    """Area-resample one trimmed binary glyph while preserving thin strokes."""
    src_box = bbox(src)
    if src_box is None:
        raise RuntimeError("empty 10x10 status glyph")
    sx0, sy0, sx1, sy1 = src_box
    sw, sh = sx1 - sx0 + 1, sy1 - sy0 + 1
    out = [[False] * dst_w for _ in range(dst_h)]
    for dy in range(dst_h):
        ya = sy0 + (dy * sh) // dst_h
        yb = sy0 + (((dy + 1) * sh + dst_h - 1) // dst_h) - 1
        yb = min(yb, sy1)
        for dx in range(dst_w):
            xa = sx0 + (dx * sw) // dst_w
            xb = sx0 + (((dx + 1) * sw + dst_w - 1) // dst_w) - 1
            xb = min(xb, sx1)
            out[dy][dx] = any(
                src[sy][sx]
                for sy in range(ya, yb + 1)
                for sx in range(xa, xb + 1)
            )
    return out


def trim_mask(src: list[list[bool]]) -> list[list[bool]]:
    src_box = bbox(src)
    if src_box is None:
        raise RuntimeError("empty status glyph")
    x0, y0, x1, y1 = src_box
    return [row[x0:x1 + 1] for row in src[y0:y1 + 1]]


def compact_status_mask(ch: str, src: list[list[bool]]) -> list[list[bool]]:
    """Build a compact two-line glyph without any resampling.

    Two outlined rows must fit inside 16 px with a fully blank separator row.
    Instead of area-OR shrinking, keep five explicitly authored scanlines from
    the game's 8x8 status glyph and crop only empty side columns.  This retains
    the same holes/stroke decisions as the approved one-line status art.
    """
    if len(src) != 8 or any(len(row) != 8 for row in src):
        raise RuntimeError(f"{ch}: expected 8x8 status mask")
    selected = COMPACT_STATUS_ROWS[ch]
    if len(selected) != 5 or len(set(selected)) != 5:
        raise RuntimeError(f"{ch}: invalid compact row selection {selected}")
    reduced = [src[y][:] for y in selected]
    return trim_mask(reduced)


def render_effect_reference_style(
    label: str,
    status_masks8: dict[str, list[list[bool]]],
    full_masks10: dict[str, list[list[bool]]],
) -> list[list[int]]:
    """Render card-effect labels into one 32x16 MFM group.

    Fake-job labels reuse the actual 8x8 overhead-status glyph skeleton.  Their
    two rows are compacted by explicit scanline selection only: no area-OR,
    nearest-neighbour, or generic 10x10 -> 6px scaling is allowed.  Each row
    gets the same 1px navy contour and F->D->9 bright face bevel as the approved
    one-line status labels.  Row 7 is guaranteed completely blank.

    가드 is single-line, so it keeps the clean 10x10 font geometry at native
    size (cropped only for empty margins) and uses the same contour/gradient.
    """
    rows = [[0] * 32 for _ in range(16)]
    core = [[False] * 32 for _ in range(16)]
    core_meta: list[tuple[int, int, int, int]] = []  # x,y,glyph_y,glyph_h

    def place_pair(pair: tuple[str, str], glyphs: list[list[list[bool]]], core_y: int) -> None:
        gap = 2
        widths = [len(glyph[0]) for glyph in glyphs]
        total_w = sum(widths) + gap
        x = (32 - total_w) // 2
        for ch, glyph in zip(pair, glyphs):
            gh = len(glyph)
            gw = len(glyph[0])
            for gy in range(gh):
                for gx in range(gw):
                    if not glyph[gy][gx]:
                        continue
                    px, py = x + gx, core_y + gy
                    if not (0 <= px < 32 and 0 <= py < 16):
                        raise RuntimeError((label, ch, px, py))
                    core[py][px] = True
                    core_meta.append((px, py, core_y, gh))
            x += gw + gap

    if label.startswith("가짜 "):
        top = ("가", "짜")
        bottom = tuple(label.split(" ", 1)[1])
        if len(bottom) != 2:
            raise RuntimeError(label)
        # Core rows 1..5 and 9..13.  Their 1px contours occupy 0..6 and 8..14,
        # leaving y=7 blank between the two complete outlined rows.
        top_glyphs = [compact_status_mask(ch, status_masks8[ch]) for ch in top]
        bottom_glyphs = [compact_status_mask(ch, status_masks8[ch]) for ch in bottom]
        place_pair(top, top_glyphs, 1)
        place_pair(bottom, bottom_glyphs, 9)
    elif label == "가드":
        pair = ("가", "드")
        glyphs = [trim_mask(full_masks10[ch]) for ch in pair]
        max_h = max(len(glyph) for glyph in glyphs)
        place_pair(pair, glyphs, (16 - max_h) // 2)
    else:
        raise RuntimeError(f"unknown effect label {label}")

    # Connected one-pixel navy contour; no detached shadow pixels.
    for x, y, _, _ in core_meta:
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if 0 <= nx < 32 and 0 <= ny < 16 and not core[ny][nx]:
                    rows[ny][nx] = 0x5

    # Photoshopfix3-like bright face gradient.
    for x, y, y0, gh in core_meta:
        rel = y - y0
        if rel * 3 < gh:
            rows[y][x] = 0xF
        elif rel * 3 < gh * 2:
            rows[y][x] = 0xD
        else:
            rows[y][x] = 0x9

    if label.startswith("가짜 ") and any(rows[7]):
        raise RuntimeError(f"{label}: two-line separator row is not blank")
    return rows


def render_status_reference_style(
    label: str, mask_a: list[list[bool]], mask_b: list[list[bool]]
) -> list[list[int]]:
    """Render Hangul inside the *exact* Japanese status-label extent.

    Do not give all four states a common Korean box.  The original game uses
    different visual extents for よび/販売/整と/会員; the separate number cell
    was authored around those extents.  Keeping the Japanese bbox exactly is
    what prevents the label from drifting into the number or neighboring OBJ.

    Bank 13: 5 navy outline, 9 lower face, D pale-blue face, F white face.
    """
    h, w = 16, 24
    ex0, ey0, ex1, ey1 = ORIGINAL_STATUS_BBOX[label]
    env_w, env_h = ex1 - ex0 + 1, ey1 - ey0 + 1
    if env_w < 5 or env_h < 5:
        raise RuntimeError((label, ORIGINAL_STATUS_BBOX[label]))

    # One-pixel contour owns the envelope edge.  Split the remaining inner
    # width into two equal glyph cells with the same total Japanese extent.
    inner_w = env_w - 2
    inner_h = env_h - 2
    gap = 2 if env_w == 24 else 1
    glyph_w = (inner_w - gap) // 2
    if glyph_w <= 0 or glyph_w * 2 + gap != inner_w:
        raise RuntimeError((label, env_w, inner_w, gap, glyph_w))
    glyph_h = inner_h

    scaled = [
        scale_cropped_mask(mask_a, glyph_w, glyph_h),
        scale_cropped_mask(mask_b, glyph_w, glyph_h),
    ]
    starts = [
        (ex0 + 1, ey0 + 1),
        (ex0 + 1 + glyph_w + gap, ey0 + 1),
    ]
    core = [[False] * w for _ in range(h)]
    core_pixels: list[tuple[int, int, int]] = []
    for gi, (glyph, (gx0, gy0)) in enumerate(zip(scaled, starts)):
        for gy, row in enumerate(glyph):
            for gx, value in enumerate(row):
                if not value:
                    continue
                x, y = gx0 + gx, gy0 + gy
                core[y][x] = True
                core_pixels.append((x, y, gi))

    out = [[0] * w for _ in range(h)]
    for x, y, _ in core_pixels:
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if ex0 <= nx <= ex1 and ey0 <= ny <= ey1 and not core[ny][nx]:
                    out[ny][nx] = 0x5

    for x, y, _ in core_pixels:
        rel = y - (ey0 + 1)
        if rel * 3 < glyph_h:
            out[y][x] = 0xF
        elif rel * 3 < glyph_h * 2:
            out[y][x] = 0xD
        else:
            out[y][x] = 0x9

    actual = bbox(out)
    expected = ORIGINAL_STATUS_BBOX[label]
    if actual != expected:
        raise RuntimeError(f"{label} bbox mismatch: actual={actual}, expected={expected}")
    return out


def recolor_date_reference_style(tile: bytes) -> bytes:
    """Keep state1_objfix Hangul geometry but restore a bright source-like bevel.

    Palette bank 7:
      3 = black outline, C = white, 2 = pale blue, B = gray lower bevel.
    state1_objfix already has a clean Korean footprint; only its face colours are
    changed by vertical position.  This avoids the previous C->B darkening that
    made the day label harder to read.
    """
    rows = unpack_4bpp(tile, 8, 8)
    out = [row[:] for row in rows]
    for y in range(8):
        for x in range(8):
            if rows[y][x] != 0xC:
                continue
            if y <= 3:
                out[y][x] = 0xC
            elif y <= 5:
                out[y][x] = 0x2
            else:
                out[y][x] = 0xB
    return pack_4bpp(out)


def merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(a, b) for a, b in merged]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", type=Path, default=BASE)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--report", type=Path, default=REPORT)
    a = ap.parse_args()

    base = bytearray(a.rom.read_bytes())
    before = bytes(base)
    good = GOOD.read_bytes()
    art = ART.read_bytes()
    meta = json.loads(META.read_text(encoding="utf-8"))
    if len(base) != 16 * 1024 * 1024 or len(good) != len(base):
        raise RuntimeError("unexpected ROM size")

    # 1) Restore the COMPLETE physical MBM ownership, including its palette.
    # This is the important main-screen fix missing from all later attempts.
    base[B36_START:B36_END] = good[B36_START:B36_END]
    base[HUD_START:HUD_END] = good[HUD_START:HUD_END]

    # 2) Do not redraw the Hangul skeleton again.  Keep the runtime fonts from
    # state1_objfix, but use the previously approved photoshopfix3 status art
    # byte-for-byte for the actual 24x16 overhead labels.
    effect_masks8: dict[str, list[list[bool]]] = {}
    for ch, off in STATUS_FONT.items():
        base[off:off + 10] = good[off:off + 10]
        effect_masks8[ch] = unpack_1bpp_8(bytes(base[off + 2:off + 10]))
    for ch, off in EXTRA_STATUS_FONT8.items():
        base[off:off + 10] = good[off:off + 10]
        effect_masks8[ch] = unpack_1bpp_8(bytes(base[off + 2:off + 10]))

    # Keep the known-good 10x10 source entries available for the single-row
    # guard label only.  Fake-job labels never pass through this renderer.
    for off in STATUS_FONT10.values():
        base[off:off + 22] = good[off:off + 22]
    effect_masks10: dict[str, list[list[bool]]] = {}
    for ch, off in EXTRA_FONT10.items():
        base[off:off + 22] = good[off:off + 22]
        effect_masks10[ch] = unpack_1bpp_10(bytes(base[off + 2:off + 22]))
    for off in DATE_FONT.values():
        base[off:off + 26] = good[off:off + 26]

    status_report = {}
    for label, layout in STATUS_LAYOUT.items():
        dst_offsets = layout["dst"]
        art_offsets = layout["art"]
        # Preserve each destination MFM code/header and transplant only the
        # approved 64-byte pixel payload from photoshopfix3.
        for dst, src in zip(dst_offsets, art_offsets):
            base[dst:dst + 2] = good[dst:dst + 2]
            base[dst + 2:dst + 66] = art[src + 2:src + 66]
        # The fourth cell is owned by the dynamic number renderer.  Japanese
        # よび/整と have 7/2 tail pixels here; those become garbage under the
        # Korean number, so start it completely blank.
        num_off = layout["numeric"]
        base[num_off:num_off + 2] = good[num_off:num_off + 2]
        base[num_off + 2:num_off + 66] = bytes(64)

        tiles = [unpack_4bpp(base[off + 2:off + 66], 8, 16) for off in dst_offsets]
        rows = [tiles[0][y] + tiles[1][y] + tiles[2][y] for y in range(16)]
        status_report[label] = {
            "bbox": list(bbox(rows) or ()),
            "palette": sorted({value for row in rows for value in row if value}),
            "byte_exact_photoshopfix3_shape": all(
                bytes(base[dst + 2:dst + 66]) == bytes(art[src + 2:src + 66])
                for dst, src in zip(dst_offsets, art_offsets)
            ),
            "numeric_cell_blank": not any(base[num_off + 2:num_off + 66]),
            "dst_codes": [hex(0x30 + (off - 0xD8178C) // 0x42) for off in dst_offsets],
        }

    # 4) Card-effect labels: patch every unique group plus duplicate MFM groups.
    # Unlike the four base job states, these use the full 32x16 area and have no
    # dynamic number cell.
    extra_status_report = {}
    for label, ranges in EXTRA_STATUS_GROUPS.items():
        rows = render_effect_reference_style(label, effect_masks8, effect_masks10)
        payloads = split_effect(rows)
        rendered_groups = []
        for start_code, end_code in ranges:
            if end_code - start_code != 3:
                raise RuntimeError((label, start_code, end_code))
            for col, code in enumerate(range(start_code, end_code + 1)):
                off = mfm_entry_offset(code)
                # Preserve original MFM code/header and replace only 64-byte art.
                base[off:off + 2] = good[off:off + 2]
                base[off + 2:off + 66] = payloads[col]
            rendered_groups.append([hex(start_code), hex(end_code)])
        top_bbox = bbox(rows[:7]) if label.startswith("가짜 ") else None
        bottom_part = rows[8:15] if label.startswith("가짜 ") else []
        bottom_bbox_raw = bbox(bottom_part) if bottom_part else None
        bottom_bbox = (
            [bottom_bbox_raw[0], bottom_bbox_raw[1] + 8, bottom_bbox_raw[2], bottom_bbox_raw[3] + 8]
            if bottom_bbox_raw else None
        )
        group_payloads = []
        for start_code, end_code in ranges:
            group_payloads.append(
                b"".join(
                    bytes(base[mfm_entry_offset(code) + 2:mfm_entry_offset(code) + 66])
                    for code in range(start_code, end_code + 1)
                )
            )
        duplicate_groups_identical = len(group_payloads) <= 1 or all(
            payload == group_payloads[0] for payload in group_payloads[1:]
        )
        extra_status_report[label] = {
            "groups": rendered_groups,
            "bbox": list(bbox(rows) or ()),
            "palette": sorted({value for row in rows for value in row if value}),
            "nonzero": sum(1 for row in rows for value in row if value),
            "render_method": (
                "native 8x8 status glyph scanline selection; no area-OR scaling"
                if label.startswith("가짜 ") else
                "native 10x10 cropped glyphs; no scaling"
            ),
            "top_line_bbox": list(top_bbox) if top_bbox else None,
            "bottom_line_bbox": bottom_bbox,
            "separator_row_7_blank": not any(rows[7]) if label.startswith("가짜 ") else None,
            "duplicate_groups_identical": duplicate_groups_identical,
        }

    # Keep the reserved 0x50..0x53 group completely blank as in the source.
    reserved_blank = []
    for code in range(0x50, 0x54):
        off = mfm_entry_offset(code)
        base[off:off + 2] = good[off:off + 2]
        base[off + 2:off + 66] = bytes(64)
        reserved_blank.append(not any(base[off + 2:off + 66]))

    # 5) Day-label fallback tiles: clean state1_objfix shape, bright white upper
    # face, pale-blue middle and gray lower bevel, black source-style outline.
    date_report = {}
    for label, off in DATE_TILES.items():
        src = good[off:off + 32]
        payload = recolor_date_reference_style(src)
        base[off:off + 32] = payload
        rows = unpack_4bpp(payload, 8, 8)
        date_report[label] = {
            "bbox": list(bbox(rows) or ()),
            "palette": sorted({value for row in rows for value in row if value}),
        }

    # 5) Restore the source format delimiter exactly.  Keep '+' between the
    # status text and %d; it is present in all four Japanese source strings and
    # is not visibly rendered in the original game.
    raw_status_report = {}
    for label, (off, payload) in RAW_STATUS.items():
        if len(payload) != 7:
            raise RuntimeError((label, len(payload)))
        base[off:off + 8] = payload + b"\x00"
        raw_status_report[label] = {
            "offset": hex(off),
            "bytes": bytes(base[off:off + 8]).hex(),
            "plus_preserved": base[off + 4] == 0x2B,
        }

    # Keep all stage-local Black Gamers labels from the same known-good ROM.
    for item in meta["black_gamers_stage_labels"]:
        off = int(item["offset"], 16)
        size = int(item["slot_bytes"])
        base[off:off + size] = good[off:off + size]
        if bytes(base[off:off + size]) != bytes.fromhex(item["encoded"]):
            raise RuntimeError(f"black gamers mismatch at 0x{off:X}")

    # Main-image invariants.  The palette pointer itself proves why the former
    # 0xB38384 copy boundary was incomplete.
    b36 = read_mbm(base, B36_START)
    if b36["tiles"] != 167:
        raise RuntimeError(f"B36 tile regression: {b36['tiles']}")
    if b36["ptrs"] != B36_PTRS:
        raise RuntimeError(f"B36 pointer regression: {b36['ptrs']}")
    palette_end = b36["pal_ptr"] + len(b36["palette"]) * 2
    if palette_end != B36_END:
        raise RuntimeError(f"B36 palette boundary mismatch: 0x{palette_end:X}")
    if bytes(base[B36_START:B36_END]) != good[B36_START:B36_END]:
        raise RuntimeError("complete B36 physical span does not match state1_objfix")

    # Status shape is a hard byte-level contract: the exact photoshopfix3
    # 24x16 shape, moved to the correct runtime three-cell group.  The numeric
    # fourth cell must be empty before the engine writes the number.
    for label, item in status_report.items():
        if not item["byte_exact_photoshopfix3_shape"]:
            raise RuntimeError(f"status art changed from approved photoshopfix3: {label}")
        if not item["numeric_cell_blank"]:
            raise RuntimeError(f"status numeric cell not blank: {label}")
    if not all(item["plus_preserved"] for item in raw_status_report.values()):
        raise RuntimeError("status '+' delimiter regression")
    for label, item in extra_status_report.items():
        if item["palette"] != [5, 9, 13, 15]:
            raise RuntimeError(f"extra status palette regression {label}: {item['palette']}")
        if item["nonzero"] <= 0:
            raise RuntimeError(f"empty extra status label {label}")
        if not item["duplicate_groups_identical"]:
            raise RuntimeError(f"duplicate effect groups diverged: {label}")
        if label.startswith("가짜 "):
            if not item["separator_row_7_blank"]:
                raise RuntimeError(f"two-line separator row is not blank: {label}")
            if item["top_line_bbox"] is None or item["bottom_line_bbox"] is None:
                raise RuntimeError(f"missing two-line bbox: {label}")
            if item["top_line_bbox"][3] >= 7 or item["bottom_line_bbox"][1] <= 7:
                raise RuntimeError(f"two-line labels overlap separator row: {label}")
    if not all(reserved_blank):
        raise RuntimeError("reserved 0x50..0x53 group is not blank")
    for label, item in date_report.items():
        if not {2, 3, 11, 12}.issubset(set(item["palette"])):
            raise RuntimeError(f"date gradient regression {label}: {item['palette']}")

    # Latest integrated messages/cards/exit-button content remains owned by BASE.
    # Only explicitly owned graphics,
    # fonts, status format strings and fixed labels may differ from it.
    allowed = [(B36_START, B36_END), (HUD_START, HUD_END)]
    allowed += [(off, off + 10) for off in STATUS_FONT.values()]
    allowed += [(off, off + 10) for off in EXTRA_STATUS_FONT8.values()]
    allowed += [(off, off + 22) for off in STATUS_FONT10.values()]
    allowed += [(off, off + 22) for off in EXTRA_FONT10.values()]
    allowed += [(off, off + 26) for off in DATE_FONT.values()]
    for layout in STATUS_LAYOUT.values():
        allowed += [(off, off + 66) for off in layout["dst"]]
        allowed.append((layout["numeric"], layout["numeric"] + 66))
    # Entire card-effect MFM range, including duplicate groups and reserved blank.
    allowed += [(mfm_entry_offset(code), mfm_entry_offset(code) + 66) for code in range(0x40, 0x60)]
    allowed += [(off, off + 32) for off in DATE_TILES.values()]
    allowed += [(off, off + 8) for off, _ in RAW_STATUS.values()]
    allowed += [
        (int(item["offset"], 16), int(item["offset"], 16) + int(item["slot_bytes"]))
        for item in meta["black_gamers_stage_labels"]
    ]
    allowed = merge_ranges(allowed)

    changed_bytes = 0
    unexpected: list[int] = []
    ri = 0
    for i, (old, new) in enumerate(zip(before, base)):
        if old == new:
            continue
        changed_bytes += 1
        while ri < len(allowed) and i >= allowed[ri][1]:
            ri += 1
        if ri >= len(allowed) or i < allowed[ri][0]:
            unexpected.append(i)
            if len(unexpected) >= 16:
                break
    if unexpected:
        raise RuntimeError("unexpected offsets: " + ", ".join(hex(x) for x in unexpected))

    a.out.write_bytes(base)
    report = {
        "purpose": "fix base 4-cell status mapping and render all card-effect labels 0x40..0x5F in the approved one-line status pixel style",
        "base": str(a.rom),
        "known_good": str(GOOD),
        "output": str(a.out),
        "sha256": hashlib.sha256(base).hexdigest().upper(),
        "b36": {
            "physical_span": [hex(B36_START), hex(B36_END)],
            "physical_bytes": B36_END - B36_START,
            "tiles": b36["tiles"],
            "ptrs": [hex(x) for x in b36["ptrs"]],
            "palette_ptr": hex(b36["pal_ptr"]),
            "palette_end": hex(palette_end),
            "range_sha256": hashlib.sha256(base[B36_START:B36_END]).hexdigest().upper(),
        },
        "hud_range_sha256": hashlib.sha256(base[HUD_START:HUD_END]).hexdigest().upper(),
        "status_groups": status_report,
        "extra_status_groups": extra_status_report,
        "reserved_0x50_0x53_blank": all(reserved_blank),
        "date_tiles": date_report,
        "raw_status_slots": raw_status_report,
        "raw_status_policy": "source-compatible Hangul+%d format with literal 0x2B '+' preserved",
        "black_gamers_labels": len(meta["black_gamers_stage_labels"]),
        "changed_bytes_vs_latest_integrated_base": changed_bytes,
        "unexpected_changed_bytes": 0,
    }
    if a.report:
        a.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
