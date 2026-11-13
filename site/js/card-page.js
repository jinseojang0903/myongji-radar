// 카드 상세 화면 (#25): 내 조건 판정, 항목 표와 근거, 마감 변경 이력, 캘린더 파일, 문의 초안, 원문 링크.
// 계산은 detail.js·ics.js(Node 테스트), 여기서는 그리기와 버튼만 다룬다. 글자는 모두 el()/textContent로 넣는다.
import { el, externalLink } from './html.js';
import { loadPage, badgeElements, ddayElement, showConditionSummary } from './page.js';
import { deadlineOf, formatDeadline, organizerLine } from './cards.js';
import { judgeCard } from './rules.js';
import { formatShortDate, parseContractDate } from './dates.js';
import { readSavedConditions, hasJudgedConditions } from './conditions.js';
import { fieldRows, sourceLinks, applyLink, deadlineHistory, deliverableBadges, inquiryDraft } from './detail.js';
import { calendarItems, calendarItemText, buildIcs, icsFileName } from './ics.js';

const BADGE_CLASS = { human: 's-human', auto: 's-auto', before: 's-before', need: 's-need', missing: 's-missing' };
const RESULT_VIEW = {
  eligible: { cls: 'j-ok', text: '● 내 조건: 신청 가능' },
  team_needed: { cls: 'j-team', text: '◆ 내 조건: 팀 구성 시 가능' },
  check: { cls: 'j-check', text: '? 내 조건: 확인 필요' },
  ineligible: { cls: 'j-no', text: '✕ 내 조건: 불가' },
};

main().catch((error) => {
  // 그리다가 예상 못 한 오류가 나도 "불러오는 중…"에 멈춰 있지 않게 (상세는 콘솔에만)
  console.error('카드 상세를 그리지 못함', error);
  document.getElementById('card-main')?.replaceChildren(el('p', { class: 'error' },
    '대회 정보를 보여 주지 못했습니다. 잠시 뒤 다시 열어 주세요. ', el('a', { href: 'index.html' }, '홈으로')));
});

async function main() {
  const container = document.getElementById('card-main');
  const page = await loadPage({ errorTarget: container });
  if (!page) return;
  const { data, now, rejected } = page;
  const id = new URLSearchParams(window.location.search).get('id');
  const card = data.cards.find((item) => item.id === id);
  if (!card) {
    const isRejected = rejected.some((item) => item.id === id);
    container.replaceChildren(el('section', { class: 'panel' },
      el('h1', {}, '대회를 찾을 수 없습니다'),
      el('p', { class: 'small' }, isRejected
        ? '이 대회 정보는 형식이 맞지 않아 보여 줄 수 없습니다.'
        : '주소가 바뀌었거나 목록에서 빠진 대회입니다.'),
      el('a', { class: 'btn', href: 'index.html' }, '홈으로')));
    return;
  }
  document.title = `명지레이더 · ${card.title}`;
  const conditions = readSavedConditions();
  showConditionSummary(conditions);

  container.replaceChildren(
    renderHeader(card, now, conditions),
    el('div', { class: 'split' },
      renderFields(card, data.boards),
      el('div', { class: 'side' },
        renderHistory(card, data.boards),
        renderCalendar(card, now),
        renderInquiry(card, conditions))),
  );
}

function renderHeader(card, now, conditions) {
  const deadline = deadlineOf(card, now);
  const deadlineText = formatDeadline(deadline);
  const byline = organizerLine(card);
  const section = el('section', { class: 'panel', 'aria-labelledby': 'h-card' },
    el('h1', { id: 'h-card' }, card.title),
    byline ? el('p', { class: 'lead' }, byline) : null,
    el('div', { class: 'row' },
      ddayElement(deadline),
      deadlineText ? el('span', { class: 'small' }, `신청 마감 ${deadlineText}`) : null,
      ...badgeElements(card),
      ...deliverableBadges(card).map((tag) => el('span', { class: 'tag' }, tag))),
  );
  section.append(renderJudgment(card, now, conditions));
  return section;
}

function renderJudgment(card, now, conditions) {
  if (!hasJudgedConditions(conditions)) {
    return el('p', { class: 'small' }, '내 조건을 고르면 이 대회에 신청할 수 있는지 판정합니다. ',
      el('a', { href: 'recommend.html' }, '맞춤 추천에서 조건 고르기'));
  }
  const judged = judgeCard(card, conditions, { now });
  const view = RESULT_VIEW[judged.result];
  const shown = judged.result === 'eligible' ? judged.reasons : judged.reasons.filter((reason) => reason.outcome !== 'ok');
  return el('div', { class: 'stack-top' },
    el('span', { class: `badge ${view.cls}` }, view.text),
    el('ul', { class: 'reason-list' }, ...shown.map((reason) => el('li', { class: 'reason' },
      reason.outcome === 'ok' ? `✓ ${reason.message}` : reason.message,
      // 계약 3절: 확인 전 근거로 '불가'가 나온 사유에만 표시 (확인 전 항목의 나머지 사유는 메시지에 '(확인 전)'이 이미 붙음)
      reason.unverified && reason.outcome === 'fail' ? el('span', { class: 'badge s-before' }, '확인 전 정보') : null,
      reason.quote ? el('span', { class: 'quote' }, `근거: "${reason.quote}"`) : null))),
    el('p', { class: 'small' }, 'AI가 정리한 결과입니다. 신청 전에 원문 공지를 꼭 확인하세요.'));
}

function renderFields(card, boards) {
  const rows = fieldRows(card, boards);
  const link = applyLink(card);
  const table = el('table', { class: 'fields' },
    el('caption', { class: 'sr-only' }, '대회 카드 항목: 값, 확인 상태, 근거'),
    el('tbody', {}, ...rows.map((row) => renderFieldRow(row, row.name === 'how_to_apply' ? link : null))));
  const sources = sourceLinks(card, boards);
  const checkedAt = parseContractDate(card.meta?.checked_at);
  return el('section', { class: 'panel', 'aria-labelledby': 'h-fields' },
    el('h2', { id: 'h-fields' }, '대회 카드'),
    table,
    el('div', { class: 'row stack-top' }, ...sources.map((source, index) => {
      const text = `원문 공지 보기 (${source.boardName}${source.postedAt ? ` ${formatShortDate(source.postedAt)}` : ''})`;
      const anchor = externalLink(source.url, text);
      // 허용되지 않은 주소면 externalLink가 글자(span)만 돌려준다. 버튼처럼 보이지 않게 한다
      anchor.className = anchor.tagName === 'A' ? (index === 0 ? 'btn primary' : 'btn') : 'small';
      return anchor;
    })),
    el('p', { class: 'small' }, '카드는 AI 정리 → 근거 문장이 원문에 있는지 코드로 대조 → 팀의 원문 대조(신청기간·자격) 순서로 확인합니다. 다른 모델로 다시 뽑아 비교하는 단계는 준비 중입니다. 항목마다 어디까지 확인했는지는 상태 배지로 보여 줍니다.',
      checkedAt ? ` 마지막 확인 ${checkedAt.date}.` : ''));
}

function renderFieldRow(row, link) {
  const evidenceId = `ev-${row.name}`;
  const valueCell = el('td', {},
    // 정보 충돌이어도 정리한 값은 보여 주고, 출처별 근거를 바로 펼친다 (화면설계.md 4절 "정보 충돌")
    row.isConflict ? el('div', {}, el('b', {}, '정보 충돌: 출처마다 다르게 적혀 있습니다')) : null,
    row.text ?? el('span', { class: 'missing' }, row.status === 'missing' ? '공지에 없음' : '표시할 수 없는 값'),
    row.note ? el('div', { class: row.status === 'needs_check' ? 'field-note need' : 'field-note' }, row.note) : null,
    link ? el('div', { class: 'stack-top' }, externalLink(link.url, '신청 페이지 열기'), el('span', { class: 'small' }, ` (${link.host})`)) : null);

  const statusCell = el('td', { class: 'st' }, el('span', { class: `badge ${BADGE_CLASS[row.badge.kind]}` }, row.badge.text));
  if (row.evidence.length > 0) {
    // 충돌이면 출처별 값을 바로 보여 준다 (화면설계.md 4절 "정보 충돌")
    const isOpen = row.isConflict;
    const evidence = el('div', { id: evidenceId }, ...row.evidence.map(renderEvidence));
    evidence.hidden = !isOpen;
    const button = el('button', {
      type: 'button', class: 'btn small-btn', 'aria-expanded': String(isOpen), 'aria-controls': evidenceId,
      // 열림·닫힘은 aria-expanded가 알린다. 이름은 고정 (보이는 글자 "근거"와 맞춤)
      'aria-label': `${row.label} 근거`,
    }, isOpen ? '근거 ▲' : '근거 ▼');
    button.addEventListener('click', () => {
      const willOpen = evidence.hidden;
      evidence.hidden = !willOpen;
      button.setAttribute('aria-expanded', String(willOpen));
      button.textContent = willOpen ? '근거 ▲' : '근거 ▼';
    });
    // 버튼 바로 뒤에 근거를 둔다. 상태 칸에 두면 펼친 근거(값 칸)가 버튼보다 앞이라,
    // 펼친 뒤 Tab·화면 읽기로 나아가면 근거와 "원문 보기"를 지나쳐 다음 행으로 간다 (#104)
    valueCell.append(el('div', {}, button), evidence);
  }
  return el('tr', {}, el('th', { scope: 'row' }, row.label), valueCell, statusCell);
}

function renderEvidence(item) {
  const where = [item.boardName, item.postedAt ? formatShortDate(item.postedAt) : null].filter(Boolean).join(' ');
  return el('div', { class: 'ev' },
    el('mark', {}, item.quote),
    el('div', { class: 'meta' },
      el('span', { class: 'loc' }, item.location),
      item.isImage ? ' 이미지에서 읽음 (본문보다 정확도가 낮을 수 있음)' : '',
      item.file ? ` · ${item.file}` : '',
      where ? ` · ${where}` : '',
      item.url ? ' · ' : '',
      item.url ? externalLink(item.url, '원문 보기') : null));
}

function renderHistory(card, boards) {
  const history = deadlineHistory(card, boards);
  if (history.length === 0) return null;
  return el('section', { class: 'panel', 'aria-labelledby': 'h-history' },
    el('h2', { id: 'h-history' }, '마감 변경 이력'),
    el('div', { class: 'timeline' }, ...history.flatMap((change) => [
      el('div', {}, el('span', { class: 'old' }, change.oldText), change.oldSource ? el('span', { class: 'small' }, ` · ${change.oldSource}`) : null),
      el('div', {}, '→ ', el('b', {}, change.newText), change.newSource ? el('span', { class: 'small' }, ` · ${change.newSource}`) : null),
      change.checkedAt ? el('div', { class: 'small' }, `최신 공지 기준으로 표시 · 확인 ${change.checkedAt}`) : null,
    ])));
}

function renderCalendar(card, now) {
  const items = calendarItems(card, now);
  const section = el('section', { class: 'panel', 'aria-labelledby': 'h-cal' }, el('h2', { id: 'h-cal' }, '캘린더에 추가'));
  if (items.length === 0) {
    section.append(el('p', { class: 'small' }, '공지에서 마감일을 읽지 못해 캘린더 파일을 만들 수 없습니다.'));
    return section;
  }
  const status = el('p', { class: 'small', role: 'status' });
  const choices = items.map((item) => {
    const input = el('input', { type: 'checkbox', checked: !item.isPast, disabled: item.isPast });
    const label = el('label', { class: 'row' }, input, `${item.label} ${calendarItemText(item)}`,
      item.isPast ? el('span', { class: 'small' }, '(지남)') : null,
      item.isVerified ? null : el('span', { class: 'badge s-before' }, '확인 전'),
      item.note ? el('span', { class: 'badge s-need' }, '! 원문 확인 필요') : null);
    return { item, input, label };
  });
  const alarm = el('input', { type: 'checkbox', checked: true });
  const button = el('button', { type: 'button', class: 'btn primary' }, '캘린더 파일 받기 (.ics)');
  button.addEventListener('click', () => {
    const selected = choices.filter((choice) => choice.input.checked).map((choice) => choice.item);
    if (selected.length === 0) {
      announce(status, '넣을 일정을 하나 이상 고르세요.');
      return;
    }
    downloadCalendar(buildIcs(card, selected, { withAlarms: alarm.checked }), icsFileName(card));
    announce(status, `일정 ${selected.length}개를 담은 파일을 받았습니다.`);
  });
  section.append(
    ...choices.map((choice) => choice.label),
    el('label', { class: 'row' }, alarm, '3일 전·1일 전 알림', el('span', { class: 'small' }, '(시각이 없는 마감은 그날 오전 9시)')),
    button, status,
    el('p', { class: 'small' }, '휴대폰 기본 달력·구글 캘린더에서 열 수 있습니다. 마감 시각이 공지에 없으면 임의로 채우지 않고 하루 종일 일정으로 넣습니다.'));
  return section;
}

// 캘린더 파일 내려받기. el()은 사이트 밖 주소를 링크로 만들지 않으므로 blob: 주소는 여기서만 직접 단다.
// 형식을 text/calendar로 고정한다 (HTML·SVG로 열리는 형식을 넘길 수 없게)
function downloadCalendar(text, fileName) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/calendar;charset=utf-8' }));
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = fileName;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  // 느린 휴대폰에서 다운로드가 시작되기 전에 주소가 사라지지 않게 넉넉히 둔다
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

// role="status" 영역은 같은 글을 다시 넣으면 읽어 주지 않으므로 한 번 비운 뒤 넣는다
function announce(target, message) {
  target.textContent = '';
  setTimeout(() => { target.textContent = message; }, 50);
}

function renderInquiry(card, conditions) {
  const draft = inquiryDraft(card, conditions);
  if (!draft) return null;
  const text = el('div', { class: 'ev draft', id: 'inquiry-text' }, draft.text);
  const status = el('span', { class: 'small', role: 'status' });
  const button = el('button', { type: 'button', class: 'btn primary' }, '✉ 문의 초안 복사');
  button.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(draft.text);
      announce(status, '복사했습니다.');
    } catch (error) {
      console.warn('클립보드에 복사하지 못함', error);
      window.getSelection()?.selectAllChildren(text);
      announce(status, '자동 복사가 막혀 글을 선택해 두었습니다. Ctrl+C로 복사하세요.');
    }
  });
  return el('section', { class: 'panel', 'aria-labelledby': 'h-ask' },
    el('h2', { id: 'h-ask' }, '담당 부서에 문의하기'),
    el('p', { class: 'small' }, `공지에 없거나 원문 확인이 필요한 항목: ${draft.items.join(', ')}`),
    text,
    el('div', { class: 'row stack-top' }, button, status),
    el('p', { class: 'small' }, `받는 곳: ${draft.recipient}`));
}
