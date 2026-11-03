# 데이터 계약 (v0.1, 2026-11-02 초안)

파이프라인(`pipeline/`)이 만들고 사이트(`site/`)가 읽는 JSON 형식이다. 서버가 없으므로 "API"는 이 파일 형식을 말한다.
**형식을 바꿀 때는 이 문서를 먼저 고치고**, 파이프라인과 사이트를 같이 고친다. JSON 키는 영어, 화면 문구는 한국어로 쓴다.

## 1. `site/data/cards.json`

```json
{
  "schema_version": "0.1",
  "generated_at": "2026-11-05T09:00:00+09:00",
  "boards": [ { "id": "mju-general", "name": "메인 일반공지", "list_url": "https://..." } ],
  "cards": [ Card ]
}
```

### Card

| 키 | 형식 | 설명 |
|---|---|---|
| `id` | string | 대회 고유 id. 같은 대회가 여러 게시판에 올라와도 하나 (중복 병합 결과) |
| `title` | string | 대회명 |
| `organizer` | string | 주관 |
| `scope` | `"교내"` \| `"외부"` | 교내 대회 / 학교 게시판에 홍보된 외부 공모전 |
| `sources` | Source[] | 이 대회가 올라온 공지들. 첫 번째가 최신 |
| `fields` | object | 항목별 값. 아래 Field 목록 |
| `deadline_history` | Change[] | 마감 변경 이력 (없으면 빈 배열) |
| `meta` | object | `prompt_version`, `extract_model`, `cross_model`, `checked_at` |

**Source:** `{ "board_id", "url", "title", "posted_at": "YYYY-MM-DD" }`

**Change:** `{ "field": "apply_end", "old": "2026-09-30", "new": "2026-10-23", "old_source": url, "new_source": url, "checked_at": "YYYY-MM-DD" }`

### Field (모든 항목 공통 형태)

```json
{
  "value": <항목별 형식, 정보가 없으면 null>,
  "status": "ai | auto_verified | needs_check | human_verified | missing",
  "evidence": [ { "quote": "원문 그대로 짧게", "location": "제목 | 본문 | 이미지 | 첨부", "file": "이미지·첨부 파일명(선택)", "source": url } ],
  "note": "애매하거나 충돌한 이유 (선택)"
}
```

| status | 화면 표시 | 뜻 |
|---|---|---|
| `ai` | AI 정리 | 추출만 됨, 검증 전 |
| `auto_verified` | 자동 검증 통과 | 근거 문장 실재, 모델 일치, 형식·상식 검사 통과 |
| `needs_check` | 원문 확인 필요 | 검증 실패, 정보 충돌, 해석 애매 |
| `human_verified` | 검수 완료 | 팀이 원문과 대조 |
| `missing` | 정보 없음 | 공지에 없음 (`value`는 null) |

**핵심 항목 규칙:** `apply`(신청기간)와 자격 항목(`campus`, `grades`, `enrollment`, `team`)은 `human_verified`가 아니면 화면에 **확인 전**으로 표시한다.

### fields 목록

| 키 | value 형식 | 예 |
|---|---|---|
| `apply` | `{ "start": "YYYY-MM-DD HH:MM" \| null, "end": ..., "time_given": bool }` | 시각이 없으면 `time_given: false`, 시각을 임의로 채우지 않는다 |
| `stages` | `[ { "name": "신청" \| "출품" \| "발표" \| ..., "end": "YYYY-MM-DD HH:MM", "time_given": bool } ]` | 마감이 여러 단계인 대회 (캘린더 분리 등록용) |
| `campus` | `["인문", "자연"]` 또는 `"any"` | `"any"`는 대상 문장이 있는데 캠퍼스로 나누지 않거나 "제한 없음"이 명시된 경우 (11/3 보완, 근거는 그 대상 문장). 대상 문장 자체가 없으면 `missing` |
| `grades` | `[1, 2, 3, 4]` 또는 `"any"` | `campus`와 같은 규칙. 예: "정규학기 학부 재학생" → `"any"` |
| `enrollment` | `{ "allowed": ["재학"], "excluded": ["휴학", "수료", "초과학기", "졸업유예"] }` | 대상 문장은 있는데 학적 언급이 없으면 두 목록 모두 빈 배열 (= 제한 문구 없음). 대상 문장이 없으면 `missing` |
| `team` | `{ "min": null, "max": 3, "individual_ok": true }` | 공지에 없는 숫자는 null ("3인 이내" → min null) |
| `deliverables` | `{ "tags": ["기획서·제안서", "영상", "에세이·수기", "코드·개발", "발표", "디자인", "현장 참가", "기타"], "text": "영상 3~5분 + 대본" }` | 제출물 배지. 태그 목록은 추출 프롬프트 v0.2와 같다 |
| `benefits` | string | 상금·상장 |
| `mileage` | `{ "text": "예선 참가자 전원 창의 10점", "area": "창의" \| null, "points": 10 \| null }` | `points`는 참가자 기준. 수상자 추가 점수는 `text`에만 |
| `how_to_apply` | `{ "text": "신청 방법 요약", "url": "https://..." \| null }` | 링크는 http·https만 |
| `contact` | `{ "dept": "", "email": "", "phone": "" }` | 문의 초안에 사용. 공지에 있는 값만 |

## 2. 조건 (자연어 해석기 출력, 버튼 입력과 같은 형식)

`site/js/parser.js`가 문장을 이 형식으로 바꾸고, 학생은 칩으로 확인·수정한다. 확장 시 AI 해석기로 바꿔도 이 형식은 유지한다.

```json
{
  "campus": "인문 | 자연 | null",
  "grade": 1,
  "is_transfer": false,
  "enrollment": "재학 | 휴학 | 수료 | 초과학기 | 졸업유예 | null",
  "participation": "개인 | 팀 | null",
  "deliverables": ["영상"],
  "period": "마감임박 | 이번주 | 이번달 | null",
  "unknown_terms": ["해석하지 못한 표현"]
}
```

`null`은 학생이 말하지 않은 조건이다. 판정에서 "모름"으로 다룬다.

## 3. 판정 결과 (`site/js/rules.js`)

```json
{ "card_id": "...", "result": "eligible | team_needed | check | ineligible",
  "reasons": [ { "field": "campus", "message": "인문캠퍼스 재학생만 참가 가능", "quote": "근거 문장" } ] }
```

| result | 화면 | 판정 순서 |
|---|---|---|
| `ineligible` | 불가 | 1. 근거가 있는 자격 조건에 학생 조건이 맞지 않음 |
| `check` | 확인 필요 | 2. 판정에 필요한 항목이 `missing`·`needs_check`·확인 전이거나, 학생 조건이 `null` |
| `team_needed` | 팀 구성 시 가능 | 3. 개인 지원인데 `team.individual_ok`가 false |
| `eligible` | 신청 가능 | 4. 위에 해당하지 않음 |

**안전 원칙:** 확실하지 않으면 `eligible`을 내지 않는다. "불가인데 가능으로 판정"(위험 오판)을 가장 나쁜 오류로 보고 따로 센다.

## 4. 바뀔 예정

- `site/data/log.json`(제작 기록 탭: 프롬프트 버전, 정확도) 형식은 11/7까지 정한다.
