# 추출 정확도 채점 (#9)

AI 추출 결과(프롬프트 v0.2 형식)를 사람이 만든 정답표(`eval/answer_key/`)로 채점한다. 모델 비교(#9), 프롬프트 개선, 결선 발표의 정확도 수치가 여기서 나온다. 채점 규칙은 `eval/answer_key/README.md` "채점 방식"을 그대로 따른다.

```
python eval/extraction/score.py eval/extraction/runs/<실행1> [<실행2> ...] --report eval/extraction/reports/<이름>.md
python -m unittest discover -s eval/extraction    # 채점기·기준선 변환기 시험
```

## 실행 폴더 (모델 하나 × 프롬프트 하나)

```
eval/extraction/runs/2026-11-06_sonnet_v0.2/
  meta.json             {"model": "sonnet", "prompt_version": "v0.2", "models_seen": [...], "totals": {...}}  (pipeline/extractor가 쓴다)
  outputs/<contest_id>.json   AI 출력 그대로 (schema.json 형식). contest_id는 정답표와 같게
  score.json            채점기가 만든다 (항목별 집계)
  judgments_todo.csv    채점기가 만든다 (사람이 판정할 칸)
```

`runs/`는 저장소에 올리지 않는다(AI 원본 출력에 공지 인용문과 정리 전 연락처가 들어 있다). 저장소에는 보고서(`reports/`)와 사람 판정(`judgments/`)만 올린다. 추출 실행(#14)이 `outputs/`를 채운다.

**채점 입력은 AI 추출 출력이다.** 내보낸 `site/data/cards.json`은 검수 전 문의 이메일을 비우는 등 공개용으로 고친 값이라 채점에 쓰지 않는다.

## 무엇을 세나

| 분류 | 뜻 |
|---|---|
| 정답 | 값이 같음 (표기 정규화 뒤). 정답이 확인 필요(`needs_check`·`conflict`)이고 AI도 확인 필요면 정답 |
| 값 틀림 | 둘 다 찾았는데 값이 다름 |
| 지어냄 | 정답은 공지에 없음(`missing`)인데 AI가 값을 냄 |
| 과신 | 정답은 확인 필요인데 AI가 값을 확정(`found`) |
| 놓침 | 공지에 있는데 AI가 비움 |
| 과잉 보류 | 정답은 찾을 수 있는데 AI가 확인 필요로 둠. 오류로 세지 않고 **확인 필요 비율**로 따로 보고 |
| 판정 전 | 뜻이 같으면 정답인 항목인데 표기가 달라 사람이 아직 판정하지 않음. 정확도 분모에서 뺀다 |
| 출력 실패 | 출력 파일이 없거나 깨짐, 또는 대회가 아니라고 판단 |

**정확도 = 정답 칸 / (전체 칸 - 판정 전).** 과잉 보류·출력 실패 칸은 오류 수에 넣지 않지만 정답도 아니라서 정확도를 낮춘다 (모든 칸을 보류하면 정확도가 0이 되도록). 출력 실패는 보고서에 대회 수로 따로 보여 준다.

- AI 출력 형식이 틀린 값(예: 캠퍼스 "서울")은 값 없음과 구분해 "(AI 출력 형식 오류)"로 채점한다. 정답이 `missing`이어도 정답으로 숨지 않는다.
- 출력 실패는 이유(파일 없음·읽지 못함·대회 아님으로 판단)를 보고서 "주의"에 남긴다.
- 정답이 `missing`인데 AI가 확인 필요로 두면서 값을 냈으면 지어냄으로 센다 (값을 냈으므로). 정답이 `found`일 때 확인 필요는 과잉 보류.
- 정답표에서 항목은 `found`인데 채점 단위 칸만 비어 있으면(문의처에 이메일이 없음, 신청 링크가 없음) 그 칸은 `missing`으로 본다.

- **자동 비교:** scope, kind, apply, stages, campus, grades, enrollment, team, 제출물 태그, 마일리지 점수, 문의 이메일. `인문,자연` = `any`, `1,2,3,4` = `any`. 단계는 날짜 순으로 정렬해 비교한다.
- **사람 판정:** title, organizer, benefits, 제출물 요약, 마일리지 영역·요약, how_to_apply. 표기가 같으면 판정 없이 정답.
- **위험 오류:** 중요 항목(apply, campus, grades, enrollment, team)에서 정답보다 더 많은 학생을 "가능"으로 만드는 오류. 마감이 더 늦음(날짜만 있는 마감은 그날 끝으로 봄), 캠퍼스·학년을 넓힘, 제외 대상을 빠뜨리거나 허용 목록을 넓힘, 최대 인원을 늘리거나 최소 인원을 줄임, 개인 불가를 가능으로. 중요 항목의 지어낸 값과, 정답 값이 없는 과신은 위험으로 센다(보수적으로).
- 숫자를 지어내지 않는다. 잴 대상이 없으면 "-", 실행 결과가 없으면 "측정 중".

## 사람 판정

1. 채점기를 돌리면 실행 폴더에 `judgments_todo.csv`가 생긴다 (`answer`·`ai` 열을 보고 `judgment`에 `정답` 또는 `오답`. 다른 값은 경고하고 버린다).
2. 채운 파일을 `eval/extraction/judgments/<실행 폴더 이름>.csv`로 저장하고 다시 채점한다. `judgments_todo.csv`에 채운 판정이 남아 있으면 덮어쓰지 않고 새 할 일을 `judgments_todo.new.csv`에 쓴다. 옮긴 뒤 두 파일을 지운다.
   - 판정은 판정할 때의 `ai` 값과 함께 저장된다. 다시 추출해 AI 값이 바뀌면 옛 판정을 쓰지 않고 다시 판정 전으로 돌린다.
3. 판정자는 그 대회 정답표를 쓰지 않은 사람이 좋다. 판정 기준은 "학생이 이 값을 보고 같은 행동을 하는가".

## 규칙 분석기 기준선

"AI 없이 수집기 규칙만 쓰면 몇 점인가"를 AI 실행과 같은 표로 잰다.

```
.\pipeline\launcher.bat analyze   # 수집기가 pipeline/data/analysis.json을 만든다 (pipeline/README.md)
python eval/extraction/rule_baseline.py pipeline/data/analysis.json eval/extraction/runs/<날짜>_rules
python eval/extraction/score.py eval/extraction/runs/<날짜>_rules eval/extraction/runs/<AI 실행> --report ...
```

- 정답표 `sources.csv`의 URL로 대회와 공지를 잇는다. 대회의 공지 중 하나라도 `analysis.json`에 없거나 두 번 있으면(어느 판인지 모름) 그 대회는 출력하지 않는다 (출력 실패로 채점. 규칙 성능이 아니라 수집 누락을 재게 되므로).
- 규칙 결과를 그대로 옮기고 없는 능력을 더하지 않는다.
  - 참가 조건(campus·grades·enrollment·team): 규칙이 단어 언급만 찾고 허용·제외를 정하지 않으므로 값 없이 확인 필요.
  - 신청 기간: 가장 늦게 기간을 적은 공지가 연장·변경 공지이고 규칙이 확정했으면 그 값(system.md 4절 (가)). 규칙이 확정하지 못한 공지(연도 없음·복수 일정)가 있으면 확인 필요. 같은 성격 공지끼리 다르면 conflict.
  - kind: 가장 늦은 공지의 규칙 분류. 결과 발표 연기·미분류는 확인 필요.
  - 이미지가 있는 공지에서 본문에 없으면 missing이 아니라 확인 필요 (이미지에 있을 수 있음).
  - `is_contest`는 규칙 유형에 대회·해커톤이 있을 때만 true.
- **규칙이 다루는 항목만 채점한다:** kind, apply, campus, grades, enrollment, team (`meta.json`의 `scored_items`). 다루지 않는 항목을 missing으로 내면 정답도 missing인 칸에서 공짜 정답을 얻어 기준선이 부풀기 때문이다. stages도 뺀다(규칙은 일정표를 읽지 못해 단계 목록이 완전한지 모른다). 그래서 전체 정확도는 분모가 달라 비교하지 않고, 중요 항목 줄과 항목별 줄로 비교한다.
- **확인 필요 비율과 같이 읽는다:** 값 없이 확인 필요로 두면 정답이 missing·확인 필요인 칸은 정답이 된다(AI도 같은 규칙). 규칙 기준선은 조건을 거의 다 보류하므로 정확도만 보면 안 된다.
- 출력은 채점용 부분 형식이다: 규칙이 다루는 항목만 있고, 근거 인용은 공백을 정리해 원문과 글자 단위로 같지 않다. 스키마 검증이나 내보내기 입력으로 쓰지 않는다.
- 같은 실행 폴더로 다시 변환하면 옛 출력을 지운다. 규칙 기준선 폴더가 아니면(AI 실행 등) 덮어쓰지 않고 멈춘다.

## 모델 비교 실행 순서 (#9)

`claude -p`는 **실행하는 사람의 Claude 로그인**으로 돈다. 회사 계정이나 다른 사람 계정으로 돌리지 않는다. 명령은 PowerShell, 저장소의 `pipeline/` 폴더 기준이다 (가상환경은 `pipeline/README.md` "Python 명령으로 실행").

**0. 준비**
- 정답표 PR(#41)이 main에 머지돼 있어야 한다 (`eval/answer_key/sources.csv`에 대회 17건).
- Claude Code 설치·로그인: `claude --version`이 나오고, `claude`를 한 번 열어 본인 계정으로 로그인했는지 확인한다.
- `ANTHROPIC_API_KEY`·`ANTHROPIC_BASE_URL` 같은 환경변수가 있으면 로그인 대신 그 계정으로 간다. 실행기는 이런 변수가 있으면 시작하지 않는다 (`Get-ChildItem Env:ANTHROPIC*`로 확인). 본인 API 키로 돌릴 때만 `--use-env-account`.
- 실행기는 도구를 모두 끄고 빈 임시 폴더에서 claude를 부른다 (공지에 숨은 지시가 PC의 파일·명령에 닿지 않게). 공지를 보내기 전에 공지 없이 한 번 불러 도구가 모두 꺼졌는지·로그인 계정인지 확인하고(`[확인]` 줄, 하루 상한에서 1번), 호출마다 다시 확인한다. **격리 옵션을 빼거나 바꾸지 않는다.** 실행기가 막히면 Claude Code를 업데이트(`claude update`)하거나 팀에 알린다.

**1. 정답표 공지 수집 확인** (claude를 부르지 않음)
```powershell
.\.venv\Scripts\python.exe -m collector run
.\.venv\Scripts\python.exe -m collector export
.\.venv\Scripts\python.exe -m extractor eval --model haiku --run-dir ..\eval\extraction\runs\check --dry-run
```
`[빠짐]`이 나온 대회는 공지가 수집 원문(`data/notices.json`)에 없거나(`없음`) 같은 주소가 두 번 있다(`중복`). 수집기는 게시판마다 최근 글만 보므로 8~9월 공지나 아직 등록되지 않은 게시판(#6) 공지는 빠질 수 있다. 빠진 채로 돌리면 그 대회는 출력 실패로 채점되어 모델끼리 비교가 흐려진다. 모두 `[준비]`가 될 때까지 수집 쪽에서 채운다.

**2. 한 건으로 시험**
```powershell
$day = Get-Date -Format yyyy-MM-dd
.\.venv\Scripts\python.exe -m extractor eval --model haiku --run-dir "..\eval\extraction\runs\${day}_haiku_v0.2" --contest ai-literacy-2026
```
- `[뽑음]`이면 `outputs\ai-literacy-2026.json`이 생긴다. `meta.json`의 `models_seen`에 실제 모델 ID가, `totals`에 토큰 수가 남는다.
- `[실패]`·`[멈춤]`이면 `logs\ai-literacy-2026.json`의 `error`(또는 화면의 그 줄)를 팀 채널에 알린다. 출력·공지 내용은 붙이지 않고, 올리기 전에 PC 경로·사용자 이름·서버 이름이 들어 있지 않은지 본다. 자주 나올 수 있는 것:
  - `결과가 없음 (... unknown option ...)`: 이 CLI 버전에 없는 옵션. `claude update` 뒤 다시.
  - `공지를 보내기 전 확인에서 멈춤: 격리 확인 실패 ...` 또는 `계정 확인 실패`: 끈 도구가 켜져 있거나 로그인이 아닌 키로 돈다. 공지는 보내지 않았다. 그대로 팀에 알린다 (옵션을 지우지 않는다).
  - `claude.CMD는 명령줄이 ...`: `--claude`로 `claude.exe` 경로를 준다.
- 실행·설정 문제로 같은 이유가 3번 연달아 나면 실행기가 스스로 멈춘다 (하루 상한을 아끼려고). 모델이 형식에 맞게 못 답한 것(`스키마 모양`, `오류 응답`)은 비교 대상이라 멈추지 않고 다음 대회로 간다.

**3. 세 모델로 전체 실행** (대회 17건 × 3 + 확인 호출 3번 = 54번. 하루 상한 기본 60번, `--max-calls`로 바꾼다. 한 터미널에서 차례로)
```powershell
foreach ($model in "haiku", "sonnet", "opus") {
  .\.venv\Scripts\python.exe -m extractor eval --model $model --run-dir "..\eval\extraction\runs\${day}_${model}_v0.2"
}
```
중간에 멈추거나 실패해도 같은 명령을 다시 돌리면 남은 대회만 부른다 (`$day`를 처음 실행한 날로 두기).

**4. 채점과 보고서**
```powershell
$runs = "haiku", "sonnet", "opus" | ForEach-Object { "..\eval\extraction\runs\${day}_${_}_v0.2" }
.\.venv\Scripts\python.exe ..\eval\extraction\score.py @runs --report "..\eval\extraction\reports\${day}_models_v0.2.md"
```
실행 폴더마다 `judgments_todo.csv`(대회명·혜택처럼 사람이 판정할 칸)가 생긴다. 위 "사람 판정" 순서로 채운 뒤 다시 채점한다. `'`로 시작하는 칸은 AI 값이 수식으로 실행되지 않게 붙인 것이다. **판정은 메모장·VS Code 같은 편집기로 채운다** (엑셀로 저장하면 `'`가 빠져 다른 사람이 열 때 수식이 될 수 있다).

**5. 올리기**
- PR로 올리는 것: `eval/extraction/reports/`의 보고서, `eval/extraction/judgments/`의 판정. 각 실행 `meta.json`의 `totals`·`models_seen` 값은 PR 설명에 옮겨 적는다 (월 비용 환산에 쓴다).
- **올리지 않는 것:** `eval/extraction/runs/`(AI 원본 출력에 공지 인용문·연락처), `pipeline/data/`. 둘 다 `.gitignore`에 있다.
- `totals.cost_usd_estimate`는 공개 단가로 계산한 추정치다. 구독 로그인이면 실제 청구액이 아니다. 실패한 호출(`calls_failed`)·확인 호출(`probe_calls`)의 토큰도 들어간다.

**지금 입력에 넣지 않는 것** (`meta.json`의 `inputs`): 포스터 이미지·첨부 글자(#12 전), 결과 공지 본문(수상자 명단이 AI로 가지 않게 제목만, 대회별로 `result_bodies_omitted`), 본문의 8자리 숫자(학번). 그래서 포스터에만 있는 정보는 놓치고, 결과 공지에만 있는 일정은 못 본다. #12 뒤에 다시 재면 숫자가 바뀐다.

**남은 위험:** 결과 공지 판정은 제목으로 한다. 제목에 결과·수상·합격·명단 같은 말이 없는 공지에 이름이 나열돼 있으면 그대로 AI로 간다 (학번만 가린다).

## 남은 것

- 규칙 기준선 실제 숫자: 수집을 돌려 `analysis.json`을 만들고 정답표(#41)가 머지된 뒤에.
- 공개 API 단가로 월 비용 환산: 위 실행의 `meta.json` `totals`(토큰 수) 뒤에.
