// 카드 상세 화면 계산 (#25). DOM을 쓰지 않아 Node로 테스트한다. 그리기는 card-page.js.
// 형식은 docs/api-contract.md 1절, 표시 규칙은 docs/ui/화면설계.md 4절.

import { KEY_FIELDS } from './rules.js';
import { teamText, deliverableTags } from './cards.js';
import { parseContractDate, formatShortDate } from './dates.js';
import { safeUrl, stripInvisible } from './html.js';
import { describeConditions } from './conditions.js';

/** 항목 표의 순서와 이름 */
export const FIELD_LABELS = [
  ['apply', '신청기간'], ['stages', '단계별 일정'], ['campus', '캠퍼스'], ['grades', '학년'], ['enrollment', '학적'],
  ['team', '팀 인원'], ['deliverables', '제출물'], ['benefits', '혜택'], ['mileage', '마일리지'],
  ['how_to_apply', '신청 방법'], ['contact', '문의처'],
];
const LABEL_BY_FIELD = new Map(FIELD_LABELS);

/**
 * 항목 상태 배지 (화면설계.md 4절). 핵심 항목은 사람 대조 전이면 "확인 전"을 앞에 붙인다.
 * @returns {{kind: 'human'|'auto'|'before'|'need'|'missing', text: string}}
 */
export function fieldStatusBadge(name, status) {
  const isKey = KEY_FIELDS.includes(name);
  if (status === 'human_verified') return { kind: 'human', text: '✓ 검수 완료' };
  if (status === 'needs_check') return { kind: 'need', text: '! 원문 확인 필요' };
  if (status === 'missing') return { kind: 'missing', text: '정보 없음' };
  if (status === 'auto_verified') return isKey ? { kind: 'before', text: '확인 전 · 자동 검증 통과' } : { kind: 'auto', text: '자동 검증 통과' };
  return { kind: 'before', text: isKey ? '확인 전 · AI 정리' : 'AI 정리' };
}

/**
 * 항목 값을 사람이 읽는 글로 바꾼다. 형식이 예상과 다르면 null (화면은 "표시할 수 없는 값"으로).
 * @param {string} name  계약 1절 fields 키
 * @param {unknown} value
 * @returns {string|null}
 */
export function fieldValueText(name, value) {
  if (value === null || value === undefined) return null;
  switch (name) {
    case 'apply': return applyText(value);
    case 'stages':
      if (!Array.isArray(value)) return null;
      return value.map((stage, index) => `${'①②③④⑤⑥⑦⑧⑨'[index] ?? `${index + 1}.`} ${stage?.name ?? '일정'} ~${dateTimeText(stage?.end, stage?.time_given)}`).join(' → ');
    case 'campus':
      if (value === 'any') return '캠퍼스 제한 없음';
      return Array.isArray(value) && value.length > 0 ? `${value.join('·')}캠퍼스` : null;
    case 'grades':
      if (value === 'any') return '학년 제한 없음';
      return Array.isArray(value) && value.length > 0 ? `${value.join('·')}학년` : null;
    case 'enrollment': {
      if (!Array.isArray(value.allowed) || !Array.isArray(value.excluded)) return null;
      if (value.allowed.length === 0 && value.excluded.length === 0) return '학적 제한 문구 없음';
      return [value.allowed.length > 0 ? `대상: ${value.allowed.join(', ')}` : null,
        value.excluded.length > 0 ? `제외: ${value.excluded.join(', ')}` : null].filter(Boolean).join(' / ');
    }
    case 'team': return typeof value === 'object' ? teamText(value) || null : null;
    case 'deliverables': return typeof value.text === 'string' && value.text !== '' ? value.text : null;
    case 'benefits': return typeof value === 'string' && value !== '' ? value : null;
    case 'mileage': return typeof value.text === 'string' && value.text !== '' ? value.text : null;
    case 'how_to_apply': return typeof value.text === 'string' && value.text !== '' ? value.text : null;
    case 'contact': {
      // 학생이 손으로 옮겨 적는 값이라 보이지 않는 글자를 지우고, 이메일은 형식이 맞을 때만 보여 준다
      const email = typeof value.email === 'string' && EMAIL_PATTERN.test(stripInvisible(value.email)) ? stripInvisible(value.email) : null;
      const parts = [value.dept, email, value.phone].filter((part) => typeof part === 'string' && part !== '').map(stripInvisible);
      return parts.length > 0 ? parts.join(' · ') : null;
    }
    default: return null;
  }
}

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function applyText(value) {
  if (typeof value !== 'object') return null;
  const end = dateTimeText(value.end, value.time_given);
  // 시작 시각은 공지에 적혀 있을 때만 값에 들어 있다 (time_given은 끝 시각 기준이라 시작은 값 자체로 판단)
  const start = parseContractDate(value.start);
  const startText = start ? `${formatShortDate(start.date)}${start.time ? ` ${start.time}` : ''}` : '';
  return `${startText} ~ ${end}`.trim();
}

// "10/21(수) 15:00", 시각이 없으면 "10/23(금) (시각 미기재)". 시각을 채우지 않는다
function dateTimeText(text, timeGiven) {
  const parsed = parseContractDate(text);
  if (!parsed) return '날짜 확인 필요';
  const date = formatShortDate(parsed.date);
  return timeGiven === true && parsed.time ? `${date} ${parsed.time}` : `${date} (시각 미기재)`;
}

/**
 * 카드 상세 항목 표의 줄들.
 * @param {object} card  validateCardData를 통과한 카드
 * @param {{id: string, name: string}[]} boards
 * @returns {{name: string, label: string, text: string|null, status: string, badge: object, note: string|null,
 *   evidence: {quote: string, location: string, file: string|null, isImage: boolean, boardName: string|null, postedAt: string|null, url: string|null}[],
 *   isConflict: boolean}[]}
 *   isConflict: needs_check이면서 서로 다른 근거 문장이 둘 이상 (출처별 값을 모두 보여 줄 것).
 *   계약에 충돌 표시가 따로 없어, 같은 내용이 여러 곳에 나온 경우와 구분하려고 문장이 다른지 본다
 */
export function fieldRows(card, boards) {
  return FIELD_LABELS.map(([name, label]) => {
    const field = card.fields?.[name];
    const status = field?.status ?? 'missing';
    const evidence = (Array.isArray(field?.evidence) ? field.evidence : [])
      .filter((item) => typeof item?.quote === 'string' && item.quote !== '')
      .map((item) => evidenceItem(item, card, boards));
    return {
      name,
      label,
      text: status === 'missing' ? null : fieldValueText(name, field?.value),
      status,
      badge: fieldStatusBadge(name, status),
      note: typeof field?.note === 'string' && field.note !== '' ? field.note : null,
      evidence,
      isConflict: status === 'needs_check' && distinctQuotes(evidence.map((item) => item.quote)).length > 1,
    };
  });
}

// 띄어쓰기·보이지 않는 글자만 다른 문장은 같은 문장으로 본다
function distinctQuotes(quotes) {
  const seen = new Map();
  for (const quote of quotes) {
    if (typeof quote !== 'string' || quote.trim() === '') continue;
    const key = stripInvisible(quote).replace(/\s+/g, '');
    if (!seen.has(key)) seen.set(key, stripInvisible(quote).trim());
  }
  return [...seen.values()];
}

function evidenceItem(item, card, boards) {
  const source = card.sources.find((candidate) => candidate.url === item.source);
  return {
    quote: item.quote,
    location: typeof item.location === 'string' ? item.location : '위치 미기재',
    file: typeof item.file === 'string' && item.file !== '' ? item.file : null,
    isImage: item.location === '이미지',
    boardName: source ? boardName(source.board_id, boards) : null,
    postedAt: source?.posted_at ?? null,
    url: safeUrl(item.source),
  };
}

function boardName(boardId, boards) {
  return boards.find((board) => board.id === boardId)?.name ?? boardId;
}

/**
 * 원문 공지 목록 (출처별 링크). 허용되지 않는 주소는 url이 null이라 글자만 보여 준다.
 * @returns {{boardName: string, title: string, postedAt: string, url: string|null}[]}
 */
export function sourceLinks(card, boards) {
  return card.sources.map((source) => ({
    boardName: boardName(source.board_id, boards),
    title: typeof source.title === 'string' ? source.title : '',
    postedAt: source.posted_at,
    url: safeUrl(source.url),
  }));
}

/**
 * 신청 링크와 호스트명. forms.gle·docs.google.com은 누구나 폼을 만들 수 있어 주소가 바뀌어도 막지 못하므로
 * 학생이 어디로 가는지 보이게 호스트명을 같이 보여 준다 (#21 보안 리뷰).
 * @returns {{url: string, host: string}|null}
 */
export function applyLink(card) {
  const url = safeUrl(card.fields?.how_to_apply?.value?.url);
  return url ? { url, host: new URL(url).hostname } : null;
}

/**
 * 마감 변경 이력 (화면설계.md 4절: 이전 값 취소선 + 출처 공지 + 확인 일시).
 * @returns {{oldText: string, newText: string, oldSource: string|null, newSource: string|null, checkedAt: string|null}[]}
 */
export function deadlineHistory(card, boards) {
  const describe = (url) => {
    const source = card.sources.find((candidate) => candidate.url === url);
    if (!source) return null;
    const posted = parseContractDate(source.posted_at);
    return `${boardName(source.board_id, boards)}${posted ? ` ${formatShortDate(posted.date)} 공지` : ''}`;
  };
  // 계약 1절 Change에는 time_given이 없어, 값에 시각이 적혀 있으면("YYYY-MM-DD HH:MM") 시각이 있는 것으로 본다
  const hasTime = (value) => parseContractDate(value)?.time !== null && parseContractDate(value) !== null;
  return (card.deadline_history ?? [])
    .filter((change) => change.field === 'apply_end')
    .map((change) => ({
      oldText: `신청 마감 ${dateTimeText(change.old, hasTime(change.old))}`,
      newText: `신청 마감 ${dateTimeText(change.new, hasTime(change.new))}`,
      oldSource: describe(change.old_source),
      newSource: describe(change.new_source),
      checkedAt: typeof change.checked_at === 'string' ? change.checked_at : null,
    }));
}

/** 제출물 배지 (계약 태그) */
export function deliverableBadges(card) {
  return deliverableTags(card);
}

// 문의 초안 항목별 질문. 담을 수 없는 항목(혜택 등)은 묻지 않는다
const INQUIRY_QUESTIONS = {
  apply: '신청 기간(마감 일시)',
  campus: '참가할 수 있는 캠퍼스',
  grades: '참가할 수 있는 학년',
  enrollment: '참가할 수 있는 학적(휴학생·초과학기생·졸업유예생 등)',
  team: '개인으로 참가할 수 있는지와 팀 인원',
  how_to_apply: '신청 방법',
};

// 받침 있으면 first(이·을), 없으면 second(가·를). 괄호로 끝나면 괄호 앞 낱말로 본다
export function withParticle(word, first, second) {
  if (typeof word !== 'string' || word === '') return '';
  const base = word.replace(/\([^)]*\)$/, '');
  const code = base.charCodeAt(base.length - 1) - 0xac00;
  const hasFinal = code >= 0 && code <= 11171 && code % 28 !== 0;
  return `${word}${hasFinal ? first : second}`;
}

/**
 * 담당 부서 문의 초안. 핵심 항목·신청 방법 중 공지에 없거나(missing·값 없음) 원문 확인이 필요한(needs_check) 항목을 묻는다.
 * 사람 대조 전(확인 전)인 항목은 원문을 직접 보면 되므로 묻지 않는다.
 * 공지 문자열은 보이지 않는 글자를 지우고 넣는다 (복사한 글에서 순서가 뒤바뀌어 보이지 않게).
 * @param {object} card
 * @param {object|null} conditions  저장된 내 조건 (있으면 자기소개 한 줄을 붙인다)
 * @returns {{items: string[], text: string, recipient: string}|null}  text: 인사/질문/자기소개/마무리를 줄바꿈으로 나눈 글.
 *   물을 것이 없으면 null
 */
export function inquiryDraft(card, conditions = null) {
  const isMissing = (field) => !field || field.status === 'missing' || field.value === null || field.value === undefined;
  const asks = Object.keys(INQUIRY_QUESTIONS)
    .map((name) => ({ name, field: card.fields?.[name] }))
    .filter(({ field }) => isMissing(field) || field.status === 'needs_check');
  if (asks.length === 0) return null;

  const title = typeof card.title === 'string' ? stripInvisible(card.title) : '대회';
  const paragraphs = [`안녕하세요. 「${title}」 공지를 보고 문의드립니다.`];
  // 공지에 없는 항목은 한 문장으로 묶는다 ("…학년, …학적을 찾지 못했습니다")
  const missing = asks.filter(({ field }) => isMissing(field)).map(({ name }) => INQUIRY_QUESTIONS[name]);
  if (missing.length > 0) {
    const listed = [...missing.slice(0, -1), withParticle(missing.at(-1), '을', '를')].join(', ');
    paragraphs.push(`공지에서 ${listed} 찾지 못했습니다. 알려 주시면 감사하겠습니다.`);
  }
  for (const { name, field } of asks.filter(({ field }) => !isMissing(field))) {
    const quotes = distinctQuotes((field.evidence ?? []).map((item) => item?.quote));
    if (quotes.length > 1) {
      paragraphs.push(`${withParticle(INQUIRY_QUESTIONS[name], '이', '가')} 공지에 ${quotes.map((quote) => `「${quote}」`).join(', ')}로 서로 다르게 적혀 있습니다. 어느 쪽이 맞는지 확인 부탁드립니다.`);
    } else {
      paragraphs.push(`${withParticle(INQUIRY_QUESTIONS[name], '을', '를')} 공지만으로는 정확히 알기 어려워 확인 부탁드립니다.`);
    }
  }
  const me = describeConditions(conditions, { withParticipation: false });
  // "재학생 학생"처럼 겹치지 않게: 학적(…생)으로 끝나면 "학생"을 붙이지 않는다
  if (me) paragraphs.push(me.endsWith('생') ? `저는 ${me}입니다.` : `저는 ${me} 학생입니다.`);
  paragraphs.push('감사합니다.');

  const contact = card.fields?.contact;
  const contactText = contact && contact.status !== 'missing' && contact.value ? fieldValueText('contact', contact.value) : null;
  return {
    items: asks.map(({ name }) => LABEL_BY_FIELD.get(name)),
    text: paragraphs.join('\n'),
    recipient: contactText ?? '원문 공지에 적힌 담당 부서',
  };
}
