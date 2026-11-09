// 자연어 해석기 테스트. 기준은 docs/api-contract.md 2절.
// 여기 문장은 Claude가 해석기를 만들며 쓴 **개발용**이라 정확도 측정에 쓰지 않는다.
// 정확도는 사전을 보지 않고 쓴 시험 문장(eval/parser/sentences.csv)으로 잰다.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseConditions, STUDENT_DELIVERABLE_TAGS, MAX_INPUT_LENGTH } from '../site/js/parser.js';
import { judgeCard } from '../site/js/rules.js';
import { parseCsv, readSentences, scoreSentences, compareField, isSameTerm, CONDITION_FIELDS } from '../eval/parser/run.js';

const EMPTY = {
  campus: null, grade: null, is_transfer: null, enrollment: null,
  participation: null, deliverables: [], period: null, unknown_terms: [],
};
const expect = (partial) => ({ ...EMPTY, ...partial });

const DEV_CASES = [
  // 사용자 시나리오 (작업 폴더 docs/03)
  ['자연캠 2학년인데 지금 낼 수 있는 대회 있어?', { campus: '자연', grade: 2 }],
  ['편입생도 신청할 수 있어?', { is_transfer: true }],
  // 여러 조건, 조사·붙여 쓰기
  ['인문캠 3학년 혼자 영상 낼 만한 거 있나요', { campus: '인문', grade: 3, participation: '개인', deliverables: ['영상'] }],
  ['용인캠 신입생 팀으로 앱 개발', { campus: '자연', grade: 1, participation: '팀', deliverables: ['코드·개발'] }],
  ['자연캠2학년', { campus: '자연', grade: 2 }],
  ['자연 캠퍼스 재학중이고 편입생은 아닙니다. 혼자 할만한 글쓰기 대회 있을까요?',
    { campus: '자연', is_transfer: false, enrollment: '재학', participation: '개인', deliverables: ['에세이·수기'] }],
  ['3명이서 앱개발로 나갈 해커톤', { participation: '팀', deliverables: ['코드·개발'] }],
  ['이학년 이번주까지 포스터', { grade: 2, deliverables: ['디자인'], period: '이번주' }],
  ['졸업유예생인데 마감 임박한 거 있어?', { enrollment: '졸업유예', period: '마감임박' }],
  ['용인 캠 다니는 3학년, 영상이나 디자인 쪽으로 이번달 마감인 거',
    { campus: '자연', grade: 3, deliverables: ['영상', '디자인'], period: '이번달' }],
  ['ㅎㅇ 2학년 휴학생인데 팀원 구해서 나갈 해커톤 ㅊㅊ',
    { grade: 2, enrollment: '휴학', participation: '팀', deliverables: ['코드·개발'] }],
  ['휴학 중인데 참가 가능한 공모전', { enrollment: '휴학' }],
  // 부정 (계약 2절 3)
  ['팀 말고 혼자 할 수 있는 코딩 대회', { participation: '개인', deliverables: ['코드·개발'] }],
  ['편입 안 했어', { is_transfer: false }],
  ['팀원이 없어서 혼자 나갈 대회', { participation: '개인' }],
  ['편입생은 안 돼?', { is_transfer: true }],
  ['영상 말고 에세이', { deliverables: ['에세이·수기'], unknown_terms: ['영상 말고'] }],
  ['팀 없이 혼자 발표하는 거 말고', { participation: '개인', unknown_terms: ['발표하는 거 말고'] }],
  // 같은 조건이 둘 이상 (계약 2절 2)
  ['1, 2학년 대상 대회', { unknown_terms: ['1학년', '2학년'] }],
  ['자연캠이랑 인문캠 둘 다 되는 거', { unknown_terms: ['자연캠', '인문캠'] }],
  ['초과학기생 혼자도 팀도 괜찮아', { enrollment: '초과학기' }],
  ['팀 아니면 혼자', {}],
  // 애매한 표현과 담을 수 없는 표현 (계약 2절 1·5)
  ['고학년인데 발표 대회', { deliverables: ['발표'], unknown_terms: ['고학년'] }],
  ['복학생 혼자 나갈 대회', { participation: '개인', unknown_terms: ['복학생'] }],
  ['대학원생도 되나요', { unknown_terms: ['대학원생'] }],
  ['휴학 예정인데 해커톤 나가고 싶어', { deliverables: ['코드·개발'], unknown_terms: ['휴학 예정'] }],
  ['오프라인 현장 참가 대회 다음 주 마감', { deliverables: ['현장 참가'], unknown_terms: ['다음주'] }],
  ['외국인 유학생도 참가 가능한 대회?', { unknown_terms: ['외국인', '유학생'] }],
  // 표현이 낱말 일부일 뿐인 경우
  ['영상학과 2학년이에요', { grade: 2, unknown_terms: ['영상학과'] }],
  ['개인정보 공모전', { unknown_terms: ['개인정보'] }],
  ['글로벌 대회', { unknown_terms: ['글로벌'] }],
  ['웹툰 그리는 대회', { deliverables: ['디자인'] }],
  ['', {}],
  // 코드 리뷰에서 나온 오인식. 틀린 값 대신 비우고 문구를 남겨야 한다
  ['편입 안 한 2학년', { grade: 2, is_transfer: false }],
  ['편입안했어', { is_transfer: false }],
  ['편입 안했어요', { is_transfer: false }],
  ['편입안돼?', { is_transfer: true }],
  ['휴학 안 한 상태', { unknown_terms: ['휴학 안 한'] }],
  ['편입생은 좀 아니고', { is_transfer: false }],
  ['영상 같은 건 아니고 글', { deliverables: ['에세이·수기'], unknown_terms: ['영상 같은 건 아니고'] }],
  ['팀플은 싫어요', { participation: '개인' }],
  ['발표는 별로', { unknown_terms: ['발표는 별로'] }],
  ['영상은 못 찍어', { unknown_terms: ['영상은 못 찍어'] }],
  ['영상 안 찍어도 되는 대회', { unknown_terms: ['영상 안 찍어'] }],
  ['편입생은 못 하나요', { unknown_terms: ['편입생은 못 하나'] }],
  ['같이 할 사람 없어서 나갈 수 있는 대회', { unknown_terms: ['같이 할 사람 없어서'] }],
  ['같이 나갈 대회', { participation: '팀' }],
  ['친구가 추천한 대회', { unknown_terms: ['친구'] }],
  ['친구랑 나갈 대회', { participation: '팀' }],
  ['자연캠 다니는데 친구가 없어서 혼자', { campus: '자연', participation: '개인', unknown_terms: ['친구'] }],
  ['게임 대회', { unknown_terms: ['게임'] }],
  ['나이 제한 없는 대회', { unknown_terms: ['나이', '제한'] }],
  ['참가비 무료인 대회', { unknown_terms: ['참가비', '무료인'] }],
  ['휴학 하려고 하는데', { unknown_terms: ['휴학 하려고'] }],
  ['휴학 할 계획', { unknown_terms: ['휴학 할 계획'] }],
  ['3학년 되는 해', { unknown_terms: ['3학년 되는'] }],
  ['2학년 이상', { unknown_terms: ['2학년 이상'] }],
  ['3학년부터', { unknown_terms: ['3학년부터'] }],
  ['편입 신입생입니다', { is_transfer: true, unknown_terms: ['신입생'] }],
  ['웹디자인', { unknown_terms: ['웹디자인'] }],
  ['디자인과 2학년', { grade: 2, unknown_terms: ['디자인과'] }],
  ['현장 실습 대회', { unknown_terms: ['현장', '실습'] }],
  ['30명 규모 대회', { unknown_terms: ['30명', '규모'] }],
  ['3명 이내', { unknown_terms: ['3명 이내'] }],
  ['3일 안에 마감인 거', { period: '마감임박' }],
  ['이번주안에 포스터', { deliverables: ['디자인'], period: '이번주' }],
  ['편입생도 할 수 있는 대회', { is_transfer: true }],
  // 절 첫머리의 부정은 앞 절의 마지막 표현을 불확실하게 만든다 (고쳐 말하기)
  ['자연캠, 아니 인문캠', { campus: '인문', unknown_terms: ['자연캠 … 아니'] }],
  ['편입생, 아니고 재학생', { enrollment: '재학', unknown_terms: ['편입생 … 아니고'] }],
  // 2차 리뷰에서 나온 오인식
  ['편입생이라 힘들어요', { unknown_terms: ['편입생이라 힘들어'] }],
  ['편입생이라서 부담돼요', { unknown_terms: ['편입생이라서 부담돼'] }],
  ['휴학할 건데', { unknown_terms: ['건데', '휴학할'] }],
  ['편입할 거야', { unknown_terms: ['편입할'] }],
  ['편입 하고 싶은데', { unknown_terms: ['편입 하고 싶은데'] }],
  ['휴학 해야 하나', { unknown_terms: ['휴학 해야'] }],
  ['자연캠 학생은 아니에요', { unknown_terms: ['자연캠 … 아니'] }],
  ['편입생 신분은 아니고 재학생', { enrollment: '재학', unknown_terms: ['신분', '편입생 … 아니고'] }],
  ['친구랑 같이 하는 건 싫어', { unknown_terms: ['친구 … 같이 하는 건 싫어', '같이 하는 건 싫어'] }],
  ['혼자 안 해봐서 걱정이에요', { unknown_terms: ['걱정', '혼자 안 해봐서'] }],
  ['친구들이 추천한 대회', { unknown_terms: ['친구들'] }],
  ['친구들과 나갈래', { participation: '팀' }],
  ['지난 학기에 휴학했는데 이번에 복학했어요', { unknown_terms: ['지난학기', '이번', '복학했어', '휴학했는데 … 지난학기'] }],
  ['같이 내용이 비슷한 대회', { unknown_terms: ['같이', '내용', '비슷한'] }],
  ['2학년인데 경험 없어서 혼자', { grade: 2, participation: '개인', unknown_terms: ['경험'] }],
  ['편입생도되나요', { is_transfer: true }],
  // PR #36 리뷰(CattonNyan)에서 나온 오인식: 이미 인식하는 표현이 결합될 때 틀린 값을 확정함
  ['지난 학기에 편입 안 했어. 자연캠 2학년 재학생 혼자',
    { campus: '자연', grade: 2, enrollment: '재학', participation: '개인', unknown_terms: ['지난학기', '편입 안 했어 … 지난학기'] }],
  ['휴학이나 수료는 아니에요', { unknown_terms: ['휴학 … 수료는 아니', '수료는 아니'] }],
  ['인문캠이나 자연캠은 아니에요', { unknown_terms: ['인문캠 … 자연캠은 아니', '자연캠은 아니'] }],
  ['영상과 발표는 말고', { unknown_terms: ['영상과 … 발표는 말고', '발표는 말고'] }],
  ['팀으로 참가 못해', { unknown_terms: ['팀으로 참가 못해'] }],
  ['친구랑 참가 못해', { unknown_terms: ['친구랑 참가 못해'] }],
  // 비교: 나열 자체, 자격을 묻는 "안 되나요"는 그대로
  ['영상과 발표', { deliverables: ['영상', '발표'] }],
  ['팀으로 참가 안 되나요', { participation: '팀' }],
  ['휴학생이나 수료생 말고 재학생', { enrollment: '재학', unknown_terms: ['휴학생 … 수료생 말고', '수료생 말고'] }],
];

for (const [sentence, partial] of DEV_CASES) {
  test(`해석: ${JSON.stringify(sentence)}`, () => {
    assert.deepEqual(parseConditions(sentence), expect(partial));
  });
}

test('문자열이 아니면 TypeError', () => {
  for (const value of [null, undefined, 3, {}, ['자연캠']]) {
    assert.throws(() => parseConditions(value), TypeError);
  }
});

test('출력은 언제나 계약 2절 형식 (무작위 문장 2만 개)', () => {
  const pieces = ['자연캠', '인문캠', '2학년', '사학년', '편입생', '아니고', '말고', '안', '했어', '휴학', '예정', '혼자', '팀',
    '영상', '앱개발로', '이번 주', '마감', '임박', '1,', '3명', '대학원생', 'ㅋㅋ', '?', '!!', '   ', 'abc', '😀', '"', '<b>'];
  let seed = 23;
  const random = () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; };
  for (let i = 0; i < 20000; i += 1) {
    const length = Math.floor(random() * 8);
    const sentence = Array.from({ length }, () => pieces[Math.floor(random() * pieces.length)]).join(random() < 0.7 ? ' ' : '');
    const result = parseConditions(sentence);
    assert.deepEqual(Object.keys(result), Object.keys(EMPTY), sentence);
    assert.ok([null, '인문', '자연'].includes(result.campus), sentence);
    assert.ok(result.grade === null || (Number.isInteger(result.grade) && result.grade >= 1 && result.grade <= 6), sentence);
    assert.ok([null, true, false].includes(result.is_transfer), sentence);
    assert.ok([null, '재학', '휴학', '수료', '초과학기', '졸업유예'].includes(result.enrollment), sentence);
    assert.ok([null, '개인', '팀'].includes(result.participation), sentence);
    assert.ok(result.deliverables.every((tag) => STUDENT_DELIVERABLE_TAGS.includes(tag)), sentence);
    assert.ok([null, '마감임박', '이번주', '이번달'].includes(result.period), sentence);
    assert.ok(result.unknown_terms.every((term) => typeof term === 'string' && term.trim() !== ''), sentence);
    assert.equal(new Set(result.unknown_terms).size, result.unknown_terms.length, sentence);
  }
});

test('입력은 200자까지, 넘으면 RangeError', () => {
  assert.equal(MAX_INPUT_LENGTH, 200);
  assert.doesNotThrow(() => parseConditions('가'.repeat(MAX_INPUT_LENGTH)));
  assert.throws(() => parseConditions('가'.repeat(MAX_INPUT_LENGTH + 1)), RangeError);
});

test('상한 길이의 나쁜 입력도 빨리 끝난다', () => {
  for (const sentence of ['1'.repeat(199) + '~', '1~'.repeat(100), '자연캠'.repeat(66), '이'.repeat(200), '팀이라서도'.repeat(40)]) {
    const started = performance.now();
    parseConditions(sentence);
    assert.ok(performance.now() - started < 50, sentence.slice(0, 10));
  }
});

test('사전에 없는 말만으로 된 문장은 조건이 모두 비어 있다 (무작위 5천 개)', () => {
  const pieces = ['대학원생', '복수전공', '게임', '나이', '상금', '마일리지', '외국인', '다음주', '고학년', '졸업반', '있어', '대회', '좀', '?'];
  let seed = 7;
  const random = () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; };
  for (let i = 0; i < 5000; i += 1) {
    const sentence = Array.from({ length: 1 + Math.floor(random() * 6) }, () => pieces[Math.floor(random() * pieces.length)]).join(' ');
    const { unknown_terms: unknownTerms, ...conditions } = parseConditions(sentence);
    const { unknown_terms: emptyTerms, ...emptyConditions } = EMPTY;
    assert.deepEqual(conditions, emptyConditions, sentence);
  }
});

test('캠퍼스·학년·학적을 부정하면 값이 남지 않는다 (위험 오판 방지)', () => {
  const values = [['자연캠', 'campus'], ['인문캠', 'campus'], ['2학년', 'grade'], ['휴학생', 'enrollment'], ['재학생', 'enrollment'], ['수료생', 'enrollment']];
  const negations = [' 말고', ' 아니고', ' 빼고', '은 좀 아니고', ' 같은 건 별로', ' 못 해', '은 싫어', '안했어'];
  for (const [word, field] of values) {
    for (const negation of negations) {
      const sentence = `${word}${negation}`;
      assert.equal(parseConditions(sentence)[field], null, sentence);
    }
  }
});

test('실제 학생 문장의 거절 표현: 극혐·귀찮아서·사절도 팀을 거절한 것이다 (#37, 첫 측정)', () => {
  for (const sentence of ['팀플 극혐하는 3학년', '팀플은 극혐', '팀플 귀찮아서 혼자 할래', '팀플은 사절', '팀플은 절대 사절']) {
    assert.equal(parseConditions(sentence).participation, '개인', sentence);
  }
  assert.equal(parseConditions('팀플 극혐하는 인문캠 3학년').grade, 3, '거절이 다른 조건을 지우지 않는다');
  for (const sentence of ['편입생이라 극혐', '편입생이라 귀찮아요', '휴학생이라 사절']) {
    const result = parseConditions(sentence);
    assert.equal(result.is_transfer, null, `${sentence}: 참가 형태 밖에서는 반대 값을 내지 않는다`);
    assert.equal(result.enrollment, null, sentence);
  }
  for (const sentence of ['영상은 극혐', '발표는 귀찮아서']) {
    const result = parseConditions(sentence);
    assert.deepEqual(result.deliverables, [], `${sentence}: 거절한 제출물은 목록에 넣지 않는다`);
    assert.equal(result.unknown_terms.length, 1, sentence);
  }
  assert.deepEqual(parseConditions('영상 사절단 활동').deliverables, ['영상'], '"사절단"은 거절이 아니다');
  assert.equal(parseConditions('편입 절대 안 했어').is_transfer, false, '"절대"가 끼어도 편입 부정이 걸린다');
});

test('편입·참가 형태 + 부정·거절: 학생이 한 말의 반대 값이 나오지 않는다', () => {
  const flips = [' 아니고', ' 말고', '은 좀 아니고', '안했어', ' 안 했어요'];
  const rejections = ['이라 힘들어요', '인데 어려워요', '은 별로', '이라서 부담돼요', '은 싫어'];
  const fuzzy = [' 학생은 아니에요', ' 안 해봐서', ' 못 해', '할 건데', ' 하고 싶은데'];
  for (const ending of flips) assert.notEqual(parseConditions(`편입생${ending}`).is_transfer, true, ending);
  for (const ending of [...rejections, ...fuzzy]) assert.equal(parseConditions(`편입생${ending}`).is_transfer, null, ending);
  for (const ending of [...flips, ...rejections]) assert.notEqual(parseConditions(`팀${ending}`).participation, '팀', ending);
  // 참가 형태는 바람을 말하는 것이라 "팀 하고 싶은데"는 팀이 맞다. 경험·능력을 말하는 부정만 비운다
  for (const ending of [' 안 해봐서', ' 못 해', '은 못 하나요']) assert.equal(parseConditions(`팀${ending}`).participation, null, ending);
});

test('같은 조건 두 값을 접속 조사로 이으면 값을 비우고 둘 다 남긴다 (계약 2절 2)', () => {
  const pairs = [['인문캠', '자연캠', 'campus'], ['1학년', '2학년', 'grade'], ['휴학생', '재학생', 'enrollment']];
  for (const joiner of ['하고 ', ' 하고 ', '이랑 ', '랑 ', '과 ', '와 ', ' 및 ', ', ']) {
    for (const [first, second, field] of pairs) {
      const sentence = `${first}${joiner}${second} 둘 다 되는 거`;
      const result = parseConditions(sentence);
      assert.equal(result[field], null, sentence);
      assert.ok(result.unknown_terms.some((term) => term.includes(first)), `${sentence}: ${result.unknown_terms}`);
      assert.ok(result.unknown_terms.some((term) => term.includes(second)), `${sentence}: ${result.unknown_terms}`);
    }
  }
});

test('나열한 같은 조건에 부정이 걸리면 어느 값도 확정하지 않는다', () => {
  const pairs = [['인문캠', '자연캠', 'campus'], ['휴학', '수료', 'enrollment'], ['1학년', '2학년', 'grade'], ['혼자', '팀', 'participation']];
  for (const joiner of ['이나 ', '나 ', ' 또는 ', '과 ', '와 ', '랑 ', '하고 ']) {
    for (const negation of ['은 아니에요', ' 말고', '은 빼고']) {
      for (const [first, second, field] of pairs) {
        const sentence = `${first}${joiner}${second}${negation}`;
        assert.equal(parseConditions(sentence)[field], null, sentence);
      }
    }
  }
});

test('시간 표현이 있는 절의 편입·학적은 부정으로 읽었어도 비운다', () => {
  for (const sentence of ['지난 학기에 편입 안 했어', '작년엔 편입생 아니었어', '원래 휴학생 아니었는데', '내년에 편입 안 할 거야']) {
    const result = parseConditions(sentence);
    assert.equal(result.is_transfer, null, sentence);
    assert.equal(result.enrollment, null, sentence);
  }
});

test('지난해·지난 겨울·저번·전에·옛날도 지난 일이라 그 절의 지금 상태를 비운다 (#37)', () => {
  const cases = [
    ['지난해에 휴학했는데', 'enrollment'], ['지난 겨울에 휴학했어요', 'enrollment'], ['저번에 휴학생이었는데', 'enrollment'],
    ['전에 편입생이었어요', 'is_transfer'], ['전엔 자연캠 다녔어', 'campus'], ['옛날에 3학년 때 수료했어', 'enrollment'],
    ['지난번에 편입 안 했어', 'is_transfer'],
    ['전에는 휴학생이었는데', 'enrollment'],
  ];
  for (const [sentence, field] of cases) {
    assert.equal(parseConditions(sentence)[field], null, sentence);
  }
  const later = parseConditions('저번에 휴학했어요. 지금은 자연캠 3학년 재학생');
  assert.equal(later.enrollment, '재학', '다른 절의 지금 상태는 그대로');
  assert.equal(later.campus, '자연');
  assert.equal(parseConditions('전공 수업 듣는 자연캠 재학생').campus, '자연', '"전"으로 시작하는 다른 낱말은 시간 표현이 아니다');
  // 알려진 한계: 앞으로의 "전에"도 지난 일로 보고 비운다 (안전한 쪽). 고치면 이 시험을 바꾼다
  assert.equal(parseConditions('졸업하기 전에 자연캠 대회 나가고 싶어').campus, null);
});

test('대회 일정·시간 여유 표현은 같은 절의 지금 상태를 비우지 않는다 (계약 2절 3, #37)', () => {
  const cases = [
    ['자연캠 3학년인데 이번 여름방학에 나갈 대회', { campus: '자연', grade: 3 }],
    ['인문캠 4학년인데 다음 주는 시험 기간이라 바빠요', { campus: '인문', grade: 4 }],
    ['휴학 중인 자연캠 2학년인데 다음 달 안에 끝나는 공모전', { campus: '자연', grade: 2, enrollment: '휴학' }],
    ['편입생인데 이번 주말에 하는 해커톤', { is_transfer: true }],
  ];
  for (const [sentence, expected] of cases) {
    const result = parseConditions(sentence);
    for (const [field, value] of Object.entries(expected)) assert.equal(result[field], value, `${sentence} → ${field}`);
  }
  // "ㅠ"는 문장부호가 아니라 한 절이어도 지금 상태는 그대로, 이번 주말은 기간 칸에
  const oneClause = parseConditions('자캠 3학년인데 다음 주는 바빠서 ㅠ 이번 주말 대회');
  assert.equal(oneClause.campus, '자연');
  assert.equal(oneClause.grade, 3);
  assert.equal(oneClause.period, '이번주');
  // 일정 표현은 부정으로 읽은 값도 비우지 않는다 (목록 표현과 다름)
  assert.equal(parseConditions('다음 주는 바빠서 편입 안 했어').is_transfer, false);
});

test('계약 2절 3의 시간 표현 목록은 일정을 말할 때도 그 절을 비운다 (겹치면 목록이 먼저, #37)', () => {
  const cases = [
    ['인문캠 2학년인데 다음 학기에 할 만한 대회', 'grade'], ['자캠 3학년인데 내년 1월 마감인 공모전', 'campus'],
    ['방학 전에 낼 수 있는 대회 찾는 인문캠 2학년', 'grade'], ['지난주에 휴학했어요', 'enrollment'],
    ['예전에 자연캠 다녔어', 'campus'], ['이전에는 편입생이었는데', 'is_transfer'], ['재작년에 3학년이었어', 'grade'],
    ['내후년에 4학년', 'grade'], ['나중에 휴학할 수도', 'enrollment'], ['복학생인 인문캠 3학년', 'campus'],
  ];
  for (const [sentence, field] of cases) {
    assert.equal(parseConditions(sentence)[field], null, sentence);
  }
  // 값과 떨어진 자리에 두어 절 단위 규칙만 시험한다 (값 바로 앞이면 다른 규칙이 먼저 비운다)
  const listed = ['지난주에', '저번에', '작년에', '재작년에', '예전에', '옛날에', '원래', '이전에', '그전에', '전에', '전엔',
    '전에는', '내년에', '내후년에', '다음 학기에', '나중에', '복학했고'];
  for (const word of listed) {
    const result = parseConditions(`자연캠 3학년인데 ${word} 대회 나갔어`);
    assert.equal(result.campus, null, `${word} → campus`);
    assert.equal(result.grade, null, `${word} → grade`);
  }
  assert.equal(parseConditions('자연캠 3학년인데 다음 주에 대회 나가').campus, '자연', '목록 밖 일정 표현은 같은 자리에서도 비우지 않는다');
});

test('리뷰 재현: 해석 결과가 판정을 "가능"으로 확정하지 않는다 (검증용 카드)', () => {
  const now = new Date('2026-10-15T00:00:00Z');
  const verified = (value) => ({ value, status: 'human_verified', evidence: [{ quote: '근거 문장', location: '본문' }] });
  const card = (overrides) => ({
    id: 'fixture',
    fields: {
      apply: verified({ start: '2026-10-01', end: '2026-10-31', time_given: false }),
      campus: verified('any'),
      grades: verified('any'),
      enrollment: verified({ allowed: [], excluded: [] }),
      team: verified({ min: null, max: 3, individual_ok: true }),
      ...overrides,
    },
  });
  const cases = [
    // [문장, 카드, 현재 기대 판정]
    ['지난 학기에 편입 안 했어. 자연캠 2학년 재학생 혼자', card({ enrollment: verified({ allowed: [], excluded: ['편입생'] }) }), 'check'],
    ['휴학이나 수료는 아니에요', card({ enrollment: verified({ allowed: ['휴학'], excluded: [] }) }), 'check'],
    ['팀으로 참가 못해', card({ team: verified({ min: 2, max: 4, individual_ok: false }) }), 'team_needed'],
    ['친구랑 참가 못해', card({ team: verified({ min: 2, max: 4, individual_ok: false }) }), 'team_needed'],
  ];
  for (const [sentence, fixture, expected] of cases) {
    assert.equal(judgeCard(fixture, parseConditions(sentence), { now }).result, expected, sentence);
  }
});

test('제출물 태그는 기타를 뺀 계약 태그', () => {
  assert.deepEqual(STUDENT_DELIVERABLE_TAGS, ['기획서·제안서', '영상', '에세이·수기', '코드·개발', '발표', '디자인', '현장 참가']);
});

test('"아이디어"만으로는 제출물이 아니고 이해하지 못한 표현에 남는다 (계약 2절 1, #37)', () => {
  for (const sentence of ['아이디어만 있으면 낼 수 있는 대회', '환경 정책 아이디어 내는 공모전', '아이디어 대회 찾아줘']) {
    const result = parseConditions(sentence);
    assert.deepEqual(result.deliverables, [], sentence);
    assert.ok(result.unknown_terms.some((term) => term.includes('아이디어')), `${sentence}: ${result.unknown_terms}`);
  }
  // 형식이 따로 있으면 그것을 담는다
  assert.deepEqual(parseConditions('아이디어 기획서 공모전').deliverables, ['기획서·제안서']);
  assert.deepEqual(parseConditions('피피티 하나만 내면 되는 아이디어 공모전').deliverables, ['기획서·제안서']);
  assert.deepEqual(parseConditions('아이디어 영상 공모전').deliverables, ['영상']);
});

test('PPT를 내는 것은 기획서·제안서, 그 밖의 PPT·PT는 발표 (계약 2절 1, #37)', () => {
  const cases = [
    ['피피티 한 장만 내면 끝나는 공모전', ['기획서·제안서']], ['PPT 5장 이내로 낼 수 있는 대회', ['기획서·제안서']],
    ['ppt 제출하는 대회', ['기획서·제안서']], ['ppt제출만 하면 됨', ['기획서·제안서']], ['PPT로 제출', ['기획서·제안서']],
    ['피피티 발표하는 대회', ['발표']], ['본선 PT 있는 대회', ['발표']], ['PT 내면 되는 대회', ['발표']], ['ppt 있는 대회', ['발표']],
    // 사이에 다른 제출물이 끼면 내는 것은 그쪽이다. 참가 형태·학년은 끼어도 된다
    ['ppt 발표 영상 제출', ['영상', '발표']], ['피피티 혼자 낼 수 있는 대회', ['기획서·제안서']],
    ['ppt 3학년이 내기만 하면 되는', ['기획서·제안서']], ['피피티 딱 하나만 띡 내면 되는', ['기획서·제안서']],
    // "내다"가 아닌 낱말: 낼모레, 내 전공, 10분 내
    ['ppt 대회 낼모레 마감', ['발표']], ['ppt 내 전공 맞는 대회', ['발표']], ['ppt 20분 내 진행', ['발표']],
  ];
  for (const [sentence, expected] of cases) {
    assert.deepEqual(parseConditions(sentence).deliverables, expected, sentence);
  }
  // 부정·거절이 붙으면 값으로 정하지 않는다 (계약 2절 3)
  for (const sentence of ['ppt 싫어', 'ppt 내기 싫어', 'ppt 안 내도 되는 대회', '피피티 말고']) {
    assert.deepEqual(parseConditions(sentence).deliverables, [], sentence);
  }
  assert.deepEqual(parseConditions('피피티 말고 영상 제출').deliverables, ['영상']);
  // 낸다는 말의 활용형은 이해하지 못한 표현으로 남지 않는다. 낱말 전체로 비교하므로 "제출기한"은 내는 말이 아니다
  for (const sentence of ['ppt 제출해서 심사', 'ppt 냈어', '피피티 제출용', 'ppt 내기만 하면 됨', 'ppt 내세요']) {
    const result = parseConditions(sentence);
    assert.deepEqual(result.deliverables, ['기획서·제안서'], sentence);
    assert.ok(!result.unknown_terms.some((term) => /^(내|냈|낸|제출)/.test(term)), `${sentence}: ${result.unknown_terms}`);
  }
  assert.deepEqual(parseConditions('ppt 제출기한 3일 뒤').deliverables, ['발표']);
});

test('시연 문장 → 판정: 해석 결과를 그대로 judgeCard에 넣을 수 있다 (#24 시연)', () => {
  const sample = JSON.parse(readFileSync(new URL('../site/data/cards.sample.json', import.meta.url), 'utf8'));
  const conditions = parseConditions('자연캠 2학년 재학생이고 편입생은 아니고 혼자 낼 대회');
  assert.deepEqual(conditions, expect({ campus: '자연', grade: 2, is_transfer: false, enrollment: '재학', participation: '개인' }));
  const card = sample.cards.find((item) => item.id === 'ai-literacy-2026');
  assert.equal(judgeCard(card, conditions, { now: new Date(sample.generated_at) }).result, 'eligible');
});

// ---- 측정 스크립트 (eval/parser/run.js) ----

const HEADER = 'id,sentence,campus,grade,is_transfer,enrollment,participation,deliverables,period,unknown,writer,written_at,note';

test('CSV 읽기: BOM, 따옴표 안 쉼표·줄바꿈, "" 이스케이프, CRLF, 줄 번호', () => {
  const { rows, error } = parseCsv('﻿a,b\r\n"x, y","줄\r\n바꿈 ""따옴표"""\r\n\r\nc,d\n');
  assert.equal(error, null);
  assert.deepEqual(rows, [
    { line: 1, cells: ['a', 'b'] },
    { line: 2, cells: ['x, y', '줄\n바꿈 "따옴표"'] },
    { line: 5, cells: ['c', 'd'] },
  ]);
});

test('CSV 읽기: 닫히지 않은 따옴표는 오류 (뒤 줄이 조용히 사라지지 않게)', () => {
  const { rows, error } = parseCsv('a,b\n"5인치 모니터,x\nc,d\n');
  assert.deepEqual(rows, []);
  assert.match(error, /2번째 줄.*닫히지 않았/);
});

const ROW = (cells) => [...cells, '작성자', '2026-11-05', ''].join(',');

test('시험 문장 읽기: 견본은 건너뛰고 값을 계약 형식으로 바꾼다', () => {
  const csv = [
    HEADER,
    'example-1,견본 문장,자연,2,,,개인,,,,,,',
    ROW(['s01', '"인문캠 편입생, 영상이나 디자인"', '인문', '', '예', '', '', '"영상,디자인,영상"', '', '"대학원생, 고학년"']),
  ].join('\n');
  const { sentences, errors } = readSentences(csv);
  assert.deepEqual(errors, []);
  assert.equal(sentences.length, 1);
  assert.deepEqual(sentences[0].expected, {
    campus: '인문', enrollment: null, participation: null, period: null, grade: null, is_transfer: true, deliverables: ['영상', '디자인'],
  });
  assert.deepEqual(sentences[0].expectedUnknown, ['대학원생', '고학년']);
  assert.equal(sentences[0].writer, '작성자');
});

test('시험 문장 읽기: 틀린 값은 엑셀 행 번호와 함께 알린다', () => {
  const csv = [
    HEADER,
    's01,문장,서울,7,몰라,재학생,둘다,그림,다음주,,,,',
    '',
    ROW(['s01', '문장', '', '', '', '', '', '', '', '']),
    ROW(['s02', '"줄이\n둘인 문장"', '', '', '', '', '', '', '', '']),
    ROW(['s03', '문장', '', '', '', '', '', '', '', '']),
  ].join('\n');
  const { sentences, errors } = readSentences(csv);
  assert.equal(sentences.length, 1);
  for (const field of ['campus', 'grade', 'is_transfer', 'enrollment', 'participation', 'deliverables', 'period', 'writer', 'written_at']) {
    assert.ok(errors.some((message) => message.startsWith('2번째 줄') && message.includes(field)), field);
  }
  assert.ok(errors.some((message) => message.startsWith('4번째 줄') && message.includes('두 번')));
  assert.ok(errors.some((message) => message.startsWith('7번째 줄') && message.includes('같은 문장')));
});

test('시험 문장 읽기: 200자 넘는 문장과 없는 날짜는 오류 (측정 중 RangeError로 멈추지 않게)', () => {
  const csv = [
    HEADER,
    ROW([ 's01', '가'.repeat(201), '', '', '', '', '', '', '', '']),
    ['s02', '문장', '', '', '', '', '', '', '', '', '작성자', '2026-13-45', ''].join(','),
  ].join('\n');
  const { errors } = readSentences(csv);
  assert.ok(errors.some((message) => message.startsWith('2번째 줄') && message.includes('200자')));
  assert.ok(errors.some((message) => message.startsWith('3번째 줄') && message.includes('written_at')));
});

test('시험 문장 읽기: 제목 줄에 열이 빠지거나 한글이 깨지면 오류', () => {
  assert.match(readSentences('id,sentence\ns01,문장').errors[0], /없는 열/);
  assert.match(readSentences(`${HEADER}\ns01,��,,,,,,,,,a,2026-11-05,`).errors[0], /CSV UTF-8/);
});

test('항목 비교: 맞음 / 다른 값 / 비움 / 말하지 않은 값', () => {
  assert.equal(compareField('campus', '자연', '자연'), 'correct');
  assert.equal(compareField('campus', '자연', '인문'), 'wrong');
  assert.equal(compareField('campus', '자연', null), 'missed');
  assert.equal(compareField('campus', null, '인문'), 'extra');
  assert.equal(compareField('deliverables', ['영상', '디자인'], ['디자인', '영상']), 'correct');
  assert.equal(compareField('deliverables', ['영상'], []), 'missed');
  assert.equal(compareField('deliverables', [], ['영상']), 'extra');
  assert.equal(compareField('is_transfer', false, true), 'wrong');
});

test('이해하지 못한 표현 비교: 띄어쓰기·끝 조사만 다르면 같고, 일부만 겹치면 다르다', () => {
  assert.ok(isSameTerm('대학원생', '대학원생도'));
  assert.ok(isSameTerm('다음 주', '다음주'));
  assert.ok(!isSameTerm('영상 말고', '영상'));
  assert.ok(!isSameTerm('고학년', '학년'));
  // 해석기 문구가 앞뒤 낱말을 더 담아도 찾은 것으로 본다
  assert.ok(isSameTerm('3학년', '내년에 3학년'));
  assert.ok(isSameTerm('편입생 아니고', '편입생 … 아니고'));
});

test('채점: 항목별·판정 조건 오인식·문장 전체·이해하지 못한 표현(1:1)', () => {
  const sentences = [
    { id: 'a', sentence: 'A', expected: expect({ campus: '자연', deliverables: ['영상', '디자인'] }), expectedUnknown: ['대학원생', '대학원'] },
    { id: 'b', sentence: 'B', expected: expect({ grade: 2 }), expectedUnknown: ['다음 주'] },
    { id: 'c', sentence: 'C', expected: expect({}), expectedUnknown: [] },
  ];
  const answers = {
    A: expect({ campus: '자연', deliverables: ['디자인', '영상'], unknown_terms: ['대학원생도', '덤'] }),
    B: expect({ grade: 3, unknown_terms: [] }),
    C: expect({ participation: '팀' }),
  };
  const result = scoreSentences(sentences, (text) => answers[text]);
  assert.deepEqual(result.full_match, { correct: 1, total: 3 });
  assert.deepEqual(result.fields.grade, { correct: 2, wrong: 1, missed: 0, extra: 0, total: 3 });
  assert.deepEqual(result.fields.participation, { correct: 2, wrong: 0, missed: 0, extra: 1, total: 3 });
  assert.equal(result.misreads, 2);
  assert.equal(Object.keys(result.fields).length, CONDITION_FIELDS.length);
  // "대학원생도" 하나가 정답 두 개를 함께 채우지 않는다
  assert.deepEqual(result.unknown_terms, { expected: 3, found: 1, extra: 1 });
  assert.deepEqual(result.mismatches.map((miss) => `${miss.id}:${miss.field}:${miss.outcome}`),
    ['a:unknown_terms:missed', 'b:grade:wrong', 'b:unknown_terms:missed', 'c:participation:extra']);
});

test('저장소의 sentences.csv는 형식이 맞다 (작성 중 실수를 테스트가 먼저 잡도록)', () => {
  const { errors } = readSentences(readFileSync(new URL('../eval/parser/sentences.csv', import.meta.url), 'utf8'));
  assert.deepEqual(errors, []);
});
