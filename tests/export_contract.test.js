// 파이프라인 내보내기(#17)와 사이트가 같은 계약을 보는지: Python 내보내기가 만든 결과 파일을
// 사이트의 형식 검사(validateCardData)·판정(judgeCard)·화면 계산으로 다시 확인한다.
// 결과 파일은 pipeline/tests/test_exporter.py가 만든다 (UPDATE_FIXTURES=1).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { validateCardData, deadlineOf, cardBadges } from '../site/js/cards.js';
import { judgeCard } from '../site/js/rules.js';
import { fieldRows, inquiryDraft } from '../site/js/detail.js';
import { calendarItems, buildIcs } from '../site/js/ics.js';

const EXPORTED = JSON.parse(readFileSync(new URL('../pipeline/tests/fixtures/exported_cards.json', import.meta.url), 'utf8'));
const NOW = new Date('2026-10-15T09:00:00+09:00');
const STUDENT = { campus: '자연', grade: 2, is_transfer: false, enrollment: '재학', participation: '개인', deliverables: [], period: null, unknown_terms: [] };

test('내보낸 cards.json은 사이트 형식 검사를 그대로 통과한다 (뺀 카드·게시판 없음)', () => {
  const { data, rejected } = validateCardData(structuredClone(EXPORTED));
  assert.deepEqual(rejected, []);
  assert.equal(data.cards.length, EXPORTED.cards.length);
  assert.equal(data.boards.length, EXPORTED.boards.length);
  assert.notEqual(EXPORTED.sample, true, '실제 내보내기 결과는 예시 표시를 달지 않는다');
});

test('내보낸 카드로 판정·화면 계산·캘린더가 오류 없이 돈다', () => {
  for (const card of EXPORTED.cards) {
    const judged = judgeCard(card, STUDENT, { now: NOW });
    assert.ok(['eligible', 'team_needed', 'check', 'ineligible'].includes(judged.result), card.id);
    assert.ok(['open', 'closed', 'none', 'unreadable'].includes(deadlineOf(card, NOW).state), card.id);
    assert.ok(cardBadges(card).length > 0, card.id);
    assert.equal(fieldRows(card, EXPORTED.boards).length, 11, card.id);
    inquiryDraft(card, STUDENT);
    const items = calendarItems(card, NOW);
    if (items.length > 0) assert.match(buildIcs(card, items, { now: NOW }), /^BEGIN:VCALENDAR\r\n/);
  }
});

test('검수 상태가 판정까지 이어진다: 핵심 항목 검수 완료 카드는 신청 가능, 충돌·정보 없음이 섞인 카드는 신청 가능이 아님', () => {
  const byId = Object.fromEntries(EXPORTED.cards.map((card) => [card.id, card]));
  assert.equal(judgeCard(byId['ai-literacy-2026'], STUDENT, { now: NOW }).result, 'eligible');
  const mixed = judgeCard(byId['mixed-status-2026'], STUDENT, { now: NOW });
  assert.notEqual(mixed.result, 'eligible');
  assert.equal(byId['mixed-status-2026'].deadline_history.length, 1);
});
