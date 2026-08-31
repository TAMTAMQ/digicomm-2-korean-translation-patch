# 얇은물마루 v1.1

- 공식 저장소: https://github.com/BinRecycle/ThinMulmaru
- 공식 릴리스: https://github.com/BinRecycle/ThinMulmaru/releases/tag/v1.1
- 버전: v1.1 (2026-05-26)
- 제작: BinRecycle
- 기반 폰트: 물마루(Mulmaru)
- 라이선스: SIL Open Font License 1.1 (`LICENSE`)
- 사용 파일: `font/ThinMulmaru.ttf` (가변폭판)

## 무결성

- `ThinMulmaru-v1.1.zip` SHA-256: `CEE36C933C0A4FE54EC300E800EADC3B38B9CA4D7920D94CA872444FBDCF5D85`
- `font/ThinMulmaru.ttf` SHA-256: `267478C4398E4CAE8296C3893132D8F0CD7CE9383C917A1004597DFD9445BEAF`
- `LICENSE` SHA-256: `01A1B39DB27B58BD0D7CD6453F12FBE47F616B602652B887E2EE03704C0783E7`

## 이 프로젝트의 래스터 설정

12px, FreeType 인덱스 0, 임계값 110, `dy=0`을 사용한다. KS X 1001
완성형 2,350자의 실제 픽셀 경계를 검사한 결과 모두 게임의 11x12 잉크 셀
안에 들어오고, 렌더 결과가 빈 글리프인 문자는 0개였다.

`ThinMulmaru Mono.ttf`는 12px에서 36자가 셀 오른쪽 경계(x=11)를 넘어가므로
사용하지 않는다. 게임 빌더가 각 글리프를 같은 11x12 셀에 넣기 때문에 가변폭판을
사용해도 ROM 안에서는 고정 셀 글꼴로 동작한다.
