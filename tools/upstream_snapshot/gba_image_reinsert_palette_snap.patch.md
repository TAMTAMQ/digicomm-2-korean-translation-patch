# `gba_image_reinsert.py` project patch

실제 최종 빌드에서 사용한 파일은 `D:\trans\translation-assistant\tools\gba_image_reinsert.py`이며 SHA-256은 `E488F157E52D9D49353760D15948430C44039D6500FB01B27F1D17D3B6BF5C70`이다.

이번 `0b0dfec_240x160.png` / `0b388f4_240x160.png` 사용자 수정본 반영을 위해 아래 변경이 추가됐다.

## 1. 사용자 RGB PNG를 원본 8bpp 팔레트에 안전하게 스냅

```python
def snap_image_to_source_palette(rom: bytes, entry: dict,
                                 image: Image.Image) -> tuple[Image.Image, int]:
    image = image.convert("RGB")
    if image.size != (entry["width"], entry["height"]):
        raise ImageError(
            f"PNG size {image.size} != container {(entry['width'], entry['height'])}"
        )
    if entry["bytes_per_tile"] != 64:
        raise ImageError("palette snapping is currently limited to 8bpp images")

    palette = [
        tuple(rgb[:3])
        for rgb in ie.read_palette(
            rom, entry["palette"], max(1, entry["palette_count"])
        )
    ]
    unique_palette = list(dict.fromkeys(palette))
    palette_set = set(unique_palette)
    cache = {}
    source = image.load()
    out = Image.new("RGB", image.size)
    target = out.load()
    changed = 0
    for y in range(image.height):
        for x in range(image.width):
            colour = tuple(source[x, y][:3])
            if colour in palette_set:
                snapped = colour
            else:
                snapped = cache.get(colour)
                if snapped is None:
                    snapped = min(
                        unique_palette,
                        key=lambda candidate: (
                            (candidate[0] - colour[0]) ** 2
                            + (candidate[1] - colour[1]) ** 2
                            + (candidate[2] - colour[2]) ** 2
                        ),
                    )
                    cache[colour] = snapped
                changed += 1
            target[x, y] = snapped
    return out, changed
```

원본 `translated_png` 파일은 절대 덮어쓰지 않고, 빌드 메모리 안에서만 가장 가까운 **실제 선언 팔레트(`palette_count`) 범위**의 색으로 변환한다. 8bpp라는 이유로 256색까지 읽으면 다음 리소스 영역을 팔레트로 오인할 수 있으므로 반드시 선언된 색 수만 사용한다.

## 2. `_apply_one()` 옵션 추가

`_apply_one(..., snap_to_source_palette: bool = False)`를 추가하고, true일 때 `snap_image_to_source_palette()`를 먼저 적용한다. 빌드 보고서에는 아래 필드를 기록한다.

```python
"source_palette_snapped": snap_to_source_palette,
"source_palette_snapped_pixels": palette_snapped_pixels,
```

## 3. CLI / trusted-reference 우회

CLI에 반복 가능한 옵션을 추가했다.

```text
--snap-to-source-palette OFFSET
```

해당 offset은 사용자 PNG를 반드시 새로 인코딩해야 하므로 trusted-reference 복사 shortcut 대상에서 제외한다. 이후 `_apply_one()`에 offset별 boolean을 전달한다.

프로젝트 정책은 `assets/build_invariants.json`의 `snap_to_source_palette_offsets`가 소유하고, `gba_build.py`가 이 목록을 위 CLI로 전달한다.

## 4. 최종 사용 대상

- `0xB0DFEC` — `translated_png/0b0dfec_240x160.png` → `fitted_png/0b0dfec_240x160.png`
- `0xB388F4` — `translated_png/0b388f4_240x160.png` → `fitted_png/0b388f4_240x160.png`, source codec 3 유지

최종 원본-ROM 클린 빌드에서 두 이미지 모두 in-place 역렌더 검증 PASS, relocation 0이다.
