# pipeline

파이프라인이 백엔드 역할을 한다 (서버 없음). 하루 1회 팀장 PC에서 실행한다.

1. 분류: 새 글이 대회·공모전 공지인지 가린다
2. 추출: Claude Code 헤드리스(`claude -p`, JSON 스키마 강제)로 대회 카드 추출
3. 검증: 근거 문장 실재 대조, Gemini 교차 추출 비교, 형식·상식 검사
4. 병합: 여러 게시판의 같은 대회를 묶고 마감 변경 이력을 만든다
5. 출력: `site/data/cards.json` (형식은 `docs/api-contract.md`)
