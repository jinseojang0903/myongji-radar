// 사람 검수 도우미(scripts/review_card.mjs, #99). 네트워크 없이 시험한다.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { canonicalJson, evidenceFingerprint, reviewItems, needsCheckItems, main, REVIEWABLE_FIELDS } from '../scripts/review_card.mjs';

const EXPORTED = JSON.parse(readFileSync(new URL('../pipeline/tests/fixtures/exported_cards.json', import.meta.url), 'utf8'));
// pipeline/tests/test_reviews.py의 FIXTURE_APPLY_FINGERPRINT와 같다: 두 언어의 지문이 같아야 검수가 맞는다
const FIXTURE_APPLY_FINGERPRINT = 'f17b4e21f56bfce13eef7d5b6a849e1a44fcc53aefa1205adf78d059aa585b11';

test('지문과 정렬 JSON이 파이프라인(Python)과 같다', () => {
  assert.equal(evidenceFingerprint(EXPORTED.cards[0].fields.apply.evidence), FIXTURE_APPLY_FINGERPRINT);
  assert.equal(canonicalJson({ b: 1, a: ['가', null] }), '{"a":["가",null],"b":1}');
  assert.ok(!REVIEWABLE_FIELDS.includes('contact'), '문의처는 검수 대상이 아니다');
});

test('검수 전이고 값·근거가 있는 항목만 칸을 만든다', () => {
  const card = structuredClone(EXPORTED.cards[0]);
  for (const name of REVIEWABLE_FIELDS) card.fields[name].status = 'ai';
  card.fields.grades.status = 'human_verified';
  card.fields.team.evidence = [];
  const items = reviewItems(card, '2026-11-16');
  const names = items.map((item) => item.name);
  assert.ok(!names.includes('grades'), '이미 검수 완료');
  assert.ok(!names.includes('team'), '근거 없음');
  const apply = items.find((item) => item.name === 'apply');
  assert.match(apply.entry, /^\[\[review\]\]\ncard_id = "ai-literacy-2026"\nfield = "apply"\n/);
  assert.ok(apply.entry.includes(`evidence_sha256 = "${FIXTURE_APPLY_FINGERPRINT}"`));
  assert.ok(apply.entry.endsWith('since = 2026-11-16'));
  // value 줄은 JSON 문자열이라 다시 읽으면 공개 값과 같다
  const valueLine = apply.entry.split('\n').find((line) => line.startsWith('value = '));
  assert.deepEqual(JSON.parse(JSON.parse(valueLine.slice('value = '.length))), card.fields.apply.value);
});

test('원문 확인 필요 항목은 칸을 만들지 않고 이슈로 남기라고 알린다', () => {
  const card = structuredClone(EXPORTED.cards[0]);
  for (const name of REVIEWABLE_FIELDS) card.fields[name].status = 'ai';
  card.fields.apply.status = 'needs_check';
  card.fields.apply.note = '내보내기 검사: 마감이 시작보다 빠름';
  assert.ok(!reviewItems(card, '2026-11-16').some((item) => item.name === 'apply'));
  assert.deepEqual(needsCheckItems(card), [{ name: 'apply', label: '신청기간', note: '내보내기 검사: 마감이 시작보다 빠름' }]);
});

test('터미널을 조작하는 제어 문자는 출력하지 않는다', async () => {
  const data = structuredClone(EXPORTED);
  data.cards[0].title = '대회\u001b[31m빨강‮';
  const lines = [];
  await main([data.cards[0].id], { log: (line) => lines.push(line), load: async () => data });
  assert.equal(lines[0], '# 대회[31m빨강');
  // 칸의 값에 든 보이지 않는 글자는 지우지 않고(지문이 어긋남) \uXXXX로 적는다
  const card = structuredClone(EXPORTED.cards[0]);
  card.fields.apply.status = 'ai';
  card.fields.apply.value = { ...card.fields.apply.value, end: '2026-10-21‮' };
  const entry = reviewItems(card, '2026-11-16').find((item) => item.name === 'apply').entry;
  assert.ok(!entry.includes('‮'));
  assert.ok(entry.includes('\\u202e'));
});

test('없는 카드·잘못된 id는 2, 있는 카드는 원문 링크와 칸을 보여 준다', async () => {
  const run = async (argv) => {
    const lines = [];
    const code = await main(argv, { log: (line) => lines.push(line), load: async () => EXPORTED, now: new Date('2026-11-16T03:00:00Z') });
    return { code, output: lines.join('\n') };
  };
  assert.equal((await run(['없는-카드'])).code, 2);
  assert.equal((await run(['Bad Id'])).code, 2);
  const card = EXPORTED.cards[0];
  const { code, output } = await run([card.id]);
  assert.equal(code, 0);
  assert.ok(output.includes(`원문: ${card.sources[0].url}`));
  if (reviewItems(card, '2026-11-16').length > 0) assert.ok(output.includes('[[review]]'));
});
