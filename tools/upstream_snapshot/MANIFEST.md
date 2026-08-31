# Shared toolkit files used by the final build

최종 통합 ROM을 만들 때 실제로 사용한 상위 공용 toolkit 위치는 `D:\trans\translation-assistant\tools`였다. 아래는 2026-08-31에 이 프로젝트 작업으로 수정된 핵심 공용 도구의 최종 파일 해시다.

| 파일 | SHA-256 | bytes | 최종 수정 |
|---|---|---:|---|
| `gba_build.py` | `9AC3122DBEEB16EBA1DF550FFDBA4E86BF7A9227A837E1E66C54B01C78F7B1BC` | 47,498 | 2026-08-31 최종 |
| `gba_message_stream.py` | `216FF4CF9CB552425AE844D89AEF28F4BE6FDD5AEF52C5471EBC99F58C37539B` | 7,507 | 2026-08-31 01:19:43 |
| `gba_text_extract.py` | `0A395E95EFCB5E586EFDD806F81F58B5E1825662D147443CED2F45EBEE5D28A7` | 17,543 | 2026-08-31 01:20:01 |
| `gba_nameentry_batchim.py` | `60DD6E89564C2E2454042E2AFED4DB1B8369048C5B85E7C58A41988A4BACCE8D` | 24,612 | 2026-08-31 00:29:21 |
| `gba_nameentry_font8.py` | `0C573C1C02DB13A7F1A238356AB74BAD461494E082BEBCCEE24E22CE5B6C2990` | 7,590 | 2026-08-31 13:00:07 |
| `gba_patch_rom_buttons.py` | `BC77B12EFEF17C63E786A78CFC74DF84B9E8B00F1DCF0A3595E3577BAC251E20` | 15,021 | 2026-08-31 12:39:33 |
| `gba_fit_image_quality.py` | `CBA630085DA379B84661344FAFB2FA8FECC3AB7A8EFBAC54CB88FDEA2BF5D0DB` | 10,889 | 2026-08-31 최종 |
| `gba_image_reinsert.py` | `E488F157E52D9D49353760D15948430C44039D6500FB01B27F1D17D3B6BF5C70` | 69,067 | 2026-08-31 최종 |

`gba_build.py`, `gba_fit_image_quality.py`를 포함한 기존 핵심 파일은 이 폴더에 프로젝트 보존용 스냅샷을 두었다. `gba_image_reinsert.py`는 큰 공용 파일 전체를 중복 저장하는 대신 이번 프로젝트에서 추가한 팔레트 스냅 변경을 `gba_image_reinsert_palette_snap.patch.md`에 보존하고 실제 사용본은 위 SHA-256으로 고정한다. 개발 당시 실제 실행 파일의 정확한 식별 기준은 위 SHA-256이다.

중요: 로컬 개발에서는 `release/digicomm_nyo_kr_final_full.ips`도 생성했지만, 현재 공개 Git 정책은 **소스/추출 텍스트/메타데이터만 공개**이므로 IPS와 ROM은 저장소에 포함하지 않는다.
