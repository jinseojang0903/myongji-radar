// 캘린더 파일(.ics, RFC 5545) 만들기. 휴대폰 기본 달력·구글 캘린더가 연다.
// 원칙: 공지에 시각이 없으면 하루 종일 일정으로 넣고 시각을 채우지 않는다 (docs/ui/화면설계.md 4절 "시각 미기재").
// 단계별 마감(신청·출품 등)은 따로 일정으로 만든다. 알림은 마감 3일 전·1일 전.
// 공지 문자열(제목·이유)은 외부 입력: 이스케이프하고, 그 안의 주소는 지운다 (달력 앱은 설명 속 주소를 링크로 만들고 호스트명을 보여 주지 않음).

import { parseContractDate, toKstInstant, kstDateString, formatShortDate } from './dates.js';
import { safeUrl, stripInvisible } from './html.js';

// 마감 단계. 예선은 제출 마감인지 심사일인지 공지마다 달라 마감으로 단정하지 않는다 ([일정]으로 표시)
const DEADLINE_STAGES = new Set(['신청', '출품']);
// UID에 쓰는 단계 이름 (날짜를 넣지 않아야 마감이 바뀌어도 같은 일정으로 갱신된다)
const STAGE_UID = { 신청: 'apply', 출품: 'submit', 예선: 'prelim', 본선: 'final', 발표: 'announce', 시상: 'award', 기타: 'other' };
// 알림: 시각이 있으면 마감 시각 기준 3일·1일 전. 하루 종일 일정은 그날 0시 기준이라 그대로 두면 자정에 울리므로
// 3일 전·1일 전 오전 9시로 맞춘다 (0시에서 2일 15시간·15시간 앞)
const TIMED_ALARMS = [{ days: 3, trigger: '-P3D' }, { days: 1, trigger: '-P1D' }];
const ALL_DAY_ALARMS = [{ days: 3, trigger: '-P2DT15H' }, { days: 1, trigger: '-PT15H' }];
const MAX_LINE_OCTETS = 75;
// 원문 주소는 학교 도메인만 달력에 넣는다 (forms.gle·docs.google.com은 누구나 만들 수 있고 달력엔 호스트명이 안 보임)
const ICS_URL_HOST = 'mju.ac.kr';

/** 마감 단계인지 (발표·본선·시상은 마감이 아니라 행사 날짜) */
export function isDeadlineStage(name) {
  return DEADLINE_STAGES.has(name);
}

/** 단계 이름 → 화면·일정 이름 ("신청" → "참가 신청 마감", "본선" → "본선") */
export function stageLabel(name) {
  if (name === '신청') return '참가 신청 마감';
  if (name === '출품') return '작품 출품 마감';
  if (DEADLINE_STAGES.has(name)) return `${name} 마감`;
  return typeof name === 'string' && name !== '' ? name : '일정';
}

/**
 * 캘린더에 넣을 수 있는 일정 목록. 단계(stages)가 있으면 단계별로, 없으면 신청 마감(apply.end) 하나.
 * 단계에 신청이 없으면 신청 마감(apply.end)을 앞에 더한다 (계약은 stages가 신청을 포함한다고 정하지 않음).
 * @param {object} card  validateCardData를 통과한 카드
 * @param {Date} now  기준 시각 (지난 일정 표시용)
 * @returns {{key: string, label: string, isDeadline: boolean, date: string, time: string|null, isPast: boolean, isVerified: boolean, note: string|null}[]}
 *   key: 같은 카드 안에서 일정을 구분하는 값(단계 이름, UID에 쓴다. 날짜를 넣지 않아 마감이 바뀌어도 같다).
 *   time이 null이면 하루 종일 일정. 읽을 수 없는 날짜는 뺀다
 */
export function calendarItems(card, now) {
  const stages = card.fields?.stages;
  const apply = card.fields?.apply;
  const useStages = stages && stages.status !== 'missing' && Array.isArray(stages.value) && stages.value.length > 0;
  const applyItem = { name: '신청', end: apply?.value?.end, timeGiven: apply?.value?.time_given === true, field: apply };
  const source = useStages
    ? stages.value.map((stage) => ({ name: stage?.name, end: stage?.end, timeGiven: stage?.time_given === true, field: stages }))
    : [applyItem];
  if (useStages && !source.some((item) => item.name === '신청') && apply?.status !== 'missing') source.unshift(applyItem);

  const today = kstDateString(now);
  const items = [];
  const seenKeys = new Set();
  const nameCounts = new Map();
  for (const { name, end, timeGiven, field } of source) {
    if (!field || field.status === 'missing') continue;
    const parsed = parseContractDate(end);
    if (!parsed) continue;
    const time = timeGiven ? parsed.time : null;
    // 같은 단계·날짜가 두 번 적혀 있으면 한 번만
    const duplicateKey = `${name}|${parsed.date}|${time}`;
    if (seenKeys.has(duplicateKey)) continue;
    seenKeys.add(duplicateKey);
    // 같은 이름 단계가 둘이면(기타·기타) 두 번째부터 번호를 붙인다. 한글 이름은 UID 문자로 바꾸면 겹칠 수 있어 영문으로
    const count = (nameCounts.get(name) ?? 0) + 1;
    nameCounts.set(name, count);
    const base = STAGE_UID[name] ?? 'stage';
    items.push({
      key: count === 1 && base !== 'stage' ? base : `${base}${count}`,
      label: stageLabel(name),
      isDeadline: isDeadlineStage(name),
      date: parsed.date,
      time,
      isPast: time ? now > toKstInstant(parsed.date, time) : parsed.date < today,
      isVerified: field.status === 'human_verified',
      note: field.status === 'needs_check' && typeof field.note === 'string' ? field.note : null,
    });
  }
  return items;
}

/** 일정 한 줄 설명: "10/23(금) (시각 미기재 → 하루 종일)", "10/21(수) 15:00" */
export function calendarItemText(item) {
  const date = formatShortDate(item.date);
  return item.time ? `${date} ${item.time}` : `${date} (시각 미기재 → 하루 종일)`;
}

/**
 * .ics 글을 만든다.
 * @param {object} card
 * @param {ReturnType<typeof calendarItems>} items  넣을 일정 (화면에서 고른 것)
 * @param {{withAlarms?: boolean, now?: Date}} [options]  now: 파일 생성 시각(DTSTAMP)
 * @returns {string} CRLF 줄바꿈, 75바이트 접기를 지킨 iCalendar 글
 * @throws {RangeError} 넣을 일정이 없을 때
 */
export function buildIcs(card, items, { withAlarms = true, now = new Date() } = {}) {
  if (!Array.isArray(items) || items.length === 0) throw new RangeError('캘린더에 넣을 일정이 없습니다');
  const title = typeof card.title === 'string' ? noticeText(card.title) : '대회';
  const sourceUrl = calendarUrl(card.sources?.[0]?.url);
  // 마감이 바뀔 때마다 1씩 커져, 같은 UID의 일정을 다시 가져오면 달력 앱이 새 값으로 갱신한다
  const sequence = Array.isArray(card.deadline_history) ? card.deadline_history.length : 0;
  const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Myongji Radar//KO', 'CALSCALE:GREGORIAN', 'METHOD:PUBLISH'];
  for (const item of items) {
    const description = [
      `${item.label}: ${calendarItemText(item)}`,
      item.isVerified ? null : '팀이 원문과 대조하기 전인 일정입니다.',
      item.note ? `원문 확인 필요: ${noticeText(item.note)}` : null,
      '명지레이더가 공지에서 정리한 일정입니다. 신청 전에 원문 공지를 꼭 확인하세요.',
      sourceUrl ? `원문: ${sourceUrl}` : null,
    ].filter(Boolean).join('\n');
    lines.push(
      'BEGIN:VEVENT',
      `UID:${uidPart(card.id)}-${uidPart(item.key)}@myongji-radar`,
      `SEQUENCE:${sequence}`,
      `DTSTAMP:${utcStamp(now)}`,
      ...eventTimes(item),
      `SUMMARY:${escapeText(`${item.isDeadline ? '[마감]' : '[일정]'} ${title} · ${item.label}`)}`,
      `DESCRIPTION:${escapeText(description)}`,
    );
    if (sourceUrl) lines.push(`URL:${sourceUrl}`);
    if (withAlarms) {
      for (const alarm of item.time ? TIMED_ALARMS : ALL_DAY_ALARMS) {
        lines.push('BEGIN:VALARM', 'ACTION:DISPLAY', `TRIGGER:${alarm.trigger}`,
          `DESCRIPTION:${escapeText(`${item.label} ${alarm.days}일 전: ${title}`)}`, 'END:VALARM');
      }
    }
    lines.push('END:VEVENT');
  }
  lines.push('END:VCALENDAR');
  return `${lines.map(foldLine).join('\r\n')}\r\n`;
}

/** 내려받을 파일 이름. id는 계약상 영문 소문자·숫자·하이픈이라 그대로 쓴다 */
export function icsFileName(card) {
  return `${uidPart(card.id)}.ics`;
}

// 시각이 있으면 그 시각 한 점(끝 시각 없음 = 길이 0, RFC 5545 3.6.1), 없으면 그날 하루 종일
function eventTimes(item) {
  if (item.time) return [`DTSTART:${utcStamp(toKstInstant(item.date, item.time))}`];
  const next = new Date(Date.parse(`${item.date}T00:00:00Z`) + 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
  return [`DTSTART;VALUE=DATE:${item.date.replaceAll('-', '')}`, `DTEND;VALUE=DATE:${next.replaceAll('-', '')}`];
}

function utcStamp(instant) {
  return instant.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
}

function uidPart(value) {
  return String(value ?? '').replace(/[^a-z0-9-]/gi, '-').slice(0, 64) || 'card';
}

// 원문 주소: 허용 주소(safeUrl) 중 학교 도메인만
function calendarUrl(url) {
  const safe = safeUrl(url);
  if (!safe) return null;
  const host = new URL(safe).hostname;
  return host === ICS_URL_HOST || host.endsWith(`.${ICS_URL_HOST}`) ? safe : null;
}

// 공지 문자열: 보이지 않는 글자를 지우고, 안에 든 주소는 지운다 (원문 주소는 검사한 "원문:" 줄 하나만 남긴다)
function noticeText(text) {
  return stripInvisible(text).replace(/\b(?:https?:\/\/|www\.)\S+/gi, '(주소 생략)');
}

// TEXT 값 이스케이프 (RFC 5545 3.3.11). 공지 문자열의 줄바꿈·쉼표·세미콜론이 속성을 깨지 않게 한다.
// 유니코드 줄바꿈(NEL·LS·PS)도 줄바꿈으로 보는 앱이 있어 \n으로 바꾼다
function escapeText(text) {
  return String(text)
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, '')
    .replace(/\\/g, '\\\\')
    .replace(/;/g, '\\;')
    .replace(/,/g, '\\,')
    .replace(/\r\n|\r|\n|\u0085|\u2028|\u2029/g, '\\n');
}

// 75바이트(UTF-8)를 넘는 줄은 접는다. 한글이 바이트 중간에서 잘리지 않게 글자 단위로 센다
function foldLine(line) {
  const encoder = new TextEncoder();
  const parts = [];
  let current = '';
  let currentOctets = 0;
  for (const char of line) {
    const octets = encoder.encode(char).length;
    const limit = parts.length === 0 ? MAX_LINE_OCTETS : MAX_LINE_OCTETS - 1;
    if (currentOctets + octets > limit) {
      parts.push(current);
      current = '';
      currentOctets = 0;
    }
    current += char;
    currentOctets += octets;
  }
  parts.push(current);
  return parts.join('\r\n ');
}
