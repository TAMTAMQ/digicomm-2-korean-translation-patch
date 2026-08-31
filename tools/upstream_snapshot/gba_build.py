#!/usr/bin/env python3
"""Build the Korean-patched ROM: font, translated messages, everything.

One entry point on purpose. When steps live outside the pipeline they get
silently dropped from a clean build -- a mistake this repo has already paid
for on another project, where a patch went missing from the output and only
stale files in the build folder hid it.

What it does, in order:

1. Hijacks unused kanji cells in the font table and draws Hangul into them.
2. For every record with at least one filled-in translation: decompresses it,
   rewrites those messages (and their length words), and re-compresses using
   **the record's original pipeline**.

Two findings the second step depends on, both established on real hardware:

* The game does not dispatch on the compression tag. Handing a Huffman record
  a valid LZ77 block hangs it, while relocating the original block byte for
  byte works. So each record must go back in the format it came in.
* Several records are Huffman wrapping LZ77. Re-encoding one with a single
  Huffman pass took a 944-byte block to 1508; going through LZ77 first brought
  it to 964. `nested_lz77` in messages.json records which ones.

A rebuilt block is written in place when it fits and relocated into the ROM's
free tail otherwise, with the record's self-pointer updated to follow it.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import struct
import sys
from pathlib import Path

import gba_text_extract as gt
import gba_message_stream as ms
import gba_lz77_encode as lz
import gba_huffman_encode as hf
import gba_image_extract as gi
import gba_font_patch as fp
import gba_font_patch_10x10 as fp10

from PIL import Image, ImageChops, ImageFont


ROM_BASE = 0x08000000
FREE_START = 0x00FC0000
FREE_END = 0x00FDC000
DEFAULT_FONT = (
    Path(__file__).resolve().parent.parent
    / "work" / "digicomm_nyo" / "assets" / "fonts" / "thinmulmaru"
    / "font" / "ThinMulmaru.ttf"
)


def materialize_final_graphics_assets(invariants_path: Path, graphics: dict) -> None:
    """Create deterministic final PNG assets before reinsertion."""
    assets_root = invariants_path.parent
    for spec in graphics.get("final_asset_materializations", []):
        source = assets_root / spec["source"]
        target = assets_root / spec["target"]
        if not source.is_file():
            raise SystemExit(f"missing final-asset source PNG: {source}")
        with Image.open(source) as opened:
            src = opened.convert("RGB")
        expected_size = tuple(spec.get("size", src.size))
        if src.size != expected_size:
            raise SystemExit(
                f"final-asset source {source} size {src.size} != {expected_size}"
            )
        background = tuple(spec["background_rgb"])
        out = Image.new("RGB", src.size, background)
        src_px = src.load()
        out_px = out.load()
        moved_pixels = 0
        for region in spec.get("regions", []):
            left, top, right, bottom = region["box"]
            dx, dy = int(region.get("dx", 0)), int(region.get("dy", 0))
            for y in range(top, bottom):
                for x in range(left, right):
                    colour = tuple(src_px[x, y][:3])
                    if colour == background:
                        continue
                    xx, yy = x + dx, y + dy
                    if not (0 <= xx < out.width and 0 <= yy < out.height):
                        raise SystemExit(
                            f"final-asset pixel moved out of bounds: {source} "
                            f"({x},{y}) -> ({xx},{yy})"
                        )
                    out_px[xx, yy] = colour
                    moved_pixels += 1
        target.parent.mkdir(parents=True, exist_ok=True)
        out.save(target)
        if Image.open(target).convert("RGB").size != expected_size:
            raise SystemExit(f"failed to materialize final asset: {target}")
        print(f"materialized final graphic: {target} ({moved_pixels} ink pixels)")


def structured_padding_after(rom: bytearray, start: int,
                             max_padding: int = 0x1000) -> int:
    """Return zero padding before the next known resource container."""
    end = min(len(rom), start + max_padding)
    pos = start
    while pos < end and rom[pos] == 0:
        pos += 1
    if pos > start and bytes(rom[pos:pos + 4]) in {
        b"DMS\x00", b"MBM\x00", b"MCM\x00", b"DSC\x00"
    }:
        return pos - start
    return 0


class Arena:
    """Bump allocator over the ROM's unused tail."""

    def __init__(self, start: int, end: int):
        self.pos = start
        self.end = end

    def take(self, size: int) -> int:
        size = (size + 3) & ~3
        if self.pos + size > self.end:
            raise SystemExit(
                f"out of free ROM space: needed {size} more bytes past {self.end:#x}"
            )
        at = self.pos
        self.pos += size
        return at


def encode_text(text: str, hangul: dict[str, str]) -> bytes:
    """Korean text to bytes, each syllable written as its stand-in kanji."""
    out = bytearray()
    for ch in text:
        if ch in hangul:
            out += bytes.fromhex(hangul[ch])
        elif ch == "♡":
            out += b"\x85\x40"
        elif ch == "\n":
            out += b"\x0a"
        else:
            try:
                out += ch.encode("cp932")
            except UnicodeEncodeError as exc:
                raise SystemExit(f"character {ch!r} has no glyph and no CP932 form") from exc
    return bytes(out)


def recompress(payload: bytes, compression: str, nested_lz77: bool) -> bytes:
    if compression == "lz77":
        return lz.compress(payload)
    if compression == "rle":
        raise SystemExit("RLE records carry no text; nothing should rewrite one")
    bits = int(compression.removeprefix("huffman"))
    if bits not in (4, 8):
        bits = 4
    if nested_lz77:
        return hf.compress(lz.compress(payload), bits)
    return hf.compress(payload, bits)


def patch_font(rom: bytearray, used: set[int], font_path: Path, index: int,
               size: int, threshold: int, dy: int) -> dict[str, str]:
    codes = fp.read_table(rom)
    position = {code: i for i, code in enumerate(codes)}
    free = [c for c in codes if c >= fp.KANJI_MIN and c not in used]
    syllables = fp.ks_x_1001_syllables()
    if len(free) < len(syllables):
        raise SystemExit(f"only {len(free)} free cells for {len(syllables)} syllables")

    font = ImageFont.truetype(str(font_path), size, index=index)
    mapping = {}
    for syllable, code in zip(syllables, free):
        entry = fp.FONT_TABLE + position[code] * fp.ENTRY_SIZE
        for r, bits in enumerate(fp.render_glyph(syllable, font, threshold, dy)):
            struct.pack_into(">H", rom, entry + 2 + r * 2, bits)
        mapping[syllable] = f"{code:04X}"
    return mapping


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--messages", type=Path, required=True)
    parser.add_argument("--used", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--out-map", type=Path)
    parser.add_argument(
        "--invariants", type=Path,
        help="build invariant policy (default: assets/build_invariants.json)",
    )
    parser.add_argument("--font", type=Path, default=DEFAULT_FONT)
    parser.add_argument("--font-index", type=int, default=0)
    parser.add_argument("--font-size", type=int, default=12)
    parser.add_argument("--font-threshold", type=int, default=110)
    parser.add_argument("--font-dy", type=int, default=0)
    parser.add_argument(
        "--allow-image-relocation", action="store_true",
        help="move image streams out of their original allocation.  Every "
             "build that did this died at the main menu: the game walks an "
             "image's compressed blocks as one physical chain.  Diagnostics "
             "only.",
    )
    parser.add_argument(
        "--skip-graphics", action="store_true",
        help="leave the Japanese graphics alone (bisecting a runtime fault)",
    )
    args = parser.parse_args()

    invariants_path = args.invariants or args.messages.parent.parent / "build_invariants.json"
    if not invariants_path.is_file():
        raise SystemExit(f"missing build invariant policy: {invariants_path}")
    invariants = json.loads(invariants_path.read_text(encoding="utf-8"))
    fixed_slot_records = {
        int(value, 16) for value in invariants["fixed_message_slot_records"]
    }
    must_stay_in_place_records = {
        int(value, 16) for value in invariants.get("must_stay_in_place_records", {})
    }
    fixed_slot_mode = invariants.get("fixed_message_slot_mode", "listed")
    if fixed_slot_mode not in {"listed", "all_reinsertable"}:
        raise SystemExit(f"unknown fixed_message_slot_mode: {fixed_slot_mode}")
    recovered_config = invariants.get("recovered_message_text", {})
    recovered_map = {}
    recovered_expected = 0
    if recovered_config:
        recovered_path = invariants_path.parent / recovered_config["translation_map"]
        recovered_map = json.loads(recovered_path.read_text(encoding="utf-8"))
        if not isinstance(recovered_map, dict) or not all(
            isinstance(source, str) and isinstance(translation, str)
            for source, translation in recovered_map.items()
        ):
            raise SystemExit(
                f"recovered message translation map must be a string object: {recovered_path}"
            )
        recovered_expected = int(recovered_config.get("expected_replacements", 0))

    graphics = invariants["graphics_text"]
    materialize_final_graphics_assets(invariants_path, graphics)
    translated_png_dir = invariants_path.parent / graphics["translated_png_dir"]
    source_png_dir = invariants_path.parent / "image_extraction/japanese_images/png"
    changed_translated_pngs = []
    for translated in sorted(translated_png_dir.glob("*.png")):
        source = source_png_dir / translated.name
        if not source.is_file():
            changed_translated_pngs.append(translated)
            continue
        with Image.open(source) as original_image, Image.open(translated) as translated_image:
            if original_image.size != translated_image.size:
                changed_translated_pngs.append(translated)
                continue
            difference = ImageChops.difference(
                original_image.convert("RGB"), translated_image.convert("RGB")
            )
            if difference.getbbox() is not None:
                changed_translated_pngs.append(translated)
    if changed_translated_pngs and not graphics["reinsertion_integrated"]:
        raise SystemExit(
            f"found {len(changed_translated_pngs)} changed translated graphics in "
            f"{translated_png_dir}, "
            "but graphics reinsertion is not integrated into gba_build.py"
        )

    rom = bytearray(args.rom.read_bytes())
    used_payload = json.loads(args.used.read_text(encoding="utf-8"))
    if not isinstance(used_payload, list) or not all(isinstance(code, int) for code in used_payload):
        raise SystemExit(
            f"--used must be a JSON array of integer CP932 codes (for this project: "
            f"analysis/used_sjis.json), not {type(used_payload).__name__}: {args.used}"
        )
    used = set(used_payload)
    records = json.loads(args.messages.read_text(encoding="utf-8"))

    hangul = patch_font(rom, used, args.font, args.font_index,
                        args.font_size, args.font_threshold, args.font_dy)
    font10_path = Path(__file__).resolve().parent.parent / fp10.FONT
    font10_written = fp10.patch_rom(rom, hangul, font10_path)
    if args.out_map:
        args.out_map.write_text(
            json.dumps({"syllables": len(hangul), "map": hangul}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    raw_ui_config = invariants.get("raw_ui_text", {})
    raw_ui_patched = 0
    fixed_raw_ui_offsets = {
        int(item["offset_hex"], 16)
        for item in raw_ui_config.get("fixed_width_replacements", [])
    }
    for item in raw_ui_config.get("fixed_width_replacements", []):
        offset = int(item["offset_hex"], 16)
        source = item["source"].encode("cp932")
        replacement = encode_text(item["translation"], hangul)
        if len(replacement) > len(source):
            raise SystemExit(
                f"raw UI {item['offset_hex']}: replacement exceeds fixed slot "
                f"{len(source)} -> {len(replacement)}"
            )
        current = bytes(rom[offset:offset + len(source)])
        if current != source:
            raise SystemExit(
                f"raw UI {item['offset_hex']}: expected {source.hex()} "
                f"for {item['source']!r}, found {current.hex()}"
            )
        rom[offset:offset + len(source)] = replacement.ljust(len(source), b"\x00")
        raw_ui_patched += 1

    raw_ui_map = raw_ui_config.get("translation_map")
    if raw_ui_map:
        raw_ui_path = invariants_path.parent / raw_ui_map
        rows = json.loads(raw_ui_path.read_text(encoding="utf-8"))
        if not isinstance(rows, dict):
            raise SystemExit(f"raw UI translation map must be an object: {raw_ui_path}")
        for offset_hex, item in rows.items():
            offset = int(offset_hex, 16)
            if offset in fixed_raw_ui_offsets:
                continue
            source = item["source"].encode("cp932")
            replacement = encode_text(item["translation"], hangul)
            if len(replacement) > len(source):
                raise SystemExit(
                    f"raw UI {offset_hex}: replacement exceeds fixed slot "
                    f"{len(source)} -> {len(replacement)}: {item['translation']!r}"
                )
            current = bytes(rom[offset:offset + len(source)])
            if current != source:
                raise SystemExit(
                    f"raw UI {offset_hex}: expected {source.hex()} for "
                    f"{item['source']!r}, found {current.hex()}"
                )
            rom[offset:offset + len(source)] = replacement.ljust(len(source), b"\x00")
            raw_ui_patched += 1

    arena = Arena(FREE_START, FREE_END)
    touched = translated = relocated = skipped = 0
    raw_message_touched = 0
    recovered_translated = 0
    padded_in_place = structured_padding_used = 0

    for record in records:
        filled = [l for l in record.get("lines", []) if l.get("translation")]
        if not filled and not recovered_map:
            continue
        if not record.get("reinsertable"):
            if filled:
                raise SystemExit(
                    f"record {record['mcm_offset_hex']} has translations but no message "
                    "stream to write them into"
                )
            continue

        tag_off = record["mcm_offset"]
        total_size, chunk_size, section_count = struct.unpack_from(
            "<III", rom, tag_off + 4
        )
        pointers = [
            struct.unpack_from("<I", rom, tag_off + 20 + i * 4)[0] - ROM_BASE
            for i in range(section_count)
        ]
        block = pointers[0]

        raw_message_mcm = False
        expected_headers = {
            line["header"] for line in record.get("lines", [])
            if isinstance(line, dict) and "header" in line
        }
        if rom[tag_off + 16] == 0 and expected_headers:
            remaining = total_size
            raw_parts = []
            for pointer in pointers:
                take = min(chunk_size, remaining)
                raw_parts.append(bytes(rom[pointer:pointer + take]))
                remaining -= take
            raw_candidate = b"".join(raw_parts)
            raw_headers = {message.header for message in ms.parse(raw_candidate)}
            if (remaining == 0 and len(raw_candidate) == total_size
                    and expected_headers.issubset(raw_headers)):
                blob = raw_candidate
                raw_message_mcm = True
            else:
                sections = [gt.decompress(rom, pointer) for pointer in pointers]
                blob = b"".join(sections)
        else:
            sections = [gt.decompress(rom, pointer) for pointer in pointers]
            blob = b"".join(sections)
        if raw_message_mcm:
            if len(blob) != total_size:
                skipped += 1
                continue
        elif (len(blob) != record["decompressed_size"]
                or len(blob) != total_size):
            skipped += 1
            continue

        replacements = {}
        by_header = {m.header: m for m in ms.parse(blob)}
        preserve_slots = (
            fixed_slot_mode == "all_reinsertable" or tag_off in fixed_slot_records
        )
        for line in filled:
            message = by_header.get(line["header"])
            if message is None:
                raise SystemExit(
                    f"record {record['mcm_offset_hex']}: no message at "
                    f"{line['header']:#x} -- messages.json is stale, re-extract"
                )
            try:
                replacements[message.header] = ms.pad_payload(
                    encode_text(line["translation"], hangul), message,
                    preserve_length=preserve_slots,
                )
            except ValueError as exc:
                raise SystemExit(
                    f"record {record['mcm_offset_hex']} message {message.header:#x}: {exc}"
                ) from exc
            translated += 1

        if recovered_map:
            for message in by_header.values():
                if message.header in replacements:
                    continue
                translation = recovered_map.get(message.text)
                if translation is None:
                    continue
                try:
                    replacements[message.header] = ms.pad_payload(
                        encode_text(translation, hangul), message,
                        preserve_length=preserve_slots,
                    )
                except ValueError as exc:
                    raise SystemExit(
                        f"record {record['mcm_offset_hex']} recovered message "
                        f"{message.header:#x}: {exc}"
                    ) from exc
                recovered_translated += 1

        if not replacements:
            continue

        rebuilt = ms.rebuild(blob, replacements)
        if preserve_slots and len(rebuilt) != len(blob):
            raise SystemExit(
                f"record {record['mcm_offset_hex']}: fixed-slot rebuild changed "
                f"size {len(blob)} -> {len(rebuilt)}"
            )
        if preserve_slots:
            payload_offsets = set()
            for header in replacements:
                message = by_header[header]
                if rebuilt[header:header + 2] != ms.TEXT_TAG:
                    raise SystemExit(
                        f"record {record['mcm_offset_hex']} message {header:#x}: "
                        "fixed-slot header moved"
                    )
                rebuilt_length = int.from_bytes(rebuilt[header + 2:header + 4], "little")
                if rebuilt_length != message.length:
                    raise SystemExit(
                        f"record {record['mcm_offset_hex']} message {header:#x}: "
                        f"fixed-slot length changed {message.length} -> {rebuilt_length}"
                    )
                payload_offsets.update(range(message.start, message.start + message.length))
            changed_outside_payload = [
                offset for offset, (before, after) in enumerate(zip(blob, rebuilt))
                if before != after and offset not in payload_offsets
            ]
            if changed_outside_payload:
                raise SystemExit(
                    f"record {record['mcm_offset_hex']}: fixed-slot rebuild changed "
                    f"{len(changed_outside_payload)} non-message bytes"
                )
        if raw_message_mcm:
            if len(rebuilt) != total_size:
                raise SystemExit(
                    f"record {record['mcm_offset_hex']}: raw message MCM changed "
                    f"size {total_size} -> {len(rebuilt)}"
                )
            remaining = total_size
            source = 0
            for pointer in pointers:
                take = min(chunk_size, remaining)
                rom[pointer:pointer + take] = rebuilt[source:source + take]
                source += take
                remaining -= take
            if remaining != 0 or source != len(rebuilt):
                raise SystemExit(
                    f"record {record['mcm_offset_hex']}: raw message MCM chunk "
                    "layout does not cover the declared payload"
                )
            raw_message_touched += 1
            touched += 1
            continue

        streams = [
            recompress(rebuilt[i * chunk_size:min(len(rebuilt), (i + 1) * chunk_size)],
                       record["compression"], record.get("nested_lz77", False))
            for i in range(section_count)
        ]
        capacities = [gi.block_consumed(rom, pointer) for pointer in pointers]

        aligned = sum((len(stream) + 3) & ~3 for stream in streams)
        compressed_room = sum((capacity + 3) & ~3 for capacity in capacities)
        chain_end = pointers[-1] + ((capacities[-1] + 3) & ~3)
        structured_padding = structured_padding_after(rom, chain_end)
        chain_room = compressed_room + structured_padding
        contiguous = all(
            pointers[i + 1] == pointers[i] + ((capacities[i] + 3) & ~3)
            for i in range(section_count - 1)
        )
        if aligned <= chain_room and (contiguous or section_count == 1):
            if aligned > compressed_room:
                padded_in_place += 1
                structured_padding_used += aligned - compressed_room
            cursor = pointers[0]
            for index, stream in enumerate(streams):
                rom[cursor:cursor + len(stream)] = stream
                struct.pack_into("<I", rom, tag_off + 20 + index * 4,
                                 ROM_BASE + cursor)
                cursor += (len(stream) + 3) & ~3
        else:
            if tag_off in must_stay_in_place_records:
                raise SystemExit(
                    f"record {record['mcm_offset_hex']} is a runtime transition MCM and "
                    f"must remain at its original physical chain; rebuilt chain needs "
                    f"{aligned} bytes but only {chain_room} bytes are available"
                )
            at = arena.take(sum((len(stream) + 3) & ~3 for stream in streams))
            cursor = at
            for index, stream in enumerate(streams):
                rom[cursor:cursor + len(stream)] = stream
                struct.pack_into("<I", rom, tag_off + 20 + index * 4,
                                 ROM_BASE + cursor)
                cursor += (len(stream) + 3) & ~3
            relocated += 1
        if len(rebuilt) != total_size:
            raise SystemExit(
                f"record {record['mcm_offset_hex']}: rebuild changed the MCM "
                f"size {total_size} -> {len(rebuilt)}"
            )
        touched += 1

    if recovered_expected and recovered_translated != recovered_expected:
        raise SystemExit(
            f"recovered message count {recovered_translated} != expected {recovered_expected}"
        )

    args.out.write_bytes(bytes(rom))

    map_for_patches = args.out_map or args.out.with_name("hangul_map_built.json")
    if not map_for_patches.is_file():
        raise SystemExit(f"name-entry patches need the built Hangul map: {map_for_patches}")
    stage = args.out.with_name(args.out.stem + ".stage.tmp" + args.out.suffix)
    try:
        for tool in ("gba_nameentry_hangul.py", "gba_nameentry_font8.py",
                     "gba_nameentry_page2.py", "gba_nameentry_batchim.py",
                     "gba_fix_name_suffix.py"):
            command = [
                sys.executable, str(Path(__file__).with_name(tool)),
                "--rom", str(args.out), "--map", str(map_for_patches),
                "--out", str(stage),
            ]
            if tool == "gba_nameentry_font8.py":
                extra = invariants.get("raw_ui_text", {}).get("extra_8x8_glyph_text")
                if extra:
                    command += ["--extra-text", str(invariants_path.parent / extra)]
            elif tool == "gba_nameentry_batchim.py":
                command += [
                    "--used", str(args.used),
                    "--report",
                    str(args.out.with_suffix(args.out.suffix + ".nameentry_batchim.json")),
                ]
            subprocess.run(command, check=True)
            stage.replace(args.out)
        button_config = invariants.get("raw_obj_text", {}).get("nameentry_buttons")
        button_tool = button_config.get("tool", "gba_patch_rom_buttons.py") \
            if button_config else "gba_patch_rom_buttons.py"
        button_command = [
            sys.executable,
            str(Path(__file__).with_name(button_tool)),
            "--rom", str(args.out), "--out", str(stage),
        ]
        if button_config:
            assets_root = invariants_path.parent
            original_dir = assets_root / button_config["original_dir"]
            translated_dir = assets_root / button_config["translated_dir"]
            button_command += [
                "--original-dir", str(original_dir),
                "--translated-dir", str(translated_dir),
            ]
        subprocess.run(button_command, check=True)
        if button_config:
            translated_count = len(list(translated_dir.glob("*.png")))
            if translated_count != button_config["expected_translated"]:
                raise SystemExit(
                    f"name-entry button translated PNG count {translated_count} != "
                    f"expected {button_config['expected_translated']}"
                )
        stage.replace(args.out)
    finally:
        if stage.exists():
            stage.unlink()

    if not args.skip_graphics and translated_png_dir.is_dir() and graphics["reinsertion_integrated"]:
        manifest = invariants_path.parent / "image_extraction/japanese_images/manifest.json"
        report = args.out.with_suffix(args.out.suffix + ".images.json")
        graphics_out = args.out.with_name(args.out.stem + ".graphics.tmp" + args.out.suffix)
        command = [
            sys.executable,
            str(Path(__file__).with_name("gba_image_reinsert.py")),
            "--rom", str(args.out),
            "--manifest", str(manifest),
            "--translated-dir", str(translated_png_dir),
            "--out", str(graphics_out),
            "--report", str(report),
            "--in-place-only",
        ]
        trusted_reference = graphics.get("trusted_reference_rom")
        if trusted_reference:
            command += [
                "--trusted-reference-rom",
                str(invariants_path.parent / trusted_reference),
            ]
            trusted_sha = graphics.get("trusted_reference_sha256")
            if trusted_sha:
                command += ["--trusted-reference-sha256", trusted_sha]
        for offset in graphics.get("generic_reinsert_excluded_offsets", []):
            command += ["--exclude-offset", offset]
        verified_count = graphics.get(
            "generic_verified_image_count", graphics.get("verified_image_count")
        )
        if verified_count:
            command += ["--require-verified", str(verified_count)]
        if args.allow_image_relocation:
            raise SystemExit(
                "image relocation is permanently disabled for digicomm_nyo; "
                "runtime testing proved relocated image chains are unsafe"
            )
        for offset in graphics.get("preserve_source_codec_offsets", []):
            command += ["--preserve-source-codec", offset]
        for offset in graphics.get("preserve_section_start_offsets", []):
            command += ["--preserve-section-start", offset]
        for offset in graphics.get("preserve_tilemap_offsets", []):
            command += ["--preserve-tilemap", offset]
        for offset in graphics.get("direct_huffman_offsets", []):
            command += ["--direct-huffman", offset]
        for offset in graphics.get("snap_to_source_palette_offsets", []):
            command += ["--snap-to-source-palette", offset]
        for offset, override in graphics.get("source_overrides", {}).items():
            command += [
                "--source-override",
                f"{offset}={invariants_path.parent / override}",
            ]
        try:
            subprocess.run(command, check=True)
            graphics_out.replace(args.out)
        finally:
            if graphics_out.exists():
                graphics_out.unlink()

    caption_config = graphics.get("captions")
    if not args.skip_graphics and caption_config:
        assets_root = invariants_path.parent
        caption_manifest = assets_root / caption_config["manifest"]
        caption_dir = assets_root / caption_config["translated_dir"]
        caption_fit_dir = assets_root / caption_config["fit_dir"]
        expected_captions = int(caption_config["expected_count"])
        caption_count = len(list(caption_dir.glob("*.png")))
        if caption_count != expected_captions:
            raise SystemExit(
                f"caption translated PNG count {caption_count} != "
                f"expected {expected_captions}"
            )
        caption_out = args.out.with_name(args.out.stem + ".captions.tmp" + args.out.suffix)
        caption_report = args.out.with_suffix(args.out.suffix + ".captions.json")
        caption_command = [
            sys.executable,
            str(Path(__file__).with_name("gba_image_reinsert.py")),
            "--rom", str(args.out),
            "--manifest", str(caption_manifest),
            "--translated-dir", str(caption_dir),
            "--out", str(caption_out),
            "--report", str(caption_report),
            "--in-place-only",
            "--require-verified", str(expected_captions),
        ]
        for offset, filename in caption_config.get("fit_overrides", {}).items():
            override = caption_fit_dir / filename
            if not override.is_file():
                raise SystemExit(f"missing fitted caption override: {override}")
            caption_command += ["--source-override", f"{offset}={override}"]
        try:
            subprocess.run(caption_command, check=True)
            caption_out.replace(args.out)
        finally:
            if caption_out.exists():
                caption_out.unlink()

    raw_obj = invariants.get("raw_obj_text", {})
    if not args.skip_graphics and raw_obj.get("reinsertion_integrated"):
        assets_root = invariants_path.parent
        obj_stage = args.out.with_name(args.out.stem + ".obj.tmp" + args.out.suffix)
        try:
            for group in ("cards", "menu"):
                config = raw_obj[group]
                translated_dir = assets_root / config["translated_dir"]
                expected_count = config["expected_translated"]
                translated_pattern = "menu_*.png" if group == "menu" else "*.png"
                command = [
                    sys.executable,
                    str(Path(__file__).with_name(config["tool"])),
                    "--rom", str(args.out),
                    "--spec", str(assets_root / config["spec"]),
                    "--out", str(obj_stage),
                    "--pram", str(assets_root / config["pram"]),
                    "--original-dir", str(assets_root / config["original_dir"]),
                    "--translated-dir", str(translated_dir),
                ]
                translated_count = len(list(translated_dir.glob(translated_pattern)))
                if translated_count != expected_count:
                    raise SystemExit(
                        f"{group} translated PNG count {translated_count} != "
                        f"expected {expected_count}"
                    )
                command += ["--from-translated"]
                subprocess.run(command, check=True)
                translated_count = len(list(translated_dir.glob(translated_pattern)))
                if translated_count != expected_count:
                    raise SystemExit(
                        f"{group} translated PNG count {translated_count} != "
                        f"expected {expected_count}"
                    )
                obj_stage.replace(args.out)

            title_start = raw_obj.get("title_start")
            if title_start:
                title_command = [
                    sys.executable,
                    str(Path(__file__).with_name(title_start["tool"])),
                    "--rom", str(args.out),
                    "--original-rom", str(args.rom),
                    "--out", str(obj_stage),
                    "--offset", title_start["offset_hex"],
                    "--pram", str(assets_root / title_start["pram"]),
                    "--original-png", str(assets_root / title_start["original_png"]),
                    "--translated-png", str(assets_root / title_start["translated_png"]),
                ]
                if title_start.get("custom_png"):
                    title_command += [
                        "--custom-png", str(assets_root / title_start["custom_png"]),
                    ]
                subprocess.run(title_command, check=True)
                obj_stage.replace(args.out)

            exit_button = raw_obj.get("exit_button")
            if exit_button:
                exit_command = [
                    sys.executable,
                    str(Path(__file__).with_name(exit_button["tool"])),
                    "--rom", str(args.out),
                    "--out", str(obj_stage),
                ]
                if exit_button.get("original_png"):
                    exit_command += [
                        "--original-png", str(assets_root / exit_button["original_png"]),
                    ]
                if exit_button.get("translated_png"):
                    exit_command += [
                        "--translated-png", str(assets_root / exit_button["translated_png"]),
                    ]
                subprocess.run(exit_command, check=True)
                obj_stage.replace(args.out)
        finally:
            if obj_stage.exists():
                obj_stage.unlink()

    minigame_recovery = graphics.get("minigame_ui_recovery")
    if not args.skip_graphics and minigame_recovery:
        final_stage = args.out.with_name(args.out.stem + ".minigame.tmp" + args.out.suffix)
        unit_report = args.out.with_suffix(args.out.suffix + ".minigame_text.json")
        recovery_report = args.out.with_suffix(args.out.suffix + ".minigame_recovery.json")
        try:
            subprocess.run([
                sys.executable,
                str(Path(__file__).with_name("gba_patch_minigame_textfix_final.py")),
                "--rom", str(args.out),
                "--map", str(map_for_patches),
                "--out", str(final_stage),
                "--report", str(unit_report),
            ], check=True)
            final_stage.replace(args.out)

            recovery_tool = invariants_path.parent / minigame_recovery["tool"]
            subprocess.run([
                sys.executable, str(recovery_tool),
                "--rom", str(args.out),
                "--out", str(final_stage),
                "--report", str(recovery_report),
            ], check=True)
            final_stage.replace(args.out)
        finally:
            if final_stage.exists():
                final_stage.unlink()

        final_rom = args.out.read_bytes()
        minigame_segment = final_rom[0x00D00000:0x00D80000]
        for stock_unit in ("万円", "人"):
            pattern = rb"[0-9]{1,7}" + re.escape(stock_unit.encode("cp932"))
            if re.search(pattern, minigame_segment):
                raise SystemExit(
                    f"minigame finalizer regression: numeric {stock_unit} remains"
                )

    print(f"font cells hijacked : {len(hangul)}")
    print(f"10x10 font patched  : {font10_written}")
    print(f"records rebuilt     : {touched} ({relocated} relocated)")
    print(f"raw message MCMs    : {raw_message_touched}")
    print(f"messages translated : {translated}")
    print(f"recovered messages  : {recovered_translated}")
    print(f"raw UI strings      : {raw_ui_patched}")
    print(f"bogus records skipped: {skipped}")
    print(f"structured-pad kept : {padded_in_place} ({structured_padding_used} extra bytes)")
    print(f"free space used     : {arena.pos - FREE_START} / {FREE_END - FREE_START} bytes")
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()
