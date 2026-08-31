# tools

이 폴더는 디지커뮤니케이션 2 한국어 번역 프로젝트에서 사용한 Python 도구를 공개용으로 보존합니다.

## 구성

루트 `tools/*.py`:

- 프로젝트 전용 미니게임/상태 UI 패처 및 검증 도구
- `make_ips_patch.py` — 로컬에서 IPS 생성/round-trip 검증
- `mbm.py` — MBM 관련 유틸리티

`tools/upstream_snapshot/`:

- 원래 `D:\trans\translation-assistant\tools`의 공용 GBA toolkit에서 이 프로젝트 작업 중 수정된 핵심 파일의 보존본
- `MANIFEST.md`에 개발 당시 실제 실행 파일의 SHA-256과 역할 기록

## 텍스트 작업에 중요한 파일

- `upstream_snapshot/gba_text_extract.py` — 원본 ROM에서 메시지 후보 추출
- `upstream_snapshot/gba_message_stream.py` — 게임 메시지 스트림 파서
- `upstream_snapshot/gba_build.py` — 최종 통합 빌드에서 사용한 빌드 로직의 프로젝트 보존본
- `upstream_snapshot/gba_nameentry_font8.py` — 8x8 한글 글리프 확장
- `upstream_snapshot/gba_nameentry_batchim.py` — 이름 입력 받침 처리

## 중요한 제한

공개 저장소에서는 이미지/폰트/원본 게임 바이너리를 의도적으로 제외합니다. 따라서 `gba_build.py`의 모든 그래픽/폰트 단계를 그대로 실행하려면 로컬에서 원본 ROM과 필요한 자산을 직접 준비해야 합니다.

`upstream_snapshot/gba_build.py`는 개발 이력과 프로젝트별 변경을 보존하기 위한 스냅샷입니다. 완전한 공용 toolkit 전체를 복제한 폴더는 아니므로, 새 환경에서 최종 ROM 전체를 재현하려면 누락된 공용 보조 모듈을 준비하거나 필요한 부분을 현재 저장소 구조에 맞게 정리해야 합니다.

반면 **번역문 수정/메시지 구조 분석**은 `gba_text_extract.py`, `gba_message_stream.py`, `assets/translation/` 자료를 중심으로 독립적으로 이어갈 수 있습니다.

## 라이선스

이 프로젝트가 직접 작성/수정한 도구 코드는 루트 `LICENSE`의 MIT License를 따릅니다. 제3자 라이브러리나 게임 데이터에는 적용되지 않습니다. 자세한 내용은 `../LICENSES.md`를 확인하세요.
