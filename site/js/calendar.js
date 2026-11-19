// 달력 화면 계산 (#177): 월 격자와 날짜별 마감 일정. 화면 그리기는 calendar-page.js.
// 일정은 캘린더 파일(.ics)과 같은 calendarItems(ics.js)에서 마감(신청·출품)만 가져온다.
// 카드 상세·캘린더 파일·달력이 같은 날짜를 보여 주게 하려는 것이다.
import { calendarItems } from './ics.js';
import { deadlineOf } from './cards.js';
import { daysBetween, kstDateString } from './dates.js';

const MONTH_PATTERN = /^(\d{4})-(0[1-9]|1[0-2])$/;
const DAY_MS = 24 * 60 * 60 * 1000;

/** "2026-11-19" → "2026-11" */
export function monthOf(date) {
  return date.slice(0, 7);
}

/**
 * 달을 옮긴다. shiftMonth("2026-12", 1) → "2027-01".
 * @param {string} month  "YYYY-MM"
 * @param {number} delta  옮길 달 수 (음수면 앞으로)
 * @returns {string|null} 형식이 다르거나 네 자리 연도를 벗어나면 null
 */
export function shiftMonth(month, delta) {
  const match = MONTH_PATTERN.exec(month);
  if (!match || !Number.isInteger(delta)) return null;
  const index = Number(match[1]) * 12 + (Number(match[2]) - 1) + delta;
  const year = Math.floor(index / 12);
  if (year < 1000 || year > 9999) return null;
  return `${year}-${String(index - year * 12 + 1).padStart(2, '0')}`;
}

/**
 * 월 격자. 일요일부터 시작하는 주 단위이고, 첫 주와 마지막 주는 앞뒤 달 날짜로 채운다.
 * @param {string} month  "YYYY-MM"
 * @returns {string[][]} 주마다 "YYYY-MM-DD" 7개. 형식이 다르면 빈 배열
 */
export function monthGrid(month) {
  if (!MONTH_PATTERN.test(month)) return [];
  const first = Date.parse(`${month}-01T00:00:00Z`);
  const weeks = [];
  for (let cursor = first - new Date(first).getUTCDay() * DAY_MS; ; cursor += 7 * DAY_MS) {
    const week = Array.from({ length: 7 }, (_, day) => new Date(cursor + day * DAY_MS).toISOString().slice(0, 10));
    if (weeks.length > 0 && monthOf(week[0]) !== month) break;
    weeks.push(week);
  }
  return weeks;
}

/**
 * 달력에 올릴 마감 일정. 발표·본선·시상 같은 행사 날짜는 마감이 아니라서 넣지 않는다.
 * 카드 하나를 읽지 못해도 나머지는 올린다 (읽지 못한 카드는 failedCardIds로 알린다).
 * @param {object[]} cards  validateCardData를 통과한 카드
 * @param {Date} now  기준 시각
 * @returns {{events: {key: string, date: string, time: string|null, label: string, isPast: boolean, isVerified: boolean, daysLeft: number, cardId: string, title: string}[], failedCardIds: string[]}}
 *   events는 날짜·시각(하루 종일 일정이 뒤)·제목 순. key는 같은 카드 안에서 일정을 구분하는 값(calendarItems의 key),
 *   daysLeft는 기준일에서 그 날짜까지 남은 날 수
 */
export function deadlineEvents(cards, now) {
  const today = kstDateString(now);
  const events = [];
  const failedCardIds = [];
  for (const card of cards) {
    try {
      for (const item of calendarItems(card, now)) {
        if (!item.isDeadline) continue;
        events.push({
          key: item.key, date: item.date, time: item.time, label: item.label, isPast: item.isPast, isVerified: item.isVerified,
          daysLeft: daysBetween(today, item.date), cardId: card.id, title: card.title,
        });
      }
    } catch (error) {
      // 카드 하나 때문에 달력 전체가 비지 않게 그 카드만 빼고, 화면이 건수를 알린다
      console.error('달력에 올리지 못한 카드', card?.id, error);
      failedCardIds.push(String(card?.id));
    }
  }
  const keyed = events.map((event) => ({ event, order: `${event.date} ${event.time ?? '99:99'} ${event.title}` }));
  keyed.sort((a, b) => (a.order < b.order ? -1 : a.order > b.order ? 1 : 0));
  return { events: keyed.map(({ event }) => event), failedCardIds };
}

/** 날짜("YYYY-MM-DD") → 그날의 일정 목록 */
export function groupByDate(events) {
  const groups = new Map();
  for (const event of events) {
    if (!groups.has(event.date)) groups.set(event.date, []);
    groups.get(event.date).push(event);
  }
  return groups;
}

/**
 * 마감일을 확인하지 못해 달력에 하나도 올리지 못한 대회 수.
 * 신청 마감일을 몰라도(홈의 "마감일 확인 필요") 출품 마감이 달력에 있으면 세지 않는다.
 * @param {object[]} cards  validateCardData를 통과한 카드
 * @param {{cardId: string}[]} events  deadlineEvents가 만든 일정
 * @param {Date} now  기준 시각
 */
export function unlistedCount(cards, events, now) {
  const listed = new Set(events.map((event) => event.cardId));
  return cards.filter((card) => !listed.has(card.id) && ['none', 'unreadable'].includes(deadlineOf(card, now).state)).length;
}
