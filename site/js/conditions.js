// "내 조건" 브라우저 저장 (계약 2절 "브라우저 저장"). 맞춤 추천(#24)이 저장하고 카드 상세(#25)·홈이 읽는다.
// 저장하는 것은 해석된 조건 값뿐이다. 입력 문장과 unknown_terms는 이름·학번이 들어 있을 수 있어 저장하지 않는다 (PR #36 보안 리뷰).
// 서버로 보내지 않는다.

export const CONDITIONS_STORAGE_KEY = 'myongji-radar.conditions.v1';
// 기본 저장소 표시. 사이트 데이터를 막은 브라우저는 localStorage에 접근하는 순간 오류를 내므로,
// 기본 매개변수에서 바로 읽지 않고 try 안에서 꺼낸다
const BROWSER_STORAGE = Symbol('localStorage');
const resolveStorage = (storage) => (storage === BROWSER_STORAGE ? globalThis.localStorage : storage);

const CHOICES = {
  campus: ['인문', '자연'],
  enrollment: ['재학', '휴학', '수료', '초과학기', '졸업유예'],
  participation: ['개인', '팀'],
  period: ['마감임박', '이번주', '이번달'],
};
const DELIVERABLE_CHOICES = ['기획서·제안서', '영상', '에세이·수기', '코드·개발', '발표', '디자인', '현장 참가'];

/**
 * 저장된 값을 계약 2절 조건으로 검사한다. 하나라도 틀리면 null (다른 버전·손으로 고친 값을 믿지 않는다).
 * @param {unknown} value
 * @returns {object|null} 계약 2절 조건 (unknown_terms는 항상 빈 배열)
 */
export function normalizeConditions(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const pick = (name, isValid) => {
    const item = value[name] ?? null;
    return item === null || isValid(item) ? { ok: true, item } : { ok: false };
  };
  const fields = {
    campus: pick('campus', (item) => CHOICES.campus.includes(item)),
    grade: pick('grade', (item) => Number.isInteger(item) && item >= 1 && item <= 6),
    is_transfer: pick('is_transfer', (item) => typeof item === 'boolean'),
    enrollment: pick('enrollment', (item) => CHOICES.enrollment.includes(item)),
    participation: pick('participation', (item) => CHOICES.participation.includes(item)),
    period: pick('period', (item) => CHOICES.period.includes(item)),
  };
  if (Object.values(fields).some((field) => !field.ok)) return null;
  const deliverables = value.deliverables ?? [];
  if (!Array.isArray(deliverables) || !deliverables.every((tag) => DELIVERABLE_CHOICES.includes(tag))) return null;
  return {
    campus: fields.campus.item,
    grade: fields.grade.item,
    is_transfer: fields.is_transfer.item,
    enrollment: fields.enrollment.item,
    participation: fields.participation.item,
    deliverables: [...new Set(deliverables)],
    period: fields.period.item,
    unknown_terms: [],
  };
}

/** 학생이 판정에 쓰는 조건(캠퍼스·학년·편입·학적·참가 형태)을 하나라도 골랐는지 */
export function hasJudgedConditions(conditions) {
  return ['campus', 'grade', 'is_transfer', 'enrollment', 'participation'].some((name) => conditions?.[name] !== null && conditions?.[name] !== undefined);
}

/**
 * 저장된 내 조건. 없거나 읽을 수 없으면 null.
 * @param {Storage|undefined} [storage]  기본 localStorage. 개인정보 보호 모드 등으로 막혀 있으면 null
 */
export function readSavedConditions(storage = BROWSER_STORAGE) {
  let text;
  try {
    text = resolveStorage(storage)?.getItem(CONDITIONS_STORAGE_KEY);
  } catch (error) {
    console.warn('저장된 조건을 읽을 수 없음', error);
    return null;
  }
  if (typeof text !== 'string') return null;
  try {
    return normalizeConditions(JSON.parse(text));
  } catch {
    return null; // 깨진 저장값은 없는 것으로 본다 (다음 저장 때 덮어씀)
  }
}

/**
 * 내 조건을 저장한다. unknown_terms는 저장하지 않는다.
 * @returns {boolean} 저장했는지 (조건 형식이 틀렸거나 저장소가 막혀 있으면 false)
 */
export function saveConditions(conditions, storage = BROWSER_STORAGE) {
  const normalized = normalizeConditions(conditions);
  if (!normalized) return false;
  const stored = { ...normalized };
  delete stored.unknown_terms;
  try {
    resolveStorage(storage).setItem(CONDITIONS_STORAGE_KEY, JSON.stringify(stored));
    return true;
  } catch (error) {
    console.warn('조건을 저장할 수 없음', error);
    return false;
  }
}

/**
 * "자연캠퍼스 2학년 재학생, 개인 참가" 같은 한 줄. 고른 것이 없으면 null.
 * @param {{withParticipation?: boolean}} [options]  false면 신분(캠퍼스·학년·편입·학적)만 (문의 초안 자기소개용)
 */
export function describeConditions(conditions, { withParticipation = true } = {}) {
  if (!conditions) return null;
  const identity = [
    conditions.campus ? `${conditions.campus}캠퍼스` : null,
    conditions.grade ? `${conditions.grade}학년` : null,
    conditions.is_transfer === true ? '편입생' : null,
    conditions.enrollment ? `${conditions.enrollment}생` : null,
  ].filter(Boolean).join(' ');
  const participation = withParticipation && conditions.participation ? `${conditions.participation} 참가` : null;
  const text = [identity, participation].filter(Boolean).join(', ');
  return text === '' ? null : text;
}
