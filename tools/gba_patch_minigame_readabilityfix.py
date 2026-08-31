from __future__ import annotations

import hashlib
import json
from pathlib import Path

from mbm import read_mbm

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "build" / "digicomm_nyo_kr_message_minigame_stage.gba"
GOOD = ROOT / "build" / "digicomm_nyo_kr_minigame_state1_objfix.gba"
META = ROOT / "build" / "digicomm_nyo_kr_minigame_state1_objfix.json"
OUT = ROOT / "build" / "digicomm_nyo_kr_minigame_readabilityfix.gba"
REPORT = ROOT / "build" / "digicomm_nyo_kr_minigame_readabilityfix.json"

B36_START = 0xB36C60
B36_END = 0xB38384
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
DATE_FONT = {"일": 0xDBA722, "차": 0xDBDD2A}
STATUS_GROUPS = {
    "호객": [0xD8178C, 0xD817CE, 0xD81810],
    "판매": [0xD81852, 0xD81894, 0xD818D6],
    "정돈": [0xD819DE, 0xD81A20, 0xD81A62],
    "회원": [0xD81AA4, 0xD81AE6, 0xD81B28],
}
DATE_TILES = {"일": 0xE3D228, "차": 0xE3D248}

# The source strings are 7-byte fixed-width forms such as よび+%d.  The prior
# Korean patch removed '+', making the rendered string one tile shorter.  When
# the state changes the old fourth tile can therefore survive and look like a
# second/previous status.  Keep the original width/control byte exactly.
RAW_STATUS = {
    "호객": (0x143370, bytes.fromhex("99bc88b62b2564")),
    "정돈": (0x143378, bytes.fromhex("94f48c722b2564")),
    "판매": (0x143380, bytes.fromhex("98e08ebd2b2564")),
    "회원": (0x143388, bytes.fromhex("99d194692b2564")),
}


def remap_date_face_darker(tile: bytes) -> bytes:
    """Keep the known-good date bitmap footprint, only darken its bright face.

    state1_objfix used palette indices 2/3/C.  Replacing C with the source date
    bevel tone B makes the face darker without changing a single on/off pixel,
    so this cannot create the thick white blobs that stylefix3 introduced.
    """
    if len(tile) != 32:
        raise ValueError(len(tile))
    out = bytearray()
    for value in tile:
        lo = value & 0x0F
        hi = value >> 4
        if lo == 0xC:
            lo = 0xB
        if hi == 0xC:
            hi = 0xB
        out.append(lo | (hi << 4))
    return bytes(out)


def nonzero_mask_4bpp(tile: bytes) -> tuple[bool, ...]:
    mask: list[bool] = []
    for value in tile:
        mask.extend(((value & 0x0F) != 0, (value >> 4) != 0))
    return tuple(mask)


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
    before = bytes(base)
    good = GOOD.read_bytes()
    meta = json.loads(META.read_text(encoding="utf-8"))
    if len(base) != 16 * 1024 * 1024 or len(good) != len(base):
        raise RuntimeError("unexpected ROM size")

    # Known-good point explicitly identified by the user.  Restore only the
    # graphics/font ranges from it; latest messages remain owned by BASE.
    base[B36_START:B36_END] = good[B36_START:B36_END]
    base[HUD_START:HUD_END] = good[HUD_START:HUD_END]

    for off in STATUS_FONT.values():
        base[off:off + 10] = good[off:off + 10]
    for off in DATE_FONT.values():
        base[off:off + 26] = good[off:off + 26]

    # Revert the later auto-redrawn 24x16 status art completely.  state1_objfix
    # is the last user-confirmed pre-regression reference, and its Korean art is
    # copied byte-for-byte rather than regenerated from a dilation algorithm.
    for offsets in STATUS_GROUPS.values():
        for off in offsets:
            base[off:off + 66] = good[off:off + 66]

    # Date fallback: preserve state1_objfix geometry exactly, but use a darker
    # face tone.  This addresses the original low-contrast request without
    # thickening or moving strokes.
    for off in DATE_TILES.values():
        src = good[off:off + 32]
        dark = remap_date_face_darker(src)
        if nonzero_mask_4bpp(src) != nonzero_mask_4bpp(dark):
            raise RuntimeError(f"date footprint changed at 0x{off:X}")
        base[off:off + 32] = dark

    # Restore the source-width status format strings.  Each slot is 8 bytes;
    # write 7 visible/control bytes and an explicit NUL terminator.
    raw_status_report = {}
    for label, (off, payload) in RAW_STATUS.items():
        if len(payload) != 7:
            raise RuntimeError((label, len(payload)))
        base[off:off + 8] = payload + b"\x00"
        raw_status_report[label] = {
            "offset": hex(off),
            "bytes": payload.hex(),
            "slot_bytes": 8,
        }

    # Keep the 43 stage-local Black Gamers labels from the known-good ROM.
    for item in meta["black_gamers_stage_labels"]:
        off = int(item["offset"], 16)
        size = int(item["slot_bytes"])
        base[off:off + size] = good[off:off + size]
        if bytes(base[off:off + size]) != bytes.fromhex(item["encoded"]):
            raise RuntimeError(f"black gamers mismatch at 0x{off:X}")

    # Hard regression guards for the main screen image.
    b36 = read_mbm(base, B36_START)
    if b36["tiles"] != 167:
        raise RuntimeError(f"B36 tile regression: {b36['tiles']}")
    if b36["ptrs"] != B36_PTRS:
        raise RuntimeError(f"B36 pointer regression: {b36['ptrs']}")
    if bytes(base[B36_START:B36_END]) != good[B36_START:B36_END]:
        raise RuntimeError("B36 does not match state1_objfix")
    if bytes(base[HUD_START:HUD_END]) != good[HUD_START:HUD_END]:
        raise RuntimeError("HUD does not match state1_objfix")

    for label, off in STATUS_FONT.items():
        if bytes(base[off:off + 10]) != good[off:off + 10]:
            raise RuntimeError(f"status font mismatch: {label}")
    for label, off in DATE_FONT.items():
        if bytes(base[off:off + 26]) != good[off:off + 26]:
            raise RuntimeError(f"date font mismatch: {label}")
    for label, offsets in STATUS_GROUPS.items():
        for off in offsets:
            if bytes(base[off:off + 66]) != good[off:off + 66]:
                raise RuntimeError(f"status art mismatch: {label} @ 0x{off:X}")
    for label, (off, payload) in RAW_STATUS.items():
        if bytes(base[off:off + 8]) != payload + b"\x00":
            raise RuntimeError(f"status width mismatch: {label}")

    # Latest messages are preserved by allowing changes only in owned graphics,
    # font, fixed-label, and the four status-format slots.
    allowed = [(B36_START, B36_END), (HUD_START, HUD_END)]
    allowed += [(off, off + 10) for off in STATUS_FONT.values()]
    allowed += [(off, off + 26) for off in DATE_FONT.values()]
    for offsets in STATUS_GROUPS.values():
        allowed += [(off, off + 66) for off in offsets]
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

    OUT.write_bytes(base)
    report = {
        "purpose": "preserve state1_objfix graphics, latest messages, source-width status strings, and darker date face",
        "base": str(BASE),
        "known_good": str(GOOD),
        "output": str(OUT),
        "sha256": hashlib.sha256(base).hexdigest().upper(),
        "b36": {
            "tiles": b36["tiles"],
            "ptrs": [hex(x) for x in b36["ptrs"]],
            "range_sha256": hashlib.sha256(base[B36_START:B36_END]).hexdigest().upper(),
        },
        "hud_range_sha256": hashlib.sha256(base[HUD_START:HUD_END]).hexdigest().upper(),
        "raw_status_slots": raw_status_report,
        "status_art": "byte-for-byte state1_objfix",
        "status_font": "byte-for-byte state1_objfix",
        "date_font": "byte-for-byte state1_objfix",
        "date_obj": "state1_objfix footprint; palette C -> B only",
        "black_gamers_labels": len(meta["black_gamers_stage_labels"]),
        "changed_bytes_vs_latest_message_base": changed_bytes,
        "unexpected_changed_bytes": 0,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
