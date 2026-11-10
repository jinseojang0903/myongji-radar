# 수동 공지 병합 미리보기 (#16 분할2)

이 단계는 검토용 후보를 만든다. `publisher`·`exporter`·예약 실행에는 연결하지 않으며 공개 카드나 수집 원문을 바꾸지 않는다. 마감 이력, 최종 종료 판정, 관측 상태 및 자동 배포는 후속 단계다.

`pipeline/config/contest_groups.toml`은 조사표의 같은 회차 출처 5개 그룹이다. 새 그룹은 사람이 연도·회차·참가 범위를 확인한다. 제목 유사도로 자동 등록하지 않는다. 캠퍼스별 별도 대회인 EVC는 포함하지 않는다.

`pipeline` 폴더에서 수집·추출이 끝난 로컬 입력만 사용한다.

```powershell
.\.venv\Scripts\python.exe -m merger --bundles data/card_bundles.json --notices data/notices.json --out preview/contest-preview.json
```

- 출력 이름은 `contest-preview.json`으로 고정하고 입력과 다른 폴더에 저장한다. `site` 안에는 저장할 수 없다. DB나 원문·기존 묶음을 출력으로 지정할 수 없다.
- 출력은 `preview_version`, `candidates`, `report`다. 운영용 `bundles` 키가 없으므로 기존 verifier/exporter 입력으로 사용할 수 없다. 후보에는 비공개 원문 인용이 있으므로 Git이나 웹에 올리지 않는다.
- 그룹이 미등록이면 입력 묶음을 그대로 복사한다. 그룹 내 현재 원문 버전·해시·출처가 맞고 모든 추출 필드의 값과 상태가 같을 때만 고정 ID로 병합한다. 근거 번호를 다시 매기고 사람 검수 상태는 승계하지 않는다. 인용 존재나 값의 의미까지 검증한 결과가 아니다.
- 공지 누락·오래된 provenance·값 충돌·확인 필요 상태·추출 경고·결과/취소·기존 이력이 있으면 원래 개별 카드와 필드를 유지한다. `report.groups`에 `fallback`과 고정 사유 코드를 기록한다. 상태 파일을 만들지 않아 설정을 고친 뒤 다시 미리보기할 수 있고 기본 공개 흐름에 복구 잠금이 생기지 않는다.
- 차단은 한 순수 함수에서 그룹 ID와 모든 구성 공지 ID로 양방향 확장한다. 차단된 그룹은 fallback을 경고하지 않는다. 보고의 `requested_ids`는 운영자가 작성한 원래 ID, `expanded_ids`는 계산된 범위, `removed_ids`는 제외된 후보, `unmatched_requested_ids`는 이번 입력과 대응하지 않은 원래 ID다. 차단을 지우라는 안내는 하지 않는다. 기존 공개본을 읽거나 수정하는 기능은 없다.
- 종료 코드 0은 미리보기 생성, 1은 개별 카드 유지 또는 원래 차단 ID 중 이번 입력과 대응하지 않은 항목이 있어 검토 필요, 2는 입력/경로 오류로 기존 출력 유지다. 0과 1 모두 운영 공개 승인을 뜻하지 않는다.

학교·AI 호출 없이 `python -m unittest discover -s tests -p test_manual_merger.py -v`로 확인한다. 실제 이미지 마감과 본선 일정 이력은 #14의 근거 연결 이후 별도로 검증한다.
