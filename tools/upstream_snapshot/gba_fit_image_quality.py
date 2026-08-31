#!/usr/bin/env python3
"""Shrink a translated MBM image until it fits its original ROM allocation.

The game walks an image's compressed sections as one physical chain, so a
translated picture that no longer fits where it already lives cannot simply be
moved (see STATUS.md, 2026-08-28).  When better compression is not enough, the
remaining lever is the picture itself.

Rather than redrawing, this merges near-identical 8x8 tiles inside the region
the translation actually changed.  Hangul strokes carry more fine detail than
the Japanese they replace, and most of the extra bytes are near-duplicate
tiles along the glyph edges.  Merging them costs a little edge fidelity and
nothing else: cells outside the changed region keep their original tiles, and
palette banks never mix.

Tolerance is the number of pixels two tiles may differ by and still be merged.
It is searched upward, so the output is the *least* degraded image that fits.
"""
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

from PIL import Image, ImageChops

import gba_image_extract as image_extract
import gba_image_reinsert as reinsert

ROM_BASE = 0x08000000


def cell_indexes(pixels, palette, ox: int, oy: int) -> tuple[int, ...]:
    return tuple(reinsert.rgb_index(pixels[ox + x, oy + y], palette)
                 for y in range(8) for x in range(8))


def distance(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    return sum(1 for x, y in zip(a, b) if x != y)


def build_cells(image: Image.Image, entry: dict, palette, tilemap):
    """Every visible cell as (palette bank, 64 palette indexes)."""
    pixels = image.load()
    cells_x = entry["width"] // 8
    eight_bpp = entry["bytes_per_tile"] == 64
    out = []
    for cell in range((entry["width"] // 8) * (entry["height"] // 8)):
        bank = (tilemap[cell] >> 12) & 15 if tilemap is not None else 0
        local = palette if eight_bpp else palette[bank * 16:bank * 16 + 16]
        ox, oy = (cell % cells_x) * 8, (cell // cells_x) * 8
        out.append((bank, cell_indexes(pixels, local, ox, oy)))
    return out


def render(cells, entry: dict, palette) -> Image.Image:
    eight_bpp = entry["bytes_per_tile"] == 64
    cells_x = entry["width"] // 8
    image = Image.new("RGB", (entry["width"], entry["height"]))
    pixels = image.load()
    for cell, (bank, indexes) in enumerate(cells):
        local = palette if eight_bpp else palette[bank * 16:bank * 16 + 16]
        ox, oy = (cell % cells_x) * 8, (cell // cells_x) * 8
        for i, value in enumerate(indexes):
            pixels[ox + i % 8, oy + i // 8] = local[value]
    return image


def merge(cells, changed: set[int], tolerance: int):
    """Collapse changed-region tiles that differ by at most `tolerance` pixels.

    Tiles that also occur outside the changed region are anchors: a changed
    cell may snap onto one, but the untouched artwork is never rewritten.
    """
    frozen = {cells[cell] for cell in range(len(cells)) if cell not in changed}
    counts: dict[tuple, int] = {}
    for cell in changed:
        counts[cells[cell]] = counts.get(cells[cell], 0) + 1
    order = sorted(counts, key=lambda key: (-counts[key], key))
    centres: list[tuple] = []
    mapping: dict[tuple, tuple] = {}
    for key in order:
        if key in frozen:
            mapping[key] = key
            centres.append(key)
            continue
        bank, indexes = key
        best = None
        for centre in centres:
            if centre[0] != bank:
                continue
            gap = distance(indexes, centre[1])
            if gap <= tolerance and (best is None or gap < best[0]):
                best = (gap, centre)
        if best is None:
            mapping[key] = key
            centres.append(key)
        else:
            mapping[key] = best[1]
    return [mapping.get(cell, cell) for cell in cells]


def compressed_size(rom: bytes, entry: dict, image: Image.Image,
                    mcm: int, pointers: list[int], chunk: int) -> int:
    payload, tilemap, tile_count = reinsert.encode_image(rom, entry, image)
    payload, _ = reinsert.zero_unreferenced_tiles(payload, tilemap, tile_count, entry)
    codec = rom[mcm + 16]
    sizes = []
    for candidate_chunk in reinsert._chunk_variants(
            len(payload), entry["bytes_per_tile"], len(pointers), chunk):
        modes = ((False, False),)
        if codec == 3:
            modes = ((False, False), (True, False), (False, True), (True, True))
        for optimal, fresh_tree in modes:
            streams = reinsert._pack_candidate(
                rom, payload, len(pointers), candidate_chunk, pointers,
                codec, optimal, fresh_tree)
            if streams is None:
                continue
            sizes.append(sum(reinsert.align4(len(stream)) for stream in streams))
    return min(sizes) if sizes else 1 << 30


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", type=Path, required=True,
                        help="ROM the image allocation is measured against")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--original-dir", type=Path, required=True)
    parser.add_argument("--translated-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--files", nargs="+", required=True)
    parser.add_argument("--min-tolerance", type=int, default=1)
    parser.add_argument("--max-tolerance", type=int, default=48)
    parser.add_argument(
        "--snap-to-source-palette", action="store_true",
        help="snap RGB user artwork to the nearest stock 8bpp palette colour before fitting",
    )
    parser.add_argument(
        "--preserve-glyph-core", action="store_true",
        help=(
            "before tile merging, try a readability-first 2-colour caption form: "
            "preserve every bright glyph-core pixel exactly and collapse only the "
            "dark decorative outline to the black background"
        ),
    )
    args = parser.parse_args()

    rom = args.rom.read_bytes()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    manifest_rows = manifest["images"] if isinstance(manifest, dict) else manifest
    rows = {row["file"]: row for row in manifest_rows}
    containers = {entry["offset"]: entry
                  for entry in image_extract.iter_containers(rom)}
    args.out_dir.mkdir(parents=True, exist_ok=True)

    for name in args.files:
        offset = int(rows[name]["offset_hex"], 16)
        entry = dict(containers[offset])
        mcm = offset + 0x18
        _, chunk, count = struct.unpack_from("<III", rom, mcm + 4)
        pointers = [struct.unpack_from("<I", rom, mcm + 20 + i * 4)[0] - ROM_BASE
                    for i in range(count)]
        room = sum(reinsert.align4(image_extract.block_consumed(rom, pointer))
                   for pointer in pointers)

        palette = reinsert.palette_rgb(rom, entry)
        tilemap = None
        if entry["map_count"]:
            tilemap = list(struct.unpack_from(f"<{entry['map_count']}H", rom,
                                              entry["tilemap"]))
        original = Image.open(args.original_dir / name).convert("RGB")
        translated = Image.open(args.translated_dir / name).convert("RGB")
        snapped_pixels = 0
        if args.snap_to_source_palette:
            translated, snapped_pixels = reinsert.snap_image_to_source_palette(
                rom, entry, translated
            )
        if args.preserve_glyph_core:
            colours = set(translated.getdata())
            expected = {(0, 0, 0), (24, 40, 96), (248, 248, 248)}
            if colours == expected:
                readable = translated.copy()
                readable.putdata([
                    (0, 0, 0) if pixel == (24, 40, 96) else pixel
                    for pixel in translated.getdata()
                ])
                readable_size = compressed_size(
                    rom, entry, readable, mcm, pointers, chunk
                )
                if readable_size <= room:
                    readable.save(args.out_dir / name)
                    white_before = sum(
                        pixel == (248, 248, 248) for pixel in translated.getdata()
                    )
                    white_after = sum(
                        pixel == (248, 248, 248) for pixel in readable.getdata()
                    )
                    print(
                        f"{name}: room={room} fitted={readable_size} "
                        "mode=preserve_glyph_core white_core_changed=0 "
                        f"white_pixels={white_before}/{white_after} "
                        f"palette_snapped_pixels={snapped_pixels} -> "
                        f"{args.out_dir / name}"
                    )
                    continue

        cells_x = entry["width"] // 8
        difference = ImageChops.difference(original, translated)
        changed = {
            cell for cell in range((entry["width"] // 8) * (entry["height"] // 8))
            if difference.crop(((cell % cells_x) * 8, (cell // cells_x) * 8,
                                (cell % cells_x) * 8 + 8,
                                (cell // cells_x) * 8 + 8)).getbbox() is not None
        }
        cells = build_cells(translated, entry, palette, tilemap)

        fitted = None
        for tolerance in range(args.min_tolerance, args.max_tolerance + 1):
            candidate = render(merge(cells, changed, tolerance), entry, palette)
            size = compressed_size(rom, entry, candidate, mcm, pointers, chunk)
            if size <= room:
                fitted = (tolerance, size, candidate)
                break
        if fitted is None:
            print(f"{name}: room={room} NOT FIT up to tolerance "
                  f"{args.max_tolerance}")
            continue
        tolerance, size, candidate = fitted
        candidate.save(args.out_dir / name)
        kept = len({cell for cell in cells})
        print(f"{name}: room={room} fitted={size} tolerance={tolerance} "
              f"changed_cells={len(changed)} distinct_tiles_before={kept} "
              f"palette_snapped_pixels={snapped_pixels} -> {args.out_dir / name}")


if __name__ == "__main__":
    main()
