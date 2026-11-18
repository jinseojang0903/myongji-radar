// 실제 loadPage·화면 시계와 계산 함수를 가짜 브라우저 시각으로 연결한다. 네트워크 요청은 없다.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { loadPage, watchPageTime } from '../site/js/page.js';
import { deadlineOf, listCards } from '../site/js/cards.js';
import { judgeCard, KEY_FIELDS } from '../site/js/rules.js';
import { recommendCards } from '../site/js/recommend.js';
import { calendarItems } from '../site/js/ics.js';

const SAMPLE = JSON.parse(readFileSync(new URL('../site/data/cards.sample.json', import.meta.url), 'utf8'));
const STUDENT = { campus: '자연', grade: 2, is_transfer: false, enrollment: '재학', participation: '개인', period: null, deliverables: [] };
const CLEANUPS = new WeakMap();
function watch(page, render) {
  CLEANUPS.get(page).push(watchPageTime(page, render));
}

async function fixture(t, { now = '2026-11-17T09:59:00+09:00', sample = false, fallback = false,
  end = '2026-11-17 10:00', timeGiven = true, generated = '2026-11-17T08:00:00+09:00' } = {}) {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: new Date(now) });
  const data = structuredClone(SAMPLE);
  delete data.sample;
  delete data.sample_note;
  if (sample) data.sample = true;
  data.generated_at = generated;
  data.cards = [data.cards[0]];
  const card = data.cards[0];
  const cleanups = [];
  t.after(() => cleanups.reverse().forEach((cleanup) => cleanup()));
  for (const field of KEY_FIELDS) card.fields[field].status = 'human_verified';
  card.fields.apply.value = { start: '2026-11-01', end, time_given: timeGiven };
  card.fields.stages = { status: 'missing', value: [], evidence: [] };
  const banner = { textContent: '', hidden: true };
  const document = Object.assign(new EventTarget(), { hidden: false, getElementById: () => banner });
  const window = new EventTarget();
  window.top = window.self = window;
  for (const [name, value] of Object.entries({ document, window })) {
    const old = Object.getOwnPropertyDescriptor(globalThis, name);
    Object.defineProperty(globalThis, name, { value, configurable: true });
    cleanups.push(() => old ? Object.defineProperty(globalThis, name, old) : delete globalThis[name]);
  }
  let requests = 0;
  t.mock.method(globalThis, 'fetch', async (url) => {
    requests++;
    return fallback && url === 'data/cards.json'
      ? { ok: false, status: 404 } : { ok: true, json: async () => data };
  });
  const page = await loadPage({ errorTarget: {}, bannerTarget: banner });
  assert.ok(page);
  CLEANUPS.set(page, cleanups);
  return { page, card: page.data.cards[0], banner, document, window, requests: () => requests };
}

test('페이지를 새로 읽지 않아도 재계산 시 현재 시각으로 홈·추천·상세를 판정한다', async (t) => {
  const { page, card, requests } = await fixture(t);
  assert.equal(judgeCard(card, STUDENT, { now: page.getNow() }).result, 'eligible');
  t.mock.timers.setTime(new Date('2026-11-17T10:01:00+09:00').getTime());
  const now = page.getNow();
  assert.equal(judgeCard(card, STUDENT, { now }).result, 'ineligible');
  assert.equal(listCards(page.data.cards, {}, now).items.length, 0);
  assert.equal(recommendCards(page.data.cards, STUDENT, now).total, 0);
  assert.equal(requests(), 1);
});

test('명시된 마감 시각까지 열려 있고 그 직후 열린 화면의 결과를 갱신한다', async (t) => {
  const { page, card } = await fixture(t, { now: '2026-11-17T09:59:59.999+09:00' });
  const seen = [];
  watch(page, (now) => seen.push(deadlineOf(card, now).state));
  t.mock.timers.tick(1);
  assert.equal(deadlineOf(card, page.getNow()).state, 'open');
  assert.deepEqual(seen, []);
  t.mock.timers.tick(1);
  assert.deepEqual(seen, ['closed']);
});

test('한국시간 자정을 넘으면 시각 없는 마감·D-day·기준일이 갱신된다', async (t) => {
  const { page, card } = await fixture(t, { now: '2026-11-17T23:59:59.999+09:00', end: '2026-11-17', timeGiven: false });
  const seen = [];
  watch(page, (now) => seen.push(deadlineOf(card, now)));
  t.mock.timers.tick(2);
  assert.equal(seen.length, 1);
  assert.equal(seen[0].state, 'closed');
  assert.equal(seen[0].daysLeft, -1);
});

test('36시간 경고는 generated_at의 초·밀리초를 유지하며 시계가 되돌아가면 숨긴다', async (t) => {
  const { page, banner, window } = await fixture(t, {
    generated: '2026-11-15T20:00:12.345+09:00', now: '2026-11-17T08:00:12.344+09:00',
  });
  watch(page, () => {});
  assert.equal(banner.hidden, true);
  t.mock.timers.tick(1);
  assert.equal(banner.hidden, true);
  t.mock.timers.tick(1);
  assert.equal(banner.hidden, false);
  assert.match(banner.textContent, /1일 전/);
  t.mock.timers.setTime(new Date('2026-11-17T08:00:00+09:00').getTime());
  window.dispatchEvent(new Event('focus'));
  assert.equal(banner.hidden, true);
  assert.equal(banner.textContent, '');
});

test('숨긴 탭은 그리지 않고 복귀 시 즉시 갱신하며 연속 포커스 이벤트는 중복 갱신하지 않는다', async (t) => {
  const { page, document, window } = await fixture(t);
  const seen = [];
  watch(page, (now) => seen.push(now));
  document.hidden = true;
  document.dispatchEvent(new Event('visibilitychange'));
  t.mock.timers.tick(120000);
  assert.equal(seen.length, 0);
  document.hidden = false;
  document.dispatchEvent(new Event('visibilitychange'));
  window.dispatchEvent(new Event('focus'));
  assert.equal(seen.length, 1);
  assert.equal(seen[0].toISOString(), '2026-11-17T01:01:00.000Z');
});

test('뒤로 가기 복원 시 갱신하고 해제 뒤에는 타이머·복귀 이벤트가 동작하지 않는다', async (t) => {
  const { page, window } = await fixture(t);
  let calls = 0;
  const stop = watchPageTime(page, () => calls++);
  window.dispatchEvent(new Event('pagehide'));
  t.mock.timers.tick(120000);
  assert.equal(calls, 0);
  window.dispatchEvent(new Event('pageshow'));
  assert.equal(calls, 1);
  stop();
  t.mock.timers.tick(86400000);
  window.dispatchEvent(new Event('pageshow'));
  assert.equal(calls, 1);
});

test('변화 없는 분에는 다시 그리지 않고 신청 외 단계 일정이 지나면 갱신한다', async (t) => {
  const { page, card } = await fixture(t, { end: '2026-11-20 10:00' });
  card.fields.stages = { status: 'human_verified', evidence: [], value: [{ name: '출품', end: '2026-11-17 10:01', time_given: true }] };
  const seen = [];
  watch(page, (now) => seen.push(calendarItems(card, now)));
  t.mock.timers.tick(60001);
  assert.equal(seen.length, 0);
  t.mock.timers.tick(60000);
  assert.equal(seen.length, 1);
  assert.equal(seen[0].find((item) => item.key === 'submit').isPast, true);
});

for (const mode of ['표시된 예시', '예시 파일 대체']) {
  test(`${mode}는 시간이 지나고 탭에 복귀해도 generated_at 기준일·안내를 유지한다`, async (t) => {
    const { page, banner, window } = await fixture(t, { sample: mode === '표시된 예시', fallback: mode === '예시 파일 대체' });
    const before = page.getNow().getTime();
    const text = banner.textContent;
    let calls = 0;
    watch(page, () => calls++);
    t.mock.timers.tick(3 * 86400000);
    window.dispatchEvent(new Event('focus'));
    assert.equal(page.getNow().getTime(), before);
    assert.equal(banner.textContent, text);
    assert.equal(calls, 0);
  });
}

for (const failure of ['render', 'key']) {
  test(`${failure} 예외 뒤에도 다음 분에 같은 변경을 다시 그린다`, async (t) => {
    const { page } = await fixture(t);
    const errors = t.mock.method(console, 'error', () => {});
    let attempts = 0;
    watch(page, () => {
      attempts++;
      if (failure === 'render' && attempts === 1) throw new Error('PRIVATE_ERROR');
    });
    const original = page.getNow;
    let broken = failure === 'key';
    page.getNow = () => {
      if (broken) { broken = false; throw new Error('PRIVATE_ERROR'); }
      return original();
    };
    t.mock.timers.tick(60001);
    assert.equal(errors.mock.callCount(), 1);
    assert.equal(attempts, failure === 'render' ? 1 : 0);
    t.mock.timers.tick(60000);
    assert.equal(attempts, failure === 'render' ? 2 : 1);
    assert.ok(!JSON.stringify(errors.mock.calls).includes('PRIVATE_ERROR'));
    t.mock.timers.tick(60000);
    assert.equal(attempts, failure === 'render' ? 2 : 1);
  });
}

test('명시된 신청 시작 시각이 지나면 시작 전 안내도 자동 갱신한다', async (t) => {
  const { page, card } = await fixture(t, { end: '2026-11-17 11:00' });
  card.fields.apply.value.start = '2026-11-17 10:00';
  const seen = [];
  watch(page, (now) => seen.push(judgeCard(card, STUDENT, { now }).reasons));
  t.mock.timers.tick(60001);
  assert.equal(seen.length, 1);
  assert.ok(seen[0].every((reason) => !reason.message.includes('신청 시작 전')));
});

for (const mode of ['링크 유지', '버튼 유지', '카드 제거', '버튼 제거']) {
  test(`자동 렌더의 포커스 복원: ${mode}`, async (t) => {
    const { page, document } = await fixture(t);
    let focused;
    const keeps = mode.endsWith('유지');
    const card = { dataset: { cardId: 'test-card' }, querySelectorAll: () => keeps ? [replacement] : [] };
    const active = { tagName: mode === '링크 유지' ? 'A' : 'BUTTON', textContent: '문의 초안 복사',
      isConnected: true, closest: () => card, getAttribute: () => 'card.html?id=test-card' };
    const replacement = { tagName: active.tagName, textContent: active.textContent,
      getAttribute: active.getAttribute, focus: () => { focused = '유지'; } };
    const status = { focus: () => { focused = '안내'; } };
    document.activeElement = active;
    document.querySelectorAll = () => mode === '카드 제거' ? [] : [card];
    document.getElementById = () => status;
    watch(page, () => { active.isConnected = false; });
    t.mock.timers.tick(60001);
    assert.equal(focused, keeps ? '유지' : '안내');
    if (!keeps) assert.equal(status.tabIndex, -1);
  });
}
