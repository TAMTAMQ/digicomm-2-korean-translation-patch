from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

from mbm import read_mbm

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "build" / "digicomm_nyo_kr_message_minigame_stage.gba"
GOOD = ROOT / "build" / "digicomm_nyo_kr_minigame_stylefix.gba"
META = ROOT / "build" / "digicomm_nyo_kr_minigame_stylefix.json"
OUT = ROOT / "build" / "digicomm_nyo_kr_minigame_recovery_final.gba"
REPORT = ROOT / "build" / "digicomm_nyo_kr_minigame_recovery_final.json"

# Runtime-confirmed exact-tree title image.  This is the 167-tile
# raw -> RLE -> Huffman8 layout, not the later regressed 165-tile rebuild.
B36_START = 0xB36C60
B36_END = 0xB38384
B36_PTRS = [0xB36C98, 0xB374E4, 0xB37F14]

# Runtime-confirmed minigame HUD atlas (앞으로/엔/명/일/만 etc.).
HUD_START = 0xD7CBC4
HUD_END = 0xD81758

# The actual mini-game runtime font entries discovered from state1.
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
DATE_FONT = {"일": 0xDBA722, "차": 0xDBDD2A}

# These are the 24x16 pre-rendered label groups.  Preserve the Japanese
# per-label placement instead of stretching every Korean label across 24 px.
STATUS_GROUPS = {
    "호객": ([0xD8178C, 0xD817CE, 0xD81810], (1, 4), (15, 4)),
    "판매": ([0xD81852, 0xD81894, 0xD818D6], (8, 4), (16, 4)),
    "정돈": ([0xD819DE, 0xD81A20, 0xD81A62], (0, 5), (8, 5)),
    "회원": ([0xD81AA4, 0xD81AE6, 0xD81B28], (0, 4), (14, 4)),
}
DATE_TILES = {"일": 0xE3D228, "차": 0xE3D248}


def unpack_1bpp_8(data: bytes) -> list[list[bool]]:
    if len(data) != 8:
        raise ValueError(len(data))
    return [[bool(data[y] & (1 << x)) for x in range(8)] for y in range(8)]


def unpack_1bpp_12(data: bytes) -> list[list[bool]]:
    if len(data) != 24:
        raise ValueError(len(data))
    rows = []
    for y in range(12):
        value = int.from_bytes(data[y * 2:y * 2 + 2], "big")
        rows.append([bool(value & (1 << (15 - x))) for x in range(12)])
    return rows


def pack_1bpp_12(rows: list[list[bool]]) -> bytes:
    out = bytearray()
    for row in rows:
        value = 0
        for x, on in enumerate(row):
            if on:
                value |= 1 << (15 - x)
        out += value.to_bytes(2, "big")
    return bytes(out)


def shift_12x12_right(data: bytes, amount: int = 2) -> bytes:
    rows = unpack_1bpp_12(data)
    out = [[False] * 12 for _ in range(12)]
    for y in range(12):
        for x in range(12):
            if rows[y][x] and x + amount < 12:
                out[y][x + amount] = True
    return pack_1bpp_12(out)


def pack_4bpp(rows: list[list[int]]) -> bytes:
    flat = [v for row in rows for v in row]
    if len(flat) % 2:
        raise ValueError("odd 4bpp pixel count")
    return bytes(flat[i] | (flat[i + 1] << 4) for i in range(0, len(flat), 2))


def split_status(rows: list[list[int]]) -> list[bytes]:
    return [pack_4bpp([row[x:x + 8] for row in rows]) for x in (0, 8, 16)]


def bbox(rows: list[list[int | bool]]):
    pts = [(x, y) for y, row in enumerate(rows) for x, v in enumerate(row) if v]
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def put_if_empty(rows: list[list[int]], x: int, y: int, value: int) -> None:
    if 0 <= y < len(rows) and 0 <= x < len(rows[0]) and rows[y][x] == 0:
        rows[y][x] = value


def render_beveled_status(mask_a: list[list[bool]], mask_b: list[list[bool]],
                           pos_a: tuple[int, int], pos_b: tuple[int, int]) -> list[list[int]]:
    """Render two 8x8 Korean glyphs with the original six-color status palette.

    Palette roles recovered from the Japanese state1 art:
      5 = dark outer edge
      1/2 = lower/right lavender shadow
      F/D/9 = bright face -> mid face -> lower bevel
    """
    h, w = 16, 24
    mask = [[False] * w for _ in range(h)]
    glyph_pixels: list[tuple[int, int, int]] = []
    for gi, (src, (x0, y0)) in enumerate(((mask_a, pos_a), (mask_b, pos_b))):
        for sy in range(8):
            for sx in range(8):
                if src[sy][sx]:
                    x, y = x0 + sx, y0 + sy
                    if 0 <= x < w and 0 <= y < h:
                        mask[y][x] = True
                        glyph_pixels.append((x, y, gi))

    out = [[0] * w for _ in range(h)]

    # Two-step lower/right bevel shadow.  It deliberately extends the same
    # amount as the Japanese art, making the total height roughly 10 px.
    for x, y, _ in glyph_pixels:
        put_if_empty(out, x + 1, y + 2, 0x1)
        put_if_empty(out, x + 2, y + 1, 0x2)

    # 8-neighbour dark edge.
    for x, y, _ in glyph_pixels:
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h and not mask[ny][nx]:
                    put_if_empty(out, nx, ny, 0x5)

    # Face bevel.  The source art changes face tone vertically; doing the
    # same is also important for 1px bitmap strokes, where a neighbour-based
    # "interior" test would otherwise never use the middle D tone.
    positions = (pos_a, pos_b)
    for x, y, gi in glyph_pixels:
        rel_y = y - positions[gi][1]
        if rel_y <= 1:
            value = 0xF
        elif rel_y <= 4:
            value = 0xD
        else:
            value = 0x9
        out[y][x] = value

    return out


def date_masks() -> dict[str, list[list[bool]]]:
    patterns = {
        "일": [
            ".###..#.",
            "#...#.#.",
            "#...#.#.",
            ".###..#.",
            "......#.",
            ".#####..",
            "....#...",
            ".#####..",
        ],
        "차": [
            "..#..#..",
            ".###.#..",
            "..#..#..",
            "..#..##.",
            ".#.#.#..",
            "#...##..",
            ".....#..",
            ".....#..",
        ],
    }
    return {k: [[c == "#" for c in row] for row in rows] for k, rows in patterns.items()}


def render_date_tile(mask: list[list[bool]]) -> bytes:
    # 8x8 cannot afford a full extra outline ring everywhere.  Keep the source
    # tile footprint and use 3 as the edge, C/B as the face bevel, and 2 as the
    # lower-right shadow.
    out = [[0] * 8 for _ in range(8)]
    for y in range(8):
        for x in range(8):
            if not mask[y][x]:
                continue
            if x + 1 < 8 and y + 1 < 8 and not mask[y + 1][x + 1]:
                put_if_empty(out, x + 1, y + 1, 0x2)
    for y in range(8):
        for x in range(8):
            if not mask[y][x]:
                continue
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < 8 and 0 <= ny < 8 and not mask[ny][nx]:
                    put_if_empty(out, nx, ny, 0x3)
    for y in range(8):
        for x in range(8):
            if not mask[y][x]:
                continue
            down = y + 1 < 8 and mask[y + 1][x]
            right = x + 1 < 8 and mask[y][x + 1]
            out[y][x] = 0xB if (not down or not right) else 0xC
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
    base = bytearray(BASE.read_bytes())
    base_before = bytes(base)
    good = GOOD.read_bytes()
    meta = json.loads(META.read_text(encoding="utf-8"))
    if len(base) != 16 * 1024 * 1024 or len(good) != len(base):
        raise RuntimeError("unexpected ROM size")

    # 1) Restore the exact runtime-proven title image and HUD atlas.  These two
    # ranges were accidentally reverted when later patches switched base ROMs.
    base[B36_START:B36_END] = good[B36_START:B36_END]
    base[HUD_START:HUD_END] = good[HUD_START:HUD_END]

    # 2) Actual runtime fonts: restore thin Korean glyphs.  Never run the old
    # stylefix3 thickening pass.  Move 12x12 일/차 two pixels right so their
    # bbox starts at x=3 just like the original 日/目 glyphs.
    status_masks: dict[str, list[list[bool]]] = {}
    for change in meta["status_font"]["changes"]:
        target = change["target"]
        off = int(change["entry"], 16)
        base[off:off + 10] = good[off:off + 10]
        status_masks[target] = unpack_1bpp_8(bytes(base[off + 2:off + 10]))

    date_font_bboxes = {}
    for change in meta["date_font"]["changes"]:
        target = change["target"]
        off = int(change["entry"], 16)
        base[off:off + 2] = good[off:off + 2]
        shifted = shift_12x12_right(good[off + 2:off + 26], 2)
        if target == "차":
            # Original 目 occupies x=3..10.  The Korean 차 source has one
            # one-pixel right protrusion after shifting; trim only that spare
            # column so the runtime bbox matches the source placement exactly.
            rows12 = unpack_1bpp_12(shifted)
            for row in rows12:
                row[11] = False
            shifted = pack_1bpp_12(rows12)
        base[off + 2:off + 26] = shifted
        date_font_bboxes[target] = bbox(unpack_1bpp_12(shifted))

    # 3) Pre-rendered label path: rebuild from the same thin 8x8 Korean glyphs,
    # but keep the Japanese per-label spacing and the full six-tone palette.
    status_report = {}
    for label, (offsets, pos_a, pos_b) in STATUS_GROUPS.items():
        a, b = label[0], label[1]
        rows = render_beveled_status(status_masks[a], status_masks[b], pos_a, pos_b)
        if label == "정돈":
            # Source 整と is the only short label: its exact vertical footprint
            # ends at y=12, so remove the generic second shadow row at y=13.
            rows[13] = [0] * 24
        payloads = split_status(rows)
        for off, payload in zip(offsets, payloads):
            base[off + 2:off + 66] = payload
        status_report[label] = {
            "bbox": bbox(rows),
            "palette": sorted({v for row in rows for v in row if v}),
            "tile_nonzero": [sum(1 for v in p for nib in (v & 15, v >> 4) if nib) for p in payloads],
        }

    # 4) 8x8 date-object fallback path with the original 4-level date palette.
    date_report = {}
    for label, mask in date_masks().items():
        off = DATE_TILES[label]
        payload = render_date_tile(mask)
        base[off:off + 32] = payload
        # Decode only for report/bbox.
        pixels = []
        for value in payload:
            pixels.extend((value & 15, value >> 4))
        rows = [pixels[y * 8:(y + 1) * 8] for y in range(8)]
        date_report[label] = {
            "bbox": bbox(rows),
            "palette": sorted({v for row in rows for v in row if v}),
        }

    # 5) Keep all 43 fixed Black Gamers labels Korean.
    for item in meta["black_gamers_stage_labels"]:
        off = int(item["offset"], 16)
        size = int(item["slot_bytes"])
        base[off:off + size] = good[off:off + size]
        expected = bytes.fromhex(item["encoded"])
        if bytes(base[off:off + size]) != expected:
            raise RuntimeError(f"black gamers label mismatch at 0x{off:X}")

    # Hard invariants: reject output immediately if the two regressions return.
    b36 = read_mbm(base, B36_START)
    if b36["tiles"] != 167:
        raise RuntimeError(f"B36 tile regression: {b36['tiles']} != 167")
    if b36["ptrs"] != B36_PTRS:
        raise RuntimeError(f"B36 section pointer regression: {b36['ptrs']}")
    if [sec[0] for sec in b36["sections"]] != [0x30, 0x30, 0x30]:
        raise RuntimeError("B36 nested RLE streams missing")

    if bytes(base[HUD_START:HUD_END]) != good[HUD_START:HUD_END]:
        raise RuntimeError("HUD atlas mismatch")

    # Six source colors must remain in every rendered status group; this catches
    # any accidental return of the two-color stylefix3 algorithm.
    for label, item in status_report.items():
        if item["palette"] != [1, 2, 5, 9, 13, 15]:
            raise RuntimeError(f"{label} palette regression: {item['palette']}")

    # Nothing outside the explicitly-owned graphics/font/fixed-label ranges is
    # allowed to change.  This preserves the latest messages.json base exactly.
    allowed = [(B36_START, B36_END), (HUD_START, HUD_END)]
    allowed += [(off, off + 10) for off in STATUS_FONT.values()]
    allowed += [(off, off + 26) for off in DATE_FONT.values()]
    for offsets, _, _ in STATUS_GROUPS.values():
        allowed += [(off + 2, off + 66) for off in offsets]
    allowed += [(off, off + 32) for off in DATE_TILES.values()]
    allowed += [
        (int(item["offset"], 16), int(item["offset"], 16) + int(item["slot_bytes"]))
        for item in meta["black_gamers_stage_labels"]
    ]
    allowed = merge_ranges(allowed)
    changed_bytes = 0
    ri = 0
    unexpected: list[int] = []
    for i, (before, after) in enumerate(zip(base_before, base)):
        if before == after:
            continue
        changed_bytes += 1
        while ri < len(allowed) and i >= allowed[ri][1]:
            ri += 1
        if ri >= len(allowed) or i < allowed[ri][0]:
            unexpected.append(i)
            if len(unexpected) >= 16:
                break
    if unexpected:
        raise RuntimeError("unexpected changed offsets: " + ", ".join(hex(x) for x in unexpected))

    OUT.write_bytes(base)
    report = {
        "purpose": "recover runtime-proven title/HUD and rebuild minigame day/status labels in source style",
        "base": str(BASE),
        "donor": str(GOOD),
        "output": str(OUT),
        "sha256": hashlib.sha256(base).hexdigest().upper(),
        "b36": {
            "tiles": b36["tiles"],
            "ptrs": [hex(x) for x in b36["ptrs"]],
            "nested_section_headers": [hex(sec[0]) for sec in b36["sections"]],
            "range_sha256": hashlib.sha256(base[B36_START:B36_END]).hexdigest().upper(),
        },
        "hud_range_sha256": hashlib.sha256(base[HUD_START:HUD_END]).hexdigest().upper(),
        "date_font_bboxes": {k: list(v) if v else None for k, v in date_font_bboxes.items()},
        "status_groups": status_report,
        "date_tiles": date_report,
        "black_gamers_labels": len(meta["black_gamers_stage_labels"]),
        "changed_bytes_vs_latest_message_base": changed_bytes,
        "unexpected_changed_bytes": 0,
        "allowed_ranges": [[hex(a), hex(b)] for a, b in allowed],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
