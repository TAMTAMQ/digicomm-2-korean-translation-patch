"""Put Hangul into the 8x8 font the name-entry grid draws with.

The grid does not use the 11x12 dialogue font. It has its own small font,
stored in an 'MFM' container:

    MFM header  +0  'MFM\0'
                +4  u8 format, u8 width, u8 height
                +8  u16 glyph count, u16 entry size
                +16 u32 pointer to the glyph array
    entry       +0  u16 big-endian CP932 code
                +2  8 rows, 1 byte each, MSB = leftmost pixel

That is why patching the grid table alone left the cells unreadable while the
name field, which does use the dialogue font, showed Hangul correctly.

The array is relocated to free space at the end of the ROM and extended rather
than overwritten, so every kana and kanji the font already carried survives.
Codes must stay sorted: the lookup is a binary search.
"""
import argparse
import json
import re
import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

MFM = 0x00DD6808
GRID = 0x0015BEE0
GRID_COUNT = 96
INK_LEFT = 1          # 188 of the 198 stock glyphs start at column 1
FREE = 0x00FDC000     # inside the zero fill that runs to the end of the ROM

FONT = "work/digicomm_nyo/assets/fonts/galmuri/font/Galmuri7Bitmap-Regular-2.40.4.ttf"
PPEM = 10             # the only size this cut's embedded strike opens at


def render(ch, font):
    """One syllable as 8 rows, MSB-first, in the stock glyph box."""
    # Tiny HUD units need hand-tuned pixels.  The generic Galmuri strike is
    # acceptable for the name-entry grid, but at 8x8 its '명' medial/final
    # strokes collapse into an unreadable blob (the shop HUD shows 90명/30명).
    # Keep these two frequent unit glyphs deterministic and legible.
    hand_tuned = {
        "명": bytes((0x72, 0x57, 0x52, 0x77, 0x38, 0x44, 0x38, 0x00)),
        "회": bytes((0x72, 0x52, 0x72, 0x00, 0x3C, 0x24, 0x3C, 0x00)),
    }
    if ch in hand_tuned:
        return hand_tuned[ch]
    im = Image.new("L", (32, 32), 0)
    ImageDraw.Draw(im).text((8, 8), ch, font=font, fill=255)
    a = [[im.getpixel((x, y)) > 128 for x in range(32)] for y in range(32)]
    ys = [y for y in range(32) if any(a[y])]
    xs = [x for x in range(32) if any(a[y][x] for y in range(32))]
    if not ys:
        return bytes(8)
    h, w = ys[-1] - ys[0] + 1, xs[-1] - xs[0] + 1
    if h > 7 or w > 7:
        raise SystemExit("%s does not fit the 7x7 box (%dx%d)" % (ch, w, h))
    rows = []
    for r in range(8):
        b = 0
        sy = ys[0] + r
        if r < h:
            for c in range(w):
                if a[sy][xs[0] + c]:
                    b |= 1 << (7 - (c + INK_LEFT))
        rows.append(b)
    return bytes(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", type=Path, required=True)
    ap.add_argument("--map", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--extra-text", type=Path,
        help="JSON containing fixed raw-UI replacements whose Hangul also "
             "needs glyphs in this 8x8 MFM font",
    )
    a = ap.parse_args()

    rom = bytearray(a.rom.read_bytes())
    if rom[MFM:MFM + 4] != b"MFM\x00":
        raise SystemExit("no MFM font header at 0x%06X" % MFM)
    fmt, w, h = rom[MFM + 4], rom[MFM + 5], rom[MFM + 6]
    count, esize = struct.unpack_from("<HH", rom, MFM + 8)
    ptr = struct.unpack_from("<I", rom, MFM + 16)[0] - 0x08000000
    if (w, h, esize) != (8, 8, 10):
        raise SystemExit("unexpected font geometry %dx%d entry=%d" % (w, h, esize))

    entries = {}
    for i in range(count):
        code = struct.unpack_from(">H", rom, ptr + i * esize)[0]
        entries[code] = bytes(rom[ptr + i * esize + 2:ptr + i * esize + 10])
    stock = len(entries)

    # what the grid actually asks for, read back out of the patched table
    hangul = json.loads(a.map.read_text(encoding="utf-8"))["map"]
    by_code = {int(v, 16): k for k, v in hangul.items()}
    wanted = []
    for i in range(GRID_COUNT):
        code = struct.unpack_from("<I", rom, GRID + i * 8)[0]
        if code in by_code:
            wanted.append((code, by_code[code]))
    if a.extra_text:
        policy = json.loads(a.extra_text.read_text(encoding="utf-8"))
        if isinstance(policy, list):
            extra_strings = policy
        else:
            raw_policy = policy.get("raw_ui_text", policy)
            extra_strings = list(raw_policy.get("fixed_width_replacements", []))
            # Some minigame character names are ordinary translated MCM strings
            # but are rendered through this same 8x8 font.  Name-entry only adds
            # syllables visible on its input grid, so composed batchim syllables
            # such as 홋/갓 (and any other name-only syllables) would otherwise
            # have no 8x8 cell and disappear at runtime.  Keep those reviewed
            # strings in the build invariant instead of hard-coding glyphs here.
            extra_strings.extend(raw_policy.get("extra_8x8_glyph_strings", []))
            translation_map = raw_policy.get("translation_map")
            if translation_map:
                translated_path = a.extra_text.parent / translation_map
                translated_rows = json.loads(translated_path.read_text(encoding="utf-8"))
                if not isinstance(translated_rows, dict):
                    raise SystemExit(
                        f"raw UI translation map must be an object: {translated_path}"
                    )
                extra_strings.extend(translated_rows.values())
        extra_chars = {
            ch
            for item in extra_strings
            for ch in item.get("translation", "")
            if re.fullmatch(r"[가-힣]", ch)
        }
        for ch in sorted(extra_chars):
            if ch not in hangul:
                raise SystemExit(f"raw UI syllable not in Hangul map: {ch}")
            wanted.append((int(hangul[ch], 16), ch))
    # The name grid and raw UI can ask for the same syllable.  Preserve one
    # sorted MFM entry per stand-in CP932 code.
    wanted = sorted(set(wanted))
    if not wanted:
        raise SystemExit("the grid table still holds kana -- run gba_nameentry_hangul.py first")

    font = ImageFont.truetype(FONT, PPEM)
    replaced = 0
    for code, ch in wanted:
        if code in entries:
            replaced += 1
        entries[code] = render(ch, font)

    blob = b"".join(struct.pack(">H", c) + entries[c] for c in sorted(entries))
    if FREE + len(blob) > 0x00FDE000:
        raise SystemExit(
            "expanded 8x8 font overlaps the page-2 table at 0xFDE000 "
            f"({len(blob)} bytes)"
        )
    if FREE + len(blob) > len(rom):
        raise SystemExit("no room at 0x%06X" % FREE)
    if any(rom[FREE:FREE + len(blob)]):
        raise SystemExit("0x%06X is not free" % FREE)
    rom[FREE:FREE + len(blob)] = blob
    struct.pack_into("<H", rom, MFM + 8, len(entries))
    struct.pack_into("<I", rom, MFM + 16, FREE + 0x08000000)

    a.out.write_bytes(rom)
    print("wrote %s" % a.out)
    print("  stock glyphs kept : %d" % (stock - replaced))
    print("  hangul added      : %d  (%d replaced a stock code)"
          % (len(wanted), replaced))
    print("  font now          : %d glyphs at 0x%06X (%d bytes)"
          % (len(entries), FREE, len(blob)))
    print("  syllables         : %s" % "".join(c for _, c in wanted))


if __name__ == "__main__":
    main()
