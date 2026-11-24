import { test } from 'node:test';
import assert from 'node:assert/strict';
import { listSavedCards, migrateSavedCardIds, readSavedCardIds, removeUnavailableSavedCards, SAVED_CARDS_KEY, toggleSavedCard } from '../site/js/saved-cards.js';

test('관심 대회는 카드 ID만 저장하고 같은 버튼으로 해제한다', () => {
  const values = new Map([[SAVED_CARDS_KEY, '["existing","existing",null,"../bad"]']]);
  const storage = { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
  assert.deepEqual([...readSavedCardIds(storage)], ['existing']);
  assert.equal(toggleSavedCard('new-card', storage), true);
  assert.equal(values.get(SAVED_CARDS_KEY), '["existing","new-card"]');
  assert.equal(toggleSavedCard('existing', storage), true);
  assert.deepEqual([...readSavedCardIds(storage)], ['new-card']);
  assert.equal(toggleSavedCard('../bad', storage), false);

  values.set(SAVED_CARDS_KEY, '{');
  assert.equal(toggleSavedCard('recovered', storage), true);
  assert.deepEqual([...readSavedCardIds(storage)], ['recovered']);
});

test('브라우저가 저장소 읽기나 쓰기를 막으면 저장 실패를 알린다', () => {
  const blocked = () => { throw new Error('storage blocked'); };
  assert.deepEqual([...readSavedCardIds({ getItem: blocked })], []);
  assert.equal(toggleSavedCard('card', { getItem: blocked, setItem: assert.fail }), false);
  assert.equal(toggleSavedCard('card', { getItem: () => null, setItem: blocked }), false);
});

test('통합된 ID를 이어서 저장하고 학생이 빠진 대회만 지울 수 있다', () => {
  let saved = '["mju-ctl-ctl-792-235972","merged","gone"]';
  const storage = { getItem: () => saved, setItem: (_key, value) => { saved = value; } };
  const cards = [{ id: 'merged', sources: [{ board_id: 'mju-ctl', url: 'https://ctl.mju.ac.kr/bbs/ctl/792/235972/artclView.do' }] }];
  assert.equal(migrateSavedCardIds(cards, storage), true);
  assert.deepEqual([...readSavedCardIds(storage)], ['merged', 'gone']);
  assert.equal(removeUnavailableSavedCards(cards, storage), true);
  assert.deepEqual([...readSavedCardIds(storage)], ['merged']);
});

test('관심 목록은 마감 가까운 대회부터 보여 주고 마감된 대회도 남긴다', () => {
  const card = (id, end) => ({ id, title: id, fields: { apply: { status: 'ai', value: { end } } } });
  const cards = [card('later', '2026-12-25'), card('past', '2026-11-22'), card('unknown', null),
    card('soon', '2026-11-24 17:00'), card('not-saved', '2026-11-23')];
  const ids = new Set(['later', 'past', 'unknown', 'soon', 'unavailable']);
  const items = listSavedCards(cards, ids, new Date('2026-11-23T12:00:00+09:00'));
  assert.deepEqual(items.map(({ card }) => card.id), ['soon', 'later', 'unknown', 'past']);
  assert.equal(items.at(-1).deadline.state, 'closed');
});
