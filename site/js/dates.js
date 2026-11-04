// 날짜 도우미. 계약 1절의 날짜 형식("YYYY-MM-DD" 또는 "YYYY-MM-DD HH:MM")은 모두 한국 시간이다.
// 판정(rules.js)과 화면(D-day, 마감 표시)이 같은 계산을 쓰도록 한곳에 둔다.

const KST_OFFSET_MS = 9 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;
const CONTRACT_DATE_PATTERN = /^(\d{4}-\d{2}-\d{2})(?: (\d{2}:\d{2}))?$/;
const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토'];

/**
 * 계약 형식 날짜 문자열을 나눈다.
 * @param {unknown} text
 * @returns {{date: string, time: string|null}|null} 형식이 다르거나 없는 날짜(2026-02-30, 24:00)면 null
 */
export function parseContractDate(text) {
  if (typeof text !== 'string') return null;
  const match = CONTRACT_DATE_PATTERN.exec(text.trim());
  if (!match) return null;
  const instant = toKstInstant(match[1], match[2] ?? '00:00');
  // 2026-02-30처럼 넘치는 날짜는 Date가 다음 달로 넘기므로 되돌려 비교한다
  if (Number.isNaN(instant.getTime()) || kstDateString(instant) !== match[1]) return null;
  return { date: match[1], time: match[2] ?? null };
}

/** 한국 시간 날짜·시각 → Date */
export function toKstInstant(date, time) {
  return new Date(`${date}T${time}:00+09:00`);
}

/** Date → 한국 시간 기준 "YYYY-MM-DD" */
export function kstDateString(instant) {
  return new Date(instant.getTime() + KST_OFFSET_MS).toISOString().slice(0, 10);
}

/** 한국 시간 기준으로 today("YYYY-MM-DD")에서 target("YYYY-MM-DD")까지 남은 날 수. 지났으면 음수 */
export function daysBetween(today, target) {
  return Math.round((Date.parse(`${target}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / DAY_MS);
}

/** "2026-10-21" → "10/21(수)" */
export function formatShortDate(date) {
  const weekday = WEEKDAYS[new Date(`${date}T00:00:00Z`).getUTCDay()];
  return `${Number(date.slice(5, 7))}/${Number(date.slice(8, 10))}(${weekday})`;
}

/** Date → 한국 시간 "YYYY-MM-DD HH:MM" */
export function formatKstDateTime(instant) {
  return new Date(instant.getTime() + KST_OFFSET_MS).toISOString().slice(0, 16).replace('T', ' ');
}
