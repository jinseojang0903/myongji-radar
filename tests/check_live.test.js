// 배포 직후 점검 스크립트(scripts/check_live_cards.mjs, #98). 네트워크 없이 파일 내용만 시험한다.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { checkCardsData, checkPages } from '../scripts/check_live_cards.mjs';

const read = (path) => JSON.parse(readFileSync(new URL(path, import.meta.url), 'utf8'));
const EXPORTED = read('../pipeline/tests/fixtures/exported_cards.json');
const SAMPLE = read('../site/data/cards.sample.json');
const justAfter = (data) => new Date(Date.parse(data.generated_at) + 3600 * 1000);

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
  assert.equal(checkCardsData({ cards: [] }, new Date()).failures.length, 1);
  const empty = { ...structuredClone(EXPORTED), cards: [] };
  assert.ok(checkCardsData(empty, justAfter(EXPORTED)).failures.includes('카드가 0장'));
});

test('오래된 generated_at은 경고다', () => {
  const late = new Date(Date.parse(EXPORTED.generated_at) + 40 * 3600 * 1000);
  const { warnings } = checkCardsData(structuredClone(EXPORTED), late);
  assert.ok(warnings.some((text) => text.includes('generated_at이 40시간 전')));
});

test('휴대전화·메일·긴 숫자·http 링크·긴 근거를 찾고, 경고에 값은 옮기지 않는다', () => {
  const data = structuredClone(EXPORTED);
  const card = data.cards[0];
  const [name, field] = Object.entries(card.fields).find(([, item]) => item.evidence.length > 0);
  const secret = '010-1234-5678 student@example.com 60201234';
  field.evidence[0].quote = secret;
  card.sources[0].url = 'http://www.mju.ac.kr/notice';
  field.evidence.push({ quote: '가'.repeat(201), location: '본문', source: card.sources[0].url });

  const { warnings } = checkCardsData(data, justAfter(data));
  const mine = warnings.filter((text) => text.startsWith(card.id));
  assert.ok(mine.some((text) => text.includes('휴대전화')));
  assert.ok(mine.some((text) => text.includes('학교 밖 메일 주소')));
  assert.ok(mine.some((text) => text.includes('8자리 이상')));
  assert.ok(mine.some((text) => text.includes('https가 아닌 링크')));
  assert.ok(mine.some((text) => text.includes(`${name}: 근거 한 문장이 201자`)));
  for (const text of warnings) {
    assert.ok(!text.includes('010-1234') && !text.includes('student@'), '경고에 값이 들어가면 안 됨');
  }
});

test('학교 도메인 메일은 근거 문장에 있어도 통과하고, 문의처 메일은 학교 도메인만 허용한다', () => {
  const data = structuredClone(EXPORTED);
  const card = data.cards[0];
  Object.values(card.fields).find((item) => item.evidence.length > 0).evidence[0].quote = '문의: office@mju.ac.kr';
  card.fields.contact = { value: { dept: '', email: 'office@mju.ac.kr', phone: '' }, status: 'human_verified', evidence: [] };
  assert.deepEqual(checkCardsData(data, justAfter(data)).warnings, []);
  card.fields.contact.value.email = 'someone@gmail.com';
  assert.ok(checkCardsData(data, justAfter(data)).warnings.some((text) => text.includes('학교 도메인이 아닌 문의 메일')));
});

test('카드 id 중복과 화면이 뺄 카드를 경고한다', () => {
  const data = structuredClone(EXPORTED);
  data.cards.push(structuredClone(data.cards[0]));
  data.cards.push({ id: 'Bad ID' });
  const { warnings } = checkCardsData(data, justAfter(data));
  assert.ok(warnings.some((text) => text.includes('카드 id 중복')));
  assert.ok(warnings.some((text) => text.includes('화면이 뺄 항목')));
});

test('화면 파일 확인은 열리지 않는 주소만 돌려준다', async () => {
  const fakeFetch = async (url) => ({ ok: !url.pathname.endsWith('log.html'), status: url.pathname.endsWith('log.html') ? 404 : 200 });
  assert.deepEqual(await checkPages('https://example.test', fakeFetch), ['/log.html: HTTP 404']);
});
