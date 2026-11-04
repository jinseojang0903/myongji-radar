// 맞춤 추천(#24) 계산 테스트: 문장 → 조건 반영, 4상태 묶기, 제출물·기간 필터, 사유 요약, 칩 상한, 저장 지우기.
// 시연 시나리오(#24 완료 기준)를 예시 데이터로 고정한다.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { validateCardData } from '../site/js/cards.js';
import { parseConditions } from '../site/js/parser.js';
import { applyParsed, periodEndDate, recommendCards, reasonSummary, hasUnverifiedFail, limitTerms, RESULT_ORDER } from '../site/js/recommend.js';
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
  assert.deepEqual(limitTerms(terms), { shown: terms.slice(0, 10), hiddenCount: 3 });
  assert.deepEqual(limitTerms(['가'.repeat(25)]).shown, [`${'가'.repeat(20)}…`]);
  assert.deepEqual(limitTerms(['', ' ', null, '대학원생']).shown, ['대학원생']);
  assert.deepEqual(limitTerms(undefined), { shown: [], hiddenCount: 0 });
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
