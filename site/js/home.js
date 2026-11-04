// 홈 화면 (#21): 대회 목록, 필터, 기준일·게시판 목록.
import { el, externalLink } from './html.js';
import { DELIVERABLE_TAGS, listCards, latestDeadlineChange, summaryLine, deliverableTags, boardNames } from './cards.js';
import { loadPage, badgeElements, ddayElement, showConditionSummary } from './page.js';
import { formatShortDate, formatKstDateTime, kstDateString } from './dates.js';

const filters = { sort: 'deadline', urgentOnly: false, changedOnly: false, tags: [], campus: null };

main();

async function main() {
  showConditionSummary();
  const page = await loadPage({ errorTarget: document.getElementById('card-list') });
  if (!page) {
    document.getElementById('summary-line').replaceChildren('대회 정보를 불러오지 못했습니다.');
    return;
  }
  const { data, now, rejected, isSample } = page;
  renderTagChips();
  renderBoards(data.boards);
  // 게시판은 목록 아래 게시판 목록에서 빠질 뿐이므로 카드 건수에 섞지 않는다
  const rejectedCardCount = rejected.filter((item) => !item.id.startsWith('board:')).length;
  const render = () => renderList(data, now, rejectedCardCount, isSample);
  bindFilters(render);
  render();
}

function renderTagChips() {
  const chips = DELIVERABLE_TAGS.filter((tag) => tag !== '기타').map((tag) => el('button', {
    type: 'button', class: 'chip', 'data-tag': tag, 'aria-pressed': 'false',
  }, tag));
  document.getElementById('tag-chips').replaceChildren(...chips);
}

function bindFilters(onChange) {
  document.querySelectorAll('[data-sort]').forEach((button) => button.addEventListener('click', () => {
    filters.sort = button.dataset.sort;
    document.querySelectorAll('[data-sort]').forEach((other) => other.setAttribute('aria-pressed', String(other === button)));
    onChange();
  }));
  document.querySelectorAll('[data-toggle]').forEach((button) => button.addEventListener('click', () => {
    const key = button.dataset.toggle;
    filters[key] = !filters[key];
    button.setAttribute('aria-pressed', String(filters[key]));
    onChange();
  }));
  document.querySelectorAll('[data-tag]').forEach((button) => button.addEventListener('click', () => {
    const { tag } = button.dataset;
    filters.tags = filters.tags.includes(tag) ? filters.tags.filter((item) => item !== tag) : [...filters.tags, tag];
    button.setAttribute('aria-pressed', String(filters.tags.includes(tag)));
    onChange();
  }));
  document.querySelectorAll('[data-campus]').forEach((button) => button.addEventListener('click', () => {
    filters.campus = filters.campus === button.dataset.campus ? null : button.dataset.campus;
    document.querySelectorAll('[data-campus]').forEach((other) => {
      other.setAttribute('aria-pressed', String(other.dataset.campus === filters.campus));
    });
    onChange();
  }));
}

function renderList(data, now, rejectedCount, isSample) {
  const { items, closedCount } = listCards(data.cards, filters, now);
  const openCount = data.cards.length - closedCount;

  document.getElementById('summary-line').replaceChildren(
    el('span', {}, '신청 중인 대회 ', el('b', {}, `${openCount}건`)),
    el('span', { 'aria-hidden': 'true' }, '·'),
    el('span', {}, `기준일 ${formatShortDate(kstDateString(now))}${isSample ? ' (예시 데이터 기준)' : ''}`),
    el('span', { 'aria-hidden': 'true' }, '·'),
    el('span', {}, `마지막 확인 ${formatKstDateTime(new Date(data.generated_at))}`),
    el('span', { 'aria-hidden': 'true' }, '·'),
    el('a', { href: '#boards' }, `확인하는 게시판 ${data.boards.length}곳 보기`),
  );

  const notes = [`${items.length}건 표시`];
  if (closedCount > 0) notes.push(`마감이 지난 ${closedCount}건은 뺐습니다`);
  if (rejectedCount > 0) notes.push(`형식이 맞지 않는 ${rejectedCount}건은 뺐습니다`);
  document.getElementById('list-status').textContent = notes.join(' · ');

  const list = document.getElementById('card-list');
  if (items.length === 0) {
    list.replaceChildren(el('p', { class: 'empty' }, '조건에 맞는 대회가 없습니다. 필터를 줄여 보세요.'));
    return;
  }
  let failedCount = 0;
  const elements = items.flatMap(({ card, deadline }) => {
    try {
      return [renderItem(card, deadline, data.boards)];
    } catch (error) {
      // 카드 하나 때문에 목록 전체가 사라지지 않게 그 카드만 빼고 알린다
      console.error('카드를 그리지 못함', card.id, error);
      failedCount += 1;
      return [];
    }
  });
  if (failedCount > 0) {
    document.getElementById('list-status').textContent += ` · 표시하지 못한 ${failedCount}건이 있습니다`;
  }
  list.replaceChildren(...elements);
}

function renderItem(card, deadline, boards) {
  const change = latestDeadlineChange(card);
  const summary = summaryLine(card, deadline);
  return el('a', { class: 'item', href: `card.html?id=${encodeURIComponent(card.id)}` },
    el('div', { class: 'row' },
      el('span', { class: 'title' }, card.title),
      ddayElement(deadline, 'spacer')),
    el('span', { class: 'org' }, [card.organizer, card.scope, boardNames(card, boards).join(', ')].filter(Boolean).join(' · ')),
    el('div', { class: 'row' },
      ...deliverableTags(card).map((tag) => el('span', { class: 'tag' }, tag)),
      ...badgeElements(card)),
    change && el('p', { class: 'notice' },
      `⚠ 마감 변경: ${formatShortDate(change.old)} → ${formatShortDate(change.new)}`),
    summary && el('span', { class: 'reason' }, summary),
  );
}

function renderBoards(boards) {
  document.getElementById('h-boards').textContent = `확인하는 게시판 (${boards.length}곳, 로그인 없는 공개 게시판만)`;
  const parts = boards.flatMap((board, index) => [
    index > 0 ? ' · ' : null,
    board.list_url ? externalLink(board.list_url, board.name) : board.name,
  ]);
  document.getElementById('board-list').replaceChildren(...parts.filter(Boolean));
}
