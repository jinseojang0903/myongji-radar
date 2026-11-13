// 홈 화면 계산(site/js/cards.js), 링크 검사(html.js), 데이터 읽기(data.js), 예시 데이터 형식 테스트.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  DELIVERABLE_TAGS, validateCardData, referenceDate, deadlineOf, ddayLabel, formatDeadline,
  cardBadges, latestDeadlineChange, summaryLine, listCards, boardNames, bannerText, STALE_HOURS, organizerLine, emptyListMessage,
} from '../site/js/cards.js';

test('빈 목록 안내는 이유를 나눈다: 모은 대회 없음 / 모두 마감 / 필터 때문 (#104)', () => {
  const filtered = '필터를 줄여 보세요.';
  assert.match(emptyListMessage({ cardCount: 0, unavailableCount: 0 }, filtered), /아직 모은 대회가 없습니다/);
  assert.match(emptyListMessage({ cardCount: 3, unavailableCount: 3 }, filtered), /지금 신청할 수 있는 대회가 없습니다/);
  assert.equal(emptyListMessage({ cardCount: 3, unavailableCount: 1 }, filtered), filtered);
  // 예시 데이터를 실제 오늘 날짜로 보면 모두 마감이다: 필터를 고르지 않았는데 "필터를 줄여 보세요"가 나오면 안 된다
  const { items, closedCount } = listCards(SAMPLE.cards, { sort: 'deadline', tags: [] }, new Date('2027-01-01T00:00:00+09:00'));
  assert.equal(items.length, 0);
  assert.notEqual(emptyListMessage({ cardCount: SAMPLE.cards.length, unavailableCount: closedCount }, filtered), filtered);
});

test('상세 화면의 주관·구분 줄은 있는 값만 잇고, 둘 다 없으면 그리지 않는다 (#104)', () => {
  assert.equal(organizerLine({ organizer: '교수학습센터', scope: '교내' }), '교수학습센터 · 교내');
  assert.equal(organizerLine({ organizer: '', scope: '교내' }), '교내');
  assert.equal(organizerLine({ organizer: null, scope: '외부' }), '외부');
  assert.equal(organizerLine({ organizer: '  ', scope: null }), null);
  assert.equal(organizerLine({}), null);
});
import { safeUrl } from '../site/js/html.js';
import { loadCardData } from '../site/js/data.js';
import { judgeCard, KEY_FIELDS } from '../site/js/rules.js';

const SAMPLE = JSON.parse(readFileSync(new URL('../site/data/cards.sample.json', import.meta.url), 'utf8'));
const AT_SAMPLE = new Date(SAMPLE.generated_at); // 2026-10-15 06:00 KST
const cardById = (id) => SAMPLE.cards.find((card) => card.id === id);

function field(value, status = 'human_verified') {
  return { value, status, evidence: [] };
}

function makeCard(overrides = {}, extra = {}) {
  return {
    id: 'c1', title: '시험 대회', organizer: '센터', scope: '교내',
    sources: [{ board_id: 'b1', url: 'https://www.mju.ac.kr/x', title: 't', posted_at: '2026-10-01' }],
    fields: {
      apply: field({ start: null, end: '2026-10-20', time_given: false }),
      campus: field('any'), grades: field('any'),
      enrollment: field({ allowed: [], excluded: [] }),
      team: field({ min: null, max: 3, individual_ok: true }),
      ...overrides,
    },
    deadline_history: [],
    ...extra,
  };
}

test.describe('예시 데이터가 계약 형식을 따른다', () => {
  test('최상위 형식과 예시 표시', () => {
    assert.equal(SAMPLE.sample, true);
    assert.ok(SAMPLE.sample_note.includes('수집기 데이터가 아닙니다'));
    const { rejected } = validateCardData(SAMPLE);
    assert.deepEqual(rejected, []);
  });

  test('모든 카드에 핵심 항목이 있고, 근거의 출처는 그 카드의 공지, 게시판 id는 boards에 있다', () => {
    const boardIds = new Set(SAMPLE.boards.map((board) => board.id));
    for (const card of SAMPLE.cards) {
      const sourceUrls = new Set(card.sources.map((source) => source.url));
      for (const name of KEY_FIELDS) assert.ok(card.fields[name], `${card.id}.${name}`);
      for (const source of card.sources) assert.ok(boardIds.has(source.board_id), `${card.id} ${source.board_id}`);
      for (const [name, item] of Object.entries(card.fields)) {
        if (item.status === 'missing') assert.equal(item.value, null, `${card.id}.${name}`);
        for (const evidence of item.evidence) {
          assert.ok(sourceUrls.has(evidence.source), `${card.id}.${name} 근거 출처`);
          assert.ok(['제목', '본문', '이미지', '첨부'].includes(evidence.location));
        }
      }
      for (const tag of card.fields.deliverables.value?.tags ?? []) assert.ok(DELIVERABLE_TAGS.includes(tag), tag);
    }
  });

  test('자동 검증 통과·검수 완료 항목에는 근거 문장이 있다 (계약: auto_verified = 근거 문장 실재)', () => {
    for (const card of SAMPLE.cards) {
      for (const [name, item] of Object.entries(card.fields)) {
        if (['auto_verified', 'human_verified'].includes(item.status)) assert.ok(item.evidence.length > 0, `${card.id}.${name}`);
      }
    }
  });

  test('모든 링크가 https·허용 도메인이다', () => {
    const urls = [
      ...SAMPLE.boards.map((board) => board.list_url).filter(Boolean),
      ...SAMPLE.cards.flatMap((card) => card.sources.map((source) => source.url)),
      ...SAMPLE.cards.map((card) => card.fields.how_to_apply?.value?.url).filter(Boolean),
    ];
    for (const url of urls) assert.ok(safeUrl(url), url);
  });

  test('판정 규칙으로 판정하면 화면 설계(PR #28)와 같다: 자연캠 2학년 재학 개인', () => {
    const student = { campus: '자연', grade: 2, is_transfer: false, enrollment: '재학', participation: '개인' };
    const results = Object.fromEntries(SAMPLE.cards.map((card) => [card.id, judgeCard(card, student, { now: AT_SAMPLE }).result]));
    assert.deepEqual(results, {
      'ai-literacy-2026': 'eligible',
      'history-video-5': 'check',
      'evc-13-nature': 'check',
      'abix-hackathon-2026': 'ineligible',
    });
  });
});

test.describe('데이터 검사', () => {
  test('최상위 형식이 틀리면 오류', () => {
    assert.throws(() => validateCardData(null));
    assert.throws(() => validateCardData({ cards: [], boards: [] }), /generated_at/);
    assert.throws(() => validateCardData({ cards: {}, boards: [], generated_at: '2026-10-15' }), /cards/);
  });

  test('id·게시일·마감 변경 날짜·게시판 형식이 틀리면 그 카드·게시판만 뺀다 (리뷰 지적: 카드 하나로 화면 전체가 멈춤)', () => {
    const withId = (id) => ({ ...makeCard(), id });
    const badPosted = makeCard();
    badPosted.sources = [{ ...badPosted.sources[0], posted_at: 20261001 }];
    const badHistory = makeCard({}, { deadline_history: [{ field: 'apply_end', old: '9월 30일', new: '2026-10-23' }] });
    const { data, rejected } = validateCardData({
      generated_at: '2026-10-15',
      boards: [{ id: 'b1', name: '게시판', list_url: null }, { id: 'b2' }, null],
      cards: [makeCard(), withId("a'(b)"), withId('A-UPPER'), withId('x'.repeat(65)), { ...badPosted, id: 'p' }, { ...badHistory, id: 'h' }],
    });
    assert.deepEqual(data.cards.map((card) => card.id), ['c1']);
    assert.equal(data.boards.length, 1);
    assert.equal(rejected.length, 7);
  });

  test('형식이 틀린 카드만 빼고 이유를 남긴다', () => {
    const broken = makeCard({ campus: { value: 'any', status: 'verified', evidence: [] } });
    broken.id = 'broken';
    const { data, rejected } = validateCardData({ generated_at: '2026-10-15', boards: [], cards: [makeCard(), broken, null] });
    assert.equal(data.cards.length, 1);
    assert.deepEqual(rejected.map((item) => item.id), ['broken', '(id 없음)']);
  });
});

test.describe('기준일과 마감', () => {
  test('예시 데이터는 generated_at을, 실제 데이터는 지금을 기준일로', () => {
    const now = new Date('2026-11-04T10:00:00+09:00');
    assert.equal(referenceDate(SAMPLE, now).toISOString(), AT_SAMPLE.toISOString());
    assert.equal(referenceDate({ ...SAMPLE, sample: false }, now), now);
    // 예시 파일을 읽었으면 파일에 sample 표시가 없어도 예시 기준일
    assert.equal(referenceDate({ ...SAMPLE, sample: false }, now, true).toISOString(), AT_SAMPLE.toISOString());
  });

  test('목록의 마감 여부는 판정(rules.js)의 마감과 같다', () => {
    const student = { campus: null, grade: null, is_transfer: null, enrollment: null, participation: '팀' };
    const ends = [['2026-10-15', false], ['2026-10-16', false], ['2026-10-15 15:00', true], ['2026-10-16 09:00', true]];
    const times = ['2026-10-15T14:59:00+09:00', '2026-10-15T15:00:00+09:00', '2026-10-15T15:01:00+09:00',
      '2026-10-15T23:59:00+09:00', '2026-10-16T00:00:00+09:00', '2026-10-15T15:30:00Z', '2026-10-17T09:00:00+09:00'];
    for (const [end, timeGiven] of ends) {
      const card = makeCard({ apply: field({ start: null, end, time_given: timeGiven }) });
      for (const time of times) {
        const now = new Date(time);
        const isClosedInList = deadlineOf(card, now).state === 'closed';
        const isClosedInJudge = judgeCard(card, student, { now }).reasons.some((reason) => reason.field === 'apply' && reason.outcome === 'fail');
        assert.equal(isClosedInList, isClosedInJudge, `${end} @ ${time}`);
      }
    }
  });

  test('UTC로는 전날이어도 한국 시간 날짜로 D-day를 센다', () => {
    const card = makeCard({ apply: field({ start: null, end: '2026-10-16', time_given: false }) });
    assert.equal(ddayLabel(deadlineOf(card, new Date('2026-10-15T15:30:00Z'))).text, 'D-day'); // KST 10/16 00:30
  });

  test('D-day와 7일 이내 강조', () => {
    const at = new Date('2026-10-15T09:00:00+09:00');
    const label = (end, timeGiven = false) => ddayLabel(deadlineOf(makeCard({ apply: field({ start: null, end, time_given: timeGiven }) }), at));
    assert.deepEqual(label('2026-10-15'), { text: 'D-day', isUrgent: true });
    assert.deepEqual(label('2026-10-22'), { text: 'D-7', isUrgent: true });
    assert.deepEqual(label('2026-10-23'), { text: 'D-8', isUrgent: false });
    assert.equal(label('2026-10-14').text, '마감됨'); // 목록에서는 빠지지만 상세(#25)에서 쓴다
    assert.equal(label('10월 20일').text, '마감일 확인 필요');
    assert.equal(deadlineOf(makeCard({ apply: field({ start: null, end: '2026-10-15 08:00', time_given: true }) }), at).state, 'closed');
    assert.equal(deadlineOf(makeCard({ apply: field(null, 'missing') }), at).state, 'none');
    assert.equal(deadlineOf(makeCard({ apply: field({ start: null, end: '10월 20일', time_given: false }) }), at).state, 'unreadable');
  });

  test('마감 표시: 시각이 없으면 시각 미기재 (23:59 등으로 채우지 않음)', () => {
    const at = new Date('2026-10-15T09:00:00+09:00');
    assert.equal(formatDeadline(deadlineOf(cardById('ai-literacy-2026'), at)), '10/21(수) 15:00');
    assert.equal(formatDeadline(deadlineOf(cardById('history-video-5'), at)), '10/23(금) (시각 미기재)');
  });
});

test.describe('카드 단위 배지 (화면설계.md 4절)', () => {
  test('핵심 항목 모두 검수 완료일 때만 검수 완료', () => {
    assert.deepEqual(cardBadges(cardById('ai-literacy-2026')), [{ kind: 'human', text: '✓ 검수 완료' }]);
  });

  test('원문 확인 필요 항목이 있으면 검수 완료를 붙이지 않는다 (PR #28 리뷰)', () => {
    const badges = cardBadges(cardById('history-video-5')).map((badge) => badge.text);
    assert.ok(badges.includes('! 원문 확인 필요'));
    assert.ok(!badges.includes('✓ 검수 완료'));
  });

  test('확인 전은 신청기간·자격을 나눠, 공지에 없는 조건은 따로', () => {
    assert.deepEqual(cardBadges(cardById('evc-13-nature')).map((badge) => badge.text),
      ['! 원문 확인 필요', '신청기간 확인 전', '자격 확인 전', '공지에 없는 조건 있음']);
  });
});

test.describe('목록 필터와 정렬', () => {
  const list = (filters) => listCards(SAMPLE.cards, filters, AT_SAMPLE).items.map((item) => item.card.id);

  test('기본: 마감 가까운 순', () => {
    assert.deepEqual(list({}), ['evc-13-nature', 'ai-literacy-2026', 'history-video-5', 'abix-hackathon-2026']);
  });

  test('같은 날 마감이면 시각이 있는 쪽이 먼저 (시각 미기재는 그날 끝으로)', () => {
    const timed = { ...makeCard({ apply: field({ start: null, end: '2026-10-20 15:00', time_given: true }) }), id: 'timed', title: '나' };
    const untimed = { ...makeCard({ apply: field({ start: null, end: '2026-10-20', time_given: false }) }), id: 'untimed', title: '가' };
    const ids = listCards([untimed, timed], {}, AT_SAMPLE).items.map((item) => item.card.id);
    assert.deepEqual(ids, ['timed', 'untimed']);
  });

  test('새로 올라온 순: 가장 최근 공지의 게시일', () => {
    assert.deepEqual(list({ sort: 'newest' }), ['abix-hackathon-2026', 'history-video-5', 'ai-literacy-2026', 'evc-13-nature']);
  });

  test('마감 7일 이내, 마감 변경된 대회', () => {
    assert.deepEqual(list({ urgentOnly: true }), ['evc-13-nature', 'ai-literacy-2026']);
    assert.deepEqual(list({ changedOnly: true }), ['history-video-5']);
  });

  test('제출물: 하나라도 맞으면', () => {
    assert.deepEqual(list({ tags: ['영상'] }), ['evc-13-nature', 'history-video-5']);
    assert.deepEqual(list({ tags: ['영상', '발표'] }), ['evc-13-nature', 'ai-literacy-2026', 'history-video-5']);
  });

  test('캠퍼스: 그 캠퍼스를 제외하는 대회만 숨긴다', () => {
    assert.deepEqual(list({ campus: '자연' }), ['evc-13-nature', 'ai-literacy-2026', 'history-video-5']);
    assert.deepEqual(list({ campus: '인문' }), ['ai-literacy-2026', 'history-video-5', 'abix-hackathon-2026']);
    const unclear = makeCard({ campus: field(['인문'], 'needs_check') });
    assert.equal(listCards([unclear], { campus: '자연' }, AT_SAMPLE).items.length, 1);
  });

  test('마감 지난 대회는 빼고 건수를 센다', () => {
    const result = listCards(SAMPLE.cards, {}, new Date('2026-10-22T09:00:00+09:00'));
    assert.deepEqual(result.items.map((item) => item.card.id), ['history-video-5', 'abix-hackathon-2026']);
    assert.equal(result.closedCount, 2);
  });
});

test('한 줄 요약, 마감 변경, 게시판 이름', () => {
  const at = AT_SAMPLE;
  const literacy = cardById('ai-literacy-2026');
  assert.equal(summaryLine(literacy, deadlineOf(literacy, at)), '신청 마감 10/21(수) 15:00 · 3인 이내, 개인 지원 가능 · 창의 10점');
  const history = cardById('history-video-5');
  assert.equal(summaryLine(history, deadlineOf(history, at)), '신청 마감 10/23(금) (시각 미기재) · 2~5인, 팀만');
  assert.deepEqual(latestDeadlineChange(history), history.deadline_history[0]);
  assert.deepEqual(boardNames(history, SAMPLE.boards), ['메인 일반공지', '대학혁신지원사업']);
  assert.deepEqual(boardNames(makeCard(), []), ['b1']);
});

test('링크 검사: https와 허용 도메인만', () => {
  assert.equal(safeUrl('https://www.mju.ac.kr/bbs/mjukr/141/235913/artclView.do'), 'https://www.mju.ac.kr/bbs/mjukr/141/235913/artclView.do');
  assert.ok(safeUrl('https://ctl.mju.ac.kr/x'));
  assert.ok(safeUrl('https://forms.gle/abc'));
  for (const bad of [
    'http://www.mju.ac.kr/x', 'javascript:alert(1)', 'https://evil.com/mju.ac.kr', 'https://mju.ac.kr.evil.com/',
    'https://notmju.ac.kr/', 'https://user:pw@www.mju.ac.kr/', 'data:text/html,hi', '', null, 42, '//www.mju.ac.kr/x',
  ]) {
    assert.equal(safeUrl(bad), null, String(bad));
  }
});

test.describe('데이터 읽기', () => {
  const response = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });

  test('실제 데이터가 있으면 그것을', async () => {
    const result = await loadCardData(async (url) => (url === 'data/cards.json' ? response(200, { a: 1 }) : response(500)));
    assert.deepEqual(result, { data: { a: 1 }, source: 'real' });
  });

  test('실제 데이터가 404면 예시를', async () => {
    const result = await loadCardData(async (url) => (url === 'data/cards.json' ? response(404) : response(200, { b: 2 })));
    assert.equal(result.source, 'sample');
  });

  test('404가 아닌 실패는 예시로 덮지 않고 오류', async () => {
    await assert.rejects(loadCardData(async () => response(500)), /HTTP 500/);
  });
});

test('안내 띠: 예시 안내, 오래된 실제 데이터 경고 (#19: 갱신이 멈추면 어제 카드가 그대로 공개된다)', () => {
  const data = { generated_at: '2026-11-05T21:00:00+09:00' };
  const at = (hours) => new Date(Date.parse(data.generated_at) + hours * 3600 * 1000);
  assert.equal(bannerText({ ...data, sample_note: '예시입니다' }, at(1000), true), '예시입니다', '예시는 오래돼도 예시 안내');
  assert.equal(bannerText(data, at(0), true), '예시 데이터입니다.');
  assert.equal(STALE_HOURS, 36);
  assert.equal(bannerText(data, at(24), false), null, '하루 전 갱신은 정상');
  assert.equal(bannerText(data, at(STALE_HOURS), false), null, '36시간까지는 정상 (하루 한 번을 건너뛴 정도)');
  assert.match(bannerText(data, at(STALE_HOURS + 1), false), /^대회 정보가 1일 전\(2026-11-05 21:00\) 기준입니다\. .*원문 공지를 꼭 확인/);
  assert.match(bannerText(data, at(24 * 5 + 2), false), /5일 전/);
  assert.equal(bannerText(data, at(-48), false), null, '시계가 어긋나 미래 시각이면 경고하지 않는다');
});
