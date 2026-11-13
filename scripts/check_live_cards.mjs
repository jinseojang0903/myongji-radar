// 공개된 cards.json을 배포 직후 한 번에 점검한다 (#98).
// 화면과 같은 형식 검사(validateCardData)를 쓰고, 내보내기(#17)의 공개 상한·연락처 가림·링크 허용 목록이
// 실제 파일에도 지켜졌는지 한 번 더 본다.
// 출력 원칙: 점검 기록을 이슈에 붙일 수 있게, 파일에서 온 글은 계약 형식에 맞는 id·알려진 키 이름·건수만 쓴다.
// 형식이 틀린 id, 모르는 키, 예외 메시지(원문 조각이 들어 있을 수 있음)는 옮기지 않고 위치 번호·고정 문구로 바꾼다.
//
// 사용: node scripts/check_live_cards.mjs                 기본 배포 주소 점검
//       node scripts/check_live_cards.mjs --base <https 주소>
//       node scripts/check_live_cards.mjs --file <cards.json 경로>   (화면 파일 확인은 건너뜀)
// 종료 코드: 0 문제 없음, 1 경고 있음(공개는 됨),
//           2 실패(못 읽음·예시 데이터 그대로·형식 오류·카드 0장·화면 파일 안 열림·점검 중 오류)

import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { validateCardData, deadlineOf, STALE_HOURS } from '../site/js/cards.js';

export const DEFAULT_BASE = 'https://myongji-radar-site.vercel.app';
const PAGES = ['/', '/recommend.html', '/card.html', '/log.html', '/data/log.json'];
const FETCH_TIMEOUT_MS = 15000;

// 내보내기와 같은 값 (pipeline/exporter/cards.py, 계약 1절)
const MAX_QUOTE_CHARS = 200;
const MAX_EVIDENCE = 5;
const MAX_CARD_QUOTE_CHARS = 1500;
const ALLOWED_HOSTS = ['mju.ac.kr', 'forms.gle', 'docs.google.com'];
const SCHOOL_DOMAIN = 'mju.ac.kr';
const CARD_ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;
const BOARD_ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,39}$/;
const HOUR_MS = 3600 * 1000;
const FUTURE_TOLERANCE_HOURS = 1;
const MAX_MODEL_LABEL = 40;

// 출력에 그대로 써도 되는 키 이름 (계약 1절에 나오는 것만)
const CARD_FIELDS = new Set(['apply', 'stages', 'campus', 'grades', 'enrollment', 'team', 'deliverables',
  'benefits', 'mileage', 'how_to_apply', 'contact']);
const KNOWN_KEYS = new Set([...CARD_FIELDS, 'id', 'title', 'organizer', 'scope', 'sources', 'fields', 'deadline_history',
  'meta', 'board_id', 'url', 'posted_at', 'last_seen_at', 'value', 'status', 'evidence', 'note', 'quote', 'location',
  'file', 'source', 'start', 'end', 'time_given', 'name', 'tags', 'text', 'area', 'points', 'dept', 'email', 'phone',
  'min', 'max', 'individual_ok', 'allowed', 'excluded', 'field', 'old', 'new', 'old_source', 'new_source',
  'checked_at', 'prompt_version', 'extract_model', 'cross_model', 'boards', 'list_url']);
// site/js/cards.js validateCardData가 던지는 최상위 오류 문구
const TOP_LEVEL_ERRORS = new Set(['카드 데이터가 객체가 아닙니다', 'cards 배열이 없습니다', 'boards 배열이 없습니다',
  'generated_at이 날짜가 아닙니다']);
const URL_KEYS = new Set(['url', 'list_url', 'source', 'old_source', 'new_source']);
const ID_KEYS = new Set(['id', 'board_id']);

// 내보내기는 보이지 않는 글자를 지우고 전각·여러 대시를 바꾼 사본에서 연락처를 찾는다 (_strip_invisible, _probe).
// 같은 정규화를 거친 뒤 같은 꼴의 정규식으로 본다. 내보내기 정규식을 모두 옮기지는 않았으므로 두 번째 그물이다.
const INVISIBLE = /[\p{Cc}\p{Cf}\p{Co}\p{Cn}͏ᅟᅠ᠎ㅤﾠ︀-️]/gu;
const DASHES = /[‐-―−﹘﹣－─━➖ㅡーｰ]/g;
const PHONE_SEPARATOR = '[\\s\\-.()/~]{0,3}';
const MOBILE = new RegExp(`(?<!\\d)(?:\\+?82${PHONE_SEPARATOR}(?:\\(0\\))?${PHONE_SEPARATOR}0?|\\(?0)1[016789]\\)?`
  + `${PHONE_SEPARATOR}\\d{3,4}${PHONE_SEPARATOR}\\d{4}(?!\\d)`);
const EMAIL = /(?<![\p{L}\p{N}_.%+-])[\p{L}\p{N}_.%+-]+\s*(?:@|\[at\]|\(at\))\s*([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)/giu;
const CONTACT_EMAIL = /^[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)$/;
const LONG_NUMBER = /(?<!\d)\d{8,}(?!\d)/;

const isSchoolDomain = (domain) => domain.toLowerCase() === SCHOOL_DOMAIN || domain.toLowerCase().endsWith(`.${SCHOOL_DOMAIN}`);
const isAllowedHost = (host) => ALLOWED_HOSTS.some((allowed) => host === allowed || host.endsWith(`.${allowed}`));
// 내보내기 길이 상한은 Python len(코드포인트) 기준이다. JS .length(UTF-16)로 세면 이모지에서 거짓 경고가 난다
const charCount = (text) => [...text].length;
const probe = (text) => text.normalize('NFKC').replace(INVISIBLE, '').replace(DASHES, '-').replace(/\s/gu, ' ');
const cardLabel = (card, index) => (typeof card?.id === 'string' && CARD_ID_PATTERN.test(card.id) ? card.id : `카드 #${index + 1}(id 형식 오류)`);
const boardLabel = (board, index) => `board:${typeof board?.id === 'string' && BOARD_ID_PATTERN.test(board.id) ? board.id : `#${index + 1}(id 형식 오류)`}`;
const keyLabel = (key) => (/^\d+$/.test(key) || KNOWN_KEYS.has(key) ? key : '(모르는 키)');
// 화면 검사 사유는 고정 문구지만 두 사유는 앞에 항목 이름(파일의 키)이 붙는다 (site/js/cards.js findCardProblem).
// 키에 "의 "가 들어 있어도 새지 않게 고정 꼬리로 끝까지 맞춘 뒤 키 전체를 keyLabel로 거른다
const KEYED_REASON = /^([\s\S]*)의 (status가 계약 값이 아님|evidence가 배열이 아님)$/;
const reasonLabel = (reason) => {
  const match = KEYED_REASON.exec(reason);
  return match ? `${keyLabel(match[1])}의 ${match[2]}` : reason;
};

/**
 * cards.json 내용을 점검한다. 값은 돌려주지 않고 위치와 사유만 돌려준다.
 * @param {unknown} data  JSON.parse한 cards.json
 * @param {Date} now
 * @returns {{failures: string[], warnings: string[], summary: object|null}}
 */
export function checkCardsData(data, now) {
  const failures = [];
  const warnings = [];
  let validated;
  try {
    validated = validateCardData(data);
  } catch (error) {
    // 화면 검사가 직접 던진 고정 문구만 옮긴다. 그 밖의 예외(데이터 접근 중 오류)는 main이 종류만 남긴다
    if (!TOP_LEVEL_ERRORS.has(error?.message)) throw error;
    return { failures: [`최상위 형식 오류: ${error.message}`], warnings, summary: null };
  }
  if (data.sample === true) failures.push('예시 데이터(sample: true)가 공개돼 있음');
  if (data.cards.length === 0) failures.push('카드가 0장');

  // 화면이 뺄 카드·게시판: 하나씩 검사해 위치 번호와 함께 남긴다 (rejected의 id는 형식이 틀린 원래 값일 수 있음)
  data.boards.forEach((board, index) => {
    const [problem] = validateCardData({ generated_at: data.generated_at, boards: [board], cards: [] }).rejected;
    if (problem) warnings.push(`화면이 뺄 항목 ${boardLabel(board, index)}: ${reasonLabel(problem.reason)}`);
  });
  data.cards.forEach((card, index) => {
    const [problem] = validateCardData({ generated_at: data.generated_at, boards: [], cards: [card] }).rejected;
    if (problem) warnings.push(`화면이 뺄 항목 ${cardLabel(card, index)}: ${reasonLabel(problem.reason)}`);
  });

  const ageHours = (now.getTime() - Date.parse(data.generated_at)) / HOUR_MS;
  if (ageHours > STALE_HOURS) warnings.push(`generated_at이 ${Math.floor(ageHours)}시간 전 (화면이 오래된 정보 경고를 띄움)`);
  if (ageHours < -FUTURE_TOLERANCE_HOURS) warnings.push('generated_at이 미래 시각');

  const seen = new Set();
  data.cards.forEach((card, index) => {
    const label = cardLabel(card, index);
    if (CARD_ID_PATTERN.test(label)) {
      if (seen.has(label)) warnings.push(`${label}: 카드 id 중복`);
      seen.add(label);
    }
    warnings.push(...checkEvidence(card, label));
    warnings.push(...checkContactEmail(card, label));
    warnings.push(...checkStrings(card, label));
  });
  data.boards.forEach((board, index) => warnings.push(...checkStrings(board, boardLabel(board, index))));

  return { failures, warnings, summary: summarize(validated.data, now, ageHours) };
}

function checkEvidence(card, label) {
  const problems = [];
  const quotes = new Set();
  const fields = card?.fields && typeof card.fields === 'object' ? card.fields : {};
  for (const [name, field] of Object.entries(fields)) {
    const evidence = Array.isArray(field?.evidence) ? field.evidence : [];
    if (evidence.length > MAX_EVIDENCE) problems.push(`${label} ${keyLabel(name)}: 근거 ${evidence.length}개 (상한 ${MAX_EVIDENCE})`);
    for (const item of evidence) {
      if (typeof item?.quote !== 'string') continue;
      const length = charCount(item.quote);
      if (length > MAX_QUOTE_CHARS) problems.push(`${label} ${keyLabel(name)}: 근거 한 문장이 ${length}자 (상한 ${MAX_QUOTE_CHARS})`);
      quotes.add(item.quote);
    }
  }
  const total = [...quotes].reduce((sum, quote) => sum + charCount(quote), 0);
  if (total > MAX_CARD_QUOTE_CHARS) problems.push(`${label}: 근거 합계 ${total}자 (상한 ${MAX_CARD_QUOTE_CHARS})`);
  return problems;
}

// 문의처 메일은 학생 메일과 구분할 수 없어 사람이 검수한 학교 메일만 공개한다 (계약 1절 contact)
function checkContactEmail(card, label) {
  const contact = card?.fields?.contact;
  const email = contact?.value?.email;
  if (typeof email !== 'string' || email === '') return [];
  const problems = [];
  const match = CONTACT_EMAIL.exec(email);
  if (!match || !isSchoolDomain(match[1])) problems.push(`${label} contact: 학교 도메인이 아닌 문의 메일`);
  if (contact.status !== 'human_verified') problems.push(`${label} contact: 사람 검수 전인데 문의 메일이 채워짐`);
  return problems;
}

// 모든 문자열을 돌며 개인 연락처·긴 숫자·허용 목록 밖 링크를 찾는다
function checkStrings(value, label) {
  const problems = [];
  walk(value, [], (text, path) => {
    const key = path[path.length - 1];
    const where = `${label} ${path.map(keyLabel).join('.')}`;
    if (URL_KEYS.has(key)) {
      if (!isAllowedLink(text)) problems.push(`${where}: https·허용 도메인이 아닌 링크`);
      return;
    }
    if (ID_KEYS.has(key) || path[0] === 'meta') return; // 형식이 정해진 식별자·기계 값
    const folded = probe(text);
    if (MOBILE.test(folded)) problems.push(`${where}: 휴대전화 번호 꼴`);
    if (LONG_NUMBER.test(folded)) problems.push(`${where}: 8자리 이상 숫자 (학번·계좌 가능성)`);
    const isContactEmail = key === 'email' && path.includes('contact');
    if (!isContactEmail && [...folded.matchAll(EMAIL)].some((match) => !isSchoolDomain(match[1]))) {
      problems.push(`${where}: 학교 밖 메일 주소 꼴`);
    }
  });
  return problems;
}

function isAllowedLink(text) {
  try {
    const url = new URL(text);
    return url.protocol === 'https:' && !url.username && !url.password && isAllowedHost(url.hostname);
  } catch {
    return false;
  }
}

function walk(value, path, visit) {
  if (typeof value === 'string') visit(value, path);
  else if (Array.isArray(value)) value.forEach((item, index) => walk(item, [...path, String(index)], visit));
  else if (value && typeof value === 'object') {
    for (const [key, item] of Object.entries(value)) walk(item, [...path, key], visit);
  }
}

function summarize(data, now, ageHours) {
  const statuses = {};
  const models = {};
  const deadlines = { open: 0, closed: 0, none: 0, unreadable: 0 };
  for (const card of data.cards) {
    for (const field of Object.values(card.fields)) statuses[field.status] = (statuses[field.status] ?? 0) + 1;
    const raw = card.meta?.extract_model;
    const model = typeof raw === 'string' && raw !== '' ? [...raw].slice(0, MAX_MODEL_LABEL).join('') : '(없음)';
    models[model] = (models[model] ?? 0) + 1;
    deadlines[deadlineOf(card, now).state] += 1;
  }
  return { cards: data.cards.length, boards: data.boards.length, ageHours: Math.round(ageHours * 10) / 10, deadlines, statuses, models };
}

/**
 * 배포된 화면 파일이 열리는지 본다.
 * @returns {Promise<string[]>} 실패한 주소와 상태
 */
export async function checkPages(base, fetchImpl = fetch) {
  const problems = [];
  for (const page of PAGES) {
    try {
      const response = await fetchImpl(new URL(page, base), { signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) });
      if (!response.ok) problems.push(`${page}: HTTP ${response.status}`);
    } catch (error) {
      problems.push(`${page}: ${error?.name ?? '오류'}`);
    }
  }
  return problems;
}

function parseArgs(argv) {
  const options = { base: DEFAULT_BASE, file: null };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--base' && argv[i + 1]) options.base = argv[++i];
    else if (argv[i] === '--file' && argv[i + 1]) options.file = argv[++i];
    else throw new CheckError('알 수 없는 인자');
  }
  if (!options.file && !options.base.startsWith('https://')) throw new CheckError('--base는 https 주소만 받습니다');
  return options;
}

// 이 스크립트가 직접 만든 고정 문구 오류. 이것만 메시지를 출력한다
class CheckError extends Error {}

async function loadCards(options, fetchImpl) {
  let text;
  if (options.file) {
    try {
      text = await readFile(options.file, 'utf8');
    } catch {
      throw new CheckError('파일을 읽을 수 없음'); // 오류 메시지에는 경로가 들어간다
    }
  } else {
    const response = await fetchImpl(new URL('/data/cards.json', options.base),
      { cache: 'no-store', signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) });
    if (!response.ok) throw new CheckError(`cards.json HTTP ${response.status} (아직 공개 전이면 404)`);
    text = await response.text();
  }
  try {
    return JSON.parse(text);
  } catch {
    throw new CheckError('cards.json이 JSON이 아님'); // 파싱 오류 메시지에는 파일 조각이 들어간다
  }
}

/**
 * @param {string[]} argv
 * @param {{now?: Date, fetchImpl?: typeof fetch, loadImpl?: typeof loadCards, log?: (line: string) => void}} [deps]  시험용
 * @returns {Promise<number>} 종료 코드
 */
export async function main(argv, { now = new Date(), fetchImpl = fetch, loadImpl = loadCards, log = console.log } = {}) {
  try {
    const options = parseArgs(argv);
    const data = await loadImpl(options, fetchImpl);
    const { failures, warnings, summary } = checkCardsData(data, now);
    if (!options.file) failures.push(...(await checkPages(options.base, fetchImpl)).map((text) => `화면 파일 ${text}`));

    if (summary) {
      log(`카드 ${summary.cards}장 · 게시판 ${summary.boards}개 · 생성 ${summary.ageHours}시간 전`);
      log(`마감: 접수 중 ${summary.deadlines.open} · 마감 ${summary.deadlines.closed} · 마감일 없음 ${summary.deadlines.none} · 읽을 수 없음 ${summary.deadlines.unreadable}`);
      log(`항목 상태: ${JSON.stringify(summary.statuses)}`);
      log(`추출 모델: ${JSON.stringify(summary.models)}`);
      if (summary.deadlines.open === 0) warnings.push('접수 중인 카드가 0장 (홈 목록이 비어 보임)');
    }
    for (const text of warnings) log(`경고: ${text}`);
    for (const text of failures) log(`실패: ${text}`);
    if (failures.length > 0) return 2;
    if (warnings.length > 0) return 1;
    log('문제 없음');
    return 0;
  } catch (error) {
    log(error instanceof CheckError ? `실패: ${error.message}` : `실패: 점검 중 오류 (${error?.name ?? '알 수 없음'})`);
    return 2;
  }
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  process.exitCode = await main(process.argv.slice(2));
}
