// 판정 규칙 테스트. 실행: npm test (또는 node --test "tests/*.test.js")
// 기준: docs/api-contract.md 3절
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { judgeCard, KEY_FIELDS } from '../site/js/rules.js';

const NOW = new Date('2026-10-15T09:00:00+09:00');

function field(value, status = 'human_verified', quote = '근거 문장') {
  return { value, status, evidence: quote ? [{ quote, location: '본문' }] : [] };
}

// 모든 핵심 항목이 검수 완료이고 누구나 개인 참가 가능한 카드
function openCard(overrides = {}) {
  return {
    id: 'test-card',
    fields: {
      apply: field({ start: '2026-10-01', end: '2026-10-21 15:00', time_given: true }),
      campus: field('any'),
      grades: field('any'),
      enrollment: field({ allowed: [], excluded: [] }),
      team: field({ min: null, max: 3, individual_ok: true }),
      ...overrides,
    },
  };
}

const STUDENT = {
  campus: '자연', grade: 2, is_transfer: false, enrollment: '재학',
  participation: '개인', deliverables: [], period: null, unknown_terms: [],
};

function judge(card, student = STUDENT, now = NOW) {
  return judgeCard(card, student, { now });
}

test('모든 핵심 항목이 검수 완료이고 맞으면 신청 가능', () => {
  const judged = judge(openCard());
  assert.equal(judged.result, 'eligible');
  assert.equal(judged.card_id, 'test-card');
  assert.ok(judged.reasons.every((reason) => reason.outcome === 'ok'));
  assert.deepEqual(new Set(judged.reasons.map((reason) => reason.field)), new Set(KEY_FIELDS));
});

test('판정 순서: 불가 > 확인 필요 > 팀 구성 > 신청 가능', () => {
  const card = openCard({
    campus: field(['인문']), // 불가
    grades: field('any', 'needs_check'), // 확인 필요
    team: field({ min: 2, max: 5, individual_ok: false }), // 팀 구성
  });
  assert.equal(judge(card).result, 'ineligible');
  card.fields.campus = field('any');
  assert.equal(judge(card).result, 'check');
  card.fields.grades = field('any');
  assert.equal(judge(card).result, 'team_needed');
  card.fields.team = field({ min: 2, max: 5, individual_ok: true });
  assert.equal(judge(card).result, 'eligible');
});

test('사유는 결과를 정한 것부터 정렬된다', () => {
  const judged = judge(openCard({ campus: field(['인문']), grades: field(null, 'missing', null) }));
  const order = judged.reasons.map((reason) => reason.outcome);
  assert.equal(order[0], 'fail');
  assert.equal(order[1], 'unknown');
  assert.equal(judged.reasons[0].quote, '근거 문장');
});

test.describe('항목 상태', () => {
  test('확인 전(ai, auto_verified) 항목이 맞으면 확인 필요', () => {
    for (const status of ['ai', 'auto_verified']) {
      const judged = judge(openCard({ campus: field('any', status) }));
      assert.equal(judged.result, 'check', status);
      const reason = judged.reasons.find((item) => item.field === 'campus');
      assert.equal(reason.unverified, true);
      assert.match(reason.message, /확인 전/);
    }
  });

  test('확인 전 항목이 맞지 않으면 불가 + 확인 전 표시', () => {
    const judged = judge(openCard({ campus: field(['인문'], 'auto_verified') }));
    assert.equal(judged.result, 'ineligible');
    assert.equal(judged.reasons[0].unverified, true);
  });

  test('검수 완료 항목이 맞지 않으면 불가, 확인 전 표시 없음', () => {
    const judged = judge(openCard({ campus: field(['인문']) }));
    assert.equal(judged.result, 'ineligible');
    assert.equal(judged.reasons[0].unverified, false);
  });

  test('원문 확인 필요(needs_check) 항목은 맞지 않아 보여도 불가를 내지 않는다', () => {
    const card = openCard({ campus: { ...field(['인문'], 'needs_check'), note: '본문과 포스터가 다름' } });
    const judged = judge(card);
    assert.equal(judged.result, 'check');
    assert.match(judged.reasons[0].message, /본문과 포스터가 다름/);
  });

  test('정보 없음(missing)·항목 자체 없음·value null은 확인 필요', () => {
    for (const campus of [field(null, 'missing', null), undefined, field(null, 'ai')]) {
      assert.equal(judge(openCard({ campus })).result, 'check');
    }
  });

  test('알 수 없는 상태 문자열은 확인 전으로 다룬다', () => {
    assert.equal(judge(openCard({ campus: field('any', 'verified') })).result, 'check');
    assert.equal(judge(openCard({ campus: field(['인문'], 'verified') })).result, 'ineligible');
  });

  test('확인 전 표시는 맞은 조건·팀 조건에만 붙고 사유 메시지에 ✓가 없다', () => {
    const judged = judge(openCard({ campus: field(['인문'], 'ai'), grades: field(null, 'ai') }));
    assert.ok(judged.reasons.every((reason) => !reason.message.includes('✓')));
    assert.ok(!judged.reasons.find((reason) => reason.field === 'campus').message.includes('확인 전'));
  });

  test('확인 전 팀 조건은 팀 구성 시 가능이 아니라 확인 필요', () => {
    const judged = judge(openCard({ team: field({ min: 2, max: 5, individual_ok: false }, 'auto_verified') }));
    assert.equal(judged.result, 'check');
  });
});

test.describe('신청기간', () => {
  const at = (iso) => new Date(iso);

  test('마감 시각이 있으면 그 시각까지 가능', () => {
    const card = openCard({ apply: field({ start: null, end: '2026-10-21 15:00', time_given: true }) });
    assert.equal(judge(card, STUDENT, at('2026-10-21T14:59:00+09:00')).result, 'eligible');
    assert.equal(judge(card, STUDENT, at('2026-10-21T15:01:00+09:00')).result, 'ineligible');
  });

  test('마감 시각이 없으면 마감일 당일은 확인 필요, 다음 날부터 불가', () => {
    const card = openCard({ apply: field({ start: null, end: '2026-10-23', time_given: false }) });
    assert.equal(judge(card, STUDENT, at('2026-10-22T23:00:00+09:00')).result, 'eligible');
    assert.equal(judge(card, STUDENT, at('2026-10-23T09:00:00+09:00')).result, 'check');
    assert.equal(judge(card, STUDENT, at('2026-10-24T00:00:00+09:00')).result, 'ineligible');
  });

  test('한국 시간 기준으로 날짜를 본다 (UTC로는 전날)', () => {
    const card = openCard({ apply: field({ start: null, end: '2026-10-23', time_given: false }) });
    // 2026-10-24 01:00 KST = 2026-10-23 16:00 UTC
    assert.equal(judge(card, STUDENT, at('2026-10-23T16:00:00Z')).result, 'ineligible');
  });

  test('마감일이 없으면(상시 접수 등) 확인 필요', () => {
    const card = openCard({ apply: field({ start: '2026-10-01', end: null, time_given: false }) });
    assert.equal(judge(card).result, 'check');
  });

  test('읽을 수 없는 날짜는 확인 필요', () => {
    for (const end of ['2026-02-30', '10월 23일', '2026-10-23 24:00']) {
      const card = openCard({ apply: field({ start: null, end, time_given: true }) });
      assert.equal(judge(card).result, 'check', end);
    }
  });

  test('신청 시작 전이면 결과는 그대로, 안내만 남긴다', () => {
    const card = openCard({ apply: field({ start: '2026-10-20', end: '2026-10-30', time_given: false }) });
    const judged = judge(card);
    assert.equal(judged.result, 'eligible');
    assert.ok(judged.reasons.some((reason) => reason.outcome === 'note' && /시작 전/.test(reason.message)));
  });
});

test.describe('캠퍼스·학년', () => {
  test('빈 목록·문자열 학년은 형식 오류로 확인 필요 (불가로 읽지 않음)', () => {
    assert.equal(judge(openCard({ campus: field([]) })).result, 'check');
    assert.equal(judge(openCard({ grades: field([]) })).result, 'check');
    assert.equal(judge(openCard({ grades: field(['2']) })).result, 'check');
  });

  test('any면 학생 조건을 몰라도 통과', () => {
    const student = { ...STUDENT, campus: null, grade: null };
    assert.equal(judge(openCard(), student).result, 'eligible');
  });

  test('목록이면 학생 값이 있어야 통과, 입력하지 않았으면 확인 필요', () => {
    const card = openCard({ campus: field(['자연']), grades: field([1, 2]) });
    assert.equal(judge(card).result, 'eligible');
    assert.equal(judge(card, { ...STUDENT, campus: null }).result, 'check');
    assert.equal(judge(card, { ...STUDENT, grade: null }).result, 'check');
    assert.equal(judge(card, { ...STUDENT, grade: 3 }).result, 'ineligible');
    assert.equal(judge(card, { ...STUDENT, campus: '인문' }).result, 'ineligible');
  });
});

test.describe('학적', () => {
  const enrollment = (allowed, excluded, status = 'human_verified') => field({ allowed, excluded }, status);

  test('제외 목록에 있으면 불가', () => {
    const card = openCard({ enrollment: enrollment(['재학'], ['휴학', '수료', '초과학기', '졸업유예']) });
    for (const status of ['휴학', '수료', '초과학기', '졸업유예']) {
      assert.equal(judge(card, { ...STUDENT, enrollment: status }).result, 'ineligible', status);
    }
    assert.equal(judge(card).result, 'eligible');
  });

  test('허용 목록 밖이면 불가, 단 초과학기와 재학은 애매하므로 확인 필요', () => {
    const card = openCard({ enrollment: enrollment(['재학'], []) });
    assert.equal(judge(card, { ...STUDENT, enrollment: '휴학' }).result, 'ineligible');
    assert.equal(judge(card, { ...STUDENT, enrollment: '초과학기' }).result, 'check');
  });

  test('허용 목록에 원문 표현(표준 낱말 밖)이 있으면 불가 대신 확인 필요', () => {
    const card = openCard({ enrollment: enrollment(['학부 재학생'], []) });
    assert.equal(judge(card, { ...STUDENT, enrollment: '휴학' }).result, 'check');
  });

  test('두 목록이 비면(제한 문구 없음) 학적을 몰라도 통과', () => {
    assert.equal(judge(openCard(), { ...STUDENT, enrollment: null }).result, 'eligible');
  });

  test('학적 조건이 있는데 학적을 입력하지 않으면 확인 필요', () => {
    const card = openCard({ enrollment: enrollment(['재학'], ['휴학']) });
    assert.equal(judge(card, { ...STUDENT, enrollment: null }).result, 'check');
  });

  test('편입생 제외: 편입생은 불가, 편입 여부 미입력은 확인 필요', () => {
    const card = openCard({ enrollment: enrollment([], ['편입생']) });
    assert.equal(judge(card, { ...STUDENT, is_transfer: true }).result, 'ineligible');
    assert.equal(judge(card, { ...STUDENT, is_transfer: null }).result, 'check');
    assert.equal(judge(card, { ...STUDENT, is_transfer: false }).result, 'eligible');
  });

  test('원문 표현이 있으면 학생 학적이 맞아 보여도 확인 필요 (리뷰 지적: 위험 오판 경로)', () => {
    const withAllowedText = openCard({ enrollment: enrollment(['재학', '직전학기 평점 3.0 이상'], []) });
    assert.equal(judge(withAllowedText).result, 'check');
    const withExcludedText = openCard({ enrollment: enrollment([], ['외국인 유학생']) });
    assert.equal(judge(withExcludedText).result, 'check');
    assert.match(judge(withExcludedText).reasons[0].message, /외국인 유학생/);
  });

  test('허용 목록의 편입생·대학원생은 해석하지 않고 확인 필요', () => {
    for (const term of ['편입생', '대학원생']) {
      const card = openCard({ enrollment: enrollment([term], []) });
      assert.equal(judge(card, { ...STUDENT, is_transfer: true }).result, 'check', term);
    }
  });

  test('편입생 제외만 있으면 학적을 입력하지 않아도 학적은 통과', () => {
    const card = openCard({ enrollment: enrollment([], ['편입생']) });
    assert.equal(judge(card, { ...STUDENT, enrollment: null, is_transfer: false }).result, 'eligible');
  });

  test('학생이 답할 수 없는 제외 대상(기존 수상자 등)은 안내만', () => {
    const judged = judge(openCard({ enrollment: enrollment([], ['기존 수상자']) }));
    assert.equal(judged.result, 'eligible');
    assert.ok(judged.reasons.some((reason) => reason.outcome === 'note' && /기존 수상자/.test(reason.message)));
  });
});

test.describe('참가 인원', () => {
  const team = (individualOk) => field({ min: 2, max: 5, individual_ok: individualOk });

  test('개인 참가 불가: 개인·미입력은 팀 구성 시 가능, 팀은 신청 가능', () => {
    const card = openCard({ team: team(false) });
    assert.equal(judge(card).result, 'team_needed');
    assert.equal(judge(card, { ...STUDENT, participation: null }).result, 'team_needed');
    assert.equal(judge(card, { ...STUDENT, participation: '팀' }).result, 'eligible');
    assert.match(judge(card).reasons[0].message, /2~5인/);
  });

  test('개인 참가 여부가 공지에 없음(null): 개인·미입력은 확인 필요, 팀은 신청 가능', () => {
    const card = openCard({ team: team(null) });
    assert.equal(judge(card).result, 'check');
    assert.equal(judge(card, { ...STUDENT, participation: null }).result, 'check');
    assert.equal(judge(card, { ...STUDENT, participation: '팀' }).result, 'eligible');
  });
});

test.describe('목업의 실제 대회 4건 (2026-10-15 기준, 자연캠 2학년 재학 개인)', () => {
  test('AI 리터러시: 모두 검수 완료 → 신청 가능', () => {
    const card = openCard({ enrollment: field({ allowed: ['재학'], excluded: ['휴학', '수료', '초과학기', '졸업유예', '대학원생'] }) });
    const judged = judge(card);
    assert.equal(judged.result, 'eligible');
    assert.ok(judged.reasons.some((reason) => reason.outcome === 'note' && /대학원생/.test(reason.message)));
  });

  test('역사 영상: 제외 대상이 원문 확인 필요 → 확인 필요 (팀 조건이 있어도)', () => {
    const card = openCard({
      apply: field({ start: '2026-09-14', end: '2026-10-23', time_given: false }),
      enrollment: field({ allowed: ['재학'], excluded: ['졸업유예', '기존 수상자'] }, 'needs_check'),
      team: field({ min: 2, max: 5, individual_ok: false }),
    });
    const judged = judge(card);
    assert.equal(judged.result, 'check');
    assert.ok(judged.reasons.some((reason) => reason.outcome === 'team'));
  });

  test('English Video Contest: 개인 참가 여부 없음 → 확인 필요', () => {
    const card = openCard({
      apply: field({ start: null, end: '2026-10-16 17:00', time_given: true }, 'auto_verified'),
      team: field({ min: null, max: null, individual_ok: null }, 'missing', null),
    });
    assert.equal(judge(card).result, 'check');
  });

  test('ABI-X 해커톤: 인문캠만(확인 전) → 불가 + 확인 전 정보', () => {
    const card = openCard({ campus: field(['인문'], 'auto_verified', '참가자격: 명지대학교 소속 인문캠퍼스 재학생') });
    const judged = judge(card);
    assert.equal(judged.result, 'ineligible');
    assert.equal(judged.reasons[0].quote, '참가자격: 명지대학교 소속 인문캠퍼스 재학생');
    assert.equal(judged.reasons[0].unverified, true);
  });
});

test('잘못된 입력은 TypeError', () => {
  assert.throws(() => judgeCard(null, STUDENT), TypeError);
  assert.throws(() => judgeCard(openCard(), null), TypeError);
  assert.throws(() => judgeCard(openCard(), STUDENT, { now: new Date('x') }), TypeError);
});

// --- 위험 오판(불가인데 가능) 0건: 무작위 조합 검사 ---
// 판정 코드와 따로, "신청 가능이 나와도 되는 조건"을 직접 적은 기준과 비교한다.

function createRandom(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 2 ** 32;
  };
}

const STATUSES = ['ai', 'auto_verified', 'needs_check', 'human_verified', 'missing', '알 수 없는 상태'];
// 표준 낱말 + 원문 표현 + 허용 목록에 오면 해석할 수 없는 낱말
const ENROLLMENT_TERMS = ['재학', '휴학', '수료', '초과학기', '졸업유예', '편입생', '대학원생', '기존 수상자',
  '학부 재학생', '직전학기 평점 3.0 이상', '외국인 유학생'];
const STUDENT_ENROLLMENTS = ['재학', '휴학', '수료', '초과학기', '졸업유예', null];
const DEADLINE_TIMES = ['09:00', '15:00', '23:59'];
const NOW_TIMES = ['00:00', '08:59', '09:00', '09:01', '14:59', '15:00', '15:01', '23:58', '23:59'];

function randomCase(random) {
  const pick = (items) => items[Math.floor(random() * items.length)];
  const subset = (items) => items.filter(() => random() < 0.15);
  const statusOf = () => (random() < 0.92 ? 'human_verified' : pick(STATUSES));
  const day = () => String(14 + Math.floor(random() * 4));
  const hasTime = random() < 0.5;
  return {
    card: {
      id: 'random',
      fields: {
        apply: field({ start: null, end: `2026-10-${day()}${hasTime ? ` ${pick(DEADLINE_TIMES)}` : ''}`, time_given: hasTime }, statusOf()),
        // 빈 목록·문자열 학년처럼 형식이 깨진 값도 섞는다
        campus: field(pick(['any', 'any', ['인문'], ['자연'], ['인문', '자연'], []]), statusOf()),
        grades: field(pick(['any', 'any', [1], [1, 2], [3, 4], [1, 2, 3, 4], [], ['2']]), statusOf()),
        enrollment: field({ allowed: subset(ENROLLMENT_TERMS), excluded: subset(ENROLLMENT_TERMS) }, statusOf()),
        team: field({ min: pick([null, 2]), max: pick([null, 5]), individual_ok: pick([true, false, null]) }, statusOf()),
      },
    },
    student: {
      ...STUDENT,
      campus: pick(['인문', '자연', null]),
      grade: pick([1, 2, 3, 4, null]),
      is_transfer: pick([true, false, null]),
      enrollment: pick(STUDENT_ENROLLMENTS),
      participation: pick(['개인', '팀', null]),
    },
    now: new Date(`2026-10-${day()}T${pick(NOW_TIMES)}:00+09:00`),
  };
}

// 신청 가능이 나와도 되는 조건 (판정 코드를 보지 않고 계약 3절에서 직접 옮김):
// 핵심 항목 모두 검수 완료 + 해석할 수 없는 표현 없음 + 학생이 모든 제한을 확실히 통과
const STUDENT_TERMS = ['재학', '휴학', '수료', '초과학기', '졸업유예'];
const NOTE_ONLY_EXCLUSIONS = ['대학원생', '기존 수상자', '졸업'];

function isSurelyEligible(card, student, now) {
  const { apply, campus, grades, enrollment, team } = card.fields;
  if (KEY_FIELDS.some((name) => card.fields[name].status !== 'human_verified')) return false;

  const [endDate, endTime] = apply.value.end.split(' ');
  const todayKst = new Date(now.getTime() + 9 * 3600 * 1000).toISOString().slice(0, 10);
  const isOpen = apply.value.time_given
    ? now <= new Date(`${endDate}T${endTime}:00+09:00`)
    : todayKst < endDate;

  const campusOk = campus.value === 'any' || (student.campus !== null && campus.value.includes(student.campus));
  const gradeOk = grades.value === 'any' || (student.grade !== null && grades.value.includes(student.grade));

  const { allowed, excluded } = enrollment.value;
  const isReadable = allowed.every((term) => STUDENT_TERMS.includes(term))
    && excluded.every((term) => STUDENT_TERMS.includes(term) || term === '편입생' || NOTE_ONLY_EXCLUSIONS.includes(term));
  const hasStatusRule = allowed.length > 0 || excluded.some((term) => STUDENT_TERMS.includes(term));
  const statusOk = student.enrollment === null
    ? !hasStatusRule
    : !excluded.includes(student.enrollment) && (allowed.length === 0 || allowed.includes(student.enrollment));
  const transferOk = !excluded.includes('편입생') || student.is_transfer === false;
  const teamOk = team.value.individual_ok === true || student.participation === '팀';

  return isOpen && campusOk && gradeOk && isReadable && statusOk && transferOk && teamOk;
}

test('위험 오판 0건: 무작위 50,000건에서 신청 가능은 확실한 경우에만', () => {
  const random = createRandom(20261104);
  let eligibleCount = 0;
  for (let index = 0; index < 50000; index += 1) {
    const { card, student, now } = randomCase(random);
    const judged = judgeCard(card, student, { now });
    assert.ok(['eligible', 'team_needed', 'check', 'ineligible'].includes(judged.result));
    if (judged.result === 'eligible') {
      eligibleCount += 1;
      assert.ok(isSurelyEligible(card, student, now), JSON.stringify({ card, student, now }));
    }
  }
  // 신청 가능이 거의 안 나오면 검사가 의미 없다 (시드가 고정이라 매번 같은 수)
  assert.ok(eligibleCount > 200, `eligible ${eligibleCount}건`);
});
