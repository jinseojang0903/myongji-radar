# tests

판정 규칙(`site/js/rules.js`)과 자연어 해석기(`site/js/parser.js`), 파이프라인 검증 함수 테스트.

## 실행

Node 22 이상. 외부 패키지는 없다.

```
npm test
```

- `rules.test.js`: 판정 규칙. 기준은 `docs/api-contract.md` 3절. 마지막 테스트는 무작위 5만 건으로 "불가인데 가능" 판정이 없는지 본다.
- `detail.test.js`: 카드 상세(항목 표·근거·마감 변경 이력·문의 초안), 캘린더 파일(RFC 5545 형식, 시각 미기재는 하루 종일, 공지 문자열 이스케이프), 내 조건 저장(해석된 값만, 깨진 값은 무시).
- `cards.test.js`: 홈 화면 계산(마감·D-day·카드 배지·필터·정렬), 외부 링크 검사, 데이터 읽기, 예시 데이터(`site/data/cards.sample.json`)가 계약 형식을 따르는지와 판정 결과가 화면 설계와 같은지.
