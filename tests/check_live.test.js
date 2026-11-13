// 배포 직후 점검 스크립트(scripts/check_live_cards.mjs, #98). 네트워크 없이 시험한다.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { checkCardsData, checkPages, main } from '../scripts/check_live_cards.mjs';

const read = (path) => JSON.parse(readFileSync(new URL(path, import.meta.url), 'utf8'));
const EXPORTED = read('../pipeline/tests/fixtures/exported_cards.json');
const SAMPLE = read('../site/data/cards.sample.json');
const justAfter = (data) => new Date(Date.parse(data.generated_at) + 3600 * 1000);
const firstEvidence = (card) => Object.entries(card.fields).find(([, item]) => item.evidence.length > 0);

async function runMain(argv, content, now = justAfter(EXPORTED)) {
  const dir = mkdtempSync(join(tmpdir(), 'check-live-'));
  try {
    const file = join(dir, 'cards.json');
    writeFileSync(file, content, 'utf8');
    const lines = [];
    const code = await main([...argv, '--file', file], { now, log: (line) => lines.push(line) });
    return { code, output: lines.join('\n') };
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test('내보내기 결과 파일은 실패·경고 없이 통과하고 요약에 건수만 담긴다', () => {
  const { failures, warnings, summary } = checkCardsData(structuredClone(EXPORTED), justAfter(EXPORTED));
  assert.deepEqual(failures, []);
  assert.deepEqual(warnings, []);
  assert.equal(summary.cards, EXPORTED.cards.length);
  assert.equal(summary.boards, EXPORTED.boards.length);
});

test('예시 데이터가 그대로 공개돼 있으면 실패다', () => {
  const { failures } = checkCardsData(structuredClone(SAMPLE), justAfter(SAMPLE));
  assert.ok(failures.some((text) => text.includes('예시 데이터')));
});

test('최상위 형식이 틀리거나 카드가 0장이면 실패다', () => {
  assert.deepEqual(checkCardsData({ cards: [] }, new Date()).failures, ['최상위 형식 오류: boards 배열이 없습니다']);
  const empty = { ...structuredClone(EXPORTED), cards: [] };
  assert.ok(checkCardsData(empty, justAfter(EXPORTED)).failures.includes('카드가 0장'));
});

test('오래된 generated_at은 경고다', () => {
  const late = new Date(Date.parse(EXPORTED.generated_at) + 40 * 3600 * 1000);
  const { warnings } = checkCardsData(structuredClone(EXPORTED), late);
  assert.ok(warnings.some((text) => text.includes('generated_at이 40시간 전')));
});

test('휴대전화·학교 밖 메일·긴 숫자·허용 밖 링크·긴 근거를 찾고, 경고에 값은 옮기지 않는다', () => {
  const data = structuredClone(EXPORTED);
  const card = data.cards[0];
  const [name, field] = firstEvidence(card);
  field.evidence[0].quote = '010-1234-5678 student@example.com 60201234';
  card.sources[0].url = 'https://evil.example.com/notice';
  field.evidence.push({ quote: '가'.repeat(201), location: '본문', source: card.sources[0].url });

  const { warnings } = checkCardsData(data, justAfter(data));
  const mine = warnings.filter((text) => text.startsWith(card.id));
  assert.ok(mine.some((text) => text.includes('휴대전화')));
  assert.ok(mine.some((text) => text.includes('학교 밖 메일 주소')));
  assert.ok(mine.some((text) => text.includes('8자리 이상')));
  assert.ok(mine.some((text) => text.includes('허용 도메인이 아닌 링크')));
  assert.ok(mine.some((text) => text.includes(`${name}: 근거 한 문장이 201자`)));
  for (const text of warnings) {
    assert.ok(!/010-1234|student@|evil\.example/.test(text), '경고에 값이 들어가면 안 됨');
  }
});

test('내보내기처럼 정규화한 뒤 찾는다: 전각 번호·띄운 구분자·한글 아이디 메일', () => {
  for (const quote of ['문의 ０１０－１２３４－５６７８', '문의 010 - 1234 - 5678', '담당 홍길동@gmail.com']) {
    const data = structuredClone(EXPORTED);
    firstEvidence(data.cards[0])[1].evidence[0].quote = quote;
    assert.equal(checkCardsData(data, justAfter(data)).warnings.length, 1, quote);
  }
});

test('근거 글자 수는 코드포인트로 센다 (이모지 200자는 상한 안)', () => {
  const data = structuredClone(EXPORTED);
  firstEvidence(data.cards[0])[1].evidence[0].quote = '🎉'.repeat(200);
  assert.deepEqual(checkCardsData(data, justAfter(data)).warnings, []);
});

test('학교 메일은 근거 문장에 있어도 통과하고, 문의처 메일은 사람이 검수한 학교 메일만 허용한다', () => {
  const data = structuredClone(EXPORTED);
  const card = data.cards[0];
  firstEvidence(card)[1].evidence[0].quote = '문의: office@mju.ac.kr';
  card.fields.contact = { value: { dept: '', email: 'office@mju.ac.kr', phone: '' }, status: 'human_verified', evidence: [] };
  assert.deepEqual(checkCardsData(data, justAfter(data)).warnings, []);

  card.fields.contact.status = 'ai';
  assert.ok(checkCardsData(data, justAfter(data)).warnings.some((text) => text.includes('사람 검수 전인데 문의 메일')));
  card.fields.contact.status = 'human_verified';
  card.fields.contact.value.email = 'someone@gmail.com';
  assert.ok(checkCardsData(data, justAfter(data)).warnings.some((text) => text.includes('학교 도메인이 아닌 문의 메일')));
});

test('형식이 틀린 id와 모르는 키는 출력에 옮기지 않고 위치 번호로 쓴다', () => {
  const data = structuredClone(EXPORTED);
  const bad = structuredClone(data.cards[0]);
  bad.id = '비밀 공지 문장 010-9999-8888';
  bad.fields['원문을 담은 키'] = { status: '???', evidence: [] };
  data.cards.push(bad, structuredClone(data.cards[0]));
  data.boards.push({ id: '게시판 원문 이름', name: 'x', list_url: 'http://www.mju.ac.kr/' });

  const { warnings } = checkCardsData(data, justAfter(data));
  const joined = warnings.join('\n');
  assert.ok(!joined.includes('비밀') && !joined.includes('원문을 담은') && !joined.includes('게시판 원문'), joined);
  assert.ok(warnings.some((text) => text.includes(`카드 #${EXPORTED.cards.length + 1}(id 형식 오류)`)));
  assert.ok(warnings.some((text) => text.includes('board:#') && text.includes('id 형식 오류')));
  assert.ok(warnings.some((text) => text.includes('카드 id 중복')));
});

test('깨진 JSON은 파일 내용 없이 고정 문구로 실패(2)한다', async () => {
  const { code, output } = await runMain([], '{"cards": [ 비밀 본문 문장');
  assert.equal(code, 2);
  assert.equal(output, '실패: cards.json이 JSON이 아님');
});

test('점검 중 예상 밖 예외와 없는 파일은 메시지 없이 종류만 남기고 실패(2)한다', async () => {
  const data = structuredClone(EXPORTED);
  Object.defineProperty(data.cards[0], 'fields', { get() { throw new TypeError('비밀 값'); }, enumerable: true });
  const lines = [];
  const code = await main(['--file', 'unused'], { now: justAfter(data), loadImpl: async () => data, log: (line) => lines.push(line) });
  assert.equal(code, 2);
  assert.deepEqual(lines, ['실패: 점검 중 오류 (TypeError)']);

  const missing = [];
  assert.equal(await main(['--file', join(tmpdir(), '없는-비밀-경로.json')], { log: (line) => missing.push(line) }), 2);
  assert.deepEqual(missing, ['실패: 파일을 읽을 수 없음']);
});

test('화면 검사의 최상위 오류 문구 네 가지를 모두 알아본다 (cards.js 문구가 바뀌면 여기서 깨진다)', () => {
  const base = structuredClone(EXPORTED);
  for (const bad of [null, { boards: [] }, { cards: [] }, { ...base, generated_at: '날짜 아님' }]) {
    const { failures } = checkCardsData(bad, new Date());
    assert.equal(failures.length, 1);
    assert.match(failures[0], /^최상위 형식 오류: /);
  }
});

test('항목 키에 "의 "가 들어 있어도 사유에서 키를 옮기지 않는다', () => {
  const data = structuredClone(EXPORTED);
  data.cards[0].fields['홍길동의 계좌 1234'] = { status: '???', evidence: [] };
  const { warnings } = checkCardsData(data, justAfter(data));
  assert.ok(warnings.some((text) => text.endsWith('(모르는 키)의 status가 계약 값이 아님')), warnings.join('\n'));
  assert.ok(!warnings.join('\n').includes('계좌'));
});

test('내보내기 결과는 main으로 돌려도 0이고 요약만 출력한다', async () => {
  const { code, output } = await runMain([], JSON.stringify(EXPORTED));
  assert.equal(code, 0);
  assert.match(output, /^카드 \d+장/);
  assert.match(output, /문제 없음$/);
});

test('화면 파일 확인은 열리지 않는 주소만 돌려준다', async () => {
  const fakeFetch = async (url) => ({ ok: !url.pathname.endsWith('log.html'), status: url.pathname.endsWith('log.html') ? 404 : 200 });
  assert.deepEqual(await checkPages('https://example.test', fakeFetch), ['/log.html: HTTP 404']);
});
