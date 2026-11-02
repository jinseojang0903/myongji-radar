# 명지레이더 수집기 프로토타입

총 7개 공개 게시판에서 글을 발견하고, 상세 페이지의 제목·본문·이미지·첨부 링크를 SQLite와 로컬 JSON에 저장한다. 구현 방향과 확장 계획은 [수집기_구현계획.md](수집기_구현계획.md)에 정리했다. 추가 5곳의 실측 비교는 [추가출처_검증결과.md](추가출처_검증결과.md)에 기록했다. 기존 초안 파일은 유지했다.

저장된 본문을 대상으로 활동 분류·날짜·참가 조건 언급을 추출하는 규칙 모듈도 구현했다. [규칙분류_구현결과.md](규칙분류_구현결과.md)에 동작과 한계를 정리했다. AI 없이 동작하며 추출값은 검수 전이다.

## 실행

### BAT 실행기

`launcher.bat`을 더블클릭하면 메뉴가 열린다. 실행 위치와 관계없이 BAT 파일이 있는 폴더를 기준으로 동작하며, 작업 후 창을 유지해 오류를 확인할 수 있다.

| 번호 | 기능 |
| --- | --- |
| 1 | 7개 출처에서 각각 최대 3건 상세 수집, 기존 DB에 반영 |
| 2 | 7개 출처에서 각각 최대 10건 상세 수집, 기존 DB에 반영 |
| 3 | 수집 결과 JSON을 메모장으로 열기 |
| 4 | 인터넷 없이 수집·분류·추출 검증 테스트 실행 |
| 5 | 가상환경이 없으면 생성하고, 고정 버전 의존성 설치 |
| 6 | 저장된 공지 전체를 규칙으로 분류·추출, 인터넷 불필요 |
| 7 | 분류·추출 결과와 원문 근거가 있는 검수 목록을 메모장으로 열기 |
| 0 | 종료 |

GitHub에서 처음 내려받았다면 Python 3.11 이상 설치 후 5번부터 실행한다. 이미 실행 환경이 설정된 폴더라면 바로 1번을 실행하면 된다. 5번 의존성 설치와 1·2번 수집에는 인터넷이 필요하다. 수집과 테스트는 의존성을 자동 설치하지 않으며, 환경이 없으면 5번 실행을 안내한다. 가상환경과 실제 수집 데이터는 저장소에 포함하지 않는다.

직접 확인할 때는 **1번 → 3번 → 원문 URL 열어 비교 → 다시 1번** 순서로 진행한다. `title`, `body_text`, `images`, `attachments`를 원문과 비교한다. 본문 텍스트는 HTML 태그 제거 과정에서 줄바꿈이 많을 수 있다. 동일한 원문을 다시 수집하면 `unchanged`로 표시된다. `new`는 처음 저장한 글, `changed`는 이전 저장 내용과 달라 새 버전을 만든 글이다. 대회 분류 전이라 일반 안내나 채용 공지도 저장된다.

명령행에서 메뉴 없이 실행할 수도 있다. 이 모드는 자동 검증을 위해 창 대기 없이 종료하며 오류 시 종료 코드 1을 반환한다.

```powershell
.\launcher.bat run
.\launcher.bat test
.\launcher.bat export
.\launcher.bat setup
.\launcher.bat analyze
```

### Python 명령으로 실행

PowerShell에서 저장소의 MJU_COLLECTOR 폴더로 이동한 뒤 실행한다. 아래 명령은 myongji-radar 저장소 루트 기준이다. 최초 설치는 다음 절차를 먼저 진행한다.

```powershell
cd MJU_COLLECTOR
.\.venv\Scripts\python.exe -m collector run --limit 3
```

다른 환경에서 최초 설치할 때는 Python 3.11 이상으로 다음을 실행한다. 이번에 검증한 의존성 버전은 lock 파일에 기록했다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
```

출처 하나만 수집하거나 더 많은 글을 확인할 수 있다. limit은 출처별 상세 요청 수이며 기본값은 3, 최대 50이다.

```powershell
.\.venv\Scripts\python.exe -m collector run --source mju_ctl --limit 5
.\.venv\Scripts\python.exe -m collector export
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

`--config 경로`로 출처 설정을, `--data-dir 경로`로 저장 위치를 바꿀 수 있다. 기본 경로는 실행 위치와 관계없이 이 프로젝트 기준으로 정해진다. 동일 데이터 경로에 여러 실행을 동시에 시작하지 않는다.

## 결과 확인

- `data/notices.json`: UTF-8 JSON. 제목·본문·원문 URL·이미지·첨부 링크와 버전 ID 확인.
- `data/collector.sqlite3`: 게시글, 원문 버전, 실행 결과 저장.
- `data/raw/`: 변경된 글의 원문 HTML. 네트워크 없이 파서 수정 검증에 활용 가능.
- `data/analysis.json`: 활동 분류, 공지 성격, 일정·조건 언급, 원문 근거·버전·규칙 버전.
- `data/analysis_review.md`: 사람이 읽는 분석 검수 목록. BAT 6번 분석 후 7번으로 열기.
- `data/analysis_history/`: 입력·설정·엔진별 분석 결과 보관.

JSON과 원문은 로컬 확인용이며 그대로 공개하지 않는다. 첨부파일 다운로드, OCR·AI 해석, HTML 과거 목록 탐색, 예약 실행은 아직 구현하지 않았다. 전체 목표 11곳 중 현재 등록한 곳은 7곳이다. 수집 성공은 해당 공지가 모집 중이거나 대회라는 의미가 아니다. 규칙의 활동 후보 분류 역시 사람 검수 전이며 개인별 참가 가능 판정은 제공하지 않는다.

직접 분류를 확인하려면 **6번 → 7번** 순서로 실행한다. 새 공지를 가져오려면 그 전에 1번 또는 2번을 실행한다. 자격 필드의 `mentions`는 조건에 등장한 단어로, 허용 대상 목록이 아니다. 예를 들어 휴학생 참가 불가 문장에서 휴학생을 발견한 경우도 포함하므로 근거를 함께 확인한다. `not_stated`와 null은 제한 없음을 의미하지 않는다. 규칙을 고칠 때는 `config/rules.toml`과 `collector/rules.py`를 수정하고 규칙 버전을 올린 뒤 6번으로 재분석한다.

## 현재 출처와 실행 순서

`config/sources.toml`의 priority가 낮을수록 먼저 실행한다. 같은 값은 설정 파일 순서를 따른다.

| 우선순위 | 출처 ID | 대상 | 발견 방식 |
| --- | --- | --- | --- |
| 1 | mju_startup | 창업교육센터 | RSS, 미이스케이프 & 보정 |
| 2 | mju_abix | ABI-X 사업단 | RSS, 미이스케이프 & 보정 |
| 3 | mju_innovation | 대학혁신지원사업 | HTML 목록 첫 페이지, RSS 미지원 |
| 4 | mju_ecc | 공학교육혁신센터 | RSS |
| 5 | mju_scict | 반도체·ICT대학 | RSS |
| 90 | mju_general | 본교 일반공지 | RSS |
| 91 | mju_ctl | 교수학습센터 | RSS |

한 곳만 확인하거나 저장 원문과 파싱 결과의 구조적 일치를 다시 점검할 수 있다.

```powershell
.\.venv\Scripts\python.exe -m collector run --source mju_startup --limit 5
.\.venv\Scripts\python.exe -m collector run --source mju_ecc --limit 5
.\.venv\Scripts\python.exe scripts/audit_samples.py
```

마지막 명령은 추가 출처 5곳의 저장된 글을 각각 최대 5건 검사한다. 새로운 네트워크 요청은 하지 않으며 결과는 `data/audit/structural_report.json`에 기록한다. 이 검사는 HTML과 파싱 결과의 구조적 일치만 확인하며 대회 분류·마감일 해석 정확도를 뜻하지 않는다.

## 검증 기록

2026-11-02, Windows Python 3.14.4에서 최초 두 출처를 확인한 기록이다. 이후 확장 결과는 위의 추가출처 검증 문서를 참고한다.

| 확인 | 결과 |
| --- | --- |
| RSS 발견 | 일반공지·교수학습센터 각각 50건 |
| 상세 수집 | 출처별 3건, 총 6건 성공, 오류 0건 |
| 수집 내용 | 본문 255~3,790자, 이미지 링크 총 5개, 첨부 링크 총 10개 |
| 같은 명령 재실행 | 신규 0건, 변경 0건, 변경 없음 6건 |
| 오프라인 테스트 | 5개 통과: RSS 보정·중복, 이미지 전용 본문, 잘못된 페이지, 변경·복원 이력, 미등록 호스트 거부 |

현재 실제 데이터에는 AI 리터러시 경진대회 모집 안내와 예선 결과 발표가 포함되어 있다. 표본 6건의 확인 결과이며 전체 게시판의 누락률·분류 정확도를 측정한 결과는 아니다.
