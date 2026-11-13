// 맞춤 추천 화면 (#24): 문장 입력 → 조건 칩, 조건 칩으로 고치기, 판정 4상태별 결과.
// 계산은 recommend.js·parser.js·rules.js(Node 테스트), 여기서는 그리기와 버튼만. 글자는 모두 el()/textContent로 넣는다.
// 입력 문장과 "이해하지 못한 표현"은 저장하지도 보내지도 않는다 (이름·학번이 들어 있을 수 있음, 계약 5절).
import { el } from './html.js';
import { loadPage, badgeElements, ddayElement, showConditionSummary } from './page.js';
import { latestDeadlineChange, deliverableTags, emptyListMessage } from './cards.js';
import { formatShortDate } from './dates.js';
import { parseConditions, MAX_INPUT_LENGTH, STUDENT_DELIVERABLE_TAGS } from './parser.js';
import { readSavedConditions, saveConditions, clearSavedConditions, emptyConditions } from './conditions.js';
import { inquiryDraft } from './detail.js';
import { applyParsed, recommendCards, limitTerms, hasUnverifiedFail, periodEndDate, clipTitleQuery, MAX_TITLE_QUERY_LENGTH, RESULT_ORDER, RESULT_VIEW } from './recommend.js';

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

// titleQuery: 제목 검색칸의 말. 조건이 아니라 목록 필터이고, 문장처럼 저장하지 않는다 (계약 5절 "제목 검색")
const state = { conditions: readSavedConditions() ?? emptyConditions(), unknownTerms: [], changed: [], read: [], titleQuery: '' };
// "이해하지 못한 표현"의 "제목에서 찾기"가 부른다. 데이터를 받은 뒤 main()이 채운다
let searchTitle = () => {};

main().catch((error) => {
  console.error('맞춤 추천을 그리지 못함', error);
  document.getElementById('result-list')?.replaceChildren(el('p', { class: 'error' }, '대회 정보를 보여 주지 못했습니다. 잠시 뒤 다시 열어 주세요.'));
});

async function main() {
  const input = document.getElementById('q');
  input.maxLength = MAX_INPUT_LENGTH;
  showConditionSummary(state.conditions);
  // 데이터를 받기 전에 Enter를 눌러도 폼이 주소에 문장을 실어 보내지 않게, 제출 막기를 가장 먼저 단다
  let onSubmit = () => {};
  document.getElementById('ask-form').addEventListener('submit', (event) => {
    event.preventDefault();
    onSubmit();
  });
  // 주소로 받은 문장(recommend.html#q=…)은 서버로 가지 않는 # 뒤에 둔다. 읽은 즉시 주소에서 지워
  // 방문 기록·다른 화면 요청에 남지 않게 한다 (#24 보안 리뷰). 예전 형식 ?q=도 같은 방식으로 읽고 지운다
  const fromAddress = readSentenceFromAddress();

  const page = await loadPage({ errorTarget: document.getElementById('result-list') });
  if (!page) {
    document.getElementById('result-heading').textContent = '대회 정보를 불러오지 못했습니다';
    return;
  }
  const { data, now, rejected } = page;
  // 게시판은 형식 검사에서 빠져도 카드 건수와 상관없다 (홈과 같은 기준)
  const rejectedCardCount = rejected.filter((item) => !item.id.startsWith('board:')).length;
  const saveStatus = document.getElementById('save-status');

  const render = () => {
    renderChips();
    renderParsed();
    renderResults(data.cards, now, rejectedCardCount);
    showConditionSummary(state.conditions);
  };
  // shouldSave=false: 화면에만 반영 (주소로 받은 문장은 학생이 칩·버튼을 누르기 전까지 저장하지 않는다)
  const update = (conditions, { shouldSave = true } = {}) => {
    state.conditions = conditions;
    if (shouldSave && !saveConditions(conditions)) {
      announce(saveStatus, '이 브라우저가 저장을 막고 있어 조건이 저장되지 않습니다. 이 화면에서만 쓰입니다.');
    }
    render();
  };

  onSubmit = () => interpret(input.value, update);
  const titleInput = document.getElementById('title-q');
  titleInput.maxLength = MAX_TITLE_QUERY_LENGTH;
  // 입력칸에서 읽기만 한다. 칠 때마다 값을 다시 넣으면 한글 조합이 끊긴다 (길이는 maxLength가 막음)
  const readTitleQuery = () => {
    state.titleQuery = clipTitleQuery(titleInput.value);
    renderResults(data.cards, now, rejectedCardCount);
  };
  // 버튼으로 바꿀 때만 입력칸에 쓴다
  const setTitleQuery = (text) => {
    titleInput.value = clipTitleQuery(text);
    readTitleQuery();
  };
  titleInput.addEventListener('input', readTitleQuery);
  // 데이터를 받기 전에 친 검색어도 첫 그리기에 쓴다
  state.titleQuery = clipTitleQuery(titleInput.value);
  document.getElementById('title-clear').addEventListener('click', () => {
    setTitleQuery('');
    titleInput.focus();
  });
  searchTitle = (term) => {
    setTitleQuery(term);
    titleInput.focus();
  };
  document.getElementById('clear-saved').addEventListener('click', () => {
    const isCleared = clearSavedConditions();
    state.conditions = emptyConditions();
    state.unknownTerms = [];
    state.changed = [];
    state.read = [];
    state.titleQuery = '';
    titleInput.value = '';
    input.value = '';
    document.getElementById('parse-status').textContent = '';
    render();
    announce(saveStatus, isCleared
      ? '저장된 조건을 지웠습니다.'
      : '이 브라우저가 저장소를 막고 있어 지우지 못했습니다. 브라우저의 사이트 데이터 삭제를 이용하세요.');
  });
  bindChips((conditions) => {
    // 칩으로 고치면 문장 해석 안내는 더 이상 지금 조건을 설명하지 않으므로 지운다
    document.getElementById('parse-status').textContent = '';
    state.unknownTerms = [];
    state.changed = [];
    state.read = [];
    update(conditions);
  });

  if (fromAddress) {
    input.value = fromAddress;
    interpret(fromAddress, (conditions) => update(conditions, { shouldSave: false }), '주소로 받은 문장이라 아직 저장하지 않았습니다. 칩을 누르거나 다시 바꾸면 저장됩니다.');
  } else {
    render();
  }
}

// 주소의 #q= (또는 예전 ?q=)를 읽고 주소에서 지운다. 200자로 자른다
function readSentenceFromAddress() {
  const fromHash = new URLSearchParams(window.location.hash.slice(1)).get('q');
  const fromSearch = new URLSearchParams(window.location.search).get('q');
  const sentence = fromHash ?? fromSearch;
  if (sentence !== null) window.history.replaceState(null, '', window.location.pathname);
  return sentence ? sentence.slice(0, MAX_INPUT_LENGTH) : null;
}

function interpret(text, update, note = null) {
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
    announce(status, error instanceof RangeError ? `문장은 ${MAX_INPUT_LENGTH}자까지 쓸 수 있습니다.` : '문장을 해석하지 못했습니다. 아래 조건을 눌러 고르세요.');
    return;
  }
  const { conditions, read, changed, kept, unknownTerms } = applyParsed(state.conditions, parsed);
  state.unknownTerms = unknownTerms;
  state.changed = changed;
  state.read = read;
  const names = (fields) => fields.map((field) => FIELD_NAMES[field]).join('·');
  const parts = [];
  if (read.length === 0) parts.push('문장에서 읽은 조건이 없습니다. 아래 조건을 눌러 고르세요.');
  else if (changed.length === 0) parts.push(`문장에서 ${names(read)} 조건을 읽었습니다 (이미 고른 값과 같음).`);
  else parts.push(`문장에서 ${names(read)} 조건을 읽었습니다. 아래에서 고칠 수 있습니다.`);
  // 지난번에 고른 조건이 조용히 섞이지 않게 무엇을 그대로 두었는지 알린다
  if (kept.length > 0) parts.push(`말하지 않은 ${names(kept)} 조건은 그대로 두었습니다.`);
  if (note) parts.push(note);
  announce(status, parts.join(' '));
  update(conditions);
}

function bindChips(onChange) {
  const container = document.getElementById('chip-groups');
  container.replaceChildren(
    ...CHIP_GROUPS.map(([field, label, options]) => el('div', { class: 'row' },
      el('span', { class: 'small filter-label', id: `chip-${field}` }, label),
      el('div', { class: 'chips', role: 'group', 'aria-labelledby': `chip-${field}` },
        ...options.map(([value, text]) => {
          const chip = el('button', { type: 'button', class: 'chip', 'data-field': field, 'aria-pressed': 'false' }, text);
          chip.addEventListener('click', () => {
            const isSame = state.conditions[field] === value;
            onChange({ ...state.conditions, [field]: isSame ? null : value });
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
            onChange({ ...state.conditions, deliverables: tags.includes(tag) ? tags.filter((item) => item !== tag) : [...tags, tag] });
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

// 문장을 해석한 직후에만 "이해하지 못한 표현"을 보여 준다 (칩으로 고치면 지움)
function renderParsed() {
  const target = document.getElementById('parsed');
  if (state.read.length === 0 && state.unknownTerms.length === 0) {
    target.replaceChildren();
    return;
  }
  const { shown, originals, hiddenCount } = limitTerms(state.unknownTerms);
  // replaceChildren은 null을 글자 "null"로 넣으므로 빈 값은 걸러 낸다
  target.replaceChildren(...[
    el('span', { class: 'small' }, '이해하지 못한 표현'),
    ...(shown.length > 0
      ? shown.flatMap((term, index) => [
        el('span', { class: 'term', dir: 'auto' }, term),
        findInTitleButton(originals[index]),
      ])
      : [el('span', { class: 'small' }, '없음')]),
    hiddenCount > 0 ? el('span', { class: 'small' }, `외 ${hiddenCount}개`) : null,
    shown.length > 0 ? el('span', { class: 'small' }, '→ 조건으로 쓰지 않았습니다. 필요하면 아래 칩으로 고르거나 제목에서 찾아보세요.') : null,
  ].filter(Boolean));
}

// 조건으로 바꾸지 못한 말("해커톤")을 제목 검색으로 잇는다 (#85)
function findInTitleButton(term) {
  const button = el('button', { type: 'button', class: 'btn', 'aria-label': `"${term}" 제목에서 찾기` }, '제목에서 찾기');
  button.addEventListener('click', () => searchTitle(term.trim()));
  return button;
}

function renderResults(cards, now, rejectedCount) {
  const { groups, closedCount, filteredCount, undatedCount, titleFilteredCount, skippedCount, total } = recommendCards(cards, state.conditions, now, { titleQuery: state.titleQuery });
  const periodEnd = periodEndDate(state.conditions.period, now);
  const isSearching = state.titleQuery.trim() !== '';
  document.getElementById('result-heading').textContent = `마감 전 대회 ${total}건`;
  document.getElementById('result-meta').textContent = [
    isSearching ? `제목에 "${state.titleQuery.trim()}"이(가) 없는 ${titleFilteredCount}건 숨김` : null,
    periodEnd ? `마감 ${formatShortDate(periodEnd)}까지` : null,
    filteredCount > 0 ? `제출물·마감 조건으로 ${filteredCount}건 숨김${undatedCount > 0 ? ` (그중 마감일이 없거나 읽을 수 없는 ${undatedCount}건)` : ''}` : null,
    closedCount > 0 ? `마감 지난 대회 ${closedCount}건 제외` : null,
    skippedCount > 0 ? `정보를 읽지 못한 대회 ${skippedCount}건 제외` : null,
  ].filter(Boolean).join(' · ');

  const list = document.getElementById('result-list');
  if (total === 0) {
    const message = emptyListMessage({ cardCount: cards.length, closedCount, skippedCount, rejectedCount }, isSearching
      ? '제목 검색과 조건에 맞는 마감 전 대회가 없습니다. 검색어를 바꾸거나 제출물·마감 조건을 풀어 보세요.'
      : '조건에 맞는 마감 전 대회가 없습니다. 제출물·마감 조건을 풀어 보세요.');
    list.replaceChildren(el('p', { class: 'empty' }, message));
    return;
  }
  list.replaceChildren(...RESULT_ORDER.filter((result) => groups[result].length > 0).map((result) => {
    const headingId = `group-${result}`;
    return el('div', { class: 'result-group', role: 'group', 'aria-labelledby': headingId },
      el('h3', { class: 'group-heading', id: headingId }, `${RESULT_VIEW[result].text} ${groups[result].length}건`),
      ...groups[result].map((item) => renderItem(item)));
  }));
}

function renderItem({ card, judged, deadline, summary }) {
  const view = RESULT_VIEW[judged.result];
  const change = latestDeadlineChange(card);
  const draft = judged.result === 'check' ? inquiryDraft(card, state.conditions) : null;
  // 복사 안내 영역은 복사 버튼이 있을 때만 (안내 영역이 카드마다 생기지 않게)
  const status = draft ? el('span', { class: 'small', role: 'status' }) : null;
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
    summary ? el('span', { class: 'reason' }, summary) : null,
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
