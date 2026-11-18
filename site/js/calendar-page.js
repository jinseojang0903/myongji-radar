// 달력 화면 (#177): 날짜별로 끝나는 대회. 계산은 calendar.js, 이 파일은 그리기만 한다.
// 달력 칸과 아래 "이 달의 마감" 목록은 같은 일정이다. 좁은 화면에서는 칸에 건수만 두고 목록에서 읽는다.
import { el } from './html.js';
import { loadPage, watchPageTime, ddayElement, showConditionSummary } from './page.js';
import { ddayLabel } from './cards.js';
import { formatShortDate, kstDateString } from './dates.js';
import { monthOf, shiftMonth, monthGrid, deadlineEvents, groupByDate, unknownDeadlineCount } from './calendar.js';

const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토'];
// 사용자가 옮겨 간 달. null이면 기준일이 있는 달 (자정을 넘기면 따라 바뀐다)
let pickedMonth = null;

main();

async function main() {
  showConditionSummary();
  const page = await loadPage({ errorTarget: document.getElementById('cal-body') });
  if (!page) {
    document.getElementById('cal-note').textContent = '대회 정보를 불러오지 못했습니다.';
    return;
  }
  const currentMonth = () => monthOf(kstDateString(page.getNow()));
  const render = (now = page.getNow()) => renderCalendar(page.data, now, page.isSample);
  const move = (delta) => {
    pickedMonth = shiftMonth(pickedMonth ?? currentMonth(), delta) ?? pickedMonth;
    render();
  };
  document.getElementById('cal-prev').addEventListener('click', () => move(-1));
  document.getElementById('cal-next').addEventListener('click', () => move(1));
  document.getElementById('cal-today').addEventListener('click', () => {
    pickedMonth = null;
    render();
  });
  render();
  watchPageTime(page, render);
}

function renderCalendar(data, now, isSample) {
  const today = kstDateString(now);
  const month = pickedMonth ?? monthOf(today);
  const { events, failedCardIds } = deadlineEvents(data.cards, now);
  const monthEvents = events.filter((event) => monthOf(event.date) === month);
  const byDate = groupByDate(monthEvents);

  document.getElementById('cal-title').textContent = `${Number(month.slice(0, 4))}년 ${Number(month.slice(5, 7))}월`;
  document.getElementById('cal-body').replaceChildren(monthTable(month, today, byDate));
  renderAgenda(byDate, monthEvents.length);
  renderNote(data, now, today, isSample, failedCardIds.length);
}

function monthTable(month, today, byDate) {
  const head = el('tr', {}, ...WEEKDAYS.map((name, index) => el('th', { scope: 'col', class: index === 0 && 'sun' },
    name, el('span', { class: 'sr-only' }, '요일'))));
  const rows = monthGrid(month).map((week) => el('tr', {}, ...week.map((date, index) => dayCell(date, index, month, today, byDate))));
  return el('table', { class: 'cal', 'aria-labelledby': 'cal-title' }, el('thead', {}, head), el('tbody', {}, ...rows));
}

function dayCell(date, weekday, month, today, byDate) {
  const isInMonth = monthOf(date) === month;
  // 앞뒤 달 날짜는 숫자만 둔다. 일정은 그 달로 옮겨 가서 본다 (목록이 달 단위라 섞이면 헷갈린다)
  const dayEvents = isInMonth ? byDate.get(date) ?? [] : [];
  const isToday = date === today;
  return el('td', { class: [!isInMonth && 'out', weekday === 0 && 'sun'].filter(Boolean).join(' ') || null, 'aria-current': isToday && 'date' },
    el('span', { class: 'cal-day' }, String(Number(date.slice(8, 10))), isToday && el('span', { class: 'cal-today' }, '오늘')),
    dayEvents.length > 0 && el('a', { class: 'cal-count', href: `#day-${date}`, 'data-tone': dayTone(dayEvents) },
      `${dayEvents.length}건`, el('span', { class: 'sr-only' }, ` 마감, ${formatShortDate(date)} 목록으로`)),
    dayEvents.length > 0 && el('ul', { class: 'cal-events' }, ...dayEvents.map((event) => el('li', {}, eventLink(event)))));
}

/** 마감 색 기준은 홈의 D-day 칩과 같다: 지남 · 7일 이내 · 그 밖 */
function toneOf(event) {
  if (event.isPast) return 'past';
  return ddayLabel({ state: 'open', daysLeft: event.daysLeft }).isUrgent ? 'soon' : 'open';
}

/** 좁은 화면의 건수 칸 색: 그날 일정 가운데 가장 급한 것 */
function dayTone(dayEvents) {
  const tones = dayEvents.map(toneOf);
  return ['soon', 'open', 'past'].find((tone) => tones.includes(tone));
}

function eventLink(event) {
  // 칸이 좁아 "참가 신청 마감"을 "신청 마감"으로 줄인다. 온전한 이름은 아래 목록에 있다
  const shortLabel = event.label.replace(/^(참가|작품) /, '');
  return el('a', { class: 'cal-event', href: `card.html?id=${encodeURIComponent(event.cardId)}`, 'data-tone': toneOf(event), 'data-verified': String(event.isVerified) },
    el('span', { class: 'cal-event-label' }, event.time ? `${shortLabel} ${event.time}` : shortLabel,
      event.isPast && el('span', { class: 'sr-only' }, ' (지남)'),
      !event.isVerified && el('span', { class: 'sr-only' }, ' (확인 전)')),
    el('span', { class: 'cal-event-title' }, event.title));
}

function renderAgenda(byDate, count) {
  document.getElementById('h-agenda').textContent = `이 달의 마감 (${count}건)`;
  const list = document.getElementById('cal-agenda');
  if (count === 0) {
    list.replaceChildren(el('li', { class: 'empty' }, '이 달에 끝나는 신청·출품 마감이 없습니다. 다른 달로 옮겨 보세요.'));
    return;
  }
  list.replaceChildren(...[...byDate.entries()].map(([date, dayEvents]) => {
    // 그날 일정이 모두 지났을 때만 "지남" (오늘 15시 마감이 지나도 23시 마감이 남아 있을 수 있다)
    const isAllPast = dayEvents.every((event) => event.isPast);
    const dday = isAllPast ? el('span', { class: 'dday none' }, '지남') : ddayElement({ state: 'open', daysLeft: dayEvents[0].daysLeft });
    return el('li', { class: 'agenda-day', id: `day-${date}` },
      el('div', { class: 'agenda-date' }, el('b', {}, formatShortDate(date)), dday),
      el('ul', {}, ...dayEvents.map((event) => el('li', {},
        el('a', { href: `card.html?id=${encodeURIComponent(event.cardId)}` }, event.title),
        el('span', { class: 'small' }, ` ${event.label} · ${event.time ?? '시각 미기재'}${event.isPast ? ' · 지남' : ''} `),
        event.isVerified ? el('span', { class: 'badge s-human' }, '✓ 검수 완료') : el('span', { class: 'badge s-before' }, '확인 전')))));
  }));
}

function renderNote(data, now, today, isSample, failedCount) {
  const unknownCount = unknownDeadlineCount(data.cards, now);
  const parts = [el('span', {}, `기준일 ${formatShortDate(today)}${isSample ? ' (예시 데이터 기준)' : ''}`)];
  if (unknownCount > 0) {
    parts.push(el('span', {}, ` · 신청 마감일을 확인하지 못한 대회 ${unknownCount}건은 달력에 없습니다. `), el('a', { href: 'index.html#list' }, '홈 목록에서 보기'));
  }
  if (failedCount > 0) parts.push(el('span', {}, ` · 표시하지 못한 대회 ${failedCount}건이 있습니다`));
  document.getElementById('cal-note').replaceChildren(...parts);
}
