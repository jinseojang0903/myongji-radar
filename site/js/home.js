// 홈 화면 (#21): 레이더 첫 화면, 검색·필터, 마감 구간별 대회 목록, 기준일·게시판 목록.
// 마감 계산·필터·정렬은 cards.js(listCards)를 그대로 쓰고, 이 파일은 묶어서 그리기만 한다.
import { el, externalLink } from './html.js';
import { DELIVERABLE_TAGS, listCards, latestDeadlineChange, summaryLine, deliverableTags, boardNames, emptyListMessage, ddayLabel } from './cards.js';
import { loadPage, watchPageTime, badgeElements, ddayElement, deadlineTone, showConditionSummary } from './page.js';
import { formatShortDate, formatKstDateTime, kstDateString } from './dates.js';
import { sourceLinks } from './detail.js';
import { renderRadar, WEEK_DAYS, MONTH_DAYS } from './radar.js';

const BASE_FILTERS = { sort: 'deadline', urgentOnly: false, changedOnly: false, tags: [], campus: null };
const filters = { ...BASE_FILTERS, query: '' };

// 마감 구간. 마감일을 모르는 대회는 마지막 구간으로 따로 빼서 마감이 확실한 대회와 섞이지 않게 한다
const LANES = [
  { id: 'week', title: '이번 주 마감', hint: '7일 이내' },
  { id: 'month', title: '이번 달 마감', hint: '30일 이내' },
  { id: 'later', title: '여유 있음', hint: '30일 뒤' },
  { id: 'unknown', title: '마감일 확인 필요', hint: '공지에서 마감일을 찾지 못했거나 읽지 못했습니다. 신청할 수 있는지 원문에서 먼저 확인하세요.' },
];

function laneOf(deadline) {
  if (deadline.state !== 'open') return 'unknown';
  if (deadline.daysLeft <= WEEK_DAYS) return 'week';
  return deadline.daysLeft <= MONTH_DAYS ? 'month' : 'later';
}

main();

async function main() {
  showConditionSummary();
  const page = await loadPage({ errorTarget: document.getElementById('card-list') });
  if (!page) {
    document.getElementById('summary-line').replaceChildren('대회 정보를 불러오지 못했습니다.');
    return;
  }
  const { data, rejected, isSample } = page;
  renderTagChips();
  renderBoards(data.boards);
  document.getElementById('scope-date').textContent = `명지대 공개 게시판 ${data.boards.length}곳을 매일 살펴봅니다`;
  // 게시판은 목록 아래 게시판 목록에서 빠질 뿐이므로 카드 건수에 섞지 않는다
  const rejectedCardCount = rejected.filter((item) => !item.id.startsWith('board:')).length;
  const render = (now = page.getNow()) => {
    renderStats(data, now);
    renderList(data, now, rejectedCardCount, isSample);
  };
  bindFilters(render);
  render();
  watchPageTime(page, render);
}

// 첫 화면 숫자: 필터와 상관없이 전체 기준. "마감 전 대회" 하나로 세면 마감일을 모르는 대회까지 신청 중처럼 보여 나눈다
function renderStats(data, now) {
  const { items } = listCards(data.cards, BASE_FILTERS, now);
  const knownCount = items.filter(({ deadline }) => deadline.state === 'open').length;
  const urgentCount = items.filter(({ deadline }) => laneOf(deadline) === 'week').length;
  const stat = (label, value, unit, extraClass = '') => el('div', { class: ['stat', extraClass].filter(Boolean).join(' ') },
    el('dt', {}, label), el('dd', {}, String(value), el('small', {}, unit)));
  document.getElementById('hero-stats').replaceChildren(
    stat('마감일이 있는 대회', knownCount, '건'),
    stat('7일 안에 마감', urgentCount, '건', urgentCount > 0 ? 'hot' : ''),
    stat('마감일 확인 필요', items.length - knownCount, '건', 'unknown'));
}

function renderTagChips() {
  const chips = DELIVERABLE_TAGS.filter((tag) => tag !== '기타').map((tag) => el('button', {
    type: 'button', class: 'chip', 'data-tag': tag, 'aria-pressed': 'false',
  }, tag));
  document.getElementById('tag-chips').replaceChildren(...chips);
}

function bindFilters(onChange) {
  document.getElementById('home-search').addEventListener('input', (event) => {
    filters.query = event.target.value;
    onChange();
  });
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

// 제목·주관·게시판 이름에 검색어가 들어 있는지 (대소문자 무시)
function matchesQuery(card, boards, query) {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return [card.title, card.organizer ?? '', ...boardNames(card, boards)].join(' ').toLowerCase().includes(needle);
}

function renderList(data, now, rejectedCount, isSample) {
  const listed = listCards(data.cards, filters, now);
  const items = listed.items.filter(({ card }) => matchesQuery(card, data.boards, filters.query));
  const { closedCount } = listed;

  document.getElementById('summary-line').replaceChildren(
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
  const radarItems = items.map(({ card, deadline }) => ({
    card, deadline, isUrgent: laneOf(deadline) === 'week', label: deadline.state === 'open' ? ddayLabel(deadline).text : '마감일 확인 필요',
  }));
  try {
    document.getElementById('radar').replaceChildren(renderRadar(radarItems, focusCard));
  } catch (error) {
    // 레이더는 보조 그림이라, 그리지 못해도 같은 정보가 있는 목록은 계속 그린다
    console.error('레이더를 그리지 못함', error);
    document.getElementById('radar').replaceChildren();
  }

  if (items.length === 0) {
    const message = emptyListMessage({ cardCount: data.cards.length, closedCount, rejectedCount },
      '조건에 맞는 대회가 없습니다. 검색어나 필터를 줄여 보세요.');
    list.replaceChildren(el('p', { class: 'empty' }, message));
    return;
  }
  let failedCount = 0;
  let index = 0;
  const lanes = LANES.map((lane) => {
    const laneItems = items.filter(({ deadline }) => laneOf(deadline) === lane.id);
    if (laneItems.length === 0) return null;
    const cards = laneItems.flatMap(({ card, deadline }) => {
      try {
        const element = renderItem(card, deadline, data.boards, index);
        index += 1;
        return [element];
      } catch (error) {
        // 카드 하나 때문에 목록 전체가 사라지지 않게 그 카드만 빼고 알린다
        console.error('카드를 그리지 못함', card.id, error);
        failedCount += 1;
        return [];
      }
    });
    return el('section', { class: `lane lane-${lane.id}`, 'aria-labelledby': `lane-${lane.id}` },
      el('div', { class: 'lane-head' },
        el('h2', { id: `lane-${lane.id}` }, lane.title, el('span', { class: 'lane-count' }, `${laneItems.length}건`)),
        el('p', { class: 'small' }, lane.hint)),
      el('div', { class: 'list' }, ...cards));
  }).filter(Boolean);
  if (failedCount > 0) {
    document.getElementById('list-status').textContent += ` · 표시하지 못한 ${failedCount}건이 있습니다`;
  }
  list.replaceChildren(...lanes);
}

// 레이더 점을 누르면 목록의 그 카드로 옮겨 가 포커스를 준다 (키보드 사용자는 목록에서 바로 닿는다)
function focusCard(cardId) {
  const target = document.querySelector(`[data-card-id="${CSS.escape(cardId)}"]`);
  if (!target) return;
  // CSS의 scroll-behavior는 여기서 준 옵션을 이기지 못해 동작 줄이기 설정을 직접 확인한다
  const isReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  target.scrollIntoView({ behavior: isReducedMotion ? 'auto' : 'smooth', block: 'center' });
  target.focus({ preventScroll: true });
  target.classList.add('flash');
  setTimeout(() => target.classList.remove('flash'), 1400);
}

function renderItem(card, deadline, boards, index) {
  const change = latestDeadlineChange(card);
  const summary = summaryLine(card, deadline);
  // 카드 전체가 링크라 이름이 제목·배지·요약까지 길게 읽혔다. 이름은 제목과 D-day로 하고, 제목을 h3로 두어
  // 제목 단위로 건너뛸 수 있게 (#104). id는 목록 순번으로 만든다 (카드 id가 겹쳐 들어와도 다른 카드 이름을 읽지 않게)
  const titleId = `card-title-${index}`;
  const ddayId = `card-dday-${index}`;
  const dday = ddayElement(deadline, 'spacer');
  dday.id = ddayId;
  // "마감일 없음"은 상시 모집처럼 읽혀서 홈에서는 확인이 필요하다고 쓴다.
  // 화면에는 레이더와 같은 '?'를 보이고, 카드 이름(aria-labelledby)으로는 '마감일 확인 필요'를 읽힌다
  if (deadline.state !== 'open') {
    dday.replaceChildren(el('span', { 'aria-hidden': 'true' }, '마감일 ?'), el('span', { class: 'sr-only' }, '마감일 확인 필요'));
  }
  const source = sourceLinks(card, boards).find((link) => link.url);
  const item = el('a', { class: 'item', href: `card.html?id=${encodeURIComponent(card.id)}`, 'data-deadline': deadlineTone(deadline), 'data-card-id': card.id, 'aria-labelledby': `${titleId} ${ddayId}` },
    el('div', { class: 'row' },
      el('h3', { class: 'title', id: titleId }, card.title),
      dday),
    el('span', { class: 'org' }, [card.organizer, card.scope, boardNames(card, boards).join(', ')].filter(Boolean).join(' · ')),
    el('div', { class: 'row' },
      ...deliverableTags(card).map((tag) => el('span', { class: 'tag' }, tag)),
      ...badgeElements(card)),
    change && el('p', { class: 'notice' },
      `⚠ 마감 변경: ${formatShortDate(change.old)} → ${formatShortDate(change.new)}`),
    summary && el('span', { class: 'reason' }, summary),
  );
  // 원문 링크는 카드 링크 안에 넣을 수 없어(링크 안 링크) 카드 아래에 붙인다
  if (!source) return el('div', { class: 'item-wrap' }, item);
  const link = externalLink(source.url, `원문 공지 · ${source.boardName} ↗`);
  link.classList.add('item-source');
  return el('div', { class: 'item-wrap' }, item, link);
}

function renderBoards(boards) {
  document.getElementById('h-boards').textContent = `확인하는 게시판 (${boards.length}곳, 로그인 없는 공개 게시판만)`;
  const parts = boards.flatMap((board, index) => [
    index > 0 ? ' · ' : null,
    board.list_url ? externalLink(board.list_url, board.name) : board.name,
  ]);
  document.getElementById('board-list').replaceChildren(...parts.filter(Boolean));
}
