# tests

판정 규칙(`site/js/rules.js`)과 자연어 해석기(`site/js/parser.js`), 파이프라인 검증 함수 테스트.

## 실행

Node 22 이상. 외부 패키지는 없다.

```
npm test
```

- `rules.test.js`: 판정 규칙. 기준은 `docs/api-contract.md` 3절. 마지막 테스트는 무작위 5만 건으로 "불가인데 가능" 판정이 없는지 본다.
