// 제작 기록(#26) 테스트: log.json 형식, "측정 중" 표시, 지어낸 수치 막기, 검수 체험 채점, 저장소 링크.
// log.json의 "내용이 근거와 맞는지"는 테스트로 다 잡을 수 없어 PR 리뷰에서 근거 문서와 대조한다.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import {
  validateLogData, isValidMeasure, measureText, checkAnswer, repoFileUrl, REPO_FILE_BASE, stageKind, optionMark,
} from '../site/js/log.js';

const RAW = JSON.parse(readFileSync(new URL('../site/data/log.json', import.meta.url), 'utf8'));
const SAMPLE = JSON.parse(readFileSync(new URL('../site/data/cards.sample.json', import.meta.url), 'utf8'));
const clone = () => structuredClone(RAW);
const allMeasures = (data) => [
  ...data.accuracy.flatMap((row) => [row.filled, row.field_accuracy, row.risky_misreads]),
  ...data.prompts.map((prompt) => prompt.accuracy),
].filter((measure) => measure !== null);

test('저장소의 log.json은 형식이 맞고 고친 항목이 없다', () => {
  const { data, rejected } = validateLogData(RAW);
  assert.deepEqual(rejected, []);
  for (const section of ['stages', 'accuracy', 'prompts', 'ai_errors', 'quiz', 'tools']) {
    assert.equal(data[section].length, RAW[section].length, section);
  }
});

test('log.json의 수치는 모두 전체 수·측정일·출처가 있고, 출처가 저장소 파일이면 실제로 있다 (지어낸 수치 금지)', () => {
  const measures = allMeasures(RAW);
  assert.ok(measures.length > 0);
  for (const measure of measures) {
    assert.ok(isValidMeasure(measure, RAW.updated_at), JSON.stringify(measure));
    if (/^[\w-]+\/[\w./가-힣-]+\.\w+$/.test(measure.source)) {
      assert.ok(existsSync(new URL(`../${measure.source}`, import.meta.url)), measure.source);
    }
  }
});

test('log.json이 가리키는 파일은 저장소에 실제로 있다', () => {
  const paths = [...RAW.prompts, ...RAW.ai_errors, ...RAW.quiz].map((item) => item.path).filter(Boolean);
  assert.ok(paths.length > 0);
  for (const path of paths) {
    assert.ok(existsSync(new URL(`../${path}`, import.meta.url)), path);
    assert.ok(repoFileUrl(path)?.startsWith(REPO_FILE_BASE), path);
  }
});

test('검수 체험의 원문 인용은 예시 데이터의 공지이고, 예시 데이터에 있는 근거 문장과 같다', () => {
  const sampleQuotes = new Set(SAMPLE.cards.flatMap((card) => Object.values(card.fields)
    .flatMap((field) => field.evidence.map((item) => `${item.source}|${item.quote}`))));
  const sampleUrls = new Set(SAMPLE.cards.flatMap((card) => card.sources.map((source) => source.url)));
  // 예선 v0.1 결과 문서(작업 폴더)에만 있는 인용: 예시 데이터에 근거로 옮기지 않은 문장
  const fromV01Only = new Set(['팀 단위 참여의 경우, 전체 상금을 나누어 시상']);
  for (const quiz of RAW.quiz) {
    for (const quote of quiz.quotes) {
      assert.ok(sampleUrls.has(quote.url), `${quiz.id}: 인용 출처는 예시 데이터의 공지 (${quote.url})`);
      if (!fromV01Only.has(quote.text)) assert.ok(sampleQuotes.has(`${quote.url}|${quote.text}`), `${quiz.id}: ${quote.text}`);
    }
  }
});

test('검수 체험: 정답 위치가 한쪽에 몰리지 않고, "확정"이 정답인 문항도 있다 (무조건 확인 필요를 고르면 되는 체험이 되지 않게)', () => {
  const answers = RAW.quiz.map((quiz) => quiz.answer);
  assert.ok(new Set(answers).size > 1, `정답 위치: ${answers}`);
  const isCautious = (quiz) => /확인 필요|비우/.test(quiz.options[quiz.answer]);
  assert.ok(RAW.quiz.some(isCautious));
  assert.ok(RAW.quiz.some((quiz) => !isCautious(quiz)), '근거가 분명해 확정하는 문항');
  assert.ok(new Set(RAW.quiz.map((quiz) => quiz.id)).size === RAW.quiz.length);
});

test('측정값: 개수·전체 수·측정일·출처가 모두 있어야 쓴다', () => {
  const ok = { value: 23, total: 350, measured_at: '2026-11-03', source: '수집기 리뷰' };
  assert.equal(isValidMeasure(null), true);
  assert.equal(isValidMeasure(ok), true);
  assert.equal(isValidMeasure({ ...ok, source: '' }), false);
  assert.equal(isValidMeasure({ ...ok, measured_at: undefined }), false);
  assert.equal(isValidMeasure({ ...ok, measured_at: '2026-02-30' }), false);
  assert.equal(isValidMeasure({ ...ok, total: undefined }), false, '비율·백분율만 쓴 값(94.5)을 막는다');
  assert.equal(isValidMeasure({ value: 94.5, measured_at: '2026-11-03', source: 'x' }), false);
  assert.equal(isValidMeasure({ ...ok, value: 23.5 }), false);
  assert.equal(isValidMeasure({ ...ok, value: 400 }), false);
  assert.equal(isValidMeasure({ ...ok, value: -1 }), false);
  assert.equal(isValidMeasure({ ...ok, value: '23' }), false);
  assert.equal(isValidMeasure({ ...ok, total: 0 }), false);
  // 기록 갱신일보다 늦은 측정일은 받지 않는다
  assert.equal(isValidMeasure(ok, '2026-11-03'), true);
  assert.equal(isValidMeasure({ ...ok, measured_at: '2026-11-05' }, '2026-11-04'), false);
});

test('측정값 표시: null이면 "측정 중", 값이면 언제·어디서 쟀는지 함께', () => {
  assert.deepEqual(measureText(null), { text: '측정 중', isPending: true, detail: null });
  assert.deepEqual(measureText({ value: 23, total: 350, unit: '항목', note: '공지 35건', measured_at: '2026-11-03', source: '수집기 리뷰' }),
    { text: '23 / 350 항목', isPending: false, detail: '공지 35건 · 2026-11-03 측정 · 수집기 리뷰' });
  assert.equal(measureText({ value: 0, total: 50000, measured_at: '2026-11-04', source: 't' }).text, '0 / 50000');
});

test('형식 검사: 틀린 수치는 그 칸만 "측정 중", 틀린 경로는 링크만 빼고, 틀린 항목은 그 항목만 뺀다 (번호는 원래 목록 기준)', () => {
  const raw = clone();
  raw.accuracy.unshift({ method: '' }); // 빠지는 줄을 앞에 넣어도 뒤 줄의 번호가 원래 번호여야 한다
  raw.accuracy[2].field_accuracy = { value: 94.5, measured_at: '2026-11-04', source: 'x' };
  raw.prompts[0].accuracy = { value: 9, total: 10 };
  raw.prompts[1].path = '../밖/파일.md';
  raw.ai_errors[0].path = 'https://evil.example/x';
  raw.quiz.push({ ...structuredClone(raw.quiz[0]) });
  raw.quiz[1].answer = 9;
  raw.tools.push({ name: '이미지 AI', used_for: '그림', status: '모름' });
  raw.stages.push({ name: '추가', by: '로봇', status: '동작', detail: 'x' });
  const { data, rejected } = validateLogData(raw);

  assert.equal(data.accuracy.length, RAW.accuracy.length);
  assert.equal(data.accuracy[1].field_accuracy, null);
  assert.equal(data.prompts[0].accuracy, null);
  assert.equal(data.prompts[1].path, null);
  assert.equal(data.prompts.length, RAW.prompts.length, '경로가 틀려도 항목은 남긴다');
  assert.equal(data.ai_errors[0].path, null);
  assert.equal(data.quiz.length, RAW.quiz.length - 1, '정답 번호가 틀린 문항과 id가 겹치는 문항');
  assert.equal(data.tools.length, RAW.tools.length);
  assert.equal(data.stages.length, RAW.stages.length);
  const keys = rejected.map((item) => `${item.section}:${item.index}`).sort();
  assert.deepEqual(keys, ['accuracy:0', 'accuracy:2', 'ai_errors:0', 'prompts:0', 'prompts:1', `quiz:${RAW.quiz.length}`, 'quiz:1',
    `stages:${RAW.stages.length}`, `tools:${RAW.tools.length}`].sort());
});

test('형식 검사: 최상위 형식이 틀리면 오류', () => {
  assert.throws(() => validateLogData(null), /객체/);
  assert.throws(() => validateLogData({ ...clone(), updated_at: '11월 4일' }), /updated_at/);
  assert.throws(() => validateLogData({ ...clone(), quiz: {} }), /quiz/);
});

test('형식 검사: 체험 문항의 인용·보기 형식', () => {
  const bad = (change) => {
    const raw = clone();
    change(raw.quiz[0]);
    return validateLogData(raw).data.quiz.length === RAW.quiz.length - 1;
  };
  assert.ok(bad((quiz) => { quiz.quotes = '문장'; }));
  assert.ok(bad((quiz) => { quiz.quotes = [{ text: '문장', location: '포스터' }]; }));
  assert.ok(bad((quiz) => { quiz.quotes = [{ text: '문장', location: '본문', url: 3 }]; }));
  assert.ok(bad((quiz) => { quiz.options = ['하나']; }));
  assert.ok(bad((quiz) => { quiz.options = ['1', '2', '3', '4', '5', '6']; }));
  assert.ok(bad((quiz) => { quiz.id = 'Bad Id'; }));
});

test('검수 체험 채점', () => {
  const quiz = RAW.quiz[0];
  assert.deepEqual(checkAnswer(quiz, quiz.answer), { isCorrect: true, answerText: quiz.options[quiz.answer] });
  assert.equal(checkAnswer(quiz, (quiz.answer + 1) % quiz.options.length).isCorrect, false);
  assert.throws(() => checkAnswer(quiz, quiz.options.length), RangeError);
  assert.throws(() => checkAnswer(quiz, -1), RangeError);
  assert.deepEqual([0, 1, 4, 5].map(optionMark), ['①', '②', '⑤', '6.']);
});

test('저장소 링크: 저장소 안 경로만, 한글은 인코딩', () => {
  assert.equal(repoFileUrl('prompts/CHANGELOG.md'), `${REPO_FILE_BASE}prompts/CHANGELOG.md`);
  assert.equal(repoFileUrl('docs/ui/화면설계.md'), `${REPO_FILE_BASE}docs/ui/${encodeURIComponent('화면설계.md')}`);
  const loneSurrogate = `docs/${String.fromCharCode(0xd800)}.md`;
  for (const bad of ['../secret', '/etc/passwd', 'https://evil.example/x', 'a//b', 'a/./b', 'a\\b', 'a?x=1', 'a#b', '%2e%2e/x', '', null, 3, loneSurrogate]) {
    assert.equal(repoFileUrl(bad), null, String(bad));
  }
});

test('단계 담당 배지 종류', () => {
  assert.deepEqual(['코드', '규칙', 'AI', '사람', '모름'].map(stageKind), ['code', 'rule', 'ai', 'human', 'code']);
});
