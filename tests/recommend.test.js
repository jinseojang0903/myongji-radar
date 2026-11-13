// 맞춤 추천(#24) 계산 테스트: 문장 → 조건 반영, 4상태 묶기, 제출물·기간 필터, 사유 요약, 칩 상한, 저장 지우기.
// 시연 시나리오(#24 완료 기준)를 예시 데이터로 고정한다.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { validateCardData, emptyListMessage } from '../site/js/cards.js';
import { parseConditions } from '../site/js/parser.js';
import { applyParsed, periodEndDate, recommendCards, reasonSummary, hasUnverifiedFail, limitTerms, normalizeTitleQuery, clipTitleQuery, MAX_TITLE_QUERY_LENGTH, RESULT_ORDER } from '../site/js/recommend.js';
import { emptyConditions, clearSavedConditions, saveConditions, readSavedConditions, CONDITIONS_STORAGE_KEY } from '../site/js/conditions.js';

const SAMPLE = validateCardData(JSON.parse(readFileSync(new URL('../site/data/cards.sample.json', import.meta.url), 'utf8'))).data;
const AT_SAMPLE = new Date(SAMPLE.generated_at); // 2026-10-15 06:00 KST (목요일)
const ids = (items) => items.map((item) => item.card.id);

test('시연 시나리오: "자연캠 2학년 재학생, 편입생 아니고 혼자" → AI 리터러시 신청 가능, 해커톤 불가', () => {
  const { conditions } = applyParsed(emptyConditions(), parseConditions('자연캠 2학년 재학생이고 편입생은 아니고 혼자 낼 대회'));
  const { groups, total, closedCount } = recommendCards(SAMPLE.cards, conditions, AT_SAMPLE);
  assert.equal(total, 4);
  assert.equal(closedCount, 0);
  assert.deepEqual(ids(groups.eligible), ['ai-literacy-2026']);
  assert.deepEqual(ids(groups.team_needed), []);
  // 역사 영상은 제외 대상이 본문·포스터에서 달라(needs_check) 확인 필요, EVC는 개인 참가 여부가 공지에 없어 확인 필요
  assert.deepEqual(ids(groups.check).sort(), ['evc-13-nature', 'history-video-5']);
  assert.deepEqual(ids(groups.ineligible), ['abix-hackathon-2026']);
  // 해커톤의 불가는 확인 전 근거(자동 검증 통과)에서 나온 것이라 "확인 전 정보"를 붙인다
  assert.equal(hasUnverifiedFail(groups.ineligible[0].judged), true);
  assert.match(groups.ineligible[0].summary, /인문캠퍼스.*근거: "참가자격: 명지대학교 소속 인문캠퍼스 재학생"/);
  assert.match(groups.eligible[0].summary, /✓/);
});

test('문장 반영: 문장이 말한 조건만 바꾸고, 말하지 않은 조건은 그대로 둔다', () => {
  const before = { ...emptyConditions(), campus: '인문', grade: 3, participation: '팀' };
  const { conditions, changed, unknownTerms } = applyParsed(before, parseConditions('혼자 낼 영상 공모전, 대학원생도 돼?'));
  assert.equal(conditions.campus, '인문');
  assert.equal(conditions.grade, 3);
  assert.equal(conditions.participation, '개인');
  assert.deepEqual(conditions.deliverables, ['영상']);
  assert.deepEqual(changed.sort(), ['deliverables', 'participation']);
  assert.deepEqual(unknownTerms, ['대학원생']);
  assert.deepEqual(conditions.unknown_terms, [], '이해하지 못한 표현은 조건에 넣지 않는다 (저장되지 않게)');
  // 이미 같은 값이면 바뀐 것으로 세지 않는다
  assert.deepEqual(applyParsed(conditions, parseConditions('혼자')).changed, []);
});

test('기간 필터의 마지막 날: 마감일 기준 (계약 2절 5)', () => {
  assert.equal(periodEndDate('마감임박', AT_SAMPLE), '2026-10-22');
  assert.equal(periodEndDate('이번주', AT_SAMPLE), '2026-10-18', '10/15(목) → 그 주 일요일');
  assert.equal(periodEndDate('이번주', new Date('2026-10-18T10:00:00+09:00')), '2026-10-18', '일요일이면 그날');
  assert.equal(periodEndDate('이번주', new Date('2026-10-19T00:30:00+09:00')), '2026-10-25', '한국 시간 월요일 0시 30분');
  assert.equal(periodEndDate('이번달', AT_SAMPLE), '2026-10-31');
  assert.equal(periodEndDate('이번달', new Date('2026-02-10T12:00:00+09:00')), '2026-02-28');
  assert.equal(periodEndDate(null, AT_SAMPLE), null);
});

test('필터: 기간·제출물은 판정이 아니라 목록에서 숨기기', () => {
  const urgent = recommendCards(SAMPLE.cards, { ...emptyConditions(), period: '마감임박' }, AT_SAMPLE);
  // 10/22까지 마감: EVC(10/16), AI 리터러시(10/21). 역사 영상(10/23)·해커톤(10/28)은 숨김
  assert.deepEqual(RESULT_ORDER.flatMap((result) => ids(urgent.groups[result])).sort(), ['ai-literacy-2026', 'evc-13-nature']);
  assert.equal(urgent.filteredCount, 2);
  const video = recommendCards(SAMPLE.cards, { ...emptyConditions(), deliverables: ['영상'] }, AT_SAMPLE);
  assert.deepEqual(RESULT_ORDER.flatMap((result) => ids(video.groups[result])).sort(), ['evc-13-nature', 'history-video-5']);
  // 조건을 하나도 고르지 않으면 신청 가능은 나오지 않는다 (말하지 않은 조건은 "모름")
  const none = recommendCards(SAMPLE.cards, emptyConditions(), AT_SAMPLE);
  assert.equal(none.groups.eligible.length, 0);
  assert.equal(none.total, 4);
});

test('제목 검색: 조건이 아닌 목록 필터, 띄어쓰기·대소문자 무시 (계약 5절, #85)', () => {
  const shown = (result) => RESULT_ORDER.flatMap((name) => ids(result.groups[name])).sort();
  // "해커톤"은 해석기가 조건으로 바꾸지 않는 말이다. 제목 검색으로는 찾을 수 있어야 한다
  const hackathon = recommendCards(SAMPLE.cards, emptyConditions(), AT_SAMPLE, { titleQuery: '해커톤' });
  assert.deepEqual(shown(hackathon), ['abix-hackathon-2026']);
  assert.equal(hackathon.titleFilteredCount, 3);
  assert.equal(hackathon.filteredCount, 0, '제목으로 빠진 수는 제출물·기간 숨김과 따로 센다');
  // 띄어쓰기·영문 대소문자를 무시한다 ("english video" ↔ "English Video", "역사영상" ↔ "역사 영상")
  assert.deepEqual(shown(recommendCards(SAMPLE.cards, emptyConditions(), AT_SAMPLE, { titleQuery: ' english  VIDEO ' })), ['evc-13-nature']);
  assert.deepEqual(shown(recommendCards(SAMPLE.cards, emptyConditions(), AT_SAMPLE, { titleQuery: '역사영상' })), ['history-video-5']);
  // 판정은 바뀌지 않는다: 자연캠 학생에게 인문캠 해커톤은 검색해도 불가
  const natural = { ...emptyConditions(), campus: '자연' };
  const judged = recommendCards(SAMPLE.cards, natural, AT_SAMPLE, { titleQuery: '해커톤' });
  assert.deepEqual(ids(judged.groups.ineligible), ['abix-hackathon-2026']);
  // 다른 필터와 함께 걸린다: 영상 제출물 + "해커톤" → 없음
  const both = recommendCards(SAMPLE.cards, { ...emptyConditions(), deliverables: ['영상'] }, AT_SAMPLE, { titleQuery: '해커톤' });
  assert.equal(both.total, 0);
  // 빈 검색어·공백뿐·문자열 아님은 검색하지 않는다
  for (const titleQuery of ['', '   ', null, 42]) {
    const all = recommendCards(SAMPLE.cards, emptyConditions(), AT_SAMPLE, { titleQuery });
    assert.equal(all.total, 4, String(titleQuery));
    assert.equal(all.titleFilteredCount, 0);
  }
  assert.equal(recommendCards(SAMPLE.cards, emptyConditions(), AT_SAMPLE).titleFilteredCount, 0, '옵션 없이 부르면 검색하지 않음');
});

test('제목 검색어는 30자까지만 쓴다', () => {
  assert.equal(normalizeTitleQuery('가'.repeat(40)), '가'.repeat(MAX_TITLE_QUERY_LENGTH));
  assert.equal(normalizeTitleQuery('AI 리터러시'), 'ai리터러시');
  assert.equal(normalizeTitleQuery(undefined), '');
  assert.equal(normalizeTitleQuery(' \t\n '), '', '공백뿐이면 검색하지 않음');
  // 글자 단위로 자른다: 30번째가 이모지여도 반쪽(서로게이트)만 남지 않는다
  const clipped = clipTitleQuery(`${'가'.repeat(29)}🏆🏆`);
  assert.equal([...clipped].length, MAX_TITLE_QUERY_LENGTH);
  assert.ok(clipped.endsWith('🏆'));
  assert.equal(clipTitleQuery(7), '');
});

test('제목 검색: 자모가 나뉜(NFD) 입력도 찾고, 마감 지난 대회는 검색 숨김으로 세지 않는다', () => {
  const shown = (result) => RESULT_ORDER.flatMap((name) => ids(result.groups[name])).sort();
  // macOS 등에서 붙여 넣은 글자는 자모가 나뉘어 있을 수 있다
  assert.deepEqual(shown(recommendCards(SAMPLE.cards, emptyConditions(), AT_SAMPLE, { titleQuery: '해커톤'.normalize('NFD') })), ['abix-hackathon-2026']);
  // 10/22에는 EVC·AI 리터러시가 마감. "해커톤"으로 숨기는 것은 남은 2장 중 역사 영상 1장뿐
  const late = recommendCards(SAMPLE.cards, emptyConditions(), new Date('2026-10-22T00:00:00+09:00'), { titleQuery: '해커톤' });
  assert.equal(late.closedCount, 2);
  assert.equal(late.titleFilteredCount, 1);
  assert.deepEqual(shown(late), ['abix-hackathon-2026']);
});

test('마감 지난 대회는 빼고 건수만 센다, 같은 상태 안에서는 마감 가까운 순', () => {
  const late = new Date('2026-10-22T00:00:00+09:00');
  const { groups, closedCount, total } = recommendCards(SAMPLE.cards, emptyConditions(), late);
  assert.equal(closedCount, 2, 'EVC(10/16)·AI 리터러시(10/21 15:00) 마감');
  assert.equal(total, 2);
  const all = recommendCards(SAMPLE.cards, emptyConditions(), AT_SAMPLE);
  // 조건을 고르지 않으면 모두 확인 필요. 마감 10/16 → 10/21 → 10/23 → 10/28
  assert.deepEqual(ids(all.groups.check), ['evc-13-nature', 'ai-literacy-2026', 'history-video-5', 'abix-hackathon-2026']);
});

test('사유 요약: 신청 가능은 맞은 조건 ✓, 나머지는 결과를 정한 사유부터', () => {
  const judged = { result: 'check', reasons: [
    { outcome: 'unknown', message: '학년 조건이 공지에 없음' },
    { outcome: 'fail', message: '인문캠만', quote: '근거 문장' },
    { outcome: 'ok', message: '신청 마감 전' },
  ] };
  assert.equal(reasonSummary(judged), '학년 조건이 공지에 없음 · 인문캠만 · 근거: "근거 문장"');
  assert.equal(reasonSummary({ result: 'eligible', reasons: [{ outcome: 'ok', message: '캠퍼스 제한 없음' }] }), '캠퍼스 제한 없음 ✓');
  assert.equal(reasonSummary(judged, 1), '학년 조건이 공지에 없음');
});

test('이해하지 못한 표현 칩: 10개·20자까지, 나머지는 "외 N개"', () => {
  const terms = Array.from({ length: 13 }, (_, index) => `표현${index}`);
  assert.deepEqual(limitTerms(terms), { shown: terms.slice(0, 10), originals: terms.slice(0, 10), hiddenCount: 3 });
  // 화면에는 줄인 글자, "제목에서 찾기"에는 줄이기 전 표현
  assert.deepEqual(limitTerms(['가'.repeat(25)]), { shown: [`${'가'.repeat(20)}…`], originals: ['가'.repeat(25)], hiddenCount: 0 });
  assert.deepEqual(limitTerms(['', ' ', null, '대학원생']), { shown: ['대학원생'], originals: ['대학원생'], hiddenCount: 0 });
  assert.deepEqual(limitTerms(undefined), { shown: [], originals: [], hiddenCount: 0 });
});

test('저장된 조건 지우기 (공용 PC에 학년·학적이 남지 않게)', () => {
  const store = new Map();
  const storage = { getItem: (key) => store.get(key) ?? null, setItem: (key, value) => store.set(key, value), removeItem: (key) => store.delete(key) };
  assert.equal(saveConditions({ ...emptyConditions(), grade: 2 }, storage), true);
  assert.ok(store.has(CONDITIONS_STORAGE_KEY));
  assert.equal(clearSavedConditions(storage), true);
  assert.equal(readSavedConditions(storage), null);
  assert.equal(clearSavedConditions({ removeItem: () => { throw new Error('막힘'); } }), false);
});

// ---- 리뷰 반영 ----

test('문장 반영: 읽은 조건·바뀐 조건·그대로 둔 조건을 따로 알려 준다', () => {
  const before = { ...emptyConditions(), grade: 4, enrollment: '휴학', deliverables: ['영상', '발표'] };
  const result = applyParsed(before, parseConditions('4학년 혼자 에세이'));
  assert.deepEqual(result.read.sort(), ['deliverables', 'grade', 'participation']);
  assert.deepEqual(result.changed.sort(), ['deliverables', 'participation'], '4학년은 이미 같은 값');
  assert.deepEqual(result.kept, ['enrollment'], '말하지 않은 휴학은 그대로 두었다고 알린다');
  assert.deepEqual(result.conditions.deliverables, ['에세이·수기'], '제출물을 말하면 목록을 문장의 것으로 바꾼다');
  assert.deepEqual(before.deliverables, ['영상', '발표'], '원래 조건 객체는 바꾸지 않는다');
  assert.deepEqual(applyParsed({ ...emptyConditions(), deliverables: undefined }, parseConditions('편입생 아니고')).conditions.is_transfer, false);
});

test('기간 경계: 한국 시간 일요일 밤과 월요일 0시, 윤년·연말', () => {
  assert.equal(periodEndDate('이번주', new Date('2026-10-18T23:59:00+09:00')), '2026-10-18');
  assert.equal(periodEndDate('이번주', new Date('2026-10-19T00:00:00+09:00')), '2026-10-25');
  assert.equal(periodEndDate('이번주', new Date('2026-10-17T12:00:00+09:00')), '2026-10-18', '토요일 → 다음 날');
  assert.equal(periodEndDate('이번달', new Date('2028-02-10T12:00:00+09:00')), '2028-02-29');
  assert.equal(periodEndDate('이번달', new Date('2026-12-31T23:30:00+09:00')), '2026-12-31');
  assert.equal(periodEndDate('마감임박', new Date('2026-12-28T12:00:00+09:00')), '2027-01-04');
});

test('기간 필터: 마감일 당일은 포함, 하루 뒤는 숨김, 마감일 없는 대회는 따로 센다', () => {
  const card = (id, end, timeGiven = false) => {
    const base = structuredClone(SAMPLE.cards.find((item) => item.id === 'ai-literacy-2026'));
    base.id = id;
    base.fields.apply.value = end === null ? { start: null, end: null, time_given: false } : { start: null, end, time_given: timeGiven };
    if (end === null) base.fields.apply.status = 'missing';
    return base;
  };
  const now = new Date('2026-10-15T09:00:00+09:00');
  const cards = [card('on-edge', '2026-10-22'), card('day-after', '2026-10-23'), card('no-deadline', null), card('today-no-time', '2026-10-15')];
  const result = recommendCards(cards, { ...emptyConditions(), period: '마감임박' }, now);
  const shown = RESULT_ORDER.flatMap((name) => ids(result.groups[name])).sort();
  assert.deepEqual(shown, ['on-edge', 'today-no-time']);
  assert.equal(result.filteredCount, 2);
  assert.equal(result.undatedCount, 1);
});

test('판정하다 오류가 나는 카드는 그 카드만 빼고 센다 (목록 전체가 사라지지 않게)', () => {
  const broken = structuredClone(SAMPLE.cards[0]);
  broken.id = 'broken';
  Object.defineProperty(broken, 'fields', { get() { throw new Error('깨진 카드'); } });
  const result = recommendCards([broken, ...SAMPLE.cards], emptyConditions(), AT_SAMPLE);
  assert.equal(result.skippedCount, 1);
  assert.equal(result.total, 4);
});

test('빈 결과 안내: 제목 검색으로 0건이면 필터 문구, 판정 오류로만 빠지면 마감이라고 하지 않는다 (#104)', () => {
  const filtered = '조건을 풀어 보세요.';
  const searched = recommendCards(SAMPLE.cards, emptyConditions(), AT_SAMPLE, { titleQuery: '없는제목검색어' });
  assert.equal(searched.total, 0);
  assert.equal(emptyListMessage({ cardCount: SAMPLE.cards.length, ...searched }, filtered), filtered);

  const broken = structuredClone(SAMPLE.cards[0]);
  Object.defineProperty(broken, 'fields', { get() { throw new Error('깨진 카드'); } });
  const onlyBroken = recommendCards([broken], emptyConditions(), AT_SAMPLE);
  assert.equal(onlyBroken.total, 0);
  const message = emptyListMessage({ cardCount: 1, ...onlyBroken }, filtered);
  assert.match(message, /정보를 읽지 못한 대회/);
  assert.doesNotMatch(message, /마감/);
});

test('사유 요약: 근거 문장은 60자까지, 신청 가능인데 맞은 사유가 없으면 기본 문구', () => {
  const long = '가'.repeat(80);
  assert.equal(reasonSummary({ result: 'ineligible', reasons: [{ outcome: 'fail', message: '불가', quote: long }] }), `불가 · 근거: "${'가'.repeat(60)}…"`);
  assert.equal(reasonSummary({ result: 'eligible', reasons: [] }), '조건이 모두 맞습니다');
});
