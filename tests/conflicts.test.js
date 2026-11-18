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
  const baseline = sample();
  delete baseline.sample;
  const baselineResult = checkCardsData(baseline, new Date(baseline.generated_at));
  assert.deepEqual(baselineResult.failures, []);
  assert.ok(!baselineResult.warnings.some((text) => text.includes('화면이 뺄 항목')));
  const mutations = [
    (field) => { field.status = 'human_verified'; },
    (field) => { field.reviewable = true; },
    (field) => { field.conflicts[0].source = 'https://unknown.example/'; },
    (field) => { field.conflicts[0].value = null; },
    (field) => { field.conflicts[0].evidence.push({ quote: '숨긴 근거', source: field.conflicts[0].source }); },
    (field) => { field.conflicts = [field.conflicts[0]]; },
    (field) => { field.conflicts = Array(6).fill(field.conflicts[0]); },
    (field) => { field.status = 'missing'; },
  ];
  for (const mutate of mutations) {
    const data = sample();
    delete data.sample;
    mutate(history(data).fields.enrollment);
    assert.ok(validateCardData(data).rejected.some((item) => item.id === 'history-video-5'));
    const checked = checkCardsData(data, new Date(data.generated_at));
    assert.deepEqual(checked.failures, [], '예시 플래그 때문에 실패한 것이 아니다');
    assert.ok(checked.warnings.some((text) => text.startsWith('화면이 뺄 항목 history-video-5:')));
  }
});

test('단일 공지 본문·포스터의 다른 인용만으로 충돌을 추측하지 않고 확인 필요와 근거를 유지한다', () => {
  const data = sample();
  const card = history(data);
  card.sources = [card.sources[0]];
  const field = card.fields.enrollment;
  delete field.conflicts;
  field.evidence.forEach((item) => { item.source = card.sources[0].url; });
  const row = fieldRows(card, data.boards).find((item) => item.name === 'enrollment');
  assert.equal(row.isConflict, false);
  assert.equal(row.status, 'needs_check');
  assert.equal(row.evidence.length, 2);
  assert.ok(!inquiryDraft(card).text.includes('서로 다르게'));
});

test('변조된 충돌 검사 키를 점검 출력에 옮기지 않는다', () => {
  for (const malformed of [true, false]) {
    const data = sample();
    delete data.sample;
    const privateKey = malformed ? '개인 비밀의 키' : 'enrollment';
    const field = structuredClone(history(data).fields.enrollment);
    if (malformed) history(data).fields[privateKey] = field;
    else {
      field.conflicts[0].source = 'https://untrusted.example/';
      history(data).fields.enrollment = field;
    }
    const checked = checkCardsData(data, new Date(data.generated_at));
    assert.ok(checked.warnings.some((text) => text.includes(malformed ? '(모르는 키)의 conflicts' : 'enrollment의 충돌 값')));
    assert.ok(!JSON.stringify(checked).includes('개인 비밀'), '키의 내용은 출력하지 않는다');
  }
});

test('문의 초안은 허용 주소와 게시판 이름을 쓰고 금지 주소를 옮기지 않는다', () => {
  const data = sample();
  const card = history(data);
  const good = inquiryDraft(card, null, data.boards).text;
  const source = card.sources.find((item) => item.url === card.fields.enrollment.conflicts[0].source);
  assert.ok(good.includes(data.boards.find((board) => board.id === source.board_id).name));
  assert.ok(!good.includes(`(${source.board_id}:`));
  for (const url of ['javascript:alert(1)', 'https://untrusted.example/private', 'http://www.mju.ac.kr/plain']) {
    card.fields.enrollment.conflicts[0].source = url;
    const text = inquiryDraft(card, null, data.boards).text;
    assert.ok(!text.includes(url));
    assert.ok(text.includes('주소 확인 필요'));
  }
});
