// 맞춤 추천 화면 (#24): 문장 입력 → 조건 칩, 조건 칩으로 고치기, 판정 4상태별 결과.
// 계산은 recommend.js·parser.js·rules.js(Node 테스트), 여기서는 그리기와 버튼만. 글자는 모두 el()/textContent로 넣는다.
// 입력 문장과 "이해하지 못한 표현"은 저장하지도 보내지도 않는다 (이름·학번이 들어 있을 수 있음, 계약 5절).
import { el } from './html.js';
import { loadPage, badgeElements, ddayElement, showConditionSummary } from './page.js';
import { latestDeadlineChange, deliverableTags } from './cards.js';
import { formatShortDate } from './dates.js';
import { parseConditions, MAX_INPUT_LENGTH, STUDENT_DELIVERABLE_TAGS } from './parser.js';
import { readSavedConditions, saveConditions, clearSavedConditions, emptyConditions } from './conditions.js';
import { inquiryDraft } from './detail.js';
import { applyParsed, recommendCards, limitTerms, hasUnverifiedFail, periodEndDate, RESULT_ORDER, RESULT_VIEW } from './recommend.js';

// 칩 묶음: [조건 이름, 보이는 이름, [값, 칩 글자]]. 한 묶음에서 하나만 고르고, 다시 누르면 고르지 않은 상태(null)
const CHIP_GROUPS = [
  ['campus', '캠퍼스', [['인문', '인문'], ['자연', '자연']]],
  ['grade', '학년', [1, 2, 3, 4, 5, 6].map((grade) => [grade, `${grade}학년`])],
  ['is_transfer', '편입', [[true, '편입생'], [false, '편입생 아님']]],
  ['enrollment', '학적', ['재학', '휴학', '수료', '초과학기', '졸업유예'].map((value) => [value, value])],
  ['participation', '참가', [['개인', '혼자(개인)'], ['팀', '팀']]],
  ['period', '마감', [['마감임박', '7일 이내'], ['이번주', '이번 주'], ['이번달', '이번 달']]],
];
const FIELD_NAMES = { campus: '캠퍼스', grade: '학년', is_transfer: '편입', enrollment: '학적', participation: '참가', period: '마감', deliverables: '제출물' };

const state = { conditions: readSavedConditions() ?? emptyConditions(), unknownTerms: [], changed: [] };

main().catch((error) => {
  console.error('맞춤 추천을 그리지 못함', error);
  document.getElementById('result-list')?.replaceChildren(el('p', { class: 'error' }, '대회 정보를 보여 주지 못했습니다. 잠시 뒤 다시 열어 주세요.'));
});

async function main() {
  const page = await loadPage({ errorTarget: document.getElementById('result-list') });
  if (!page) return;
  const { data, now } = page;
  const input = document.getElementById('q');
  input.maxLength = MAX_INPUT_LENGTH;

  const render = () => {
    renderChips();
    renderParsed();
    renderResults(data.cards, now);
    showConditionSummary(state.conditions);
  };
  const update = (conditions) => {
    state.conditions = conditions;
    saveConditions(conditions);
    render();
  };

  document.getElementById('ask-form').addEventListener('submit', (event) => {
    event.preventDefault();
    interpret(input.value, update);
  });
  document.getElementById('clear-saved').addEventListener('click', () => {
    clearSavedConditions();
    state.conditions = emptyConditions();
    state.unknownTerms = [];
    state.changed = [];
    input.value = '';
    render();
    announce(document.getElementById('save-status'), '저장된 조건을 지웠습니다.');
  });
  bindChips(update);

  // 주소로 받은 문장(recommend.html?q=…)도 입력창과 같은 상한을 거쳐 해석한다 (PR #36 보안 리뷰)
  const fromUrl = new URLSearchParams(window.location.search).get('q');
  if (fromUrl) {
    input.value = fromUrl.slice(0, MAX_INPUT_LENGTH);
    interpret(input.value, update);
  } else {
    render();
  }
}

function interpret(text, update) {
  const status = document.getElementById('parse-status');
  if (text.trim() === '') {
    announce(status, '문장을 입력하거나 아래 조건을 눌러 고르세요.');
    return;
  }
  let parsed;
  try {
    parsed = parseConditions(text.slice(0, MAX_INPUT_LENGTH));
  } catch (error) {
    console.warn('문장을 해석하지 못함', error);
    announce(status, `문장은 ${MAX_INPUT_LENGTH}자까지 쓸 수 있습니다.`);
    return;
  }
  const { conditions, changed, unknownTerms } = applyParsed(state.conditions, parsed);
  state.unknownTerms = unknownTerms;
  state.changed = changed;
  announce(status, changed.length > 0
    ? `문장에서 ${changed.map((field) => FIELD_NAMES[field]).join('·')} 조건을 읽었습니다. 아래에서 고칠 수 있습니다.`
    : '문장에서 새로 읽은 조건이 없습니다. 아래 조건을 눌러 고르세요.');
  update(conditions);
}

function bindChips(update) {
  const container = document.getElementById('chip-groups');
  container.replaceChildren(
    ...CHIP_GROUPS.map(([field, label, options]) => el('div', { class: 'row' },
      el('span', { class: 'small filter-label', id: `chip-${field}` }, label),
      el('div', { class: 'chips', role: 'group', 'aria-labelledby': `chip-${field}` },
        ...options.map(([value, text]) => {
          const chip = el('button', { type: 'button', class: 'chip', 'data-field': field, 'aria-pressed': 'false' }, text);
          chip.addEventListener('click', () => {
            const isSame = state.conditions[field] === value;
            update({ ...state.conditions, [field]: isSame ? null : value });
          });
          chip.chipValue = value;
          return chip;
        })))),
    el('div', { class: 'row' },
      el('span', { class: 'small filter-label', id: 'chip-deliverables' }, '제출물'),
      el('div', { class: 'chips', role: 'group', 'aria-labelledby': 'chip-deliverables' },
        ...STUDENT_DELIVERABLE_TAGS.map((tag) => {
          const chip = el('button', { type: 'button', class: 'chip', 'data-field': 'deliverables', 'aria-pressed': 'false' }, tag);
          chip.addEventListener('click', () => {
            const tags = state.conditions.deliverables ?? [];
            update({ ...state.conditions, deliverables: tags.includes(tag) ? tags.filter((item) => item !== tag) : [...tags, tag] });
          });
          chip.chipValue = tag;
          return chip;
        }))));
}

function renderChips() {
  document.querySelectorAll('#chip-groups .chip').forEach((chip) => {
    const { field } = chip.dataset;
    const isPressed = field === 'deliverables'
      ? (state.conditions.deliverables ?? []).includes(chip.chipValue)
      : state.conditions[field] === chip.chipValue;
    chip.setAttribute('aria-pressed', String(isPressed));
  });
}

function renderParsed() {
  const target = document.getElementById('parsed');
  if (state.changed.length === 0 && state.unknownTerms.length === 0) {
    target.replaceChildren();
    return;
  }
  const { shown, hiddenCount } = limitTerms(state.unknownTerms);
  // replaceChildren은 null을 글자 "null"로 넣으므로 빈 값은 걸러 낸다
  target.replaceChildren(...[
    el('span', { class: 'small' }, '이해하지 못한 표현'),
    ...(shown.length > 0
      ? shown.map((term) => el('span', { class: 'badge s-before', dir: 'auto' }, term))
      : [el('span', { class: 'small' }, '없음')]),
    hiddenCount > 0 ? el('span', { class: 'small' }, `외 ${hiddenCount}개`) : null,
    shown.length > 0 ? el('span', { class: 'small' }, '→ 조건으로 쓰지 않았습니다. 필요하면 아래 칩으로 고르세요.') : null,
  ].filter(Boolean));
}

function renderResults(cards, now) {
  const { groups, closedCount, filteredCount, total } = recommendCards(cards, state.conditions, now);
  const periodEnd = periodEndDate(state.conditions.period, now);
  document.getElementById('result-heading').textContent = `마감 전 대회 ${total}건`;
  document.getElementById('result-meta').textContent = [
    periodEnd ? `마감 ${formatShortDate(periodEnd)}까지` : null,
    filteredCount > 0 ? `제출물·마감 조건으로 ${filteredCount}건 숨김` : null,
    closedCount > 0 ? `마감 지난 대회 ${closedCount}건 제외` : null,
  ].filter(Boolean).join(' · ');

  const list = document.getElementById('result-list');
  if (total === 0) {
    list.replaceChildren(el('p', { class: 'empty' }, '조건에 맞는 마감 전 대회가 없습니다. 제출물·마감 조건을 풀어 보세요.'));
    return;
  }
  list.replaceChildren(...RESULT_ORDER.filter((result) => groups[result].length > 0).map((result) => el('section', { class: 'result-group', 'aria-label': `${RESULT_VIEW[result].text} ${groups[result].length}건` },
    ...groups[result].map((item) => renderItem(item)))));
}

function renderItem({ card, judged, deadline, summary }) {
  const view = RESULT_VIEW[judged.result];
  const change = latestDeadlineChange(card);
  const draft = judged.result === 'check' ? inquiryDraft(card, state.conditions) : null;
  const status = el('span', { class: 'small', role: 'status' });
  const copyButton = draft ? el('button', { type: 'button', class: 'btn spacer' }, '✉ 문의 초안 복사') : null;
  copyButton?.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(draft.text);
      announce(status, '복사했습니다.');
    } catch (error) {
      console.warn('클립보드에 복사하지 못함', error);
      announce(status, '자동 복사가 막혔습니다. 카드 보기에서 초안을 선택해 복사하세요.');
    }
  });
  return el('div', { class: 'item' },
    el('div', { class: 'row' },
      el('span', { class: `badge ${view.cls}` }, view.text),
      el('span', { class: 'title' }, card.title),
      ddayElement(deadline, 'spacer')),
    el('span', { class: 'reason' }, summary),
    change ? el('p', { class: 'notice' }, `⚠ 마감 변경: ${shortDate(change.old)} → ${shortDate(change.new)}`) : null,
    el('div', { class: 'row' },
      ...deliverableTags(card).map((tag) => el('span', { class: 'tag' }, tag)),
      ...badgeElements(card),
      hasUnverifiedFail(judged) ? el('span', { class: 'small' }, '팀이 원문과 대조하기 전 정보입니다') : null,
      copyButton, status,
      el('a', { class: copyButton ? 'btn' : 'btn spacer', href: `card.html?id=${encodeURIComponent(card.id)}` }, '카드 보기')));
}

function shortDate(value) {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}/.test(value) ? formatShortDate(value.slice(0, 10)) : String(value);
}

// role="status"는 같은 글을 다시 넣으면 읽어 주지 않으므로 한 번 비운 뒤 넣는다
function announce(target, message) {
  target.textContent = '';
  setTimeout(() => { target.textContent = message; }, 50);
}
