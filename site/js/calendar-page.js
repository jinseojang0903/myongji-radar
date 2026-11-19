// 달력 화면 (#177): 날짜별로 끝나는 대회. 계산은 calendar.js, 이 파일은 그리기만 한다.
// 달력 칸과 아래 "이 달의 마감" 목록은 같은 일정이다. 좁은 화면에서는 칸에 건수만 두고 목록에서 읽는다.
import { el } from './html.js';
import { loadPage, watchPageTime, ddayElement, showConditionSummary } from './page.js';
import { ddayLabel } from './cards.js';
import { formatShortDate, kstDateString } from './dates.js';
import { monthOf, shiftMonth, monthGrid, deadlineEvents, groupByDate, unlistedCount } from './calendar.js';

const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토'];
// 사용자가 옮겨 간 달. null이면 기준일이 있는 달 (자정을 넘기면 따라 바뀐다)
let pickedMonth = null;

main();

async function main() {
  showConditionSummary();
  const note = document.getElementById('cal-note');
  const page = await loadPage({ errorTarget: document.getElementById('cal-body') });
  if (!page) {
    note.textContent = ''; // 이유는 loadPage가 달력 자리에 적는다
    return;
  }
  const currentMonth = () => monthOf(kstDateString(page.getNow()));
  const draw = (now = page.getNow()) => renderCalendar(page.data, now, page.isSample);
  // 버튼으로 다시 그릴 때는 여기서 한 번 잡아 알린다. 시간 변화로 다시 그릴 때는 watchPageTime이 잡고 다음 확인 때 다시 시도한다
  const render = () => {
    try {
      draw();
    } catch (error) {
      console.error('달력을 그리지 못함', error);
      note.textContent = '달력을 그리지 못했습니다. 잠시 뒤 다시 열어 주세요.';
    }
  };
  const move = (delta) => {
    const next = shiftMonth(pickedMonth ?? currentMonth(), delta);
    if (!next) return;
    // 이번 달로 돌아왔으면 고정을 푼다 (자정을 넘겨 달이 바뀌면 따라가게)
    pickedMonth = next === currentMonth() ? null : next;
    render();
  };
  document.getElementById('cal-prev').addEventListener('click', () => move(-1));
  document.getElementById('cal-next').addEventListener('click', () => move(1));
  document.getElementById('cal-this-month').addEventListener('click', () => {
    pickedMonth = null;
    render();
  });
  render();
  watchPageTime(page, draw);
}

function renderCalendar(data, now, isSample) {
  const today = kstDateString(now);
  const month = pickedMonth ?? monthOf(today);
  const { events, failedCardIds } = deadlineEvents(data.cards, now);
  const monthEvents = events.filter((event) => monthOf(event.date) === month);
  const byDate = groupByDate(monthEvents);

  // 제목은 aria-live라 글자가 바뀔 때만 넣는다 (시간 변화로 다시 그릴 때마다 읽지 않게)
  const title = document.getElementById('cal-title');
  const titleText = `${Number(month.slice(0, 4))}년 ${Number(month.slice(5, 7))}월`;
  if (title.textContent !== titleText) title.textContent = titleText;
  document.getElementById('cal-body').replaceChildren(monthTable(month, today, byDate));
  renderAgenda(byDate, monthEvents.length);
  renderNote(unlistedCount(data.cards, events, now), today, isSample, failedCardIds.length);
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
      `${dayEvents.length}건`, el('span', { class: 'sr-only' }, ` 마감${TONE_WORDS[dayTone(dayEvents)]}, ${formatShortDate(date)} 목록으로`)),
    dayEvents.length > 0 && el('ul', { class: 'cal-events' }, ...dayEvents.map((event) => el('li', { 'data-card-id': focusKey('cell', event) }, eventLink(event)))));
}

// 좁은 화면의 건수 칸은 색으로만 급함·지남을 나타내므로 화면 읽기 프로그램에는 글자로 알린다
const TONE_WORDS = { soon: ' (7일 이내)', open: '', past: ' (지남)' };

/**
 * 같은 대회 링크가 달력 칸과 아래 목록에 한 번씩 있다 (주소가 같다). 시간 변화로 다시 그린 뒤 포커스를 되돌릴 때
 * (page.js restoreTimeFocus) 둘을 구분하도록 자리와 일정으로 만든 값을 data-card-id에 둔다. 카드 id 자체가 아니라 포커스 범위 이름이다.
 */
function focusKey(place, event) {
  return `calendar-${place}|${event.cardId}|${event.key}|${event.date}`;
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
      el('ul', {}, ...dayEvents.map((event) => el('li', { 'data-card-id': focusKey('list', event) },
        el('a', { href: `card.html?id=${encodeURIComponent(event.cardId)}` }, event.title),
        el('span', { class: 'small' }, ` ${event.label} · ${event.time ?? '시각 미기재'}${event.isPast ? ' · 지남' : ''} `),
        event.isVerified ? el('span', { class: 'badge s-human' }, '✓ 검수 완료') : el('span', { class: 'badge s-before' }, '확인 전')))));
  }));
}

function renderNote(unlisted, today, isSample, failedCount) {
  const parts = [el('span', {}, `기준일 ${formatShortDate(today)}${isSample ? ' (예시 데이터 기준)' : ''}`)];
  if (unlisted > 0) {
    parts.push(el('span', {}, ` · 마감일을 확인하지 못해 달력에 올리지 못한 대회가 ${unlisted}건 있습니다. `), el('a', { href: 'index.html#list' }, '홈 목록에서 보기'));
  }
  if (failedCount > 0) parts.push(el('span', {}, ` · 표시하지 못한 대회 ${failedCount}건이 있습니다`));
  document.getElementById('cal-note').replaceChildren(...parts);
}
