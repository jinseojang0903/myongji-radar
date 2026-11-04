// 자연어 해석기(site/js/parser.js) 정확도 측정 (#23).
// 시험 문장은 eval/parser/sentences.csv. 해석기 사전을 보지 않고 쓴 문장이어야 정확도가 정직하다 (README).
// 실행: npm run eval:parser → 결과를 화면에 쓰고 eval/parser/result.json(최신)과 eval/parser/runs/(측정마다 한 파일)에 저장.
// 제작 기록 탭(#26)과 발표에는 **처음 측정한 파일**(runs/의 가장 이른 것)을 쓴다. 측정 뒤 사전을 고치면 그 문장은 이미 본 문장이다.

import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import { parseConditions, PARSER_VERSION, STUDENT_DELIVERABLE_TAGS, MAX_INPUT_LENGTH } from '../../site/js/parser.js';

export const CONDITION_FIELDS = ['campus', 'grade', 'is_transfer', 'enrollment', 'participation', 'deliverables', 'period'];
// 판정(rules.js)에 쓰는 조건. 여기를 틀린 값으로 읽으면 판정이 틀릴 수 있다
export const JUDGED_FIELDS = ['campus', 'grade', 'is_transfer', 'enrollment', 'participation'];
export const TARGET_SENTENCE_COUNT = 50;
const COLUMNS = ['id', 'sentence', 'campus', 'grade', 'is_transfer', 'enrollment', 'participation', 'deliverables', 'period', 'unknown', 'writer', 'written_at', 'note'];
const CHOICES = {
  campus: ['인문', '자연'],
  enrollment: ['재학', '휴학', '수료', '초과학기', '졸업유예'],
  participation: ['개인', '팀'],
  period: ['마감임박', '이번주', '이번달'],
};
const EXAMPLE_ID_PREFIX = 'example';

/**
 * CSV 글을 줄 목록으로 읽는다 (따옴표 안의 쉼표·줄바꿈, "" 이스케이프, 맨 앞 BOM 처리).
 * @returns {{rows: {line: number, cells: string[]}[], error: string|null}}
 *   line: 그 줄이 시작하는 파일의 줄 번호 (엑셀 행 번호와 같다). 빈 줄은 뺀다.
 *   error: 닫히지 않은 따옴표처럼 파일 전체를 믿을 수 없을 때
 */
export function parseCsv(text) {
  const source = text.startsWith('﻿') ? text.slice(1) : text;
  const rows = [];
  let cells = [];
  let cell = '';
  let isQuoted = false;
  let line = 1;
  let rowLine = 1;
  let quoteLine = 0;
  const endRow = () => {
    cells.push(cell);
    if (cells.some((value) => value.trim() !== '')) rows.push({ line: rowLine, cells });
    cells = [];
    cell = '';
  };
  for (let i = 0; i < source.length; i += 1) {
    const char = source[i];
    if (char === '\n') line += 1;
    if (isQuoted) {
      if (char === '"' && source[i + 1] === '"') { cell += '"'; i += 1; }
      else if (char === '"') isQuoted = false;
      else if (char === '\r' && source[i + 1] === '\n') { /* 따옴표 안 CRLF는 \n 하나로 */ }
      else cell += char;
    } else if (char === '"') { isQuoted = true; quoteLine = line; }
    else if (char === ',') { cells.push(cell); cell = ''; }
    else if (char === '\n' || char === '\r') {
      if (char === '\r' && source[i + 1] === '\n') { i += 1; line += 1; }
      else if (char === '\r') line += 1;
      endRow();
      rowLine = line;
    } else cell += char;
  }
  if (isQuoted) return { rows: [], error: `${quoteLine}번째 줄에서 연 따옴표(")가 닫히지 않았습니다. 셀 안에 따옴표를 쓰려면 셀 전체를 따옴표로 감싸고 ""로 씁니다` };
  if (cell !== '' || cells.length > 0) endRow();
  return { rows, error: null };
}

/**
 * sentences.csv 글 → 채점할 문장 목록. `example`로 시작하는 id는 견본이라 건너뛴다.
 * @returns {{sentences: {id: string, sentence: string, writer: string, expected: object, expectedUnknown: string[]}[], errors: string[]}}
 */
export function readSentences(text) {
  if (text.includes('�')) {
    return { sentences: [], errors: ['한글이 깨져 있습니다. 엑셀에서 "다른 이름으로 저장 → CSV UTF-8(쉼표로 분리)"로 다시 저장하세요'] };
  }
  const { rows, error } = parseCsv(text);
  if (error) return { sentences: [], errors: [error] };
  const [header = { cells: [] }, ...body] = rows;
  const names = header.cells.map((name) => name.trim());
  const missingColumns = COLUMNS.filter((name) => !names.includes(name));
  if (missingColumns.length > 0) return { sentences: [], errors: [`제목 줄에 없는 열: ${missingColumns.join(', ')}`] };

  const sentences = [];
  const errors = [];
  const seenIds = new Set();
  const seenSentences = new Set();
  for (const { line, cells } of body) {
    const get = (name) => (cells[names.indexOf(name)] ?? '').trim();
    const id = get('id');
    if (id.startsWith(EXAMPLE_ID_PREFIX)) continue;
    const rowErrors = [];
    if (!id) rowErrors.push('id가 비어 있음');
    else if (seenIds.has(id)) rowErrors.push(`id ${id}가 두 번 나옴`);
    seenIds.add(id);
    const sentence = get('sentence');
    if (!sentence) rowErrors.push('sentence가 비어 있음');
    else if (seenSentences.has(sentence)) rowErrors.push('같은 문장이 이미 있음');
    else if (sentence.length > MAX_INPUT_LENGTH) rowErrors.push(`sentence가 ${MAX_INPUT_LENGTH}자보다 김 (${sentence.length}자). 화면 입력창도 ${MAX_INPUT_LENGTH}자까지 받는다`);
    seenSentences.add(sentence);
    if (!get('writer')) rowErrors.push('writer(작성자)가 비어 있음');
    if (!isRealDate(get('written_at'))) rowErrors.push('written_at은 있는 날짜를 YYYY-MM-DD 형식으로 써야 함');

    const expected = {};
    for (const field of ['campus', 'enrollment', 'participation', 'period']) {
      const value = get(field);
      if (value === '') expected[field] = null;
      else if (CHOICES[field].includes(value)) expected[field] = value;
      else rowErrors.push(`${field} 값 "${value}"는 ${CHOICES[field].join('·')} 중 하나여야 함`);
    }
    const grade = get('grade');
    if (grade === '') expected.grade = null;
    else if (/^[1-6]$/.test(grade)) expected.grade = Number(grade);
    else rowErrors.push(`grade 값 "${grade}"는 1~6 숫자여야 함`);
    const transfer = get('is_transfer');
    if (transfer === '') expected.is_transfer = null;
    else if (['예', 'true'].includes(transfer)) expected.is_transfer = true;
    else if (['아니오', '아니요', 'false'].includes(transfer)) expected.is_transfer = false;
    else rowErrors.push(`is_transfer 값 "${transfer}"는 예·아니오 중 하나여야 함`);
    const tags = [...new Set(splitList(get('deliverables')))];
    const badTags = tags.filter((tag) => !STUDENT_DELIVERABLE_TAGS.includes(tag));
    if (badTags.length > 0) rowErrors.push(`deliverables 값 "${badTags.join(', ')}"는 ${STUDENT_DELIVERABLE_TAGS.join(' / ')} 중에서 골라야 함`);
    expected.deliverables = tags;

    if (rowErrors.length > 0) errors.push(...rowErrors.map((message) => `${line}번째 줄${id ? ` (${id})` : ''}: ${message}`));
    else sentences.push({ id, sentence, writer: get('writer'), expected, expectedUnknown: splitList(get('unknown')) });
  }
  return { sentences, errors };
}

function splitList(value) {
  return value.split(',').map((item) => item.trim()).filter(Boolean);
}

const isEmpty = (field, value) => (field === 'deliverables' ? value.length === 0 : value === null);

/** 정답과 해석 결과 비교: correct(맞음) / wrong(다른 값) / missed(비움) / extra(말하지 않은 조건에 값) */
export function compareField(field, expected, actual) {
  const isSame = field === 'deliverables'
    ? [...expected].sort().join('|') === [...actual].sort().join('|')
    : expected === actual;
  if (isSame) return 'correct';
  if (isEmpty(field, actual)) return 'missed';
  if (isEmpty(field, expected)) return 'extra';
  return 'wrong';
}

function isRealDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

// "이해하지 못한 표현"을 찾았는지: 정답 표현의 낱말이 모두 해석기 문구의 낱말에 들어 있으면 찾은 것이다.
// 낱말은 끝 조사를 떼고 비교한다 ("대학원생" ⊂ "대학원생도", "3학년" ⊂ "내년에 3학년", "다음 주" = "다음주").
// 해석기 문구가 앞뒤 낱말(부정어·시간 표현)을 더 담아도 감점하지 않고, 정답의 낱말이 빠지면 못 찾은 것이다
// ("영상 말고" ⊄ "영상"). 한계: 낱말 일부만 겹치는 표현("고학년"과 "학년")은 다르게 본다.
const TERM_PARTICLES = /(으로|에서|이랑|인데|이고|이라|은|는|이|가|을|를|도|만|에|로|랑|요)$/;
const termWords = (term) => term.toLowerCase().replace(/…/g, ' ').split(/\s+/).filter(Boolean)
  .map((word) => (word.length > 2 ? word.replace(TERM_PARTICLES, '') : word));
export function isSameTerm(expectedTerm, actualTerm) {
  const squash = (term) => term.replace(/\s+/g, '').toLowerCase();
  if (squash(expectedTerm) !== '' && squash(expectedTerm).replace(TERM_PARTICLES, '') === squash(actualTerm).replace(TERM_PARTICLES, '')) return true;
  const expectedWords = termWords(expectedTerm);
  const actualWords = termWords(actualTerm);
  return expectedWords.length > 0 && expectedWords.every((word) => actualWords.includes(word));
}

/**
 * 문장마다 해석기를 돌려 정답과 비교한다.
 * - fields: 조건 7개 각각의 correct·wrong·missed·extra
 * - misreads: 판정에 쓰는 조건(JUDGED_FIELDS)을 틀린 값으로 읽은 수 (wrong + extra). 가장 나쁜 오류
 * - full_match: 조건 7개가 모두 맞은 문장
 * - unknown_terms: 정답에 적은 표현을 돌려줬는지(found), 정답에 없는데 돌려준 표현 수(extra). 1:1로 짝짓는다
 */
export function scoreSentences(sentences, parse = parseConditions) {
  const fields = Object.fromEntries(CONDITION_FIELDS.map((field) => [field, { correct: 0, wrong: 0, missed: 0, extra: 0, total: 0 }]));
  const fullMatch = { correct: 0, total: 0 };
  const unknown = { expected: 0, found: 0, extra: 0 };
  const mismatches = [];

  for (const { id, sentence, expected, expectedUnknown } of sentences) {
    const actual = parse(sentence);
    let isAllCorrect = true;
    for (const field of CONDITION_FIELDS) {
      const outcome = compareField(field, expected[field], actual[field]);
      fields[field][outcome] += 1;
      fields[field].total += 1;
      if (outcome !== 'correct') {
        isAllCorrect = false;
        mismatches.push({ id, sentence, field, outcome, expected: expected[field], actual: actual[field] });
      }
    }
    fullMatch.total += 1;
    if (isAllCorrect) fullMatch.correct += 1;

    const remaining = [...actual.unknown_terms];
    const missed = [];
    for (const term of expectedUnknown) {
      const at = remaining.findIndex((got) => isSameTerm(term, got));
      if (at === -1) missed.push(term);
      else remaining.splice(at, 1);
    }
    unknown.expected += expectedUnknown.length;
    unknown.found += expectedUnknown.length - missed.length;
    unknown.extra += remaining.length;
    if (missed.length > 0) mismatches.push({ id, sentence, field: 'unknown_terms', outcome: 'missed', expected: missed, actual: actual.unknown_terms });
  }
  const misreads = JUDGED_FIELDS.reduce((sum, field) => sum + fields[field].wrong + fields[field].extra, 0);
  return { parser_version: PARSER_VERSION, sentences: sentences.length, fields, misreads, full_match: fullMatch, unknown_terms: unknown, mismatches };
}

function percent(correct, total) {
  return total === 0 ? '-' : `${((correct / total) * 100).toFixed(1)}% (${correct}/${total})`;
}

const sha256 = (text) => createHash('sha256').update(text).digest('hex');

function main() {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const csvText = readFileSync(path.join(here, 'sentences.csv'), 'utf8');
  const { sentences, errors } = readSentences(csvText);
  if (errors.length > 0) {
    console.error('sentences.csv에 고칠 곳이 있습니다:');
    for (const message of errors) console.error(`  ${message}`);
    process.exitCode = 1;
    return;
  }
  if (sentences.length === 0) {
    console.log('측정 중: 시험 문장이 아직 없습니다 (eval/parser/sentences.csv, 작성 방법은 eval/parser/README.md).');
    return;
  }
  const isComplete = sentences.length >= TARGET_SENTENCE_COUNT;
  if (!isComplete) {
    console.warn(`주의: 시험 문장이 ${sentences.length}개입니다 (목표 ${TARGET_SENTENCE_COUNT}개). 정식 측정(runs/)으로 남기지 않고 result.json에만 씁니다.`);
  }
  const parserSource = readFileSync(path.join(here, '../../site/js/parser.js'), 'utf8');
  const measuredAt = new Date().toISOString();
  const result = {
    ...scoreSentences(sentences),
    measured_at: measuredAt,
    // 사전을 고쳤는데 PARSER_VERSION을 올리지 않아도 측정마다 어떤 해석기였는지 구분되게 한다
    parser_sha256: sha256(parserSource),
    sentences_sha256: sha256(csvText),
    writers: [...new Set(sentences.map((item) => item.writer))],
    is_complete: isComplete,
  };

  console.log(`해석기 v${result.parser_version}, 시험 문장 ${result.sentences}개`);
  for (const field of CONDITION_FIELDS) {
    const { correct, wrong, missed, extra, total } = result.fields[field];
    console.log(`  ${field.padEnd(14)} ${percent(correct, total)}  다른 값 ${wrong} · 비움 ${missed} · 말하지 않은 값 ${extra}`);
  }
  console.log(`  문장 전체 일치    ${percent(result.full_match.correct, result.full_match.total)}`);
  console.log(`  판정 조건을 틀린 값으로 읽음: ${result.misreads}건 (가장 나쁜 오류)`);
  const { expected, found, extra } = result.unknown_terms;
  console.log(`  이해하지 못한 표현: 정답 ${expected}개 중 ${found}개 찾음, 정답에 없는 표현 ${extra}개`);
  for (const miss of result.mismatches) {
    console.log(`  [${miss.id}] ${miss.field} (${miss.outcome}): 정답 ${JSON.stringify(miss.expected)}, 해석 ${JSON.stringify(miss.actual)} ← ${miss.sentence}`);
  }

  const json = `${JSON.stringify(result, null, 2)}\n`;
  writeFileSync(path.join(here, 'result.json'), json);
  if (!isComplete) {
    console.log('저장: eval/parser/result.json (최신, 미완성 측정)');
    return;
  }
  // 문장이 다 찬 측정만 runs/에 쌓는다. 발표에는 그중 가장 이른 파일을 쓴다
  const runsDir = path.join(here, 'runs');
  mkdirSync(runsDir, { recursive: true });
  const runFile = `${measuredAt.replace(/[:.]/g, '-')}_v${result.parser_version}.json`;
  writeFileSync(path.join(runsDir, runFile), json);
  console.log(`저장: eval/parser/runs/${runFile} (이번 측정), eval/parser/result.json (최신)`);
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href.toLowerCase() === import.meta.url.toLowerCase()) main();
