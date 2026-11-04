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
| `apply` | `{ "start": "YYYY-MM-DD HH:MM" \| null, "end": ..., "time_given": bool }` | 시각이 없으면 `time_given: false`이고 값은 `"YYYY-MM-DD"`만. 시각을 임의로 채우지 않는다. `time_given`은 `end`의 시각 기준 |
| `stages` | `[ { "name": "신청" \| "출품" \| "발표" \| ..., "end": "YYYY-MM-DD HH:MM", "time_given": bool } ]` | 마감이 여러 단계인 대회 (캘린더 분리 등록용) |
| `campus` | `["인문", "자연"]` 또는 `"any"` | `"any"`는 대상 문장이 있는데 캠퍼스로 나누지 않거나 "제한 없음"이 명시된 경우 (11/3 보완, 근거는 그 대상 문장). 대상 문장 자체가 없으면 `missing` |
| `grades` | `[1, 2, 3, 4]` 또는 `"any"` | `campus`와 같은 규칙. 예: "정규학기 학부 재학생" → `"any"` |
| `enrollment` | `{ "allowed": ["재학"], "excluded": ["휴학", "수료", "초과학기", "졸업유예"] }` | 대상 문장은 있는데 학적 언급이 없으면 두 목록 모두 빈 배열 (= 제한 문구 없음). 대상 문장이 없으면 `missing` |
| `team` | `{ "min": null, "max": 3, "individual_ok": true \| false \| null }` | 공지에 없는 숫자는 null ("3인 이내" → min null). `individual_ok`가 null이면 개인 참가 가능 여부가 공지에 없거나 애매한 것이다 (판정에서 개인 참가 학생은 `check`) |
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

`judgeCard(card, conditions, { now })` → 아래 형식. `now`는 판정 기준 시각(기본은 지금, 테스트·목업은 고정 날짜). 날짜는 한국 시간으로 본다.

```json
{ "card_id": "...", "result": "eligible | team_needed | check | ineligible",
  "reasons": [ { "field": "campus", "outcome": "fail", "message": "인문캠퍼스 재학생만 참가 가능 (내 조건: 자연캠)",
                 "quote": "근거 문장 | null", "unverified": true } ] }
```

| result | 화면 | 판정 순서 (위에서부터 하나라도 해당하면 그 결과) |
|---|---|---|
| `ineligible` | 불가 | 1. 근거가 있는 핵심 항목에 학생 조건이 맞지 않음, 또는 신청 마감이 지남 |
| `check` | 확인 필요 | 2. 판정에 필요한 핵심 항목이 `missing`·`needs_check`·확인 전이거나, 대회가 제한하는 조건을 학생이 입력하지 않음(`null`), 또는 개인 참가 학생인데 `team.individual_ok`가 `null` |
| `team_needed` | 팀 구성 시 가능 | 3. 개인 참가(또는 참가 방식 미입력) 학생인데 `team.individual_ok`가 `false` |
| `eligible` | 신청 가능 | 4. 위에 해당하지 않음 |

**핵심 항목:** `apply`, `campus`, `grades`, `enrollment`, `team`. 그 밖의 항목은 판정에 쓰지 않는다 (제출물·기간은 #24 화면의 필터).

**항목 상태와 판정**

| 항목 `status` | 학생 조건과 맞음 | 맞지 않음 |
|---|---|---|
| `human_verified` | 통과 | `ineligible` |
| `ai`, `auto_verified`, 그 밖의 값 (확인 전) | `check` ("확인 전") | `ineligible` + `unverified: true` (화면에 "확인 전 정보" 표시) |
| `needs_check` | `check` | `check` (애매한 값으로 불가를 내지 않는다) |
| `missing` | `check` | `check` |

**항목별 규칙**

- `apply`: `end`가 지났으면 불가. 시각이 없으면(`time_given: false`) 마감일 다음 날부터 불가로 보고, **마감일 당일은 `check`**(시각 미기재). `end`가 없으면(상시 접수 등) `check`. 시작 전이면 결과는 바꾸지 않고 `outcome: "note"`로 "신청 시작 전"을 남긴다.
- `campus`·`grades`: `"any"`면 학생 조건과 상관없이 통과. 목록이면 학생 값이 목록에 있어야 한다. 학생 값이 `null`이면 `check`. 빈 목록이나 숫자가 아닌 학년은 추출 오류로 보고 `check` (불가로 읽지 않는다).
- `enrollment`: 학생이 고를 수 있는 학적(재학, 휴학, 수료, 초과학기, 졸업유예)으로 판정한다. 학생 학적이 `excluded`에 있으면 불가. `allowed`가 비어 있으면 통과, 있으면 학생 학적이 그 안에 있어야 하고 아니면 불가(초과학기생이 `재학`에 포함되는지는 애매하므로 `check`). **학생 조건으로 답할 수 없는 표현이 있으면 맞든 안 맞든 `check`:** `allowed`의 위 다섯 낱말 밖 표현(원문 표현, `편입생`, `대학원생` 등), `excluded`의 표준 낱말 밖 원문 표현(예: "외국인 유학생"). 편입생(`is_transfer: true`)은 `excluded`에 `편입생`이 있으면 불가, 편입 여부가 `null`이면 `check`. `excluded`의 `기존 수상자`·`대학원생`·`졸업`은 결과를 바꾸지 않고 `note`로 남긴다. 학생 학적이 `null`이면 학적 제한(`allowed`가 있거나 `excluded`에 위 다섯 낱말이 있음)이 없을 때만 통과.
- `team`: `individual_ok`가 `true`면 통과. `false`면 팀 참가 학생은 통과, 개인·미입력은 `team_needed`. `null`이면 팀 참가 학생은 통과, 개인·미입력은 `check`.

**reasons:** 결과를 정한 사유가 앞에 온다 (`fail` → `unknown` → `team` → `note` → `ok`). `outcome`은 `fail`·`unknown`·`team`·`note`·`ok`. `eligible`의 `ok` 사유는 화면의 "맞은 조건 요약"에 쓴다 (✓ 표시는 화면이 붙이고 메시지에는 넣지 않는다). 확인 전 항목의 `ok`·`team`은 `unknown`이 되고 메시지 끝에 "(확인 전)"이 붙는다. `quote`는 그 항목 근거의 첫 문장, 없으면 `null`.

**안전 원칙:** 확실하지 않으면 `eligible`을 내지 않는다. "불가인데 가능으로 판정"(위험 오판)을 가장 나쁜 오류로 보고 따로 센다. `eligible`은 핵심 항목 5개가 모두 `human_verified`이고 모두 학생 조건과 맞을 때만 나온다.

## 4. 바뀔 예정

- `site/data/log.json`(제작 기록 탭: 프롬프트 버전, 정확도) 형식은 11/7까지 정한다.
