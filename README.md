# 명지레이더

흩어진 명지대학교 교내 공지에서 **대회·공모전**을 모아, 생성형 AI가 포스터·첨부까지 읽고 대회 카드로 정리하는 서비스입니다. 카드의 항목마다 원문 근거를 붙이고, 학생이 고른 조건(캠퍼스·학년·참가 형태 등)으로 신청 가능 여부를 판정합니다.

> 2026학년도 명지대학교 AI 리터러시 경진대회 출품작 (결선: 2026-11-26)

## 구조

```
pipeline/    Python 프로젝트. 공개 게시판 수집·규칙 분석 구현, AI 추출·검증·병합·공개 JSON은 후속 단계
  collector/ 수집·분석 패키지와 CLI
  config/    출처(sources.toml)·규칙(rules.toml)의 단일 설정
  tests/     수집·분석 테스트
prompts/     추출 프롬프트 버전별 원문과 바꾼 이유
site/        정적 웹사이트 (GitHub Pages). 조건 해석·판정·캘린더는 브라우저에서 처리하고 AI를 호출하지 않는다
eval/        정답표와 정확도 측정
tests/       판정 규칙·해석기 테스트
scripts/     매일 실행 스크립트
docs/        데이터 계약(api-contract.md), 수집기 설계·검증 기록(collector/) 등 문서
```

## 원칙

- **로그인 없는 공개 게시판만** 하루 1회 확인하고, 아직 저장하지 않은 글을 우선 수집한다.
- 공지 **원문과 첨부는 저장소에 올리지 않는다** (`data/`는 커밋 제외). 사이트에는 요약, 짧은 근거 문장, 원문 링크만 싣는다.
- AI는 공지를 해석해 정리하고, 신청 가능 여부는 **규칙 코드**가 판정한다. 확실하지 않으면 "확인 필요"로 표시한다.
- API 키와 로그인 정보는 저장소에 두지 않는다 (`.env`는 커밋 제외, 예시는 `.env.example`).

## 실행 방법

Python 3.11 이상을 설치하고 저장소 루트에서 다음을 실행한다 (Windows PowerShell).

```powershell
cd pipeline
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\launcher.bat test
```

수집하려면 `.\launcher.bat run`, 저장된 공지를 규칙으로 분석하려면 `.\launcher.bat analyze`를 실행한다. 수집은 발견한 목록에서 출처별 최대 50건을 처리하며 새 글을 우선한다. 빠른 시험은 BAT 메뉴 1번(3건) 또는 `.\.venv\Scripts\python.exe -m collector run --limit 3`으로 제한한다. 전체 실행은 출처·응답 속도에 따라 몇 분이 걸릴 수 있다.

현재 등록 출처는 7곳이며 전체 목표 11곳으로 확장 중이다. 예약 실행은 아직 구현 전이다. RSS 최근 목록과 HTML 첫 페이지 밖의 과거 공지까지 탐색하는 기능은 아직 없다.

자세한 실행법과 기존 환경 이동 방법은 [pipeline/README.md](pipeline/README.md), 추가할 4곳은 [출처 추가 대상](docs/collector/출처_추가대상.md)을 참고한다. 출처 설정은 `pipeline/config/sources.toml` 한 곳에서만 관리한다.

사이트 JS 테스트는 저장소 루트에서 `node --test "tests/*.test.js"`로 실행할 수 있다. 데이터 형식은 [데이터 계약](docs/api-contract.md)을 따른다.
