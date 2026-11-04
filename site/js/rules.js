// 판정 규칙: 학생 조건 × 대회 카드 → 신청 가능 / 팀 구성 시 가능 / 확인 필요 / 불가.
// AI를 쓰지 않는다. 규칙의 기준은 docs/api-contract.md 3절이다. 규칙을 바꿀 때는 계약을 먼저 고친다.
// 안전 원칙: 확실하지 않으면 eligible을 내지 않는다 ("불가인데 가능"이 가장 나쁜 오류).

import { parseContractDate, toKstInstant, kstDateString } from './dates.js';

export const KEY_FIELDS = ['apply', 'campus', 'grades', 'enrollment', 'team'];

// 계약 3절: 결과를 정한 사유가 앞에 오도록 하는 순서
const OUTCOME_ORDER = ['fail', 'unknown', 'team', 'note', 'ok'];

// 학생이 조건으로 고를 수 있는 학적 (계약 2절 enrollment)
const STUDENT_ENROLLMENT_TERMS = new Set(['재학', '휴학', '수료', '초과학기', '졸업유예']);
// 학생 조건에 없는 제외 대상 (추출 프롬프트 v0.2의 표준 낱말). 결과는 바꾸지 않고 안내만 한다.
const UNANSWERABLE_EXCLUSIONS = new Set(['기존 수상자', '대학원생', '졸업']);
const TRANSFER_TERM = '편입생';

/**
 * 카드 하나를 학생 조건으로 판정한다.
 * @param {object} card  계약 1절 Card (`id`, `fields`)
 * @param {object} conditions  계약 2절 조건. 말하지 않은 조건은 null
 * @param {{now?: Date}} [options]  now: 판정 기준 시각 (기본: 지금)
 * @returns {{card_id: string, result: 'eligible'|'team_needed'|'check'|'ineligible', reasons: object[]}}
 * @throws {TypeError} card나 conditions가 객체가 아니거나 now가 올바른 Date가 아닐 때
 */
export function judgeCard(card, conditions, options = {}) {
  if (!card || typeof card !== 'object') throw new TypeError('card가 없습니다');
  if (!conditions || typeof conditions !== 'object') throw new TypeError('conditions가 없습니다');
  const now = options.now ?? new Date();
  if (!(now instanceof Date) || Number.isNaN(now.getTime())) throw new TypeError('now가 올바른 날짜가 아닙니다');

  const fields = card.fields ?? {};
  const reasons = [
    ...judgeField('apply', fields.apply, (value) => checkApply(value, now)),
    ...judgeField('campus', fields.campus, (value) => checkCampus(value, conditions.campus)),
    ...judgeField('grades', fields.grades, (value) => checkGrades(value, conditions.grade)),
    ...judgeField('enrollment', fields.enrollment, (value) => checkEnrollment(value, conditions)),
    ...judgeField('team', fields.team, (value) => checkTeam(value, conditions.participation)),
  ];
  reasons.sort((a, b) => OUTCOME_ORDER.indexOf(a.outcome) - OUTCOME_ORDER.indexOf(b.outcome));

  return { card_id: card.id ?? null, result: decideResult(reasons), reasons };
}

function decideResult(reasons) {
  const outcomes = new Set(reasons.map((reason) => reason.outcome));
  if (outcomes.has('fail')) return 'ineligible';
  if (outcomes.has('unknown')) return 'check';
  if (outcomes.has('team')) return 'team_needed';
  return 'eligible';
}

// 항목 상태(status)에 따라 규칙 결과를 바꾼다 (계약 3절 "항목 상태와 판정" 표).
// human_verified가 아닌 상태는 (알 수 없는 문자열도) 모두 확인 전으로 다룬다.
function judgeField(fieldName, field, checkValue) {
  if (!field || field.status === 'missing' || field.value === null || field.value === undefined) {
    return [makeReason(fieldName, field, rule('unknown', `${FIELD_LABELS[fieldName]}이 공지에 없음`))];
  }
  const ruleReasons = checkValue(field.value);

  if (field.status === 'needs_check') {
    const noteText = field.note ? ` (${field.note})` : '';
    return [
      makeReason(fieldName, field, rule('unknown', `${FIELD_LABELS[fieldName]}: 원문 확인 필요${noteText}`)),
      ...ruleReasons.filter((reason) => reason.outcome === 'note').map((reason) => makeReason(fieldName, field, reason)),
    ];
  }

  const isVerified = field.status === 'human_verified';
  return ruleReasons.map((reason) => {
    const fieldReason = makeReason(fieldName, field, reason);
    if (isVerified) return fieldReason;
    if (reason.outcome === 'ok' || reason.outcome === 'team') {
      // 확인 전 항목은 맞더라도, 팀 구성 필요라도 확정하지 않는다
      return { ...fieldReason, outcome: 'unknown', message: `${reason.message} (확인 전)`, unverified: true };
    }
    return { ...fieldReason, unverified: true };
  });
}

const FIELD_LABELS = {
  apply: '신청기간',
  campus: '캠퍼스 조건',
  grades: '학년 조건',
  enrollment: '학적 조건',
  team: '참가 인원 조건',
};

// 계약 3절 reasons 한 건
function makeReason(fieldName, field, { outcome, message }) {
  return { field: fieldName, outcome, message, quote: firstQuote(field), unverified: false };
}

function firstQuote(field) {
  const quote = field?.evidence?.[0]?.quote;
  return typeof quote === 'string' && quote !== '' ? quote : null;
}

function rule(outcome, message) {
  return { outcome, message };
}

// --- 항목별 규칙. 반환값은 { outcome, message } 목록이다. ---

function checkApply(apply, now) {
  const end = parseContractDate(apply.end);
  const start = parseContractDate(apply.start);
  const reasons = [];
  if (start && now < toKstInstant(start.date, start.time ?? '00:00')) {
    reasons.push(rule('note', `신청 시작 전 (${apply.start}부터)`));
  }
  if (apply.end === null || apply.end === undefined) {
    reasons.push(rule('unknown', '마감일이 공지에 없음 (상시 접수 등)'));
    return reasons;
  }
  if (!end) {
    reasons.push(rule('unknown', `마감일 형식을 읽을 수 없음: ${apply.end}`));
    return reasons;
  }
  const hasEndTime = apply.time_given === true && end.time !== null;
  if (hasEndTime) {
    const deadline = toKstInstant(end.date, end.time);
    reasons.push(now > deadline
      ? rule('fail', `신청 마감됨 (${apply.end})`)
      : rule('ok', `신청 마감 전 (${apply.end}까지)`));
    return reasons;
  }
  const today = kstDateString(now);
  if (today > end.date) {
    reasons.push(rule('fail', `신청 마감됨 (${end.date})`));
  } else if (today === end.date) {
    reasons.push(rule('unknown', `오늘 마감인데 마감 시각이 공지에 없음 (${end.date})`));
  } else {
    reasons.push(rule('ok', `신청 마감 전 (${end.date}까지, 시각 미기재)`));
  }
  return reasons;
}

function checkCampus(campus, studentCampus) {
  if (campus === 'any') return [rule('ok', '캠퍼스 제한 없음')];
  // 빈 목록은 추출 실패일 가능성이 높아 불가로 읽지 않는다
  if (!Array.isArray(campus) || campus.length === 0) return [rule('unknown', '캠퍼스 조건 형식을 읽을 수 없음')];
  const label = `${campus.join('·')}캠퍼스`;
  if (!studentCampus) return [rule('unknown', `${label}만 참가 가능 (내 캠퍼스를 입력하지 않음)`)];
  return campus.includes(studentCampus)
    ? [rule('ok', `${label} 참가 가능`)]
    : [rule('fail', `${label} 재학생만 참가 가능 (내 조건: ${studentCampus}캠)`)];
}

function checkGrades(grades, studentGrade) {
  if (grades === 'any') return [rule('ok', '학년 제한 없음')];
  if (!Array.isArray(grades) || grades.length === 0 || !grades.every(Number.isInteger)) {
    return [rule('unknown', '학년 조건 형식을 읽을 수 없음')];
  }
  const label = `${grades.join('·')}학년`;
  if (studentGrade === null || studentGrade === undefined) {
    return [rule('unknown', `${label}만 참가 가능 (내 학년을 입력하지 않음)`)];
  }
  return grades.includes(studentGrade)
    ? [rule('ok', `${label} 참가 가능`)]
    : [rule('fail', `${label}만 참가 가능 (내 조건: ${studentGrade}학년)`)];
}

function checkEnrollment(enrollment, conditions) {
  const { allowed, excluded } = enrollment;
  if (!Array.isArray(allowed) || !Array.isArray(excluded)) return [rule('unknown', '학적 조건 형식을 읽을 수 없음')];

  // 학생 조건으로 답할 수 없는 표현(원문 표현, 허용 목록의 편입생·대학원생 등)이 있으면
  // 맞든 안 맞든 확정하지 않는다. 예: 허용 ["재학", "직전학기 평점 3.0 이상"]
  const unreadableAllowed = allowed.filter((term) => !STUDENT_ENROLLMENT_TERMS.has(term));
  const unreadableExcluded = excluded.filter((term) => !STUDENT_ENROLLMENT_TERMS.has(term)
    && term !== TRANSFER_TERM && !UNANSWERABLE_EXCLUSIONS.has(term));
  const unreadable = [...unreadableAllowed, ...unreadableExcluded];

  const reasons = [];
  if (unreadable.length > 0) reasons.push(rule('unknown', `학적 조건에 확인이 필요한 표현: ${unreadable.join(', ')}`));
  const unanswerable = excluded.filter((term) => UNANSWERABLE_EXCLUSIONS.has(term));
  if (unanswerable.length > 0) reasons.push(rule('note', `참가 불가 대상: ${unanswerable.join(', ')}`));
  reasons.push(...checkTransfer(excluded, conditions.is_transfer));
  reasons.push(checkEnrollmentStatus(allowed.filter((term) => STUDENT_ENROLLMENT_TERMS.has(term)),
    excluded, conditions.enrollment, unreadableAllowed.length > 0));
  return reasons;
}

function checkEnrollmentStatus(allowed, excluded, status, hasUnreadableAllowed) {
  const hasStatusRule = allowed.length > 0 || hasUnreadableAllowed
    || excluded.some((term) => STUDENT_ENROLLMENT_TERMS.has(term));
  if (!status) {
    return hasStatusRule
      ? rule('unknown', '학적 조건이 있는데 내 학적을 입력하지 않음')
      : rule('ok', '학적 제한 문구 없음');
  }
  if (excluded.includes(status)) return rule('fail', `${status}생은 참가 불가 (제외: ${excluded.join(', ')})`);
  if (allowed.includes(status) || (allowed.length === 0 && !hasUnreadableAllowed)) {
    return rule('ok', `${status} 학적 참가 가능`);
  }
  if (hasUnreadableAllowed || (status === '초과학기' && allowed.includes('재학'))) {
    return rule('unknown', `허용 학적에 ${status}생이 포함되는지 공지로는 알 수 없음`);
  }
  return rule('fail', `${allowed.join(', ')}생만 참가 가능 (내 조건: ${status})`);
}

function checkTransfer(excluded, isTransfer) {
  if (!excluded.includes(TRANSFER_TERM)) return [];
  if (isTransfer === true) return [rule('fail', '편입생은 참가 불가')];
  if (isTransfer === false) return [];
  return [rule('unknown', '편입생은 참가 불가인데 편입 여부를 입력하지 않음')];
}

function checkTeam(team, participation) {
  const sizeText = formatTeamSize(team);
  if (team.individual_ok === true) return [rule('ok', `개인 참가 가능${sizeText}`)];
  if (participation === '팀') return [rule('ok', `팀 참가 가능${sizeText}`)];
  if (team.individual_ok === false) return [rule('team', `팀으로만 참가 가능${sizeText}`)];
  return [rule('unknown', `개인 참가 가능 여부가 공지에 없음${sizeText}`)];
}

function formatTeamSize(team) {
  const hasMin = Number.isInteger(team.min);
  const hasMax = Number.isInteger(team.max);
  if (hasMin && hasMax) return ` (${team.min}~${team.max}인)`;
  if (hasMax) return ` (${team.max}인 이내)`;
  if (hasMin) return ` (${team.min}인 이상)`;
  return '';
}
