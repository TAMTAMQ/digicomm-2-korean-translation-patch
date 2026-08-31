# 개발 / 직접 수정 참고

이 저장소의 주 목적은 **한국어 패치 배포**입니다. 이 문서는 저장소에 함께 공개된 번역 텍스트와 도구를 이용해 패치를 분석하거나 번역을 수정하려는 사람을 위한 부가 개발 자료입니다.

번역 수정이 목적이라면 먼저 `TRANSLATION_GUIDE.md`를 읽으세요.

## 1. 주요 파일

- `README.md` — 배포 안내와 패치 개요
- `TRANSLATION_GUIDE.md` — AI 번역 기반 번역을 수정/검수할 때의 필수 기준
- `assets/translation/GLOSSARY.md` / `glossary.tsv` — 고유명사/말투/용어
- `assets/translation/messages.json` — 메인 대사 원문/번역/위치 정보
- `assets/translation/ui_strings.json` / `ui_strings_ko.json` — raw UI 추출/번역
- `assets/translation/recovered_messages_ko.json` — 일반 추출에서 빠졌던 복구 메시지
- `STATUS.md` — 구현·실험·버그 수정 상세 이력

`STATUS.md`는 작업 일지 성격이 강하므로 상단 현재 상태와 최신 날짜 항목을 우선 확인하세요.

## 2. 원본 ROM 기준

```text
SHA-256: 3A098B5963DAF8BF38D67780F95325158FE09783980B1B74FA24BA02ECD16A5C
크기:    16,777,216 bytes
게임코드: BDKJ
```

오프셋 기반 작업이 많으므로 다른 덤프/리비전에서는 같은 결과를 보장할 수 없습니다.

## 3. 번역 데이터

최종 작업 기준:

- 재삽입 가능한 메시지: **4,669 / 4,669 번역**
- recovered 메시지: **142개**
- raw UI 적용 항목: **311개**
- 기본 12x12/10x10 한글 매핑: **2,350 음절**

`messages.json`의 핵심 필드:

- `mcm_offset` / `mcm_offset_hex` — 메시지 컨테이너 위치
- `header` — 레코드 내부 위치
- `text` — 일본어 원문
- `translation` — 현재 한국어 번역
- `reinsertable` — 재삽입 대상 여부

번역을 고칠 때는 `translation`만 보고 판단하지 말고 반드시 `text`와 실제 장면을 함께 확인하세요.

## 4. 텍스트 추출

보존된 추출기:

```bash
python tools/upstream_snapshot/gba_text_extract.py \
  --rom "path/to/original.gba" \
  --output "messages_extracted.json"
```

기존 `messages.json`과 새 추출 결과를 비교할 때는 `mcm_offset` + `header`를 우선 식별자로 사용합니다.

메시지 스트림 파서는 `tools/upstream_snapshot/gba_message_stream.py`에 있습니다. 이 프로젝트에서 확인된 인라인 제어바이트와 일부 예외 처리가 반영되어 있습니다.

## 5. 도구 구조

개발 당시 실제 공용 GBA toolkit은 프로젝트 바깥의 다음 경로를 사용했습니다.

```text
D:\trans\translation-assistant\tools
```

`tools/upstream_snapshot/`에는 이 프로젝트 작업 중 변경된 핵심 공용 파일과 실제 사용본의 SHA-256 기록을 남겨 두었습니다. 기준은 `tools/upstream_snapshot/MANIFEST.md`입니다.

이 공개 저장소는 ROM, 이미지, 폰트 바이너리를 포함하지 않으므로 현재 상태만으로 최종 ROM을 완전히 one-click 재현하는 독립 SDK는 아닙니다. 텍스트 추출/분석/번역 수정 및 도구 참고가 주 용도입니다.

## 6. 그래픽 관련

로컬 최종 작업에서는 일반 이미지, 자막 350장, 카드/메뉴 OBJ 등도 한글화했습니다. 하지만 원본/번역 이미지 파일은 공개 Git에 포함하지 않습니다.

대신 다음 텍스트 메타데이터는 남겨 둡니다.

- `assets/build_invariants.json`
- `assets/captions_index.json`
- `assets/images_index.json`
- 이미지/OBJ 관련 manifest/spec

그래픽 작업을 다시 하려면 본인이 소유한 원본 ROM에서 자산을 다시 추출한 뒤 로컬에서 작업해야 합니다.

## 7. 이미 해결된 주요 문제

중복 조사 방지를 위해 핵심만 적습니다.

- raw MCM 첫 바이트 `0x03`을 압축 헤더로 잘못 판단하던 문제
- 메시지 내부 인라인 제어바이트 때문에 추출이 끊기던 문제
- 카드 상세 종류 라벨 raw 문자열 경로
- 이름 입력 2단계 받침 처리
- 미니게임 캐릭터명 전용 8x8 글리프 누락 (`홋`, `갓`, `뭐`, `밀`)
- 명함 보기 하단 버튼 최종 문구 `취소 / 삭제 / 정렬`

상세 오프셋과 구현은 `STATUS.md`에서 검색하세요.

## 8. 최소 정적 검사

```bash
python -m compileall -q tools
git diff --check
git status --short
```

Git 후보에 ROM, raw 바이너리, 이미지, 폰트 바이너리, 세이브/스테이트가 없는지도 확인하세요.
