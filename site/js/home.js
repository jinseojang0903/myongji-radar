// 홈 화면 (#21): 레이더 첫 화면, 검색·필터, 마감 구간별 대회 목록, 기준일·게시판 목록.
// 마감 계산·필터·정렬은 cards.js(listCards)를 그대로 쓰고, 이 파일은 묶어서 그리기만 한다.
import { el, externalLink } from './html.js';
import { DELIVERABLE_TAGS, listCards, latestDeadlineChange, summaryLine, deliverableTags, boardNames, emptyListMessage, ddayLabel, upcomingApplication } from './cards.js';
import { loadPage, watchPageTime, badgeElements, ddayElement, deadlineTone, showConditionSummary, bindScopeFilter } from './page.js';
import { formatShortDate, formatKstDateTime, kstDateString } from './dates.js';
import { sourceLinks } from './detail.js';
import { renderRadar, WEEK_DAYS, MONTH_DAYS } from './radar.js';
import { createRadarPopover, popoverContent } from './radar-popover.js';
import { listSavedCards, migrateSavedCardIds, notifySavedCards, readSavedCardIds, removeUnavailableSavedCards, savedCardButton, watchSavedCards } from './saved-cards.js';

const BASE_FILTERS = { sort: 'deadline', urgentOnly: false, changedOnly: false, tags: [], campus: null };
const filters = { ...BASE_FILTERS, query: '', scope: '' };
// 레이더 점 안내 상자 (화면당 하나). 게시판 이름은 데이터를 읽은 뒤 채운다
let boardList = [];
const radarPopover = createRadarPopover((item) => popoverContent(item, boardList));

// 마감 구간. 마감일을 모르는 대회는 마지막 구간으로 따로 빼서 마감이 확실한 대회와 섞이지 않게 한다
const LANES = [
  { id: 'week', title: `${WEEK_DAYS}일 이내 마감` },
  { id: 'month', title: `${WEEK_DAYS + 1}~${MONTH_DAYS}일 남은 마감` },
  { id: 'later', title: `${MONTH_DAYS + 1}일 이상 남은 마감` },
  { id: 'upcoming', title: '접수 예정' },
  { id: 'unknown', title: '마감일 확인 필요', hint: '공지에서 마감일을 찾지 못했거나 읽지 못했습니다. 신청할 수 있는지 원문에서 먼저 확인하세요.' },
];

function laneOf(deadline, opening) {
  if (opening) return 'upcoming';
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
    document.getElementById('saved-contest-list').replaceChildren(el('p', { class: 'small' }, '관심 대회 정보를 불러오지 못했습니다.'));
    return;
  }
  const { data, rejected, isSample } = page;
  if (!isSample) migrateSavedCardIds(data.cards);
  renderTagChips();
  renderBoards(data.boards);
  boardList = data.boards;
  document.getElementById('scope-date').textContent = `명지대 공개 게시판 ${data.boards.length}곳을 매일 살펴봅니다`;
  // 게시판은 목록 아래 게시판 목록에서 빠질 뿐이므로 카드 건수에 섞지 않는다
  const rejectedCardCount = rejected.filter((item) => !item.id.startsWith('board:')).length;
  const render = (now = page.getNow()) => {
    renderStats(data, now);
    renderList(data, now, rejectedCardCount, isSample);
  };
  bindFilters(render);
  render();
  if (isSample) {
    document.getElementById('saved-contest-list').replaceChildren(el('p', { class: 'small' }, '실제 대회 정보를 불러오면 관심 대회를 볼 수 있습니다.'));
  } else {
    renderSavedList(data.cards, readSavedCardIds(), page.getNow());
    watchSavedCards((ids) => renderSavedList(data.cards, ids, page.getNow()));
  }
  watchPageTime(page, (now) => {
    render(now);
    if (!isSample) renderSavedList(data.cards, readSavedCardIds(), now);
  });
}

function renderSavedList(cards, ids, now) {
  const list = document.getElementById('saved-contest-list');
  try {
    drawSavedList(list, cards, ids, now);
  } catch {
    list?.replaceChildren(el('p', { class: 'small' }, '관심 대회를 표시하지 못했습니다.'));
  }
}

function drawSavedList(list, cards, ids, now) {
  const hadFocus = list.contains(document.activeElement);
  const focusedId = hadFocus ? document.activeElement.dataset.savedCard : null;
  const items = listSavedCards(cards, ids, now);
  const rows = items.map(({ card, deadline }) => el('li', { class: 'row saved-contest' },
    el('a', { href: `card.html?id=${encodeURIComponent(card.id)}` }, card.title),
    ddayElement(deadline), savedCardButton(card, true)));
  const missing = ids.size - items.length;
  const status = el('span', { class: 'small', role: 'status' });
  const clear = el('button', { type: 'button', class: 'btn' }, '빠진 대회 지우기');
  clear.addEventListener('click', () => {
    if (removeUnavailableSavedCards(cards)) notifySavedCards();
    else status.textContent = '이 브라우저에서 관심 대회를 지울 수 없습니다.';
  });
  list.replaceChildren(
    rows.length ? el('ul', { class: 'saved-contest-list' }, ...rows)
      : el('p', { class: 'empty' }, '다시 보고 싶은 대회의 관심 대회 버튼을 눌러 저장하세요.'),
    missing > 0 ? el('div', { class: 'row' }, el('span', { class: 'small' }, `현재 목록에서 빠진 관심 대회 ${missing}건`), clear, status) : null);
  if (hadFocus) {
    const next = [...list.querySelectorAll('[data-saved-card]')].find((button) => button.dataset.savedCard === focusedId);
    (next ?? document.getElementById('h-saved')).focus({ preventScroll: true });
  }
}

// 첫 화면 숫자는 필터와 상관없이 전체 기준으로 센다.
function renderStats(data, now) {
  const { items } = listCards(data.cards, BASE_FILTERS, now);
  let upcomingCount = 0;
  let openCount = 0;
  let urgentCount = 0;
  for (const { card, deadline } of items) {
    const lane = laneOf(deadline, upcomingApplication(card, deadline, now));
    if (lane === 'upcoming') upcomingCount += 1;
    else if (deadline.state === 'open') openCount += 1;
    if (lane === 'week') urgentCount += 1;
  }
  const stat = (label, value, unit, extraClass = '') => el('div', { class: ['stat', extraClass].filter(Boolean).join(' ') },
    el('dt', {}, label), el('dd', {}, String(value), el('small', {}, unit)));
  document.getElementById('hero-stats').replaceChildren(
    stat('접수 중', openCount, '건'),
    stat('접수 예정', upcomingCount, '건'),
    stat(`${WEEK_DAYS}일 이내 마감`, urgentCount, '건', urgentCount > 0 ? 'hot' : ''),
    stat('마감일 확인 필요', items.length - openCount - upcomingCount, '건', 'unknown'));
}

function renderTagChips() {
  const chips = DELIVERABLE_TAGS.filter((tag) => tag !== '기타').map((tag) => el('button', {
    type: 'button', class: 'chip', 'data-tag': tag, 'aria-pressed': 'false',
  }, tag));
  document.getElementById('tag-chips').replaceChildren(...chips);
}

function bindFilters(onChange) {
  bindScopeFilter((scope) => {
    filters.scope = scope;
    onChange();
  });
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
  const radarItems = items.map(({ card, deadline }) => {
    const opening = upcomingApplication(card, deadline, now);
    const lane = laneOf(deadline, opening);
    return { card, deadline, opening, lane, isUrgent: lane === 'week',
      label: opening ?? (deadline.state === 'open' ? ddayLabel(deadline).text : '마감일 확인 필요') };
  });
  // 다시 그리면 점이 새로 만들어지므로 떠 있던 안내 상자는 닫는다
  radarPopover.hide();
  try {
    document.getElementById('radar').replaceChildren(renderRadar(radarItems, radarPopover.handlers), radarPopover.element);
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
  const savedIds = isSample ? null : readSavedCardIds();
  const lanes = LANES.map((lane) => {
    const laneItems = radarItems.filter((item) => item.lane === lane.id);
    if (laneItems.length === 0) return null;
    const cards = laneItems.flatMap(({ card, deadline, opening }) => {
      try {
        const element = renderItem(card, deadline, data.boards, index, opening);
        if (savedIds) {
          const source = element.querySelector('.item-source');
          element.append(el('div', { class: 'row item-actions' }, source, savedCardButton(card, savedIds.has(card.id))));
        }
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
        lane.hint ? el('p', { class: 'small' }, lane.hint) : null),
      el('div', { class: 'list' }, ...cards));
  }).filter(Boolean);
  if (failedCount > 0) {
    document.getElementById('list-status').textContent += ` · 표시하지 못한 ${failedCount}건이 있습니다`;
  }
  list.replaceChildren(...lanes);
}

function renderItem(card, deadline, boards, index, opening) {
  const change = latestDeadlineChange(card);
  const summary = summaryLine(card, deadline);
  // 카드 전체가 링크라 이름이 제목·배지·요약까지 길게 읽혔다. 이름은 제목과 D-day로 하고, 제목을 h3로 두어
  // 제목 단위로 건너뛸 수 있게 (#104). id는 목록 순번으로 만든다 (카드 id가 겹쳐 들어와도 다른 카드 이름을 읽지 않게)
  const titleId = `card-title-${index}`;
  const ddayId = `card-dday-${index}`;
  const dday = ddayElement(deadline, 'spacer');
  dday.id = ddayId;
  const openingId = opening ? `card-opening-${index}` : null;
  // "마감일 없음"은 상시 모집처럼 읽혀서 홈에서는 확인이 필요하다고 쓴다.
  // 화면에는 레이더와 같은 '?'를 보이고, 카드 이름(aria-labelledby)으로는 '마감일 확인 필요'를 읽힌다
  if (deadline.state !== 'open') {
    dday.replaceChildren(el('span', { 'aria-hidden': 'true' }, '마감일 ?'), el('span', { class: 'sr-only' }, '마감일 확인 필요'));
  }
  const source = sourceLinks(card, boards).find((link) => link.url);
  const item = el('a', { class: 'item', href: `card.html?id=${encodeURIComponent(card.id)}`, 'data-deadline': deadlineTone(deadline), 'data-card-id': card.id, 'aria-labelledby': [titleId, openingId, ddayId].filter(Boolean).join(' ') },
    el('div', { class: 'row' },
      el('h3', { class: 'title', id: titleId }, card.title),
      dday),
    opening ? el('span', { class: 'badge s-before', id: openingId }, opening) : null,
    el('span', { class: 'org' }, [card.organizer, card.scope, boardNames(card, boards).join(', ')].filter(Boolean).join(' · ')),
    el('div', { class: 'row' },
      ...deliverableTags(card).map((tag) => el('span', { class: 'tag' }, tag)),
      ...badgeElements(card)),
    change && el('p', { class: 'notice' },
      `⚠ 마감 변경: ${formatShortDate(change.old)} → ${formatShortDate(change.new)}`),
    summary && el('span', { class: 'reason' }, summary),
  );
  // 원문 링크는 카드 링크 안에 넣을 수 없어(링크 안 링크) 카드 아래에 붙인다
  const link = source ? externalLink(source.url, `원문 공지 · ${source.boardName} ↗`) : null;
  link?.classList.add('item-source');
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
