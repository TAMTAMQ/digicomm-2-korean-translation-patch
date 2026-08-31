#!/usr/bin/env python3
"""Extract every CP932 message this game stores in its MCM record tree.

The whole picture, finally
--------------------------
Each `MCM\0` record is: tag(4) + 4x u32 + a self-pointer that resolves to
record+24, and at that address a compressed block in a standard GBA BIOS
format, identified by its first byte:

    0x10  LZ77   (SWI 0x11/0x12)
    0x2N  Huffman, N-bit symbols (SWI 0x13)
    0x30  RLE    (SWI 0x14/0x15)

Blocks nest: several Huffman blocks decompress to an LZ77 block that has to be
decompressed again before any text appears.

Everything earlier passes described as "control bytes wedged in at arbitrary
positions, even inside a two-byte Shift-JIS character" was simply LZ77 flag
and back-reference bytes being read as if they were text. That misreading is
why the old corpus came out as thousands of 3-character shards that no amount
of filtering could repair, and why so much of the script appeared to be missing
from the ROM entirely. Decompress properly and the messages are clean,
continuous CP932 -- no wedged bytes, no gaps, no reconstruction heuristics.

The decompressed payload is a small stream of u16 fields where `03 00` plus a
u16 length introduces one message; this reads the text runs directly, which is
robust to the fields whose meaning is still unknown.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
from pathlib import Path


ROM_BASE = 0x08000000
MAX_OUTPUT = 1 << 20
MAX_NESTING = 4

HIRA_RE = re.compile(r"[ぁ-ゟ]")
KATA_RE = re.compile(r"[ァ-ヺ]")
JP_RE = re.compile(r"[ぁ-ゟァ-ヿ㐀-鿿々〆ー]")
GOOD_CHAR_RE = re.compile(
    "[ぁ-ゟァ-ヿ㐀-鿿々〆ー"
    "、。！？「」『』【】〔〕・…（）　 "
    "％＋×－０-９Ａ-Ｚａ-ｚ\n]"
)
KANJI_RE = re.compile(r"[㐀-鿿]")
LEVEL2_MIN = 0x989F
KANJI_WHITELIST = frozenset("亊殲璧")


class DecompressError(Exception):
    pass


def decompress_lz77(src: bytes, off: int) -> bytes:
    size = int.from_bytes(src[off + 1 : off + 4], "little")
    if size == 0 or size > MAX_OUTPUT:
        raise DecompressError(f"implausible LZ77 size {size}")
    out = bytearray()
    pos = off + 4
    while len(out) < size:
        if pos >= len(src):
            raise DecompressError("LZ77 ran past end of input")
        flags = src[pos]
        pos += 1
        for bit in range(8):
            if len(out) >= size:
                break
            if flags & (0x80 >> bit):
                if pos + 1 >= len(src):
                    raise DecompressError("LZ77 ran past end of input")
                b1, b2 = src[pos], src[pos + 1]
                pos += 2
                length = (b1 >> 4) + 3
                disp = (((b1 & 0x0F) << 8) | b2) + 1
                if disp > len(out):
                    raise DecompressError("LZ77 back-reference before start")
                for _ in range(length):
                    out.append(out[len(out) - disp])
            else:
                if pos >= len(src):
                    raise DecompressError("LZ77 ran past end of input")
                out.append(src[pos])
                pos += 1
    return bytes(out[:size])


def decompress_huffman(src: bytes, off: int) -> bytes:
    """SWI 0x13. Header: tag 0x2N, u24 size, u8 (tree bytes / 2 - 1), tree,
    then the bitstream as 32-bit little-endian words read MSB first."""
    bits = src[off] & 0x0F
    if bits not in (4, 8):
        raise DecompressError(f"unsupported Huffman symbol width {bits}")
    size = int.from_bytes(src[off + 1 : off + 4], "little")
    if size == 0 or size > MAX_OUTPUT:
        raise DecompressError(f"implausible Huffman size {size}")

    tree_start = off + 4
    if tree_start >= len(src):
        raise DecompressError("Huffman header past end of input")
    pos = tree_start + (src[tree_start] + 1) * 2
    root = tree_start + 1
    node = root

    out = bytearray()
    pending = None
    while len(out) < size:
        if pos + 4 > len(src):
            raise DecompressError("Huffman bitstream ran past end of input")
        word = int.from_bytes(src[pos : pos + 4], "little")
        pos += 4
        for shift in range(31, -1, -1):
            if len(out) >= size:
                break
            bit = (word >> shift) & 1
            entry = src[node]
            child = (node & ~1) + (entry & 0x3F) * 2 + 2
            is_leaf = bool(entry & (0x40 if bit else 0x80))
            node = child + bit
            if node >= len(src):
                raise DecompressError("Huffman tree node past end of input")
            if is_leaf:
                value = src[node]
                if bits == 8:
                    out.append(value)
                elif pending is None:
                    pending = value & 0x0F
                else:
                    out.append(((value & 0x0F) << 4) | pending)
                    pending = None
                node = root
    return bytes(out[:size])


def decompress_rle(src: bytes, off: int) -> bytes:
    size = int.from_bytes(src[off + 1 : off + 4], "little")
    if size == 0 or size > MAX_OUTPUT:
        raise DecompressError(f"implausible RLE size {size}")
    out = bytearray()
    pos = off + 4
    while len(out) < size:
        if pos >= len(src):
            raise DecompressError("RLE ran past end of input")
        flag = src[pos]
        pos += 1
        if flag & 0x80:
            if pos >= len(src):
                raise DecompressError("RLE ran past end of input")
            out.extend(bytes([src[pos]]) * ((flag & 0x7F) + 3))
            pos += 1
        else:
            count = (flag & 0x7F) + 1
            out.extend(src[pos : pos + count])
            pos += count
    return bytes(out[:size])


def decompress(src: bytes, off: int = 0, depth: int = 0) -> bytes:
    """Decompress one block, following nested blocks (Huffman wrapping LZ77)."""
    if depth >= MAX_NESTING or off + 4 > len(src):
        return src[off:]
    tag = src[off]
    if tag == 0x10:
        out = decompress_lz77(src, off)
    elif tag & 0xF0 == 0x20:
        out = decompress_huffman(src, off)
    elif tag == 0x30:
        out = decompress_rle(src, off)
    else:
        return src[off:]
    if out and out[0] in (0x10, 0x30) or (out and out[0] & 0xF0 == 0x20):
        try:
            return decompress(out, 0, depth + 1)
        except DecompressError:
            return out
    return out


def _is_level2_kanji(ch: str) -> bool:
    if ch in KANJI_WHITELIST or not KANJI_RE.match(ch):
        return False
    try:
        return int.from_bytes(ch.encode("cp932"), "big") >= LEVEL2_MIN
    except UnicodeEncodeError:
        return True


def text_runs(blob: bytes):
    """Yield (offset, text) for each run of real text in a decompressed blob."""
    chars = []
    i = 0
    n = len(blob)
    while i < n:
        b = blob[i]
        if (0x81 <= b <= 0x9F or 0xE0 <= b <= 0xFC) and i + 1 < n:
            b2 = blob[i + 1]
            if 0x40 <= b2 <= 0xFC and b2 != 0x7F:
                try:
                    ch = bytes([b, b2]).decode("cp932")
                except UnicodeDecodeError:
                    ch = "�"
                chars.append((ch, i))
                i += 2
                continue
        if b == 0x0A:
            chars.append(("\n", i))
        elif 0x20 <= b <= 0x7E or 0xA1 <= b <= 0xDF:
            try:
                chars.append((bytes([b]).decode("cp932"), i))
            except UnicodeDecodeError:
                chars.append(("�", i))
        else:
            chars.append(("\x00", i))
        i += 1

    runs = []
    current = []
    start = None
    for ch, offset in chars:
        if GOOD_CHAR_RE.match(ch) and not _is_level2_kanji(ch):
            if start is None:
                start = offset
            current.append(ch)
            continue
        if current:
            runs.append((start, "".join(current)))
        current = []
        start = None
    if current:
        runs.append((start, "".join(current)))

    for offset, text in runs:
        text = text.strip(" \n　")
        if len(text) < 2:
            continue
        if not HIRA_RE.search(text) and len(KATA_RE.findall(text)) < 2:
            continue
        jp = len(JP_RE.findall(text))
        if jp < 2 or jp < len(text) * 0.5:
            continue
        yield offset, text


def _has_nested_lz77(data: bytes, block: int, tag: int) -> bool:
    """True when the record is Huffman wrapping an LZ77 block."""
    if tag & 0xF0 != 0x20:
        return False
    try:
        inner = decompress_huffman(data, block)
    except DecompressError:
        return False
    return bool(inner) and inner[0] == 0x10


def iter_records(data: bytes):
    """Yield (mcm_offset, block_offset, tag, limit) for every valid MCM record."""
    starts = [m.start() for m in re.finditer(b"MCM\x00", data)]
    for index, tag_off in enumerate(starts):
        if tag_off + 24 > len(data):
            continue
        self_ptr = struct.unpack_from("<I", data, tag_off + 20)[0]
        if self_ptr < ROM_BASE:
            continue
        block = self_ptr - ROM_BASE
        if not (24 <= block - tag_off <= 256) or block + 5 > len(data):
            continue
        payload_len = struct.unpack_from("<I", data, tag_off + 4)[0]
        limit = len(data)
        if index + 1 < len(starts):
            limit = starts[index + 1]
        if 0 < payload_len <= MAX_OUTPUT:
            limit = min(limit, block + payload_len + 16)
        chunk_size, section_count = struct.unpack_from("<II", data, tag_off + 8)
        pointers = [block]
        if 1 < section_count <= 64 and block - tag_off == 20 + section_count * 4:
            pointers = [
                struct.unpack_from("<I", data, tag_off + 20 + i * 4)[0] - ROM_BASE
                for i in range(section_count)
            ]
            if any(not (0 <= p < len(data)) for p in pointers):
                pointers = [block]
        yield tag_off, block, data[block], limit, pointers, chunk_size


def extract(data: bytes):
    """Extract every message, keyed by its position in the message stream."""
    import gba_message_stream as stream

    records = []
    for tag_off, block, tag, limit, pointers, chunk_size in iter_records(data):
        raw_message_mcm = False
        total_size = struct.unpack_from("<I", data, tag_off + 4)[0]
        if data[tag_off + 16] == 0 and total_size > 0:
            remaining = total_size
            raw_parts = []
            for pointer in pointers:
                take = min(chunk_size, remaining)
                raw_parts.append(data[pointer:pointer + take])
                remaining -= take
            raw_candidate = b"".join(raw_parts)
            if (
                remaining == 0
                and len(raw_candidate) == total_size
                and stream.parse(raw_candidate)
            ):
                blob = raw_candidate
                raw_message_mcm = True

        if not raw_message_mcm:
            if tag == 0x10 or tag == 0x30 or tag & 0xF0 == 0x20:
                try:
                    sections = [decompress(data, pointer) for pointer in pointers]
                except DecompressError:
                    continue
                blob = b"".join(sections)
            else:
                if limit <= block:
                    continue
                blob = data[block:limit]
        messages = stream.parse(blob)
        if messages:
            lines = [
                {"header": m.header, "text": m.text, "translation": ""}
                for m in messages
            ]
            reinsertable = True
        else:
            lines = [
                {"blob_offset": offset, "text": text, "translation": ""}
                for offset, text in text_runs(blob)
            ]
            reinsertable = False
        if lines:
            records.append(
                {
                    "mcm_offset": tag_off,
                    "mcm_offset_hex": hex(tag_off),
                    "compression": (
                        "raw" if raw_message_mcm
                        else {0x10: "lz77", 0x30: "rle"}.get(tag, f"huffman{tag & 0xF}")
                    ),
                    "nested_lz77": (
                        False if raw_message_mcm
                        else _has_nested_lz77(data, block, tag)
                    ),
                    "reinsertable": reinsertable,
                    "decompressed_size": len(blob),
                    "section_count": len(pointers),
                    "chunk_size": chunk_size,
                    "lines": lines,
                }
            )
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    data = args.rom.read_bytes()
    records = extract(data)
    args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [l for r in records for l in r["lines"]]
    chars = sum(len(l["text"]) for l in lines)
    print(f"records: {len(records)}, messages: {len(lines)}, characters: {chars} -> {args.output}")


if __name__ == "__main__":
    main()
