// 달력 화면 계산(site/js/calendar.js) 테스트 (#177): 월 격자, 달 옮기기, 날짜별 마감 일정.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { monthOf, shiftMonth, monthGrid, deadlineEvents, groupByDate, unlistedCount } from '../site/js/calendar.js';
import { calendarItems } from '../site/js/ics.js';
import { listCards } from '../site/js/cards.js';

const SAMPLE = JSON.parse(readFileSync(new URL('../site/data/cards.sample.json', import.meta.url), 'utf8'));
const AT_SAMPLE = new Date(SAMPLE.generated_at); // 2026-10-15 06:00 KST

function field(value, status = 'ai') {
  return { value, status, evidence: [] };
}

function makeCard(id, fields) {
  return { id, title: `대회 ${id}`, organizer: null, scope: null, sources: [], fields };
}

test('달을 옮기면 연도가 넘어가고, 형식이 다른 값은 null이다', () => {
  assert.equal(monthOf('2026-11-19'), '2026-11');
  assert.equal(shiftMonth('2026-11', 1), '2026-12');
  assert.equal(shiftMonth('2026-12', 1), '2027-01');
  assert.equal(shiftMonth('2026-01', -1), '2025-12');
  assert.equal(shiftMonth('2026-03', -15), '2024-12');
  assert.equal(shiftMonth('2026-11', 0), '2026-11');
  for (const bad of ['2026-13', '2026-1', '26-11', '', '2026-11-01']) assert.equal(shiftMonth(bad, 1), null, bad);
  assert.equal(shiftMonth('2026-11', 0.5), null);
  assert.equal(shiftMonth('9999-12', 1), null, '네 자리 연도를 벗어나지 않는다');
});

test('월 격자는 일요일부터 시작하고 그 달의 모든 날을 한 번씩 담는다', () => {
  const november = monthGrid('2026-11'); // 2026-11-01은 일요일
  assert.equal(november[0][0], '2026-11-01');
  assert.equal(november.length, 5);
  assert.equal(november.at(-1).at(-1), '2026-12-05');

  const december = monthGrid('2026-12'); // 2026-12-01은 화요일
  assert.deepEqual(december[0].slice(0, 3), ['2026-11-29', '2026-11-30', '2026-12-01']);
  assert.equal(december.at(-1).at(-1), '2027-01-02');

  for (const month of ['2026-02', '2028-02', '2026-05', '2027-08']) {
    const weeks = monthGrid(month);
    assert.ok(weeks.every((week) => week.length === 7), month);
    const inMonth = weeks.flat().filter((date) => monthOf(date) === month);
    const lastDay = new Date(Date.UTC(Number(month.slice(0, 4)), Number(month.slice(5, 7)), 0)).getUTCDate();
    assert.equal(inMonth.length, lastDay, `${month}: 날 수`);
    assert.equal(new Set(inMonth).size, lastDay, `${month}: 겹치는 날 없음`);
    assert.ok(weeks.every((week) => week.some((date) => monthOf(date) === month)), `${month}: 빈 주 없음`);
  }
  assert.equal(monthGrid('2028-02').flat().filter((date) => date === '2028-02-29').length, 1, '윤년');
  assert.deepEqual(monthGrid('2026-13'), []);
});

test('마감 일정은 캘린더 파일과 같은 날짜를 쓰고, 행사 날짜는 넣지 않는다', () => {
  const { events, failedCardIds } = deadlineEvents(SAMPLE.cards, AT_SAMPLE);
  assert.deepEqual(failedCardIds, []);
  assert.ok(events.length > 0);
  for (const card of SAMPLE.cards) {
    const expected = calendarItems(card, AT_SAMPLE).filter((item) => item.isDeadline);
    const actual = events.filter((event) => event.cardId === card.id);
    assert.deepEqual(actual.map((event) => [event.date, event.time, event.label].join('|')).sort(),
      expected.map((item) => [item.date, item.time, item.label].join('|')).sort(), card.id);
    assert.ok(actual.every((event) => event.title === card.title));
  }
  // 날짜 순
  const dates = events.map((event) => event.date);
  assert.deepEqual(dates, [...dates].sort());
});

test('남은 날 수·지남·검수 여부를 일정마다 알려 준다', () => {
  const now = new Date('2026-11-19T10:00:00+09:00');
  const cards = [
    makeCard('timed', { apply: field({ start: null, end: '2026-11-19 09:00', time_given: true }) }),
    makeCard('today', { apply: field({ start: null, end: '2026-11-19', time_given: false }) }),
    makeCard('verified', { apply: field({ start: null, end: '2026-11-27 23:00', time_given: true }, 'human_verified') }),
    makeCard('past', { apply: field({ start: null, end: '2026-11-10', time_given: false }) }),
    makeCard('staged', {
      apply: field({ start: null, end: '2026-12-01', time_given: false }),
      stages: field([{ name: '신청', end: '2026-12-01' }, { name: '출품', end: '2026-12-20 18:00', time_given: true }, { name: '시상', end: '2027-01-15' }]),
    }),
  ];
  const { events } = deadlineEvents(cards, now);
  const of = (id) => events.filter((event) => event.cardId === id);
  assert.deepEqual(of('timed').map((event) => [event.daysLeft, event.isPast, event.time]), [[0, true, '09:00']], '오늘이어도 시각이 지나면 지남');
  assert.deepEqual(of('today').map((event) => [event.daysLeft, event.isPast, event.time]), [[0, false, null]], '시각이 없으면 그날까지');
  assert.deepEqual(of('verified').map((event) => [event.daysLeft, event.isVerified]), [[8, true]]);
  assert.deepEqual(of('past').map((event) => [event.daysLeft, event.isPast]), [[-9, true]]);
  assert.deepEqual(of('staged').map((event) => event.label), ['참가 신청 마감', '작품 출품 마감'], '시상은 마감이 아니다');
  assert.ok(events.every((event) => event.isVerified === (event.cardId === 'verified')));
  // 같은 날: 시각이 있는 일정이 먼저, 하루 종일 일정이 뒤
  assert.deepEqual(events.filter((event) => event.date === '2026-11-19').map((event) => event.cardId), ['timed', 'today']);

  const groups = groupByDate(events);
  assert.equal(groups.get('2026-11-19').length, 2);
  assert.equal(groups.has('2027-01-15'), false);
});

test('마감일을 모르는 대회는 달력에 없고 건수로만 알린다', () => {
  const now = new Date('2026-11-19T10:00:00+09:00');
  const cards = [
    makeCard('known', { apply: field({ start: null, end: '2026-11-27', time_given: false }) }),
    makeCard('missing', { apply: field(null, 'missing') }),
    makeCard('empty-end', { apply: field({ start: null, end: null, time_given: false }) }),
    makeCard('unreadable', { apply: field({ start: null, end: '11월 말', time_given: false }, 'needs_check') }),
    // 신청 마감일은 모르지만 출품 마감은 달력에 있다: "달력에 올리지 못한 대회"로 세면 안 된다
    makeCard('submit-only', { apply: field(null, 'missing'), stages: field([{ name: '출품', end: '2026-12-20 18:00', time_given: true }]) }),
  ];
  const { events, failedCardIds } = deadlineEvents(cards, now);
  assert.deepEqual(events.map((event) => [event.cardId, event.key, event.date]), [['known', 'apply', '2026-11-27'], ['submit-only', 'submit', '2026-12-20']]);
  assert.deepEqual(failedCardIds, []);
  assert.equal(unlistedCount(cards, events, now), 3);
  // 홈의 "마감일 확인 필요"는 신청 마감 기준이라 출품만 아는 대회까지 4건이다
  const homeUnknown = listCards(cards, { sort: 'deadline', tags: [] }, now).items.filter(({ deadline }) => deadline.state !== 'open');
  assert.equal(homeUnknown.length, 4);
  assert.equal(unlistedCount([], [], now), 0);
});

test('오늘과 지남은 한국 시간 기준이다 (자정 경계)', () => {
  const cards = [
    makeCard('d18', { apply: field({ start: null, end: '2026-11-18', time_given: false }) }),
    makeCard('d19', { apply: field({ start: null, end: '2026-11-19', time_given: false }) }),
  ];
  // UTC로는 아직 11/18 15:30이지만 한국은 11/19 00:30이다
  const justAfterMidnight = deadlineEvents(cards, new Date('2026-11-18T15:30:00Z')).events;
  assert.deepEqual(justAfterMidnight.map((event) => [event.cardId, event.daysLeft, event.isPast]), [['d18', -1, true], ['d19', 0, false]]);
  // 한국 11/18 23:30: 11/18 마감은 아직 그날이다
  const justBeforeMidnight = deadlineEvents(cards, new Date('2026-11-18T14:30:00Z')).events;
  assert.deepEqual(justBeforeMidnight.map((event) => [event.cardId, event.daysLeft, event.isPast]), [['d18', 0, false], ['d19', 1, false]]);
});

test('읽지 못한 카드가 있어도 나머지 일정은 올리고, 어떤 카드인지 알린다', () => {
  const now = new Date('2026-11-19T10:00:00+09:00');
  const broken = makeCard('broken', {});
  Object.defineProperty(broken, 'fields', { get() { throw new TypeError('깨진 카드'); } });
  const original = console.error;
  const logged = [];
  console.error = (...args) => logged.push(args);
  try {
    const { events, failedCardIds } = deadlineEvents([broken, makeCard('ok', { apply: field({ start: null, end: '2026-11-27', time_given: false }) })], now);
    assert.deepEqual(events.map((event) => event.cardId), ['ok']);
    assert.deepEqual(failedCardIds, ['broken']);
    assert.equal(logged.length, 1);
  } finally {
    console.error = original;
  }
});
