// 공개된 cards.json을 배포 직후 한 번에 점검한다 (#98).
// 화면과 같은 형식 검사(validateCardData)를 쓰고, 내보내기(#17)의 공개 상한·연락처 가림이 실제 파일에도
// 지켜졌는지 한 번 더 본다. 출력에는 카드 id·항목 이름·건수만 남기고 공지 문장·값은 옮기지 않는다.
//
// 사용: node scripts/check_live_cards.mjs                 기본 배포 주소 점검
//       node scripts/check_live_cards.mjs --base <https 주소>
//       node scripts/check_live_cards.mjs --file <cards.json 경로>   (화면 파일 확인은 건너뜀)
// 종료 코드: 0 문제 없음, 1 경고 있음(공개는 됨), 2 실패(못 읽음·예시 데이터 그대로·형식 오류·카드 0장)

import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { validateCardData, deadlineOf, STALE_HOURS } from '../site/js/cards.js';

export const DEFAULT_BASE = 'https://myongji-radar-site.vercel.app';
const PAGES = ['/', '/recommend.html', '/card.html', '/log.html', '/data/log.json'];

// 내보내기 상한과 같은 값 (pipeline/exporter/cards.py, 계약 1절)
const MAX_QUOTE_CHARS = 200;
const MAX_EVIDENCE = 5;
const MAX_CARD_QUOTE_CHARS = 1500;
const HOUR_MS = 3600 * 1000;
const FUTURE_TOLERANCE_HOURS = 1;

const URL_KEYS = new Set(['url', 'list_url', 'source', 'old_source', 'new_source']);
const MOBILE = /(?<!\d)(?:\+?82[-.\s]?(?:\(0\))?[-.\s]?0?|0)1[016789][-.\s)]?\d{3,4}[-.\s]?\d{4}(?!\d)/;
// 내보내기는 학교 도메인 메일은 글에 남기고 그 밖의 메일만 가린다 (cards.py _clean). 같은 기준으로 본다.
const EMAIL = /[\w.%+-]+\s*(?:@|\[at\]|\(at\))\s*([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)/gi;
const LONG_NUMBER = /(?<!\d)\d{8,}(?!\d)/;
const SCHOOL_EMAIL = /^[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)*mju\.ac\.kr$/i;
const isSchoolDomain = (domain) => /^(?:[a-z0-9-]+\.)*mju\.ac\.kr$/i.test(domain);

/**
 * cards.json 내용을 점검한다. 값은 돌려주지 않고 위치와 사유만 돌려준다.
 * @param {unknown} data  JSON.parse한 cards.json
 * @param {Date} now
 * @returns {{failures: string[], warnings: string[], summary: object}}
 */
export function checkCardsData(data, now) {
  const failures = [];
  const warnings = [];
  let validated;
  try {
    validated = validateCardData(data);
  } catch (error) {
    return { failures: [`최상위 형식 오류: ${error.message}`], warnings, summary: null };
  }
  if (data.sample === true) failures.push('예시 데이터(sample: true)가 공개돼 있음');
  if (data.cards.length === 0) failures.push('카드가 0장');
  for (const item of validated.rejected) warnings.push(`화면이 뺄 항목 ${item.id}: ${item.reason}`);

  const ageHours = (now.getTime() - Date.parse(data.generated_at)) / HOUR_MS;
  if (ageHours > STALE_HOURS) warnings.push(`generated_at이 ${Math.floor(ageHours)}시간 전 (화면이 오래된 정보 경고를 띄움)`);
  if (ageHours < -FUTURE_TOLERANCE_HOURS) warnings.push('generated_at이 미래 시각');

  const seen = new Set();
  for (const card of data.cards) {
    const id = typeof card?.id === 'string' ? card.id.slice(0, 64) : '(id 없음)';
    if (seen.has(id)) warnings.push(`${id}: 카드 id 중복`);
    seen.add(id);
    warnings.push(...checkEvidence(card, id));
    warnings.push(...checkStrings(card, id));
  }
  warnings.push(...checkStrings({ boards: data.boards }, 'boards'));

  return { failures, warnings, summary: summarize(validated.data, now, ageHours) };
}

function checkEvidence(card, id) {
  const problems = [];
  const quotes = new Set();
  for (const [name, field] of Object.entries(card?.fields ?? {})) {
    const evidence = Array.isArray(field?.evidence) ? field.evidence : [];
    if (evidence.length > MAX_EVIDENCE) problems.push(`${id} ${name}: 근거 ${evidence.length}개 (상한 ${MAX_EVIDENCE})`);
    for (const item of evidence) {
      if (typeof item?.quote !== 'string') continue;
      if (item.quote.length > MAX_QUOTE_CHARS) problems.push(`${id} ${name}: 근거 한 문장이 ${item.quote.length}자 (상한 ${MAX_QUOTE_CHARS})`);
      quotes.add(item.quote);
    }
  }
  const total = [...quotes].reduce((sum, quote) => sum + quote.length, 0);
  if (total > MAX_CARD_QUOTE_CHARS) problems.push(`${id}: 근거 합계 ${total}자 (상한 ${MAX_CARD_QUOTE_CHARS})`);
  return problems;
}

// 모든 문자열을 돌며 개인 연락처·긴 숫자·https 아닌 링크를 찾는다. 문의처 메일은 학교 도메인만 허용한다.
function checkStrings(value, id) {
  const problems = [];
  walk(value, [], (text, path) => {
    const key = path[path.length - 1];
    const where = `${id} ${path.join('.')}`;
    if (URL_KEYS.has(key)) {
      if (!text.startsWith('https://')) problems.push(`${where}: https가 아닌 링크`);
      return;
    }
    if (MOBILE.test(text)) problems.push(`${where}: 휴대전화 번호 꼴`);
    if (LONG_NUMBER.test(text)) problems.push(`${where}: 8자리 이상 숫자 (학번·계좌 가능성)`);
    const isContactEmail = key === 'email' && path[path.length - 2] === 'value' && path.includes('contact');
    if (isContactEmail) {
      if (text !== '' && !SCHOOL_EMAIL.test(text)) problems.push(`${where}: 학교 도메인이 아닌 문의 메일`);
    } else if ([...text.matchAll(EMAIL)].some((match) => !isSchoolDomain(match[1]))) {
      problems.push(`${where}: 학교 밖 메일 주소 꼴`);
    }
  });
  return problems;
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
    const model = typeof card.meta?.extract_model === 'string' ? card.meta.extract_model : '(없음)';
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
      const response = await fetchImpl(new URL(page, base));
      if (!response.ok) problems.push(`${page}: HTTP ${response.status}`);
    } catch (error) {
      problems.push(`${page}: ${error.name}`);
    }
  }
  return problems;
}

function parseArgs(argv) {
  const options = { base: DEFAULT_BASE, file: null };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--base' && argv[i + 1]) options.base = argv[++i];
    else if (argv[i] === '--file' && argv[i + 1]) options.file = argv[++i];
    else throw new Error(`알 수 없는 인자: ${argv[i]}`);
  }
  if (!options.file && !options.base.startsWith('https://')) throw new Error('--base는 https 주소만 받습니다');
  return options;
}

async function loadCards(options) {
  if (options.file) return JSON.parse(await readFile(options.file, 'utf8'));
  const response = await fetch(new URL('/data/cards.json', options.base), { cache: 'no-store' });
  if (!response.ok) throw new Error(`cards.json HTTP ${response.status} (아직 공개 전이면 404)`);
  return response.json();
}

async function main(argv) {
  let options;
  let data;
  try {
    options = parseArgs(argv);
    data = await loadCards(options);
  } catch (error) {
    console.error(`실패: ${error.message}`);
    return 2;
  }
  const { failures, warnings, summary } = checkCardsData(data, new Date());
  if (!options.file) failures.push(...(await checkPages(options.base)).map((text) => `화면 파일 ${text}`));

  if (summary) {
    console.log(`카드 ${summary.cards}장 · 게시판 ${summary.boards}개 · 생성 ${summary.ageHours}시간 전`);
    console.log(`마감: 접수 중 ${summary.deadlines.open} · 마감 ${summary.deadlines.closed} · 마감일 없음 ${summary.deadlines.none} · 읽을 수 없음 ${summary.deadlines.unreadable}`);
    console.log(`항목 상태: ${JSON.stringify(summary.statuses)}`);
    console.log(`추출 모델: ${JSON.stringify(summary.models)}`);
    if (summary.deadlines.open === 0) warnings.push('접수 중인 카드가 0장 (홈 목록이 비어 보임)');
  }
  for (const text of warnings) console.log(`경고: ${text}`);
  for (const text of failures) console.log(`실패: ${text}`);
  if (failures.length > 0) return 2;
  if (warnings.length > 0) return 1;
  console.log('문제 없음');
  return 0;
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  process.exitCode = await main(process.argv.slice(2));
}
