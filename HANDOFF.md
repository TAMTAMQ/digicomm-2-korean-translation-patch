# 개발 인수인계

이 문서는 새 작업자가 이 저장소를 받아 **텍스트 번역/추출/도구 개발을 바로 이어가기 위한 현재 기준**을 요약합니다.

## 1. 가장 먼저 볼 파일

1. `README.md` — 저장소 공개 범위와 기본 작업법
2. `LICENSES.md` — 코드/번역문/게임 원문 권리 구분
3. `assets/translation/GLOSSARY.md` / `glossary.tsv` — 고유명사/말투/용어
4. `assets/translation/messages.json` — 메인 대사 번역 작업 파일
5. `assets/translation/ui_strings.json` / `ui_strings_ko.json` — raw UI 추출/번역
6. `assets/translation/recovered_messages_ko.json` — 일반 추출에서 빠진 복구 메시지
7. `STATUS.md` — 지금까지의 조사/실험/버그 수정 상세 이력

`STATUS.md`는 매우 긴 작업 일지입니다. **상단 현재 상태와 최신 날짜 항목을 먼저 보고**, 오래된 SHA나 당시 미착수 기록은 역사 기록으로 취급하세요.

## 2. 현재 텍스트 기준

- 재삽입 가능한 메시지: **4,669 / 4,669 번역 완료**
- recovered 메시지: **142개**
- raw UI 최종 적용 항목: **311개**
- `ui_strings.json`의 나머지 항목 중 상당수는 제작진 크레딧, 이름 금칙어, 입력 테이블, 추출 파편 등 의도적으로 번역하지 않은 데이터
- 기본 12x12/10x10 한글 매핑: **2,350 음절**

`messages.json`의 핵심 필드:

- `mcm_offset` / `mcm_offset_hex` — 메시지가 들어 있는 MCM 위치
- `header` — 해당 레코드 내부 메시지 위치
- `text` — 추출 일본어 원문
- `translation` — 실제 한국어 번역
- `reinsertable` — 재삽입 대상 여부

`text`는 비교/문맥/검증을 위해 저장소에 남깁니다. 수정 작업은 기본적으로 `translation`을 변경하세요.

## 3. 원본 ROM 기준

대상 일본판 ROM SHA-256:

```text
3A098B5963DAF8BF38D67780F95325158FE09783980B1B74FA24BA02ECD16A5C
```

- 크기: `16,777,216 bytes`
- 게임 코드: `BDKJ`

오프셋 기반 작업이 많으므로 다른 덤프/리비전을 사용하면 안 됩니다.

## 4. Git에 포함되는 것 / 포함되지 않는 것

### 포함

- `tools/**/*.py` 소스코드
- `assets/**/*.json`, `*.tsv`, `*.txt`, `*.md`, `*.csv` 중 작업에 필요한 텍스트/메타데이터
- 번역문
- 추출된 일본어 텍스트 및 오프셋/매니페스트
- 구조 분석 노트
- 각 폰트의 README/라이선스 텍스트 (폰트 바이너리는 제외)

### 포함하지 않음

- ROM / BIOS
- ROM에서 잘라낸 raw 바이너리
- 세이브 / 세이브스테이트
- 원본/번역 이미지
- 폰트 바이너리
- IPS/BPS/xdelta
- 빌드 결과 ROM
- 에뮬레이터 캡처/분석 캐시

즉 **추출한 텍스트는 공개 가능하지만 실제 게임 데이터 파일은 공개하지 않는 정책**입니다.

## 5. 텍스트 추출

보존된 추출기:

```bash
python tools/upstream_snapshot/gba_text_extract.py \
  --rom "path/to/original.gba" \
  --output "messages_extracted.json"
```

기존 `messages.json`과 새 추출 결과를 비교할 때는 `mcm_offset` + `header`를 우선 식별자로 사용하세요.

메시지 스트림 파서는 `tools/upstream_snapshot/gba_message_stream.py`에 있습니다. 이 프로젝트에서 확인된 인라인 제어바이트 `0x03`, `0x1A`와 private heart 코드 등 예외가 반영되어 있습니다.

## 6. 번역 수정 시 주의점

- `$0`, `$1`, `$2`, `$3`, `%d` 등 치환 토큰 보존
- 줄바꿈 `\n` 보존/의도적 조정
- CP932/프로젝트 한글 매핑 기준의 **실제 인코딩 바이트 길이** 확인
- 고정 슬롯은 번역이 길면 주변 코드/데이터를 침범할 수 있으므로 임의 확장 금지
- 캐릭터 말투/고유명사는 용어집 우선
- `reinsertable=false` 항목은 추출 오탐 또는 비재삽입 자료일 수 있으므로 무조건 번역 대상으로 바꾸지 않기

## 7. 도구 구조

개발 당시 실제 공용 GBA toolkit은 프로젝트 바깥의:

```text
D:\trans\translation-assistant\tools
```

를 사용했습니다.

이 저장소의 `tools/upstream_snapshot/`에는 이 프로젝트 작업 중 변경된 핵심 공용 파일과 실제 사용 파일 SHA-256을 보존했습니다. `MANIFEST.md`가 기준입니다.

주의: 공개 저장소는 이미지/폰트 등 로컬 자산을 제외하므로 현재 그대로는 **최종 ROM 전체를 one-click 재현하는 독립 배포형 SDK가 아닙니다.** 텍스트 추출/번역 수정/구조 분석은 이어갈 수 있으며, 전체 빌드를 복구하려면 `gba_build.py`가 참조하는 공용 helper와 본인 원본에서 준비한 이미지/폰트 자산을 추가로 준비해야 합니다.

## 8. 이미 해결된 중요한 텍스트/폰트 문제

후속 작업자가 다시 같은 문제를 파지 않도록 핵심만 남깁니다.

- raw MCM 첫 바이트 `0x03`을 압축 헤더로 오인하던 문제 수정
- 메시지 내부 인라인 제어바이트 때문에 추출이 끊기던 문제 수정
- 카드 상세 종류 라벨 raw 문자열 경로 확인
- 이름 입력 2단계 받침 처리
- 미니게임 캐릭터명 전용 8x8 글리프 누락 (`홋`, `갓`, `뭐`, `밀`) 수정
- 명함 보기 하단 버튼 문구 최종 기준: `취소 / 삭제 / 정렬`

상세 오프셋과 구현은 `STATUS.md`에서 검색하세요.

## 9. 그래픽 관련 현황

로컬 최종 작업에서는 일반 이미지, 자막 350장, 카드/메뉴 OBJ 등도 한글화되어 있었지만 **이미지 파일은 이 Git 저장소에 올리지 않습니다.**

JSON/TSV 매니페스트와 `build_invariants.json`은 남겨 두므로 구조/오프셋/파일 목록은 확인할 수 있습니다. 그래픽 작업을 재개하려면 원본 ROM에서 이미지를 다시 추출하고 번역 이미지를 별도 로컬 자산으로 준비하세요.

공개 Git에 포함되지 않는 마지막 로컬 통합 빌드 기준 SHA-256은 작업 이력 확인용으로만 기록합니다:

```text
161F4B20DFFBDA4D8692327A42E1BFC5B090239ABF3BC192E467D07B3E77F2D2
```

이 SHA의 ROM 자체는 저장소에 포함하지 않습니다.

## 10. 커밋 전 최소 검사

```bash
python -m compileall -q tools
git diff --check
git status --short
```

추가로 Git 후보에 `*.gba`, `*.bin`, `*.png`, `*.ttf`, `*.ips`, 세이브/스테이트가 없는지 확인하세요.
