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
const PROMOTABLE = new Set(['ai', 'needs_check']);
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
      // canonicalJson 글을 JSON 문자열로 한 번 더 감싸면 TOML 기본 문자열 문법과 같다 (\" \\ \n \uXXXX)
      `value = ${JSON.stringify(canonicalJson(field.value))}`, `evidence_sha256 = "${evidenceFingerprint(field.evidence)}"`,
      `since = ${today}`].join('\n');
    return [{ name, label: FIELD_LABELS[name], status: field.status, value: field.value, evidence: field.evidence,
      note: field.note ?? '', entry }];
  });
}

async function loadCards(options) {
  const text = options.file
    ? await readFile(options.file, 'utf8')
    : await (await fetch(new URL('/data/cards.json', DEFAULT_BASE), { cache: 'no-store', signal: AbortSignal.timeout(15000) })).text();
  return JSON.parse(text);
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
    log(`실패: cards.json을 읽지 못함 (${error?.name ?? '오류'})`);
    return 2;
  }
  const card = Array.isArray(data?.cards) ? data.cards.find((item) => item?.id === cardId) : undefined;
  if (!card) {
    log(`실패: 공개된 카드에 ${cardId}가 없음`);
    return 2;
  }
  log(`# ${card.title}`);
  for (const source of card.sources ?? []) log(`원문: ${source.url} (${source.posted_at})`);
  const items = reviewItems(card, kstToday(now));
  if (items.length === 0) {
    log('검수할 항목이 없음 (값·근거가 없거나 이미 검수 완료)');
    return 0;
  }
  log('\n원문을 열어 항목마다 값과 근거를 대조하고, 맞은 항목의 칸만 pipeline/config/human_reviews.toml에 붙여 넣으세요.');
  log('틀린 항목은 붙이지 말고 이슈로 알려 주세요. 신청기간·자격(캠퍼스·학년·학적·팀 인원)이 판정에 쓰입니다.\n');
  for (const item of items) {
    log(`## ${item.label} (${item.name}) — 지금 상태: ${item.status === 'ai' ? 'AI 정리' : '원문 확인 필요'}`);
    log(`값: ${canonicalJson(item.value)}`);
    if (item.note) log(`메모: ${item.note}`);
    for (const evidence of item.evidence) log(`근거 (${evidence.location}${evidence.file ? ` ${evidence.file}` : ''}): ${evidence.quote}`);
    log(`\n${item.entry}\n`);
  }
  return 0;
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  process.exitCode = await main(process.argv.slice(2));
}
