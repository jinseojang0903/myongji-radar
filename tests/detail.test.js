// 카드 상세(#25) 계산 테스트: 항목 표·근거, 마감 변경 이력, 문의 초안, 캘린더 파일, 내 조건 저장.
// 예시 데이터(site/data/cards.sample.json)는 실제 공지에서 옮긴 값이라 화면 설계(목업)와 같은 결과가 나와야 한다.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { validateCardData } from '../site/js/cards.js';
import {
  FIELD_LABELS, fieldStatusBadge, fieldValueText, fieldRows, sourceLinks, applyLink, deadlineHistory, inquiryDraft, withParticle,
} from '../site/js/detail.js';
import { calendarItems, calendarItemText, buildIcs, icsFileName, stageLabel } from '../site/js/ics.js';
import {
  CONDITIONS_STORAGE_KEY, normalizeConditions, readSavedConditions, saveConditions, describeConditions, hasJudgedConditions,
} from '../site/js/conditions.js';

const SAMPLE = validateCardData(JSON.parse(readFileSync(new URL('../site/data/cards.sample.json', import.meta.url), 'utf8'))).data;
const AT_SAMPLE = new Date(SAMPLE.generated_at); // 2026-10-15 06:00 KST
const cardById = (id) => structuredClone(SAMPLE.cards.find((card) => card.id === id));
const STUDENT = { campus: '자연', grade: 2, is_transfer: false, enrollment: '재학', participation: '개인', deliverables: [], period: null, unknown_terms: [] };

// ---- 항목 표 ----

test('항목 상태 배지: 핵심 항목은 사람 대조 전이면 "확인 전"', () => {
  assert.deepEqual(fieldStatusBadge('apply', 'human_verified'), { kind: 'human', text: '✓ 검수 완료' });
  assert.deepEqual(fieldStatusBadge('enrollment', 'auto_verified'), { kind: 'before', text: '확인 전 · 자동 검증 통과' });
  assert.deepEqual(fieldStatusBadge('benefits', 'auto_verified'), { kind: 'auto', text: '자동 검증 통과' });
  assert.deepEqual(fieldStatusBadge('team', 'ai'), { kind: 'before', text: '확인 전 · AI 정리' });
  assert.deepEqual(fieldStatusBadge('mileage', 'ai'), { kind: 'before', text: 'AI 정리' });
  assert.deepEqual(fieldStatusBadge('campus', 'needs_check'), { kind: 'need', text: '! 원문 확인 필요' });
  assert.deepEqual(fieldStatusBadge('mileage', 'missing'), { kind: 'missing', text: '정보 없음' });
});

test('항목 값 글: 시각이 없으면 채우지 않고 "시각 미기재"', () => {
  assert.equal(fieldValueText('apply', { start: '2026-09-14', end: '2026-10-23', time_given: false }), '9/14(월) ~ 10/23(금) (시각 미기재)');
  assert.equal(fieldValueText('apply', { start: null, end: '2026-10-21 15:00', time_given: true }), '~ 10/21(수) 15:00');
  assert.equal(fieldValueText('stages', [{ name: '신청', end: '2026-10-23', time_given: false }, { name: '출품', end: '2026-11-11', time_given: false }]),
    '① 신청 ~10/23(금) (시각 미기재) → ② 출품 ~11/11(수) (시각 미기재)');
  assert.equal(fieldValueText('campus', 'any'), '캠퍼스 제한 없음');
  assert.equal(fieldValueText('campus', ['인문']), '인문캠퍼스');
  assert.equal(fieldValueText('grades', [1, 2]), '1·2학년');
  assert.equal(fieldValueText('enrollment', { allowed: ['재학'], excluded: ['휴학', '수료'] }), '대상: 재학 / 제외: 휴학, 수료');
  assert.equal(fieldValueText('enrollment', { allowed: [], excluded: [] }), '학적 제한 문구 없음');
  assert.equal(fieldValueText('team', { min: 2, max: 5, individual_ok: false }), '2~5인, 팀만');
  assert.equal(fieldValueText('contact', { dept: '잉글리쉬카페', email: 'ecafe@mju.ac.kr', phone: '' }), '잉글리쉬카페 · ecafe@mju.ac.kr');
  // 형식이 예상과 다르면 지어내지 않고 null
  assert.equal(fieldValueText('campus', []), null);
  assert.equal(fieldValueText('enrollment', { allowed: '재학' }), null);
  assert.equal(fieldValueText('apply', { end: '2026-13-01', time_given: false }), '~ 날짜 확인 필요');
});

test('항목 표: 역사 영상의 학적은 정보 충돌로 출처별 근거를 모두 보여 준다 (목업과 같음)', () => {
  const rows = fieldRows(cardById('history-video-5'), SAMPLE.boards);
  assert.deepEqual(rows.map((row) => row.name), FIELD_LABELS.map(([name]) => name));
  const enrollment = rows.find((row) => row.name === 'enrollment');
  assert.equal(enrollment.isConflict, true);
  assert.equal(enrollment.badge.kind, 'need');
  assert.equal(enrollment.evidence.length, 2);
  assert.deepEqual(enrollment.evidence.map((item) => [item.location, item.isImage]), [['본문', false], ['이미지', true]]);
  assert.ok(enrollment.evidence.every((item) => item.boardName && item.url?.startsWith('https://')));
  const mileage = rows.find((row) => row.name === 'mileage');
  assert.equal(mileage.text, null);
  assert.equal(mileage.badge.kind, 'missing');
  // 근거가 하나인 needs_check는 충돌이 아니다
  const evcTeam = fieldRows(cardById('evc-13-nature'), SAMPLE.boards).find((row) => row.name === 'team');
  assert.equal(evcTeam.isConflict, false);
  assert.equal(evcTeam.badge.kind, 'need');
});

test('근거·원문 링크: 허용되지 않는 주소는 링크로 만들지 않는다', () => {
  const card = cardById('history-video-5');
  card.fields.enrollment.evidence[0].source = 'javascript:alert(1)';
  card.sources[0].url = 'http://www.mju.ac.kr/plain-http';
  const enrollment = fieldRows(card, SAMPLE.boards).find((row) => row.name === 'enrollment');
  assert.equal(enrollment.evidence[0].url, null);
  assert.equal(enrollment.evidence[0].boardName, null);
  assert.equal(sourceLinks(card, SAMPLE.boards)[0].url, null);
  assert.ok(sourceLinks(card, SAMPLE.boards)[1].url.startsWith('https://'));
});

test('신청 링크는 호스트명을 함께 준다 (#21 보안 리뷰: 폼 주소 바꿔치기 대비)', () => {
  assert.deepEqual(applyLink(cardById('evc-13-nature')), { url: 'https://forms.gle/GgcCYLgYgvRr64Sz8', host: 'forms.gle' });
  assert.equal(applyLink(cardById('ai-literacy-2026')), null);
  const card = cardById('evc-13-nature');
  card.fields.how_to_apply.value.url = 'https://evil.example/forms';
  assert.equal(applyLink(card), null);
});

test('마감 변경 이력: 이전 값·새 값·출처 공지·확인 일시', () => {
  assert.deepEqual(deadlineHistory(cardById('history-video-5'), SAMPLE.boards), [{
    oldText: '신청 마감 9/30(수) (시각 미기재)',
    newText: '신청 마감 10/23(금) (시각 미기재)',
    oldSource: '대학혁신지원사업 10/2(금) 공지',
    newSource: '메인 일반공지 10/7(수) 공지',
    checkedAt: '2026-10-13',
  }]);
  assert.deepEqual(deadlineHistory(cardById('ai-literacy-2026'), SAMPLE.boards), []);
});

// ---- 문의 초안 ----

test('문의 초안: 공지에 없거나 원문 확인이 필요한 항목만 묻는다', () => {
  assert.equal(inquiryDraft(cardById('ai-literacy-2026')), null);

  const history = inquiryDraft(cardById('history-video-5'), STUDENT);
  assert.deepEqual(history.items, ['학적']);
  assert.match(history.text, /^안녕하세요\. 「제5회/);
  assert.ok(history.text.includes('「(휴학생, 졸업생, 초과학기생 신청 불가)」, 「(*휴학생, 졸업생, 졸업유예생, 기존 수상자 참여 불가)」'));
  // 인사 / 질문 / 자기소개 / 마무리를 줄로 나눈다 (이메일에 붙이기 쉽게)
  assert.deepEqual(history.text.split(String.fromCharCode(10)).map((line) => line.slice(0, 6)), ['안녕하세요.', '참가할 수 ', '저는 자연캠', '감사합니다.']);
  assert.ok(history.text.includes('저는 자연캠퍼스 2학년 재학생입니다.'));
  assert.equal(history.recipient, '방목기초교육대학');

  const evc = inquiryDraft(cardById('evc-13-nature'));
  assert.deepEqual(evc.items, ['학년', '학적', '팀 인원']);
  assert.ok(evc.text.includes('공지에서 참가할 수 있는 학년, 참가할 수 있는 학적(휴학생·초과학기생·졸업유예생 등)을 찾지 못했습니다. 알려 주시면 감사하겠습니다.'));
  assert.equal(evc.text.split('알려 주시면').length, 2, '감사 인사를 되풀이하지 않는다');
  assert.ok(evc.text.includes('개인으로 참가할 수 있는지와 팀 인원을 공지만으로는 정확히 알기 어려워'));
  assert.ok(!evc.text.includes('저는'), '조건이 없으면 자기소개를 넣지 않는다');
  assert.ok(inquiryDraft(cardById('evc-13-nature'), { ...STUDENT, enrollment: null }).text.includes('저는 자연캠퍼스 2학년 학생입니다.'));
  assert.equal(evc.recipient, '잉글리쉬카페 · ecafe@mju.ac.kr · 031-330-6643');

  const abix = inquiryDraft(cardById('abix-hackathon-2026'));
  assert.deepEqual(abix.items, ['팀 인원', '신청 방법']);
  assert.equal(abix.recipient, '원문 공지에 적힌 담당 부서');
});

test('조사: 받침에 따라 을/를·이/가', () => {
  assert.equal(withParticle('캠퍼스', '을', '를'), '캠퍼스를');
  assert.equal(withParticle('학년', '을', '를'), '학년을');
  assert.equal(withParticle('신청 기간(마감 일시)', '이', '가'), '신청 기간(마감 일시)이');
  assert.equal(withParticle('방법', '이', '가'), '방법이');
});

// ---- 캘린더 ----

test('캘린더 일정: 단계별로, 시각이 없으면 하루 종일', () => {
  const items = calendarItems(cardById('history-video-5'), AT_SAMPLE);
  assert.deepEqual(items.map(({ label, date, time, isPast, isVerified }) => ({ label, date, time, isPast, isVerified })), [
    { label: '참가 신청 마감', date: '2026-10-23', time: null, isPast: false, isVerified: false },
    { label: '작품 출품 마감', date: '2026-11-11', time: null, isPast: false, isVerified: false },
  ]);
  assert.equal(calendarItemText(items[0]), '10/23(금) (시각 미기재 → 하루 종일)');
  const literacy = calendarItems(cardById('ai-literacy-2026'), AT_SAMPLE);
  assert.deepEqual(literacy.map(({ time, isVerified }) => ({ time, isVerified })), [{ time: '15:00', isVerified: true }]);
  // needs_check 단계는 이유를 함께 준다
  assert.ok(calendarItems(cardById('evc-13-nature'), AT_SAMPLE).every((item) => item.note === '출품 마감 연도 추정'));
  assert.equal(stageLabel('본선'), '본선');
});

test('캘린더 일정: 지난 일정 표시, 단계가 없으면 신청 마감, 읽을 수 없는 날짜는 뺀다', () => {
  const late = new Date('2026-10-24T00:00:00+09:00');
  assert.deepEqual(calendarItems(cardById('history-video-5'), late).map((item) => item.isPast), [true, false]);
  // 시각이 있는 마감은 그 시각이 지나야 지난 일정
  assert.equal(calendarItems(cardById('ai-literacy-2026'), new Date('2026-10-21T14:59:00+09:00'))[0].isPast, false);
  assert.equal(calendarItems(cardById('ai-literacy-2026'), new Date('2026-10-21T15:01:00+09:00'))[0].isPast, true);

  const noStages = cardById('history-video-5');
  noStages.fields.stages = { value: null, status: 'missing', evidence: [] };
  assert.deepEqual(calendarItems(noStages, AT_SAMPLE).map((item) => [item.label, item.date, item.isVerified]), [['참가 신청 마감', '2026-10-23', true]]);

  const broken = cardById('history-video-5');
  broken.fields.stages.value = [{ name: '신청', end: '10월 23일', time_given: false }, { name: '출품', end: '2026-11-11', time_given: false },
    { name: '출품', end: '2026-11-11', time_given: false }];
  assert.deepEqual(calendarItems(broken, AT_SAMPLE).map((item) => item.date), ['2026-11-11']);
});

function unfold(ics) {
  return ics.replace(/\r\n /g, '');
}

test('.ics: RFC 5545 형식 (CRLF, 75바이트 접기, 하루 종일·시각 일정, 알림)', () => {
  const card = cardById('history-video-5');
  const ics = buildIcs(card, calendarItems(card, AT_SAMPLE), { now: new Date('2026-10-15T00:00:00Z') });
  assert.ok(ics.endsWith('\r\n'));
  assert.ok(!/[^\r]\n/.test(ics), '줄바꿈은 모두 CRLF');
  const encoder = new TextEncoder();
  for (const line of ics.split('\r\n')) assert.ok(encoder.encode(line).length <= 75, line);
  const lines = unfold(ics).split('\r\n');
  assert.equal(lines[0], 'BEGIN:VCALENDAR');
  assert.equal(lines.filter((line) => line === 'BEGIN:VEVENT').length, 2);
  assert.ok(lines.includes('DTSTART;VALUE=DATE:20261023'));
  assert.ok(lines.includes('DTEND;VALUE=DATE:20261024'));
  assert.ok(lines.includes('DTSTART;VALUE=DATE:20261111'));
  assert.ok(lines.includes('DTSTAMP:20261015T000000Z'));
  // 하루 종일 일정은 0시 기준이라 3일 전·1일 전 오전 9시로 맞춘다
  assert.equal(lines.filter((line) => line === 'TRIGGER:-P2DT15H').length, 2);
  assert.equal(lines.filter((line) => line === 'TRIGGER:-PT15H').length, 2);
  assert.ok(lines.includes('SEQUENCE:1'), '마감 변경 1번 → SEQUENCE 1');
  assert.ok(lines.some((line) => line.startsWith('SUMMARY:[마감] 제5회') && line.endsWith('· 참가 신청 마감')));
  assert.ok(lines.some((line) => line === 'URL:https://www.mju.ac.kr/bbs/mjukr/141/236150/artclView.do'));
  // 시각 미기재 마감에 시각을 채우지 않는다
  assert.ok(!lines.some((line) => line.startsWith('DTSTART:') || line.startsWith('DTEND:')), '시각이 든 DTSTART·DTEND가 없어야 한다');
  // UID는 일정마다 다르고 다시 만들어도 같다
  const uids = lines.filter((line) => line.startsWith('UID:'));
  assert.equal(new Set(uids).size, 2);
  assert.deepEqual(uids, unfold(buildIcs(card, calendarItems(card, AT_SAMPLE))).split('\r\n').filter((line) => line.startsWith('UID:')));
  assert.deepEqual(uids, ['UID:history-video-5-apply@myongji-radar', 'UID:history-video-5-submit@myongji-radar']);
  assert.equal(icsFileName(card), 'history-video-5.ics');
});

test('.ics: 시각이 있으면 한국 시간을 UTC로, 끝 시각은 넣지 않는다. 알림은 끌 수 있다', () => {
  const card = cardById('ai-literacy-2026');
  const lines = unfold(buildIcs(card, calendarItems(card, AT_SAMPLE), { withAlarms: false })).split('\r\n');
  assert.ok(lines.includes('DTSTART:20261021T060000Z'));
  assert.ok(!lines.some((line) => line.startsWith('TRIGGER:')), '알림을 끄면 TRIGGER가 없다');
  assert.ok(!lines.some((line) => line.startsWith('DTEND')));
  assert.ok(!lines.includes('BEGIN:VALARM'));
});

test('.ics: 공지 문자열의 줄바꿈·쉼표·세미콜론이 속성을 깨지 않는다', () => {
  const card = cardById('ai-literacy-2026');
  card.title = '대회, 이름; 줄\r\nBEGIN:VEVENT\\끝\u0007';
  card.sources[0].url = 'javascript:alert(1)';
  const lines = unfold(buildIcs(card, calendarItems(card, AT_SAMPLE))).split('\r\n');
  assert.equal(lines.filter((line) => line === 'BEGIN:VEVENT').length, 1, '제목 속 BEGIN:VEVENT가 새 일정이 되면 안 된다');
  const summary = lines.find((line) => line.startsWith('SUMMARY:'));
  assert.equal(summary, 'SUMMARY:[마감] 대회\\, 이름\\; 줄\\nBEGIN:VEVENT\\\\끝 · 참가 신청 마감');
  assert.ok(!lines.some((line) => line.startsWith('URL:')), '허용되지 않는 원문 주소는 넣지 않는다');
  assert.throws(() => buildIcs(card, []), RangeError);
});

// ---- 내 조건 저장 ----

function fakeStorage(initial = {}) {
  const store = new Map(Object.entries(initial));
  return { getItem: (key) => (store.has(key) ? store.get(key) : null), setItem: (key, value) => store.set(key, String(value)), store };
}

test('내 조건 저장: 해석된 값만, unknown_terms는 저장하지 않는다', () => {
  const storage = fakeStorage();
  assert.equal(saveConditions({ ...STUDENT, deliverables: ['영상'], unknown_terms: ['홍길동 20231234'] }, storage), true);
  const stored = JSON.parse(storage.store.get(CONDITIONS_STORAGE_KEY));
  assert.ok(!('unknown_terms' in stored));
  assert.ok(!JSON.stringify(stored).includes('홍길동'));
  assert.deepEqual(readSavedConditions(storage), { ...STUDENT, deliverables: ['영상'], unknown_terms: [] });
  assert.equal(saveConditions({ ...STUDENT, campus: '서울' }, storage), false);
});

test('내 조건 읽기: 없거나 깨졌거나 범위 밖이면 null, 저장소가 막혀도 멈추지 않는다', () => {
  assert.equal(readSavedConditions(fakeStorage()), null);
  assert.equal(readSavedConditions(fakeStorage({ [CONDITIONS_STORAGE_KEY]: '{깨진' })), null);
  assert.equal(readSavedConditions(fakeStorage({ [CONDITIONS_STORAGE_KEY]: JSON.stringify({ grade: 7 }) })), null);
  assert.equal(readSavedConditions(fakeStorage({ [CONDITIONS_STORAGE_KEY]: JSON.stringify({ deliverables: ['그림'] }) })), null);
  assert.equal(readSavedConditions({ getItem: () => { throw new Error('막힘'); } }), null);
  assert.equal(readSavedConditions(undefined), null);
  // 빠진 키는 "말하지 않음"(null)
  assert.deepEqual(readSavedConditions(fakeStorage({ [CONDITIONS_STORAGE_KEY]: JSON.stringify({ campus: '인문' }) })),
    { campus: '인문', grade: null, is_transfer: null, enrollment: null, participation: null, deliverables: [], period: null, unknown_terms: [] });
  assert.equal(saveConditions(STUDENT, { setItem: () => { throw new Error('가득 참'); } }), false);
});

test('내 조건 설명과 판정 조건 여부', () => {
  assert.equal(describeConditions(STUDENT), '자연캠퍼스 2학년 재학생, 개인 참가');
  assert.equal(describeConditions(STUDENT, { withParticipation: false }), '자연캠퍼스 2학년 재학생');
  assert.equal(describeConditions({ ...STUDENT, is_transfer: true, enrollment: '초과학기' }, { withParticipation: false }), '자연캠퍼스 2학년 편입생 초과학기생');
  assert.equal(describeConditions(normalizeConditions({ period: '이번주' })), null);
  assert.equal(hasJudgedConditions(normalizeConditions({ period: '이번주' })), false);
  assert.equal(hasJudgedConditions(normalizeConditions({ is_transfer: false })), true);
  assert.equal(hasJudgedConditions(null), false);
});

// ---- 리뷰 반영 (코드·보안) ----

test('.ics UID: 마감이 바뀌어도 같은 일정으로 갱신된다 (날짜를 UID에 넣지 않음)', () => {
  const before = cardById('history-video-5');
  const after = cardById('history-video-5');
  after.fields.stages.value[0].end = '2026-10-30';
  after.deadline_history.push({ ...after.deadline_history[0], old: '2026-10-23', new: '2026-10-30' });
  const idLines = (card) => unfold(buildIcs(card, calendarItems(card, AT_SAMPLE))).split('\r\n').filter((line) => /^(UID|SEQUENCE):/.test(line));
  assert.deepEqual(idLines(before).filter((line) => line.startsWith('UID')), idLines(after).filter((line) => line.startsWith('UID')));
  assert.ok(idLines(after).includes('SEQUENCE:2'), '마감이 바뀔 때마다 SEQUENCE가 커진다');
});

test('.ics: 마감이 아닌 단계(발표·본선)는 [마감]을 붙이지 않는다. 단계에 신청이 없으면 신청 마감을 더한다', () => {
  const card = cardById('history-video-5');
  card.fields.stages.value = [{ name: '출품', end: '2026-11-11', time_given: false }, { name: '발표', end: '2026-11-20', time_given: false }];
  const items = calendarItems(card, AT_SAMPLE);
  assert.deepEqual(items.map((entry) => [entry.key, entry.label, entry.isDeadline]),
    [['apply', '참가 신청 마감', true], ['submit', '작품 출품 마감', true], ['announce', '발표', false]]);
  const summaries = unfold(buildIcs(card, items)).split('\r\n').filter((line) => line.startsWith('SUMMARY:'));
  assert.ok(summaries[2].startsWith('SUMMARY:[일정] '));
  assert.ok(summaries.slice(0, 2).every((line) => line.startsWith('SUMMARY:[마감] ')));
  // 같은 이름 단계가 둘이면 번호를 붙여 UID가 겹치지 않는다
  card.fields.stages.value = [{ name: '기타', end: '2026-11-01', time_given: false }, { name: '기타', end: '2026-11-02', time_given: false }];
  assert.deepEqual(calendarItems(card, AT_SAMPLE).map((entry) => entry.key), ['apply', 'other', 'other2']);
});

test('.ics 보안: 제목·이유 속 주소는 지우고, 원문 주소는 학교 도메인만, 유니코드 줄바꿈도 이스케이프', () => {
  const card = cardById('evc-13-nature');
  const LS = String.fromCharCode(0x2028);
  const RLO = String.fromCharCode(0x202e);
  card.title = `가짜 공지 https://evil.example/login www.evil.example${LS}BEGIN:VEVENT${RLO}`;
  card.fields.stages.note = '연도 추정 http://phish.example';
  const ics = unfold(buildIcs(card, calendarItems(card, AT_SAMPLE)));
  assert.ok(!/evil\.example|phish\.example/.test(ics), '공지 속 주소가 달력 설명에 남지 않는다');
  assert.ok(ics.includes('(주소 생략)'));
  assert.ok(!ics.includes(LS) && !ics.includes(RLO));
  assert.equal(ics.split('\r\n').filter((line) => line === 'BEGIN:VEVENT').length, 2);
  assert.ok(ics.includes('URL:https://www.mju.ac.kr/'), '학교 원문 주소는 넣는다');

  const formOnly = cardById('evc-13-nature');
  formOnly.sources[0].url = 'https://docs.google.com/forms/d/abc';
  assert.ok(!unfold(buildIcs(formOnly, calendarItems(formOnly, AT_SAMPLE))).includes('URL:'), '구글 폼 주소는 달력 URL로 넣지 않는다');
});

test('정보 충돌은 근거 문장이 서로 다를 때만 (같은 문장이 두 곳에 나온 것은 충돌이 아님)', () => {
  const card = cardById('history-video-5');
  const quote = card.fields.enrollment.evidence[0].quote;
  card.fields.enrollment.evidence[1].quote = ` ${quote} `;
  const row = fieldRows(card, SAMPLE.boards).find((entry) => entry.name === 'enrollment');
  assert.equal(row.isConflict, false);
  assert.ok(row.text.startsWith('대상: 재학'), '충돌 여부와 상관없이 정리한 값은 보여 준다');
  assert.ok(inquiryDraft(card).text.includes('공지만으로는 정확히 알기 어려워'), '같은 문장이면 "서로 다르게"라고 하지 않는다');
});

test('문의 초안: 값이 비어 있는 항목도 묻고, 보이지 않는 글자를 지우고, 이상한 이메일은 받는 곳에 쓰지 않는다', () => {
  const card = cardById('ai-literacy-2026');
  card.fields.team = { value: null, status: 'ai', evidence: [] };
  const RLO = String.fromCharCode(0x202e);
  const ZWSP = String.fromCharCode(0x200b);
  card.title = `AI${ZWSP} 리터러시${RLO}`;
  card.fields.contact.value = { dept: `교수${RLO}학습센터`, email: 'ctl@mju', phone: '' };
  const draft = inquiryDraft(card);
  assert.deepEqual(draft.items, ['팀 인원']);
  assert.ok(draft.text.includes('「AI 리터러시」'));
  assert.ok(!draft.text.includes(RLO) && !draft.text.includes(ZWSP));
  assert.equal(draft.recipient, '교수학습센터');
  assert.equal(withParticle('', '을', '를'), '');
});

test('신청기간: 시작 시각이 값에 있으면 보여 준다', () => {
  assert.equal(fieldValueText('apply', { start: '2026-10-01 09:00', end: '2026-10-21 15:00', time_given: true }), '10/1(목) 09:00 ~ 10/21(수) 15:00');
});

test('내 조건 읽기: 브라우저가 저장소 접근을 막아도(접근 자체가 오류) null', () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, get() { throw new Error('SecurityError'); } });
  try {
    assert.equal(readSavedConditions(), null);
    assert.equal(saveConditions(STUDENT), false);
    // 정상인 브라우저 저장소는 인자 없이도 쓴다
    const storage = fakeStorage();
    Object.defineProperty(globalThis, 'localStorage', { configurable: true, get: () => storage });
    assert.equal(saveConditions(STUDENT), true);
    assert.deepEqual(readSavedConditions(), STUDENT);
  } finally {
    if (original) Object.defineProperty(globalThis, 'localStorage', original);
    else delete globalThis.localStorage;
  }
});
