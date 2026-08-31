#!/usr/bin/env python3
"""Create and verify a classic IPS patch for the final 16 MiB GBA ROM.

The source and target must have the same size.  Records are emitted only for
changed byte ranges, split to the IPS 16-bit record-size limit.  The reserved
record offset 0x454F46 (ASCII 'EOF') is avoided so ordinary IPS patchers do not
mistake a data record for the footer.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

EOF_OFFSET = 0x454F46
MAX_RECORD = 0xFFFF


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def changed_ranges(source: bytes, target: bytes):
    start = None
    for i, (a, b) in enumerate(zip(source, target)):
        if a != b and start is None:
            start = i
        elif a == b and start is not None:
            yield start, i
            start = None
    if start is not None:
        yield start, len(source)


def emit_record(out: bytearray, offset: int, data: bytes) -> None:
    if not 0 <= offset <= 0xFFFFFF:
        raise ValueError(f"IPS offset out of range: {offset:#x}")
    if offset == EOF_OFFSET:
        raise ValueError("IPS record would collide with EOF marker")
    if not 1 <= len(data) <= MAX_RECORD:
        raise ValueError(f"bad IPS record size: {len(data)}")
    out += offset.to_bytes(3, "big")
    out += len(data).to_bytes(2, "big")
    out += data


def make_patch(source: bytes, target: bytes) -> bytes:
    if len(source) != len(target):
        raise ValueError("source and target sizes differ")
    if len(source) > 0x1000000:
        raise ValueError("classic IPS cannot safely address ROMs above 16 MiB")

    out = bytearray(b"PATCH")
    for start, end in changed_ranges(source, target):
        pos = start
        while pos < end:
            # Never begin a record at the reserved EOF marker.  Fold that byte
            # into the preceding record when possible, otherwise start one byte
            # earlier and include the already-equal byte; patching it is harmless.
            if pos == EOF_OFFSET:
                pos -= 1
            size = min(MAX_RECORD, end - pos)
            if pos < start:
                size = min(MAX_RECORD, end - pos)
            # Also avoid the next chunk landing exactly on EOF_OFFSET.
            if pos < EOF_OFFSET < pos + size and pos + size == EOF_OFFSET:
                size -= 1
            emit_record(out, pos, target[pos:pos + size])
            pos += size
    out += b"EOF"
    return bytes(out)


def apply_patch(source: bytes, patch: bytes) -> bytes:
    if not patch.startswith(b"PATCH"):
        raise ValueError("not an IPS patch")
    out = bytearray(source)
    pos = 5
    while True:
        if patch[pos:pos + 3] == b"EOF":
            pos += 3
            break
        offset = int.from_bytes(patch[pos:pos + 3], "big")
        size = int.from_bytes(patch[pos + 3:pos + 5], "big")
        pos += 5
        if size == 0:
            rle_size = int.from_bytes(patch[pos:pos + 2], "big")
            value = patch[pos + 2]
            pos += 3
            out[offset:offset + rle_size] = bytes([value]) * rle_size
        else:
            out[offset:offset + size] = patch[pos:pos + size]
            pos += size
    if pos != len(patch):
        raise ValueError("unexpected trailing IPS data")
    return bytes(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--target", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--expected-source-sha256")
    args = ap.parse_args()

    source = args.source.read_bytes()
    target = args.target.read_bytes()
    source_sha = sha256(source)
    target_sha = sha256(target)
    if args.expected_source_sha256 and source_sha != args.expected_source_sha256.upper():
        raise SystemExit(
            f"source SHA-256 mismatch: {source_sha} != {args.expected_source_sha256.upper()}"
        )

    patch = make_patch(source, target)
    rebuilt = apply_patch(source, patch)
    if rebuilt != target:
        raise SystemExit("IPS round-trip verification failed")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(patch)
    print(f"source sha256 : {source_sha}")
    print(f"target sha256 : {target_sha}")
    print(f"patch bytes   : {len(patch)}")
    print(f"verified      : yes")
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()
