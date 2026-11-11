// 맞춤 추천 계산 (#24): 문장 해석 결과를 조건에 반영, 대회별 판정을 4상태로 묶기, 제출물·기간 필터, 사유 요약.
// DOM을 쓰지 않아 Node로 테스트한다. 그리기는 recommend-page.js.
// 판정은 rules.js(계약 3절)가 한다. 이 파일은 묶고 고르고 요약만 한다.

import { judgeCard } from './rules.js';
import { deadlineOf, deliverableTags } from './cards.js';
import { kstDateString } from './dates.js';

export const RESULT_ORDER = ['eligible', 'team_needed', 'check', 'ineligible'];
export const RESULT_VIEW = {
  eligible: { cls: 'j-ok', text: '● 신청 가능' },
  team_needed: { cls: 'j-team', text: '◆ 팀 구성 시 가능' },
  check: { cls: 'j-check', text: '? 확인 필요' },
  ineligible: { cls: 'j-no', text: '✕ 불가' },
};
const URGENT_DAYS = 7; // 계약 2절 5: 마감임박 = 마감 7일 이내 (홈 필터와 같다)
const CONDITION_FIELDS = ['campus', 'grade', 'is_transfer', 'enrollment', 'participation', 'period'];

/**
 * 문장 해석 결과를 지금 조건에 반영한다. 문장이 말한 조건만 바꾸고, 말하지 않은 조건(null)은 그대로 둔다.
 * 제출물은 문장에 있으면 그 목록으로 바꾼다. unknown_terms는 화면 표시용으로만 돌려준다 (저장하지 않음).
 * @param {object} current  지금 조건 (계약 2절)
 * @param {object} parsed  parseConditions 결과
 * 제출물을 말하면 칩으로 고른 제출물 목록을 문장의 목록으로 바꾼다 (말한 조건만 바꾼다는 규칙과 같다, 계약 5절).
 * @returns {{conditions: object, read: string[], changed: string[], kept: string[], unknownTerms: string[]}}
 *   read: 문장에서 읽은 조건(값이 같아도 포함), changed: 그중 값이 바뀐 것, kept: 문장이 말하지 않아 그대로 둔 고른 조건
 */
export function applyParsed(current, parsed) {
  const conditions = { ...current, deliverables: [...(current.deliverables ?? [])], unknown_terms: [] };
  const read = [];
  const changed = [];
  for (const field of CONDITION_FIELDS) {
    if (parsed[field] === null || parsed[field] === undefined) continue;
    read.push(field);
    if (parsed[field] !== current[field]) changed.push(field);
    conditions[field] = parsed[field];
  }
  if (Array.isArray(parsed.deliverables) && parsed.deliverables.length > 0) {
    read.push('deliverables');
    if (parsed.deliverables.join('|') !== conditions.deliverables.join('|')) changed.push('deliverables');
    conditions.deliverables = [...parsed.deliverables];
  }
  const kept = [...CONDITION_FIELDS, 'deliverables'].filter((field) => !read.includes(field)
    && (field === 'deliverables' ? conditions.deliverables.length > 0 : conditions[field] !== null && conditions[field] !== undefined));
  return { conditions, read, changed, kept, unknownTerms: Array.isArray(parsed.unknown_terms) ? parsed.unknown_terms : [] };
}

/**
 * 기간 필터의 마지막 날 (마감일 기준, 계약 2절 5). 한국 시간으로 계산한다.
 * - 마감임박: 오늘부터 7일 뒤까지
 * - 이번주: 이번 주 일요일까지 (월요일 시작, 오늘이 일요일이면 오늘)
 * - 이번달: 이번 달 마지막 날까지
 * @returns {string|null} "YYYY-MM-DD", 기간을 고르지 않았으면 null
 */
export function periodEndDate(period, now) {
  const today = kstDateString(now);
  const at = (days) => new Date(Date.parse(`${today}T00:00:00Z`) + days * 86400000).toISOString().slice(0, 10);
  if (period === '마감임박') return at(URGENT_DAYS);
  if (period === '이번주') {
    const weekday = new Date(`${today}T00:00:00Z`).getUTCDay(); // 0 = 일요일
    return at(weekday === 0 ? 0 : 7 - weekday);
  }
  if (period === '이번달') {
    const [year, month] = today.split('-').map(Number);
    return new Date(Date.UTC(year, month, 0)).toISOString().slice(0, 10);
  }
  return null;
}

export const MAX_TITLE_QUERY_LENGTH = 30;

// 띄어쓰기·영문 대소문자를 무시하고 비교하려고 둘 다 같은 꼴로 바꾼다 ("AI 해커톤" ↔ "ai해커톤")
function foldForSearch(text) {
  return text.normalize('NFC').replace(/\s+/g, '').toLowerCase();
}

/**
 * 제목 검색어를 30자로 자른다 (글자 단위라 이모지 같은 서로게이트 쌍을 반으로 가르지 않는다).
 * @param {unknown} text
 * @returns {string}  문자열이 아니면 ''
 */
export function clipTitleQuery(text) {
  return typeof text === 'string' ? [...text].slice(0, MAX_TITLE_QUERY_LENGTH).join('') : '';
}

/**
 * 제목 검색어를 비교할 꼴로 바꾼다 (계약 5절 "제목 검색"). 문자열이 아니거나 비면 '' (검색하지 않음).
 * @param {unknown} text  검색칸에 쓴 말. 30자까지만 쓴다
 * @returns {string}
 */
export function normalizeTitleQuery(text) {
  return foldForSearch(clipTitleQuery(text));
}

/**
 * 대회별 판정을 4상태로 묶는다. 마감 지난 대회는 뺀다. 제출물·기간·제목 검색은 판정이 아니라 필터다 (계약 3절 "핵심 항목", 5절).
 * @param {object[]} cards  validateCardData를 통과한 카드
 * @param {object} conditions  계약 2절 조건
 * @param {Date} now  기준 시각
 * @param {{titleQuery?: string}} [options]  titleQuery: 제목 검색칸에 쓴 말 (비면 검색하지 않음)
 * @returns {{groups: Record<string, {card: object, judged: object, deadline: object, summary: string}[]>,
 *   closedCount: number, filteredCount: number, undatedCount: number, titleFilteredCount: number, skippedCount: number, total: number}}
 *   filteredCount: 제출물·기간 필터로 빠진 수 (그중 undatedCount는 마감일이 없거나 읽을 수 없어 기간 안인지 모르는 대회).
 *   titleFilteredCount: 제목에 검색어가 없어 빠진 수 (제출물·기간보다 먼저 세어 겹쳐 세지 않는다).
 *   skippedCount: 판정하다 오류가 나 뺀 대회 (한 장 때문에 목록 전체가 사라지지 않게). total: 보여 주는 대회 수
 */
export function recommendCards(cards, conditions, now, { titleQuery = '' } = {}) {
  const groups = Object.fromEntries(RESULT_ORDER.map((result) => [result, []]));
  const periodEnd = periodEndDate(conditions.period, now);
  const tags = conditions.deliverables ?? [];
  const query = normalizeTitleQuery(titleQuery);
  let closedCount = 0;
  let filteredCount = 0;
  let undatedCount = 0;
  let titleFilteredCount = 0;
  let skippedCount = 0;
  for (const card of cards) {
    // 한 장이 깨져 있어도(마감 계산·판정 오류) 그 카드만 빼고 센다
    try {
      const deadline = deadlineOf(card, now);
      if (deadline.state === 'closed') {
        closedCount += 1;
        continue;
      }
      if (query !== '' && !foldForSearch(card.title).includes(query)) {
        titleFilteredCount += 1;
        continue;
      }
      // 기간을 고르면 마감일을 읽을 수 있는 대회만 (마감일 없음·읽을 수 없음은 그 기간 안인지 알 수 없다)
      const isOutOfPeriod = periodEnd && !(deadline.state === 'open' && deadline.endDate <= periodEnd);
      const isOtherDeliverable = tags.length > 0 && !deliverableTags(card).some((tag) => tags.includes(tag));
      if (isOutOfPeriod || isOtherDeliverable) {
        filteredCount += 1;
        if (isOutOfPeriod && deadline.state !== 'open') undatedCount += 1;
        continue;
      }
      const judged = judgeCard(card, conditions, { now });
      groups[judged.result].push({ card, judged, deadline, summary: reasonSummary(judged) });
    } catch (error) {
      console.error('카드를 판정하지 못해 뺌', card?.id, error);
      skippedCount += 1;
    }
  }
  for (const items of Object.values(groups)) items.sort(compareDeadline);
  const total = Object.values(groups).reduce((sum, items) => sum + items.length, 0);
  return { groups, closedCount, filteredCount, undatedCount, titleFilteredCount, skippedCount, total };
}

function compareDeadline(a, b) {
  // 날짜 문자열은 로케일과 상관없이 비교한다. 마감일이 없거나 읽을 수 없으면 뒤로
  const key = ({ deadline }) => (deadline.state === 'open' ? `${deadline.endDate} ${deadline.endTime ?? '24:00'}` : '9999');
  if (key(a) !== key(b)) return key(a) < key(b) ? -1 : 1;
  return a.card.title.localeCompare(b.card.title, 'ko');
}

/**
 * 판정 사유 한 줄. 신청 가능이면 맞은 조건(✓), 아니면 결과를 정한 사유부터 (계약 3절 reasons 순서).
 * 불가 사유에는 근거 문장을 붙인다 (화면설계.md 4절 "맞지 않는 조건과 근거 문장").
 * @param {{result: string, reasons: object[]}} judged
 * @param {number} [limit]  보여 줄 사유 수
 */
export function reasonSummary(judged, limit = 3) {
  if (judged.result === 'eligible') {
    const ok = judged.reasons.filter((reason) => reason.outcome === 'ok').slice(0, limit);
    return ok.length > 0 ? ok.map((reason) => `${reason.message} ✓`).join(' · ') : '조건이 모두 맞습니다';
  }
  const deciding = judged.reasons.filter((reason) => reason.outcome !== 'ok').slice(0, limit);
  return deciding.map((reason) => {
    // 근거 문장은 목록에서는 60자까지 (전체는 카드 상세에서)
    const shortQuote = typeof reason.quote === 'string' && [...reason.quote].length > 60 ? `${[...reason.quote].slice(0, 60).join('')}…` : reason.quote;
    const quote = reason.outcome === 'fail' && shortQuote ? ` · 근거: "${shortQuote}"` : '';
    return `${reason.message}${quote}`;
  }).join(' · ');
}

/** 판정 사유 중 확인 전 근거로 "불가"가 나온 것이 있는지 ("확인 전 정보" 표시용, 계약 3절) */
export function hasUnverifiedFail(judged) {
  return judged.reasons.some((reason) => reason.outcome === 'fail' && reason.unverified);
}

/**
 * "이해하지 못한 표현" 칩을 화면에 맞게 줄인다 (PR #36 보안 리뷰: 칩 개수·길이 상한).
 * @returns {{shown: string[], originals: string[], hiddenCount: number}}
 *   originals: shown과 같은 순서의 줄이기 전 표현 ("제목에서 찾기"가 잘린 글자로 찾지 않게)
 */
export function limitTerms(terms, { maxCount = 10, maxLength = 20 } = {}) {
  const list = Array.isArray(terms) ? terms.filter((term) => typeof term === 'string' && term.trim() !== '') : [];
  const originals = list.slice(0, maxCount);
  const shown = originals.map((term) => ([...term].length > maxLength ? `${[...term].slice(0, maxLength).join('')}…` : term));
  return { shown, originals, hiddenCount: Math.max(0, list.length - maxCount) };
}

