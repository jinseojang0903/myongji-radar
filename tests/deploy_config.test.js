// 배포 설정(site/vercel.json)의 프레임 차단 응답 헤더 (#183). 파일 내용만 본다. 실제 응답은 배포 뒤 site/README.md "배포"의 방법으로 확인한다.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// JSON으로 읽히지 않으면 Vercel 배포가 실패하므로 읽기부터 시험이다
const CONFIG = JSON.parse(readFileSync(new URL('../site/vercel.json', import.meta.url), 'utf8'));

test('모든 경로의 응답에 다른 사이트의 프레임을 막는 헤더를 건다', () => {
  const rule = CONFIG.headers.find((entry) => entry.source === '/(.*)');
  assert.ok(rule, '모든 경로에 거는 규칙이 있다');
  const sent = Object.fromEntries(rule.headers.map(({ key, value }) => [key.toLowerCase(), value]));
  // 스크립트·스타일 출처는 페이지의 meta CSP가 맡는다. 응답 헤더가 같은 항목을 따로 정하면 둘이 어긋날 수 있어 프레임 항목만 둔다
  assert.equal(sent['content-security-policy'], "frame-ancestors 'none'");
  assert.equal(sent['x-frame-options'], 'DENY');
});
