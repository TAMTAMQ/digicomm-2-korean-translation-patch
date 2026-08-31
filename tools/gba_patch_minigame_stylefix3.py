from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "build" / "digicomm_nyo_kr_message_minigame_stage.gba"
DONOR = ROOT / "build" / "digicomm_nyo_kr_minigame_stylefix2.gba"
META = ROOT / "build" / "digicomm_nyo_kr_minigame_stylefix2.json"
OUT = ROOT / "build" / "digicomm_nyo_kr_minigame_stylefix3.gba"
REPORT = ROOT / "build" / "digicomm_nyo_kr_minigame_stylefix3.json"


def unpack_4bpp(data: bytes, width: int, height: int) -> list[list[int]]:
    pixels: list[int] = []
    for value in data:
        pixels.extend((value & 0x0F, value >> 4))
    if len(pixels) != width * height:
        raise ValueError((len(pixels), width, height))
    return [pixels[y * width : (y + 1) * width] for y in range(height)]


def pack_4bpp(rows: list[list[int]]) -> bytes:
    flat = [value for row in rows for value in row]
    if len(flat) % 2:
        raise ValueError("odd 4bpp pixel count")
    return bytes(flat[i] | (flat[i + 1] << 4) for i in range(0, len(flat), 2))


def assemble_status_group(rom: bytes, offsets: list[int]) -> list[list[int]]:
    tiles = [unpack_4bpp(rom[off + 2 : off + 66], 8, 16) for off in offsets]
    return [tiles[0][y] + tiles[1][y] + tiles[2][y] for y in range(16)]


def split_status_group(rows: list[list[int]]) -> list[bytes]:
    return [pack_4bpp([row[x0 : x0 + 8] for row in rows]) for x0 in (0, 8, 16)]


def high_contrast_status(rows: list[list[int]]) -> list[list[int]]:
    # stylefix2의 형태는 유지하되 면을 흰색(F), 외곽을 원본 상태 팔레트의
    # 진한 남색(5)으로 강제해 희미한 중간톤을 없앤다.
    h, w = len(rows), len(rows[0])
    mask = [[rows[y][x] != 0 for x in range(w)] for y in range(h)]
    out = [[0 for _ in range(w)] for _ in range(h)]

    # 글자 2개가 서로 붙지 않게 좌/우 영역을 별도로 외곽 처리한다.
    regions = ((0, 12), (12, 24))
    for x0, x1 in regions:
        for y in range(h):
            for x in range(x0, x1):
                if not mask[y][x]:
                    continue
                out[y][x] = 0xF
                for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and x0 <= nx < x1 and not mask[ny][nx]:
                        out[ny][nx] = 0x5
    return out


def high_contrast_date(tile: bytes) -> bytes:
    rows = unpack_4bpp(tile, 8, 8)
    mask = [[rows[y][x] != 0 for x in range(8)] for y in range(8)]
    out = [[0 for _ in range(8)] for _ in range(8)]
    for y in range(8):
        for x in range(8):
            if not mask[y][x]:
                continue
            out[y][x] = 0xC  # 원본 날짜 팔레트의 흰 면
            for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < 8 and 0 <= nx < 8 and not mask[ny][nx]:
                    out[ny][nx] = 0x3  # 검정 외곽
    return pack_4bpp(out)


def thicken_12x12_1bpp(data: bytes) -> bytes:
    if len(data) != 24:
        raise ValueError(len(data))
    mask = [[False] * 12 for _ in range(12)]
    for y in range(12):
        value = int.from_bytes(data[y * 2 : y * 2 + 2], "big")
        for x in range(12):
            mask[y][x] = bool(value & (1 << (15 - x)))

    # 한 방향으로만 1px 보강해 획을 읽기 쉽게 하면서 12x12 안에서 뭉침을 제한한다.
    thick = [row[:] for row in mask]
    for y in range(12):
        for x in range(11):
            if mask[y][x] and not mask[y][x + 1]:
                thick[y][x + 1] = True

    out = bytearray()
    for y in range(12):
        value = 0
        for x in range(12):
            if thick[y][x]:
                value |= 1 << (15 - x)
        out += value.to_bytes(2, "big")
    return bytes(out)


def main() -> None:
    base = bytearray(BASE.read_bytes())
    donor = DONOR.read_bytes()
    meta = json.loads(META.read_text(encoding="utf-8"))
    if len(base) != 16 * 1024 * 1024 or len(donor) != len(base):
        raise RuntimeError("unexpected ROM size")

    # 실제 런타임 글꼴 경로도 항상 한글로 고정한다.
    for change in meta["status_font"]["changes"]:
        off = int(change["entry"], 16)
        base[off : off + 10] = donor[off : off + 10]

    date_font_offsets: list[int] = []
    for change in meta["date_font"]["changes"]:
        off = int(change["entry"], 16)
        base[off : off + 26] = donor[off : off + 26]
        date_font_offsets.append(off)

    # 12x12 일/차 글리프도 획을 1px 보강한다. 헤더 2바이트는 그대로 둔다.
    for off in date_font_offsets:
        payload = bytes(base[off + 2 : off + 26])
        base[off + 2 : off + 26] = thicken_12x12_1bpp(payload)

    # 캐릭터 머리 위 24x16 상태 라벨: stylefix2의 한글 형태를 가져와
    # 흰 면 + 진한 외곽선으로 다시 만든다.
    grouped: dict[str, list[int]] = {}
    for change in meta["state1_status_obj"]["changes"]:
        grouped.setdefault(change["group"], []).append(int(change["entry"], 16))

    for group, offsets in grouped.items():
        offsets.sort()
        if len(offsets) != 3:
            raise RuntimeError(f"status group {group}: {len(offsets)} entries")
        rows = assemble_status_group(donor, offsets)
        rendered = high_contrast_status(rows)
        for off, payload in zip(offsets, split_status_group(rendered)):
            # 코드 2바이트는 원본과 동일하게 유지하고 픽셀 64바이트만 교체한다.
            base[off + 2 : off + 66] = payload

    # 상단 일/차 8x8 OBJ도 희미한 중간톤 대신 흰 면 + 검정 외곽선으로 고정한다.
    for change in meta["state1_date_obj"]["changes"]:
        off = int(change["tile"], 16)
        base[off : off + 32] = high_contrast_date(donor[off : off + 32])

    # 미니게임 스테이지 정의의 블랙 게이머즈 43개도 직전 정상 번역을 유지한다.
    for item in meta["black_gamers_stage_labels"]:
        off = int(item["offset"], 16)
        size = int(item["slot_bytes"])
        base[off : off + size] = donor[off : off + size]

    OUT.write_bytes(base)

    status_offsets = sorted(off for offsets in grouped.values() for off in offsets)
    report = {
        "purpose": "high-contrast Korean minigame status/date labels",
        "base": str(BASE),
        "donor": str(DONOR),
        "output": str(OUT),
        "sha256": hashlib.sha256(base).hexdigest().upper(),
        "status_obj_entries": [hex(x) for x in status_offsets],
        "date_obj_tiles": [change["tile"] for change in meta["state1_date_obj"]["changes"]],
        "date_font_entries": [hex(x) for x in date_font_offsets],
        "black_gamers_labels": len(meta["black_gamers_stage_labels"]),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
