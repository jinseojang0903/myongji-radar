import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { validateCardData } from '../site/js/cards.js';
import { fieldRows, inquiryDraft } from '../site/js/detail.js';
import { isPromotable } from '../scripts/review_card.mjs';
import { checkCardsData } from '../scripts/check_live_cards.mjs';

const sample = () => JSON.parse(readFileSync(new URL('../site/data/cards.sample.json', import.meta.url), 'utf8'));
const history = (data) => data.cards.find((card) => card.id === 'history-video-5');

test('Python 공개 내보내기 결과에서도 양쪽 값·근거가 화면 계약을 통과한다', () => {
  const data = JSON.parse(readFileSync(new URL('../pipeline/tests/fixtures/group_conflicts.json', import.meta.url), 'utf8'));
  assert.deepEqual(validateCardData(data).rejected, []);
  const row = fieldRows(data.cards[0], data.boards).find((item) => item.name === 'campus');
  assert.equal(row.isConflict, true);
  assert.deepEqual(row.conflicts.map((variant) => variant.text), ['자연캠퍼스', '인문캠퍼스']);
  assert.ok(row.conflicts.every((variant) => variant.evidence.length > 0));
  assert.deepEqual(checkCardsData(data, new Date(data.generated_at)).failures, []);
});

test('명시한 충돌은 값·출처·양쪽 근거를 연결해 표시하고 문의에 조건을 쓴다', () => {
  const data = sample();
  const card = history(data);
  const row = fieldRows(card, data.boards).find((item) => item.name === 'enrollment');
  assert.equal(validateCardData(data).rejected.length, 0);
  assert.equal(row.isConflict, true);
  assert.equal(row.conflicts.length, 2);
  assert.ok(row.conflicts[0].text.includes('초과학기'));
  assert.ok(!row.conflicts[0].text.includes('졸업유예'));
  assert.ok(row.conflicts[1].text.includes('졸업유예'));
  for (const variant of row.conflicts) {
    assert.ok(variant.url.startsWith('https://'));
    assert.equal(variant.evidence[0].url, variant.url);
    assert.ok(variant.boardName);
    assert.ok(inquiryDraft(card).text.includes(variant.url));
    assert.ok(inquiryDraft(card).text.includes(variant.text));
  }
  assert.equal(isPromotable({ ...card.fields.enrollment, reviewable: true }), false);
  assert.equal(isPromotable({ ...card.fields.enrollment, status: 'auto_verified' }), false);
});

test('근거 문장이 같아도 서로 다른 값의 충돌 표시는 유지한다', () => {
  const data = sample();
  const card = history(data);
  const field = card.fields.enrollment;
  field.conflicts[1].evidence[0].quote = field.conflicts[0].evidence[0].quote;
  assert.equal(fieldRows(card, data.boards).find((row) => row.name === 'enrollment').isConflict, true);
});

test('공개 상한으로 인용이 빠져도 충돌 값과 출처는 남는다', () => {
  const data = sample();
  const field = history(data).fields.enrollment;
  field.evidence = [];
  field.conflicts.forEach((variant) => { variant.evidence = []; });
  assert.equal(validateCardData(data).rejected.length, 0);
  const row = fieldRows(history(data), data.boards).find((item) => item.name === 'enrollment');
  assert.equal(row.isConflict, true);
  assert.equal(row.conflicts.length, 2);
});

test('중첩 충돌 근거로 공개 상한이나 출처 검사를 우회할 수 없다', () => {
  const mutations = [
    (field) => { field.status = 'human_verified'; },
    (field) => { field.reviewable = true; },
    (field) => { field.conflicts[0].source = 'https://unknown.example/'; },
    (field) => { field.conflicts[0].value = null; },
    (field) => { field.conflicts[0].evidence.push({ quote: '숨긴 근거', source: field.conflicts[0].source }); },
    (field) => { field.conflicts = [field.conflicts[0]]; },
  ];
  for (const mutate of mutations) {
    const data = sample();
    mutate(history(data).fields.enrollment);
    assert.ok(validateCardData(data).rejected.some((item) => item.id === 'history-video-5'));
    assert.ok(checkCardsData(data, new Date(data.generated_at)).failures.length > 0);
  }
});
