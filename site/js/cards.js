// 카드 데이터 다루기 (화면 없이 계산만). 형식은 docs/api-contract.md 1절.
// 홈(#21)·추천(#24)·상세(#25)가 같이 쓴다. DOM을 쓰지 않으므로 Node 테스트로 검사한다.

import { KEY_FIELDS } from './rules.js';
import { parseContractDate, toKstInstant, kstDateString, daysBetween, formatShortDate, formatKstDateTime } from './dates.js';

export const DELIVERABLE_TAGS = ['기획서·제안서', '영상', '에세이·수기', '코드·개발', '발표', '디자인', '현장 참가', '기타'];
const FIELD_STATUSES = new Set(['ai', 'auto_verified', 'needs_check', 'human_verified', 'missing']);
const URGENT_DAYS = 7;
// 매일 한 번 갱신한다(#19). 하루 한 번을 건너뛴 정도(36시간)를 넘으면 오래된 정보라고 알린다
export const STALE_HOURS = 36;
const HOUR_MS = 3600 * 1000;

// 카드 id: 주소(card.html?id=)에 그대로 들어가므로 계약 1절의 형식만 받는다
const CARD_ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;
const POSTED_AT_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

/**
 * 읽어 온 JSON의 형식을 경계에서 한 번 검사한다. 카드·게시판 단위로 걸러, 일부가 틀려도 화면 전체가 멈추지 않게 한다.
 * 여기를 통과한 카드는 화면 계산(이 파일)이 가정하는 타입을 모두 만족한다.
 * @param {unknown} data  cards.json 또는 cards.sample.json을 JSON.parse한 값
 * @returns {{data: object, rejected: {id: string, reason: string}[]}}  rejected: 뺀 카드(id)·게시판(board:id)과 이유
 * @throws {Error} 최상위 형식(cards 배열, boards 배열, generated_at 날짜)이 틀리면
 */
export function validateCardData(data) {
  if (!data || typeof data !== 'object') throw new Error('카드 데이터가 객체가 아닙니다');
  if (!Array.isArray(data.cards)) throw new Error('cards 배열이 없습니다');
  if (!Array.isArray(data.boards)) throw new Error('boards 배열이 없습니다');
  if (typeof data.generated_at !== 'string' || Number.isNaN(Date.parse(data.generated_at))) {
    throw new Error('generated_at이 날짜가 아닙니다');
  }

  const rejected = [];
  const boards = data.boards.filter((board) => {
    const reason = findBoardProblem(board);
    if (reason) rejected.push({ id: `board:${String(board?.id ?? '(id 없음)')}`, reason });
    return !reason;
  });
  const cards = data.cards.filter((card) => {
    const reason = findCardProblem(card);
    if (reason) rejected.push({ id: String(card?.id ?? '(id 없음)').slice(0, 64), reason });
    return !reason;
  });
  return { data: { ...data, boards, cards }, rejected };
}

const isText = (value) => typeof value === 'string' && value !== '';
const isOptionalText = (value) => value === null || value === undefined || typeof value === 'string';

function findBoardProblem(board) {
  if (!board || typeof board !== 'object') return '게시판이 객체가 아님';
  if (!isText(board.id) || !isText(board.name)) return '게시판 id·name 없음';
  if (!isOptionalText(board.list_url)) return 'list_url이 문자열이 아님';
  return null;
}

function findCardProblem(card) {
  if (!card || typeof card !== 'object') return '카드가 객체가 아님';
  if (typeof card.id !== 'string' || !CARD_ID_PATTERN.test(card.id)) return 'id 형식이 계약과 다름';
  if (!isText(card.title)) return 'title 없음';
  if (!isOptionalText(card.organizer) || !isOptionalText(card.scope)) return 'organizer·scope가 문자열이 아님';
  if (!card.fields || typeof card.fields !== 'object') return 'fields 없음';
  if (!Array.isArray(card.sources) || card.sources.length === 0) return 'sources 없음';
  for (const source of card.sources) {
    if (!source || !isText(source.board_id) || !isText(source.url)) return 'sources의 board_id·url 없음';
    if (typeof source.posted_at !== 'string' || !POSTED_AT_PATTERN.test(source.posted_at)) return 'sources의 posted_at 형식이 다름';
  }
  for (const [name, field] of Object.entries(card.fields)) {
    if (!field || !FIELD_STATUSES.has(field.status)) return `${name}의 status가 계약 값이 아님`;
    if (!Array.isArray(field.evidence)) return `${name}의 evidence가 배열이 아님`;
  }
  const history = card.deadline_history ?? [];
  if (!Array.isArray(history)) return 'deadline_history가 배열이 아님';
  for (const change of history) {
    if (!change || !isText(change.field)) return 'deadline_history 항목 형식이 다름';
    if (change.field === 'apply_end' && (!parseContractDate(change.old) || !parseContractDate(change.new))) {
      return 'deadline_history의 날짜 형식이 다름';
    }
  }
  return null;
}

/**
 * 판정·D-day 기준 시각. 예시 데이터는 이미 마감된 대회라 generated_at을 기준으로 쓴다 (계약 1절).
 * @param {{generated_at: string, sample?: boolean}} data  validateCardData를 통과한 데이터
 * @param {Date} [now]
 * @param {boolean} [isSample]  예시 파일을 읽었는지 (기본: data.sample)
 * @returns {Date}
 */
export function referenceDate(data, now = new Date(), isSample = data.sample === true) {
  return isSample ? new Date(data.generated_at) : now;
}

/**
 * 화면 맨 위 안내 띠의 글. 예시 데이터면 예시 안내, 실제 데이터가 STALE_HOURS보다 오래됐으면 오래된 정보 경고, 아니면 null.
 * 파이프라인이 며칠 멈추면 어제 카드가 그대로 공개되므로(#19), 학생이 원문을 다시 확인하게 한다.
 * @param {{generated_at: string, sample_note?: string}} data  validateCardData를 통과한 데이터
 * @param {Date} now  지금 (기준일이 아니라 실제 시각)
 * @param {boolean} isSample
 * @returns {string|null}
 */
export function bannerText(data, now, isSample) {
  if (isSample) return typeof data.sample_note === 'string' && data.sample_note !== '' ? data.sample_note : '예시 데이터입니다.';
  const generated = new Date(data.generated_at);
  const hours = (now.getTime() - generated.getTime()) / HOUR_MS;
  if (!(hours > STALE_HOURS)) return null; // 미래 시각(시계 어긋남)도 경고하지 않는다
  const days = Math.floor(hours / 24);
  return `대회 정보가 ${days}일 전(${formatKstDateTime(generated)}) 기준입니다. 그 뒤 바뀐 공지는 반영되지 않았을 수 있으니 신청 전에 원문 공지를 꼭 확인하세요.`;
}

/**
 * 신청 마감 정보. 마감 여부는 rules.js 판정과 같다 (시각이 없으면 마감일 다음 날부터 마감).
 * @param {object} card  validateCardData를 통과한 카드
 * @param {Date} now  기준 시각
 * @returns {{state: 'open'|'closed'|'none'|'unreadable', endDate: string|null, endTime: string|null, daysLeft: number|null}}
 *   none: 마감일이 공지에 없음, unreadable: 형식을 읽을 수 없음
 */
export function deadlineOf(card, now) {
  const apply = card.fields?.apply;
  const endText = apply?.value?.end;
  if (!apply || apply.status === 'missing' || endText === null || endText === undefined) {
    return { state: 'none', endDate: null, endTime: null, daysLeft: null };
  }
  const end = parseContractDate(endText);
  if (!end) return { state: 'unreadable', endDate: null, endTime: null, daysLeft: null };
  const endTime = apply.value.time_given === true ? end.time : null;
  const daysLeft = daysBetween(kstDateString(now), end.date);
  const isClosed = endTime ? now > toKstInstant(end.date, endTime) : daysLeft < 0;
  return { state: isClosed ? 'closed' : 'open', endDate: end.date, endTime, daysLeft };
}

/** "D-6", "D-day". 마감 7일 이내면 isUrgent */
export function ddayLabel(deadline) {
  const closedText = { none: '마감일 없음', unreadable: '마감일 확인 필요', closed: '마감됨' };
  if (deadline.state !== 'open') return { text: closedText[deadline.state], isUrgent: false };
  return { text: deadline.daysLeft === 0 ? 'D-day' : `D-${deadline.daysLeft}`, isUrgent: deadline.daysLeft <= URGENT_DAYS };
}

/** "10/21(수) 15:00", 시각이 없으면 "10/23(금) (시각 미기재)" */
export function formatDeadline(deadline) {
  if (!deadline.endDate) return null;
  const date = formatShortDate(deadline.endDate);
  return deadline.endTime ? `${date} ${deadline.endTime}` : `${date} (시각 미기재)`;
}

/**
 * 카드 단위 배지 (docs/ui/화면설계.md 4절 "카드 단위 배지").
 * - 핵심 항목이 모두 human_verified → "✓ 검수 완료" 하나
 * - 핵심 항목 중 needs_check가 있으면 "! 원문 확인 필요"
 * - 사람 대조 전(ai·auto_verified)이면 "확인 전". 무엇이 확인 전인지 알 수 있게 신청기간·자격을 나눈다
 * - 핵심 항목이 공지에 없으면(missing) "공지에 없는 조건 있음"
 * @returns {{kind: 'human'|'need'|'before'|'missing', text: string}[]}
 */
export function cardBadges(card) {
  const statuses = KEY_FIELDS.map((name) => ({ name, status: card.fields?.[name]?.status ?? 'missing' }));
  if (statuses.every((item) => item.status === 'human_verified')) return [{ kind: 'human', text: '✓ 검수 완료' }];

  const badges = [];
  if (statuses.some((item) => item.status === 'needs_check')) badges.push({ kind: 'need', text: '! 원문 확인 필요' });
  const isBefore = (item) => item.status !== 'human_verified' && item.status !== 'needs_check' && item.status !== 'missing';
  if (statuses.some((item) => item.name === 'apply' && isBefore(item))) badges.push({ kind: 'before', text: '신청기간 확인 전' });
  if (statuses.some((item) => item.name !== 'apply' && isBefore(item))) badges.push({ kind: 'before', text: '자격 확인 전' });
  if (statuses.some((item) => item.status === 'missing')) badges.push({ kind: 'missing', text: '공지에 없는 조건 있음' });
  return badges;
}

/** 마지막 신청 마감 변경. 없으면 null */
export function latestDeadlineChange(card) {
  const changes = (card.deadline_history ?? []).filter((change) => change.field === 'apply_end');
  return changes.length > 0 ? changes[changes.length - 1] : null;
}

/**
 * 상세 화면 제목 아래 "주관 · 교내/외부" 줄. 계약상 둘 다 비거나 null일 수 있어 있는 것만 잇는다.
 * @returns {string|null}  둘 다 없으면 null (줄을 그리지 않음)
 */
export function organizerLine(card) {
  const parts = [card.organizer, card.scope].filter((part) => typeof part === 'string' && part.trim() !== '');
  return parts.length > 0 ? parts.join(' · ') : null;
}

/**
 * 목록이 비었을 때의 안내. 필터를 고르지 않은 학생에게 "필터를 줄여 보세요"가 나가지 않게 이유를 나눈다 (#104).
 * @param {{cardCount: number, unavailableCount: number}} counts
 *   cardCount: 형식 검사를 통과한 전체 카드 수, unavailableCount: 그중 마감됐거나 읽지 못해 목록에서 뺀 수
 * @param {string} filteredMessage  필터·조건 때문에 0건일 때의 안내 (화면마다 다름)
 * @returns {string}
 */
export function emptyListMessage({ cardCount, unavailableCount }, filteredMessage) {
  if (cardCount === 0) return '아직 모은 대회가 없습니다. 학교 게시판에서 직접 확인해 주세요.';
  if (unavailableCount >= cardCount) return '지금 신청할 수 있는 대회가 없습니다. 마감이 지난 대회는 목록에서 뺐습니다.';
  return filteredMessage;
}

/** 목록 한 줄 요약: 마감 · 인원 · 마일리지 */
export function summaryLine(card, deadline) {
  const parts = [];
  const deadlineText = formatDeadline(deadline);
  if (deadlineText) parts.push(`신청 마감 ${deadlineText}`);
  const team = card.fields?.team;
  if (team?.value && team.status !== 'missing') parts.push(teamText(team.value));
  const mileage = card.fields?.mileage?.value;
  if (mileage && Number.isInteger(mileage.points)) parts.push(`${mileage.area ? `${mileage.area} ` : '마일리지 '}${mileage.points}점`);
  return parts.filter(Boolean).join(' · ');
}

/** 팀 인원 설명: "2~5인, 팀만", "3인 이내, 개인 지원 가능" */
export function teamText(team) {
  const size = Number.isInteger(team.min) && Number.isInteger(team.max) ? `${team.min}~${team.max}인`
    : Number.isInteger(team.max) ? `${team.max}인 이내`
      : Number.isInteger(team.min) ? `${team.min}인 이상` : '';
  const individual = team.individual_ok === true ? '개인 지원 가능'
    : team.individual_ok === false ? '팀만' : '개인 참가 여부 미기재';
  return [size, individual].filter(Boolean).join(', ');
}

/** 카드의 제출물 태그 (계약 태그만) */
export function deliverableTags(card) {
  const tags = card.fields?.deliverables?.value?.tags;
  return Array.isArray(tags) ? tags.filter((tag) => DELIVERABLE_TAGS.includes(tag)) : [];
}

/** 게시판 id → 이름. 모르는 id는 id 그대로 */
export function boardNames(card, boards) {
  const nameById = new Map(boards.map((board) => [board.id, board.name]));
  return [...new Set(card.sources.map((source) => nameById.get(source.board_id) ?? source.board_id))];
}

/**
 * 홈 목록: 마감 지난 대회를 빼고, 필터·정렬을 적용한다.
 * @param {object[]} cards
 * @param {{sort?: 'deadline'|'newest', urgentOnly?: boolean, changedOnly?: boolean, tags?: string[], campus?: '인문'|'자연'|null}} filters
 *   tags: 하나라도 맞으면 보여 줌. campus: 그 캠퍼스를 제외하는 대회만 숨김 (캠퍼스 조건이 없거나 애매하면 보여 줌)
 * @returns {{items: {card: object, deadline: object}[], closedCount: number}}
 */
export function listCards(cards, filters, now) {
  const withDeadline = cards.map((card) => ({ card, deadline: deadlineOf(card, now) }));
  const notClosed = withDeadline.filter((item) => item.deadline.state !== 'closed');
  const tags = filters.tags ?? [];
  const items = notClosed.filter(({ card, deadline }) => {
    if (filters.urgentOnly && !(deadline.state === 'open' && deadline.daysLeft <= URGENT_DAYS)) return false;
    if (filters.changedOnly && !latestDeadlineChange(card)) return false;
    if (tags.length > 0 && !deliverableTags(card).some((tag) => tags.includes(tag))) return false;
    if (filters.campus && excludesCampus(card, filters.campus)) return false;
    return true;
  });
  items.sort(filters.sort === 'newest' ? compareNewest : compareDeadline);
  return { items, closedCount: withDeadline.length - notClosed.length };
}

function excludesCampus(card, campus) {
  const field = card.fields?.campus;
  if (!field || field.status === 'missing' || field.status === 'needs_check') return false;
  return Array.isArray(field.value) && field.value.length > 0 && !field.value.includes(campus);
}

// 마감 가까운 순. 마감일이 없거나 읽을 수 없는 대회는 뒤로
function compareDeadline(a, b) {
  const keyOf = ({ deadline }) => (deadline.state === 'open'
    ? `${deadline.endDate} ${deadline.endTime ?? '24:00'}` : '9999');
  const [keyA, keyB] = [keyOf(a), keyOf(b)];
  if (keyA !== keyB) return keyA < keyB ? -1 : 1;
  return a.card.title.localeCompare(b.card.title, 'ko');
}

// 새로 올라온 순: 가장 최근 공지의 게시일
function compareNewest(a, b) {
  const latest = (card) => card.sources.map((source) => source.posted_at ?? '').sort().at(-1);
  return latest(b.card).localeCompare(latest(a.card)) || a.card.title.localeCompare(b.card.title, 'ko');
}
