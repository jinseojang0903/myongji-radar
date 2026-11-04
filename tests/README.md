# tests

판정 규칙(`site/js/rules.js`)과 자연어 해석기(`site/js/parser.js`), 파이프라인 검증 함수 테스트.

## 실행

Node 22 이상. 외부 패키지는 없다.

```
npm test
```

- `rules.test.js`: 판정 규칙. 기준은 `docs/api-contract.md` 3절. 마지막 테스트는 무작위 5만 건으로 "불가인데 가능" 판정이 없는지 본다.
- `parser.test.js`: 자연어 해석기. 기준은 계약 2절. 여기 문장은 해석기를 만들며 쓴 개발용이라 정확도 측정에 쓰지 않는다 (정확도는 `npm run eval:parser`). 무작위 문장 2만 개로 출력 형식도 본다. 측정 스크립트 `eval/parser/run.js`의 CSV 읽기·채점도 여기서 시험한다.
- `log.test.js`: 제작 기록. `site/data/log.json` 형식, 모든 수치에 측정일·출처가 있는지(지어낸 수치 금지), 가리키는 파일이 저장소에 있는지, 검수 체험 채점.
- `cards.test.js`: 홈 화면 계산(마감·D-day·카드 배지·필터·정렬), 외부 링크 검사, 데이터 읽기, 예시 데이터(`site/data/cards.sample.json`)가 계약 형식을 따르는지와 판정 결과가 화면 설계와 같은지.
