// 제작 기록 계산 (#26): log.json 형식 검사, 측정값 표시, 검수 체험 채점, 저장소 링크. DOM 없음 (Node 테스트).
// 형식은 docs/api-contract.md 4절. 원칙: 측정하지 않은 수치는 보여 주지 않는다 ("측정 중").

// 저장소 파일 링크의 기준 주소. log.json의 path를 여기에 붙인다 (외부 주소를 그대로 받지 않는다)
export const REPO_FILE_BASE = 'https://github.com/jinseojang0903/myongji-radar/blob/main/';

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;
const STAGE_BY = new Set(['코드', '규칙', 'AI', '사람']);
const STAGE_STATUS = new Set(['동작', '만드는 중']);
const TOOL_STATUS = new Set(['사용', '예정']);
const QUOTE_LOCATIONS = new Set(['본문', '이미지', '첨부']);
const CIRCLED_NUMBERS = ['①', '②', '③', '④', '⑤'];

const isText = (value) => typeof value === 'string' && value.trim() !== '';
const isDate = (value) => typeof value === 'string' && DATE_PATTERN.test(value) && !Number.isNaN(Date.parse(`${value}T00:00:00Z`))
  && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;

/**
 * 저장소 안 파일 경로 → 저장소 웹 주소. 경로가 아니거나 밖으로 나가는 경로면 null.
 * @param {unknown} path  예: "prompts/CHANGELOG.md"
 */
export function repoFileUrl(path) {
  if (!isText(path)) return null;
  const segments = path.split('/');
  const isSafe = segments.every((segment) => segment !== '' && segment !== '.' && segment !== '..' && !/[\\:?#%]/.test(segment));
  if (!isSafe) return null;
  try {
    return REPO_FILE_BASE + segments.map(encodeURIComponent).join('/');
  } catch {
    return null; // 짝이 안 맞는 서로게이트처럼 인코딩할 수 없는 글자: 링크만 빼고 화면은 그린다
  }
}

/**
 * 측정값이 믿을 수 있는 형식인지 (계약 4절). 수치는 개수(value)와 전체(total)로 쓰고, 언제(measured_at)·어디서(source)
 * 쟀는지가 있어야 한다. 없으면 지어낸 수치일 수 있어 쓰지 않는다. 기록 갱신일(updatedAt)보다 늦은 측정일도 받지 않는다.
 * @param {unknown} measure
 * @param {string} [updatedAt]  log.json의 updated_at
 * @returns {boolean}
 */
export function isValidMeasure(measure, updatedAt) {
  if (measure === null) return true;
  if (!measure || typeof measure !== 'object') return false;
  if (!Number.isInteger(measure.value) || measure.value < 0) return false;
  if (!Number.isInteger(measure.total) || measure.total <= 0 || measure.value > measure.total) return false;
  if (!isDate(measure.measured_at) || !isText(measure.source)) return false;
  return updatedAt === undefined || measure.measured_at <= updatedAt;
}

/**
 * 측정값 표시. null이면 "측정 중".
 * @returns {{text: string, isPending: boolean, detail: string|null}}  detail: 언제·어디서 쟀는지
 */
export function measureText(measure) {
  if (measure === null || measure === undefined) return { text: '측정 중', isPending: true, detail: null };
  const unit = isText(measure.unit) ? ` ${measure.unit}` : '';
  const text = `${measure.value} / ${measure.total}${unit}`;
  const detail = [isText(measure.note) ? measure.note : null, `${measure.measured_at} 측정`, measure.source].filter(Boolean).join(' · ');
  return { text, isPending: false, detail };
}

/**
 * log.json 형식을 검사한다. 틀린 항목은 빼고, 틀린 측정값은 "측정 중"으로, 쓸 수 없는 경로는 링크만 빼고 무엇을 고쳤는지 돌려준다.
 * @param {unknown} raw
 * @returns {{data: object, rejected: {section: string, index: number, reason: string}[]}}  index: 원래 목록의 번호
 * @throws {Error} 최상위 형식(각 목록이 배열, updated_at 날짜)이 틀리면 (화면 전체가 오류 안내)
 */
export function validateLogData(raw) {
  if (!raw || typeof raw !== 'object') throw new Error('log.json이 객체가 아닙니다');
  if (!isDate(raw.updated_at)) throw new Error('updated_at 날짜 형식이 틀렸습니다');
  const sections = ['stages', 'accuracy', 'prompts', 'ai_errors', 'quiz', 'tools'];
  for (const section of sections) {
    if (!Array.isArray(raw[section])) throw new Error(`${section}이 배열이 아닙니다`);
  }
  const rejected = [];
  const reject = (section, index, reason) => rejected.push({ section, index, reason });
  // check가 이유를 돌려주면 그 항목을 빼고, 통과하면 fix로 칸 단위 손질(측정값·경로)을 한다
  const keep = (section, check, fix = (item) => item) => raw[section].flatMap((item, index) => {
    const reason = item && typeof item === 'object' ? check(item) : '객체가 아님';
    if (reason) {
      reject(section, index, reason);
      return [];
    }
    return [fix(item, index)];
  });
  const fixMeasure = (section, index, key, measure) => {
    if (isValidMeasure(measure ?? null, raw.updated_at)) return measure ?? null;
    reject(section, index, `${key}: 측정일·출처·전체 수가 없거나 형식이 틀린 수치`);
    return null;
  };
  const fixPath = (section, index, item) => {
    if (item.path === null || item.path === undefined || repoFileUrl(item.path)) return { ...item, path: item.path ?? null };
    reject(section, index, 'path: 저장소 안 경로가 아님 (링크를 뺌)');
    return { ...item, path: null };
  };
  const seenQuizIds = new Set();

  const data = {
    schema_version: raw.schema_version,
    updated_at: raw.updated_at,
    stages: keep('stages', (item) => (isText(item.name) && STAGE_BY.has(item.by) && STAGE_STATUS.has(item.status) && isText(item.detail) ? null : '이름·담당·상태·설명')),
    // 측정값이 틀리면 그 칸만 "측정 중"으로 (줄 전체를 빼면 비교 표에서 방법이 사라진다)
    accuracy: keep('accuracy', (item) => (isText(item.method) ? null : '방법 이름'), (item, index) => ({
      method: item.method,
      filled: fixMeasure('accuracy', index, 'filled', item.filled),
      field_accuracy: fixMeasure('accuracy', index, 'field_accuracy', item.field_accuracy),
      risky_misreads: fixMeasure('accuracy', index, 'risky_misreads', item.risky_misreads),
    })),
    prompts: keep('prompts', (item) => (isText(item.version) && isDate(item.date) && isText(item.changes) && isText(item.reason) ? null : '버전·날짜·바꾼 것·이유'),
      (item, index) => fixPath('prompts', index, { ...item, accuracy: fixMeasure('prompts', index, 'accuracy', item.accuracy) })),
    ai_errors: keep('ai_errors', (item) => (isDate(item.date) && isText(item.where) && isText(item.ai_did) && isText(item.caught_by) && isText(item.fix) ? null : '날짜·작업·틀린 것·잡은 방법·고친 것'),
      (item, index) => fixPath('ai_errors', index, item)),
    quiz: keep('quiz', (item) => {
      const reason = checkQuiz(item);
      if (reason) return reason;
      if (seenQuizIds.has(item.id)) return `id ${item.id}가 두 번 나옴`;
      seenQuizIds.add(item.id);
      return null;
    }, (item, index) => fixPath('quiz', index, item)),
    tools: keep('tools', (item) => (isText(item.name) && isText(item.used_for) && TOOL_STATUS.has(item.status) ? null : '이름·쓴 곳·상태')),
  };
  return { data, rejected };
}

function checkQuiz(item) {
  if (!ID_PATTERN.test(item.id ?? '')) return 'id 형식';
  if (!isText(item.kind) || !isText(item.question) || !isText(item.explanation)) return '종류·질문·해설';
  if (!Array.isArray(item.options) || item.options.length < 2 || item.options.length > CIRCLED_NUMBERS.length || !item.options.every(isText)) return '보기는 글 2~5개';
  if (!Number.isInteger(item.answer) || item.answer < 0 || item.answer >= item.options.length) return '정답 번호';
  const isQuote = (quote) => isText(quote?.text) && QUOTE_LOCATIONS.has(quote.location) && (quote.url === null || quote.url === undefined || typeof quote.url === 'string');
  if (!Array.isArray(item.quotes) || !item.quotes.every(isQuote)) return '원문 인용';
  return null;
}

/** 보기 번호 표시 "①" (0부터) */
export function optionMark(index) {
  return CIRCLED_NUMBERS[index] ?? `${index + 1}.`;
}

/**
 * 검수 체험 채점.
 * @returns {{isCorrect: boolean, answerText: string}}
 * @throws {RangeError} 보기 번호가 범위 밖이면
 */
export function checkAnswer(quiz, choice) {
  if (!Number.isInteger(choice) || choice < 0 || choice >= quiz.options.length) throw new RangeError('보기 번호가 범위 밖입니다');
  return { isCorrect: choice === quiz.answer, answerText: quiz.options[quiz.answer] };
}

/** 단계 담당 → 배지 종류 (처리 단계 라벨 .step과 함께 씀) */
export function stageKind(by) {
  return { 코드: 'code', 규칙: 'rule', AI: 'ai', 사람: 'human' }[by] ?? 'code';
}
