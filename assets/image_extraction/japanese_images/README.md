# Japanese image translation set

`assets/images` 전체에서 화면에 일본어가 들어간 비캡션 이미지를 번역 작업용으로 모은 폴더다.

- `png/`: 원본 PNG 사본. 원본 `assets/images`는 변경하지 않는다.
- `contact_sheets/`: 검수 및 번역 진행 확인용 시트.
- `manifest.tsv`: 스프레드시트에서 보기 쉬운 목록.
- `manifest.json`: 이미지 메타데이터와 선별 기준을 포함한 목록.

## 선별 범위

- 전체 이미지: 1,257장
- `captions_index.json`과 오프셋이 같은 caption 이미지: 350장 제외
- 직접 검수한 비캡션 이미지: 907장
- 1차 일본어 후보 이미지: 137장
- 사용자 선별 후 남은 번역 대상: 111장

`category`는 다음 의미다.

- `ui_text`: 메뉴, 안내문, 결과 화면, 로고 등의 일본어
- `text_fragments`: 여러 조각으로 분리된 간판/역명 등의 일본어
- `scenery_text`: 배경과 일러스트 안에 포함된 일본어 간판
- `font_glyphs`: 일본어 글리프 및 글리프 아틀라스

번역본은 원본 파일명과 크기, 색상 형식, 투명 영역을 유지해야 한다.
