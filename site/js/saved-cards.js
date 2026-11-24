import { deadlineOf, resolveCard } from './cards.js';
import { el } from './html.js';

export const SAVED_CARDS_KEY = 'myongji-radar.saved-cards.v1';
const CHANGE_EVENT = 'saved-cards-change';
const CARD_ID = /^[a-z0-9][a-z0-9-]{0,63}$/;
const validId = (id) => typeof id === 'string' && CARD_ID.test(id);

function readIds(storage) {
  let value;
  try {
    value = JSON.parse(storage.getItem(SAVED_CARDS_KEY));
  } catch (error) {
    if (error instanceof SyntaxError) return new Set();
    throw error;
  }
  return new Set(Array.isArray(value) ? value.filter(validId) : []);
}

export function readSavedCardIds(storage) {
  try {
    return readIds(storage ?? globalThis.localStorage);
  } catch {
    return new Set();
  }
}

export function toggleSavedCard(id, storage) {
  if (!validId(id)) return false;
  return updateSavedIds((ids) => {
    if (!ids.delete(id)) ids.add(id);
  }, storage);
}

function updateSavedIds(update, storage) {
  try {
    storage ??= globalThis.localStorage;
    const ids = readIds(storage);
    const original = JSON.stringify([...ids]);
    update(ids);
    const next = JSON.stringify([...ids]);
    if (next !== original) storage.setItem(SAVED_CARDS_KEY, next);
    return true;
  } catch {
    return false;
  }
}

export function migrateSavedCardIds(cards, storage) {
  return updateSavedIds((ids) => {
    for (const id of [...ids]) {
      const current = resolveCard(cards, id);
      if (current && current.id !== id) {
        ids.delete(id);
        ids.add(current.id);
      }
    }
  }, storage);
}

export function removeUnavailableSavedCards(cards, storage) {
  return updateSavedIds((ids) => {
    for (const id of ids) if (!resolveCard(cards, id)) ids.delete(id);
  }, storage);
}

export function listSavedCards(cards, ids, now) {
  const items = cards.filter((card) => ids.has(card.id)).map((card) => ({ card, deadline: deadlineOf(card, now) }));
  const sortKey = (deadline) => deadline.state === 'open' ? `0 ${deadline.endDate} ${deadline.endTime ?? '24:00'}`
    : deadline.state === 'closed' ? '2' : '1';
  return items.sort((a, b) => sortKey(a.deadline).localeCompare(sortKey(b.deadline)) || a.card.title.localeCompare(b.card.title, 'ko'));
}

export function savedCardButton(card, isSaved) {
  const status = el('span', { class: 'small', role: 'status', hidden: true });
  const button = el('button', {
    type: 'button', class: 'btn saved-card-toggle', 'data-saved-card': card.id,
    'aria-pressed': String(isSaved), 'aria-label': `관심 대회 · ${card.title}`,
  }, '관심 대회');
  button.addEventListener('click', () => {
    if (!toggleSavedCard(card.id)) {
      status.hidden = false;
      status.textContent = '이 브라우저에 관심 대회를 저장할 수 없습니다.';
      return;
    }
    status.hidden = true;
    status.textContent = '';
    notifySavedCards();
  });
  return el('div', { class: 'row' }, button, status);
}

export function notifySavedCards() {
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function watchSavedCards(onChange = () => {}) {
  const refresh = () => {
    const ids = readSavedCardIds();
    for (const button of document.querySelectorAll('[data-saved-card]')) {
      button.setAttribute('aria-pressed', String(ids.has(button.dataset.savedCard)));
    }
    onChange(ids);
  };
  window.addEventListener(CHANGE_EVENT, refresh);
  window.addEventListener('pageshow', (event) => { if (event.persisted) refresh(); });
  window.addEventListener('storage', (event) => { if (event.key === SAVED_CARDS_KEY || event.key === null) refresh(); });
}
