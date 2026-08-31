"""Korean-localise Digicomm Nyo's raw 32x16 OBJ button sprites.

The sprites are not runtime text and are not inside MCM compression.  They are
literal 4bpp tile runs in ROM.  Only their flat interior is cleared; the
original bevel, corners, palette indices, dimensions, and surrounding ROM are
preserved byte-for-byte.
"""
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


BUTTONS = {
    0xE385A4: ("決める", "결정"),
    0xE386A8: ("もどる", "뒤로"),
    0xE387AC: ("切替", "전환"),
    0xE388B0: ("カナ", "전환"),
    0xE389B4: ("いいえ", "아니요"),
    0xE393DC: ("はい", "예"),
    0xF5FB80: ("もどる", "취소"),
    0xF5FE8C: ("回転", "삭제"),
    0xF5FF90: ("近づく", "정렬"),
}
VIEWER_BUTTON_OFFSETS = {0xF5FB80, 0xF5FE8C, 0xF5FF90}
VIEWER_PALETTE_OFFSET = 0xF599AC
VIEWER_PALETTE_BANK = 9
VIEWER_PALETTE_EXPECTED = (
    0x77C0, 0x2001, 0x1084, 0x150F,
    0x4210, 0x3A37, 0x0000, 0x5AD6,
    0x5EF7, 0x7B38, 0x6B5A, 0x6F7B,
    0x5F5E, 0x67BF, 0x5BFF, 0x7FFF,
)
VIEWER_RUNTIME_INDEX_MAP = {
    0: 0,
    3: 10,
    5: 12,
    12: 5,
    13: 13,
    15: 15,
}
VIEWER_PALETTE_PATCH = {
    5: 0x4210,
    10: 0x6B5A,
    12: 0x5F5E,
    13: 0x5EF7,
}
VIEWER_ICON_SPRITES = {
    0xF5F8F4: 128,
    0xF5F978: 256,
    0xF5FA7C: 256,
}
DEFAULT_FONT = (
    Path(__file__).resolve().parent.parent / "work" / "digicomm_nyo" /
    "assets" / "fonts" / "galmuri" / "font" /
    "Galmuri7Bitmap-Regular-2.40.4.ttf"
)


def decode(blob: bytes) -> list[list[int]]:
    pixels = [[0] * 32 for _ in range(16)]
    for tile in range(8):
        tx, ty = tile % 4, tile // 4
        data = blob[tile * 32:(tile + 1) * 32]
        for y in range(8):
            for x in range(8):
                value = data[y * 4 + x // 2]
                pixels[ty * 8 + y][tx * 8 + x] = (value >> (4 * (x & 1))) & 15
    return pixels


def encode(pixels: list[list[int]]) -> bytes:
    out = bytearray(256)
    for tile in range(8):
        tx, ty = tile % 4, tile // 4
        for y in range(8):
            for x in range(8):
                value = pixels[ty * 8 + y][tx * 8 + x]
                at = tile * 32 + y * 4 + x // 2
                out[at] |= value << (4 * (x & 1))
    return bytes(out)


def glyph_mask(text: str, font: ImageFont.FreeTypeFont) -> Image.Image:
    canvas = Image.new("1", (128, 32), 0)
    draw = ImageDraw.Draw(canvas)
    draw.text((4, 4), text, font=font, fill=1, spacing=0)
    box = canvas.getbbox()
    if box is None:
        raise ValueError(f"empty label: {text}")
    return canvas.crop(box)


def dilate(mask: Image.Image, radius: int) -> set[tuple[int, int]]:
    points = {(x, y) for y in range(mask.height) for x in range(mask.width) if mask.getpixel((x, y))}
    return {(x + dx, y + dy) for x, y in points
            for dy in range(-radius, radius + 1)
            for dx in range(-radius, radius + 1)
            if abs(dx) + abs(dy) <= radius}


def redraw(blob: bytes, label: str, font: ImageFont.FreeTypeFont) -> bytes:
    pixels = decode(blob)
    for y in range(2, 14):
        for x in range(3, 29):
            pixels[y][x] = 4

    mask = glyph_mask(label, font)
    outer_radius = 2
    outer = dilate(mask, outer_radius)
    inner = dilate(mask, 1)
    ink = dilate(mask, 0)
    width = max(x for x, _ in outer) - min(x for x, _ in outer) + 1
    height = max(y for _, y in outer) - min(y for _, y in outer) + 1
    if width > 26:
        outer_radius = 1
        outer = dilate(mask, outer_radius)
        width = max(x for x, _ in outer) - min(x for x, _ in outer) + 1
        height = max(y for _, y in outer) - min(y for _, y in outer) + 1
    if width > 26 or height > 12:
        raise SystemExit(f"button label {label!r} does not fit: {width}x{height}")
    min_x, min_y = min(x for x, _ in outer), min(y for _, y in outer)
    ox = 3 + (26 - width) // 2 - min_x
    oy = 2 + (12 - height) // 2 - min_y
    layers = ((outer, 6), (inner, 7), (ink, 15)) if outer_radius == 2 else ((inner, 7), (ink, 15))
    for layer, colour in layers:
        for x, y in layer:
            dx, dy = ox + x, oy + y
            if 3 <= dx < 29 and 2 <= dy < 14:
                pixels[dy][dx] = colour
    return encode(pixels)


def redraw_viewer_button(blob: bytes, label: str, font: ImageFont.FreeTypeFont) -> bytes:
    pixels = decode(blob)
    for y in range(3, 14):
        for x in range(4, 28):
            pixels[y][x] = 3
    mask = glyph_mask(label, font)
    if mask.width > 24 or mask.height > 11:
        raise SystemExit(f"viewer button label {label!r} does not fit: {mask.width}x{mask.height}")
    ox = 4 + (24 - mask.width) // 2
    oy = 3 + (11 - mask.height) // 2
    for y in range(mask.height):
        for x in range(mask.width):
            if mask.getpixel((x, y)):
                pixels[oy + y][ox + x] = 15
    return encode(pixels)


def remap_viewer_runtime(blob: bytes) -> bytes:
    pixels = decode(blob)
    for y in range(16):
        for x in range(32):
            value = pixels[y][x]
            if value not in VIEWER_RUNTIME_INDEX_MAP:
                raise SystemExit(f"unexpected viewer palette index {value} at {x},{y}")
            pixels[y][x] = VIEWER_RUNTIME_INDEX_MAP[value]
    return encode(pixels)


def bgr555_rgb(value: int) -> tuple[int, int, int]:
    return ((value & 31) * 255 // 31, ((value >> 5) & 31) * 255 // 31, ((value >> 10) & 31) * 255 // 31)


def palette_from_words(words: tuple[int, ...] | list[int]) -> list[tuple[int, int, int]]:
    return [bgr555_rgb(value) for value in words]


def palette_indices(blob: bytes) -> set[int]:
    return {value for byte in blob for value in (byte & 15, byte >> 4)}


BUTTON_PALETTE = [
    (0, 0, 0), (0, 0, 0), (255, 255, 255), (213, 213, 213),
    (255, 238, 205), (246, 213, 189), (189, 139, 115), (123, 65, 41),
    (90, 123, 246), (115, 123, 246), (32, 32, 32), (82, 82, 82),
    (131, 131, 131), (189, 189, 189), (222, 222, 222), (255, 255, 255),
]


def indexed_image(blob: bytes, rgb_palette: list[tuple[int, int, int]] | None = None) -> Image.Image:
    pixels = decode(blob)
    image = Image.new("P", (32, 16), 0)
    source_palette = BUTTON_PALETTE if rgb_palette is None else rgb_palette
    palette = [component for rgb in source_palette for component in rgb]
    image.putpalette(palette + [0] * (768 - len(palette)))
    image.putdata([pixels[y][x] for y in range(16) for x in range(32)])
    image.info["transparency"] = 0
    return image


def render_indexed(blob: bytes, scale: int = 5, rgb_palette: list[tuple[int, int, int]] | None = None) -> Image.Image:
    return indexed_image(blob, rgb_palette).convert("RGBA").resize((32 * scale, 16 * scale), Image.Resampling.NEAREST)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--font", type=Path, default=DEFAULT_FONT)
    parser.add_argument("--preview", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--original-dir", type=Path)
    parser.add_argument("--translated-dir", type=Path)
    args = parser.parse_args()
    original = args.rom.read_bytes()
    rom = bytearray(original)
    font = ImageFont.truetype(str(args.font), 10)

    viewer_words = tuple(struct.unpack_from("<16H", original, VIEWER_PALETTE_OFFSET))
    if viewer_words != VIEWER_PALETTE_EXPECTED:
        raise SystemExit("viewer OBJ palette changed unexpectedly at " + f"0x{VIEWER_PALETTE_OFFSET:06X}: " + " ".join(f"{value:04X}" for value in viewer_words))
    for icon_offset, icon_size in VIEWER_ICON_SPRITES.items():
        overlap = palette_indices(original[icon_offset:icon_offset + icon_size]) & set(VIEWER_PALETTE_PATCH)
        if overlap:
            raise SystemExit(f"viewer icon 0x{icon_offset:06X} uses recoloured indices: {sorted(overlap)}")
    patched_viewer_words = list(viewer_words)
    for index, value in VIEWER_PALETTE_PATCH.items():
        patched_viewer_words[index] = value
        struct.pack_into("<H", rom, VIEWER_PALETTE_OFFSET + index * 2, value)
    patched_viewer_palette = palette_from_words(patched_viewer_words)
    for logical_index, runtime_index in VIEWER_RUNTIME_INDEX_MAP.items():
        if logical_index == 0:
            continue
        expected_rgb = BUTTON_PALETTE[logical_index]
        actual_rgb = patched_viewer_palette[runtime_index]
        if actual_rgb != expected_rgb:
            raise SystemExit(f"viewer runtime colour mismatch: logical {logical_index} -> runtime {runtime_index}: {actual_rgb} != {expected_rgb}")

    records = []
    previews = []
    for offset, (source, translation) in BUTTONS.items():
        before = original[offset:offset + 256]
        is_viewer = offset in VIEWER_BUTTON_OFFSETS
        if is_viewer:
            logical_after = redraw_viewer_button(before, translation, font)
            after = remap_viewer_runtime(logical_after)
        else:
            logical_after = redraw(before, translation, font)
            after = logical_after
        rom[offset:offset + 256] = after
        previews.append((source, translation, render_indexed(logical_after, rgb_palette=BUTTON_PALETTE)))
        archive_name = f"button_{offset:06X}_32x16.png"
        if args.original_dir:
            args.original_dir.mkdir(parents=True, exist_ok=True)
            indexed_image(before, BUTTON_PALETTE).save(args.original_dir / archive_name, transparency=0)
        if args.translated_dir:
            args.translated_dir.mkdir(parents=True, exist_ok=True)
            indexed_image(logical_after, BUTTON_PALETTE).save(args.translated_dir / archive_name, transparency=0)
        record = {"offset": f"0x{offset:06X}", "source": source, "translation": translation,
                  "changed_bytes": sum(a != b for a, b in zip(before, after))}
        if is_viewer:
            record.update({
                "runtime_palette_bank": VIEWER_PALETTE_BANK,
                "runtime_palette_rom_offset": f"0x{VIEWER_PALETTE_OFFSET:06X}",
                "runtime_index_map": {str(k): v for k, v in VIEWER_RUNTIME_INDEX_MAP.items()},
                "palette_patch_indices": sorted(VIEWER_PALETTE_PATCH),
            })
        records.append(record)

    allowed = set()
    for offset in BUTTONS:
        allowed.update(range(offset, offset + 256))
    for index in VIEWER_PALETTE_PATCH:
        palette_at = VIEWER_PALETTE_OFFSET + index * 2
        allowed.update(range(palette_at, palette_at + 2))
    outside = [i for i, (a, b) in enumerate(zip(original, rom)) if a != b and i not in allowed]
    if outside:
        raise SystemExit(f"changed bytes outside button ranges: {outside[:8]}")
    args.out.write_bytes(rom)

    if args.preview:
        sheet = Image.new("RGBA", (360, len(previews) * 104), (48, 48, 48, 255))
        draw = ImageDraw.Draw(sheet)
        for row, (source, translation, image) in enumerate(previews):
            y = row * 104
            draw.text((4, y + 3), f"{source} -> {translation}", fill="white")
            sheet.alpha_composite(image, (4, y + 21))
        args.preview.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(args.preview)
    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"patched {len(BUTTONS)} raw button sprites -> {args.out}")
    print(f"changed bytes: {sum(a != b for a, b in zip(original, rom))}")
    print(f"viewer OBJ palette bank {VIEWER_PALETTE_BANK}: ROM 0x{VIEWER_PALETTE_OFFSET:06X}, source-gray design, patched indices " + ",".join(str(index) for index in sorted(VIEWER_PALETTE_PATCH)))
    if args.original_dir:
        print(f"original PNGs    -> {args.original_dir}")
    if args.translated_dir:
        print(f"translated PNGs -> {args.translated_dir}")


if __name__ == "__main__":
    main()
