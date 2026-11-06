# 정답표 작성 기록 (#7)

## 대상 (17건)

| 구분 | contest_id |
|---|---|
| 예선 조사 기간(9/13~10/13) 12건 | ai-literacy-2026, whatssup-2026-2, history-video-5, report-2026, speakup-7, startup-idea-2026, student-major-design-2026, evc-13-humanities, evc-13-nature, market-ai-video-2026, mjp-essay-31, energy-capstone-1 |
| 10/14 이후 신규 5건 | startup-story-pt-2026, abix-hackathon-2026, biopharm-idea-2026, disaster-ai-hackathon-2026, kspo-public-data-2026 |

- 예선 12건은 작업 폴더 `docs/02_조사_공지목록.md`가 저장소에 없어 조건(기간, 교내 게시판, 9.30 vs 10.23 사례 포함)으로 다시 골랐다. 원본 목록과 다르면 맞춘다.
- 공지 목록 전체는 `docs/survey/notices_2026.csv`.

## 결과

| flag | 줄 수 |
|---|---|
| found | 214 |
| missing | 19 |
| needs_check | 14 |
| conflict | 8 |

- 본문·제목 근거 176개는 원문과 글자 대조 통과 (공백 정규화 후, 2026-11-03).
- 이미지 근거 61줄은 포스터를 보고 옮겨 적었다.
- 읽지 못한 첨부: WHAT-SSUP 세부안내문(hwp), 의약품 경진대회 개요서(pdf), AI 리터러시 학생 안내문(pdf).

## README에 없어 정한 표기 (확인 필요)

| 경우 | 쓴 값 |
|---|---|
| 팀 인원 숫자 없이 개인 가능만 명시 | `~, 개인 가능` |
| 하루짜리 일정(발표·시상) | `발표: ~ 2026-11-23` (끝 날짜만) |
| 문의 이메일이 접수란에만 있음 | 부서 공용 메일만 contact에 넣고 note에 표시. 담당자 개인 접수 메일은 넣지 않음 |
| 마감 뒤 같은 공지를 다시 올림 | kind는 최신 공지 성격(모집)을 따르고 note에 재게시라고 씀 |
