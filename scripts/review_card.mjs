// 사람 검수 도우미 (#99). 공개된 카드 하나의 항목 값·근거·원문 링크를 보여 주고,
// 원문과 대조해 맞은 항목을 pipeline/config/human_reviews.toml에 붙여 넣을 칸을 만든다.
// 지문 규칙은 pipeline/exporter/reviews.py와 같다 (키 정렬 JSON, 공백 없음, 비ASCII 그대로 → SHA-256).
//
// 사용: npm run review -- <카드 id>                    배포된 사이트의 카드
//       npm run review -- <카드 id> --file <cards.json>  로컬 파일
// 종료 코드: 0 칸을 만듦, 2 카드를 찾지 못함·읽지 못함

import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';

export const DEFAULT_BASE = 'https://myongji-radar-site.vercel.app';
// 문의처는 공개 전에 근거·메일을 비워서 공개 화면만으로는 원문과 대조할 수 없다 (reviews.py와 같은 목록)
export const REVIEWABLE_FIELDS = ['apply', 'stages', 'campus', 'grades', 'enrollment', 'team', 'deliverables', 'benefits',
  'mileage', 'how_to_apply'];
// "AI 정리"만 검수로 올린다 (reviews.py PROMOTABLE과 같다). "원문 확인 필요"는 내보내기가 값을 고쳤거나 공지끼리
// 충돌한 경우 등 이유가 여럿이라, 한쪽만 대조하고 검수 완료가 되지 않게 이슈로 남긴다
const PROMOTABLE = new Set(['ai']);
// 터미널을 조작하는 제어 문자(ESC 등)를 출력 전에 지운다. 배포 데이터는 내보내기가 이미 지우지만 --file은 아무 파일이나 받는다
const CONTROL = /[\p{Cc}\p{Cf}]/gu;
const printable = (text) => String(text).replace(CONTROL, '');
const escapeInvisible = (text) => text.replace(CONTROL, (char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, '0')}`);
const FIELD_LABELS = { apply: '신청기간', stages: '단계별 일정', campus: '캠퍼스', grades: '학년', enrollment: '학적',
  team: '팀 인원', deliverables: '제출물', benefits: '혜택', mileage: '마일리지', how_to_apply: '신청 방법' };
const CARD_ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;

/** Python json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)와 같은 글 */
export function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

export function evidenceFingerprint(evidence) {
  return createHash('sha256').update(canonicalJson(evidence), 'utf8').digest('hex');
}

/**
 * 카드 하나 → 검수할 항목 목록. 값과 근거가 있고 아직 검수 전인 항목만.
 * @returns {{name: string, label: string, status: string, value: unknown, evidence: object[], note: string, entry: string}[]}
 */
export function reviewItems(card, today) {
  return REVIEWABLE_FIELDS.flatMap((name) => {
    const field = card.fields?.[name];
    if (!field || !PROMOTABLE.has(field.status) || field.value == null || !Array.isArray(field.evidence) || field.evidence.length === 0) {
      return [];
    }
    const entry = ['[[review]]', `card_id = ${JSON.stringify(card.id)}`, `field = ${JSON.stringify(name)}`,
      // canonicalJson 글을 JSON 문자열로 한 번 더 감싸면 TOML 기본 문자열 문법과 같다 (\" \\ \n \uXXXX).
      // 보이지 않는 글자(U+202E 등)는 \uXXXX로 적어 터미널에 그대로 나가지 않게 한다 (TOML이 같은 글자로 되돌린다)
      `value = ${escapeInvisible(JSON.stringify(canonicalJson(field.value)))}`, `evidence_sha256 = "${evidenceFingerprint(field.evidence)}"`,
      `since = ${today}`].join('\n');
    return [{ name, label: FIELD_LABELS[name], status: field.status, value: field.value, evidence: field.evidence,
      note: field.note ?? '', entry }];
  });
}

async function loadCards(options) {
  if (options.file) return JSON.parse(await readFile(options.file, 'utf8'));
  const response = await fetch(new URL('/data/cards.json', DEFAULT_BASE), { cache: 'no-store', signal: AbortSignal.timeout(15000) });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

/** 검수 대상이 아닌 "원문 확인 필요" 항목 (이슈로 남길 것) */
export function needsCheckItems(card) {
  return REVIEWABLE_FIELDS.filter((name) => card.fields?.[name]?.status === 'needs_check')
    .map((name) => ({ name, label: FIELD_LABELS[name], note: card.fields[name].note ?? '' }));
}

function kstToday(now = new Date()) {
  return new Date(now.getTime() + 9 * 3600 * 1000).toISOString().slice(0, 10);
}

export async function main(argv, { log = console.log, load = loadCards, now = new Date() } = {}) {
  const [cardId, ...rest] = argv;
  const options = { file: null };
  for (let i = 0; i < rest.length; i += 1) {
    if (rest[i] === '--file' && rest[i + 1]) options.file = rest[++i];
    else { log('사용: npm run review -- <카드 id> [--file <cards.json>]'); return 2; }
  }
  if (typeof cardId !== 'string' || !CARD_ID_PATTERN.test(cardId)) {
    log('사용: npm run review -- <카드 id>  (카드 주소 card.html?id=<여기>)');
    return 2;
  }
  let data;
  try {
    data = await load(options);
  } catch (error) {
    log(`실패: cards.json을 읽지 못함 (${printable(error?.message ?? error?.name ?? '오류').slice(0, 60)})`);
    return 2;
  }
  const card = Array.isArray(data?.cards) ? data.cards.find((item) => item?.id === cardId) : undefined;
  if (!card) {
    log(`실패: 공개된 카드에 ${cardId}가 없음`);
    return 2;
  }
  log(`# ${printable(card.title)}`);
  for (const source of card.sources ?? []) log(`원문: ${printable(source.url)} (${printable(source.posted_at)})`);
  const items = reviewItems(card, kstToday(now));
  const skipped = needsCheckItems(card);
  if (items.length > 0) {
    log('\n원문을 열어 항목마다 값과 근거를 대조하고, 맞은 항목의 칸만 pipeline/config/human_reviews.toml에 붙여 넣으세요.');
    log('틀린 항목은 붙이지 말고 이슈로 알려 주세요. 신청기간·자격(캠퍼스·학년·학적·팀 인원)이 판정에 쓰입니다.\n');
  }
  for (const item of items) {
    log(`## ${item.label} (${item.name}) — 지금 상태: AI 정리`);
    log(`값: ${printable(canonicalJson(item.value))}`);
    if (item.note) log(`메모: ${printable(item.note)}`);
    for (const evidence of item.evidence) {
      log(`근거 (${printable(evidence.location)}${evidence.file ? ` ${printable(evidence.file)}` : ''}): ${printable(evidence.quote)}`);
    }
    log(`\n${item.entry}\n`);
  }
  if (skipped.length > 0) {
    log('## 검수 대상이 아닌 항목 (원문 확인 필요): 원문과 다르면 카드 id·항목·원문 내용을 이슈로 남겨 주세요');
    for (const item of skipped) log(`- ${item.label} (${item.name})${item.note ? `: ${printable(item.note)}` : ''}`);
  }
  if (items.length === 0 && skipped.length === 0) log('검수할 항목이 없음 (값·근거가 없거나 이미 검수 완료)');
  return 0;
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  process.exitCode = await main(process.argv.slice(2));
}
