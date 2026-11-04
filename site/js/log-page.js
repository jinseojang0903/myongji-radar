// 제작 기록 화면 (#26): 처리 단계별 담당, 정확도 표, 프롬프트 버전, AI가 틀린 것과 잡은 방법, 1분 AI 검수 체험, 사용한 AI 도구.
// 계산은 log.js(Node 테스트), 여기서는 그리기와 버튼만. 글자는 모두 el()/textContent로 넣는다.
import { el, externalLink } from './html.js';
import { validateLogData, measureText, checkAnswer, repoFileUrl, stageKind, optionMark } from './log.js';

const STAGE_CLASS = { code: 'step', rule: 'step rule', ai: 'step ai', human: 'step human' };

main().catch((error) => {
  console.error('제작 기록을 그리지 못함', error);
  showError('제작 기록을 불러오지 못했습니다. 잠시 뒤 다시 열어 주세요.');
});

async function main() {
  // 다른 사이트의 프레임 안에서 열리면 그리지 않는다 (page.js loadPage와 같은 이유: meta CSP로는 frame-ancestors를 못 건다)
  if (window.top !== window.self) {
    showError('이 화면은 다른 사이트 안에서 열 수 없습니다. 주소창에서 직접 열어 주세요.');
    return;
  }
  const response = await fetch('data/log.json', { cache: 'no-cache' });
  if (!response.ok) throw new Error(`log.json 응답 ${response.status}`);
  const { data, rejected } = validateLogData(await response.json());
  if (rejected.length > 0) console.warn('형식이 맞지 않아 뺀 제작 기록 항목', rejected);

  document.getElementById('log-updated').textContent = `기록 갱신 ${data.updated_at}`;
  // 항목이 모두 빠진 목록은 빈 표 대신 안내 문구
  const fill = (id, items, render) => document.getElementById(id).replaceChildren(
    ...(items.length > 0 ? [render(items)].flat() : [el(id === 'log-stages' ? 'li' : 'p', { class: 'empty' }, '아직 기록이 없습니다.')]));
  fill('log-stages', data.stages, renderStages);
  fill('log-accuracy', data.accuracy, renderAccuracy);
  fill('log-prompts', data.prompts, renderPrompts);
  fill('log-errors', data.ai_errors, renderErrors);
  fill('log-quiz', data.quiz, (quizzes) => quizzes.map(renderQuiz));
  fill('log-tools', data.tools, renderTools);
}

function showError(message) {
  document.getElementById('log-main')?.replaceChildren(el('p', { class: 'error' }, message));
}

// 저장소 파일 링크. el()은 사이트 밖 주소를 막으므로, 검사한 저장소 주소만 여기서 직접 단다
function repoLink(path, text) {
  const url = repoFileUrl(path);
  if (!url) return null;
  const link = el('a', {}, text);
  link.href = url;
  link.rel = 'noopener noreferrer';
  link.target = '_blank';
  return link;
}

function renderStages(stages) {
  return stages.map((stage) => el('li', {},
    el('span', { class: STAGE_CLASS[stageKind(stage.by)] }, stage.by),
    ' ', el('b', {}, stage.name),
    stage.status === '동작' ? null : el('span', { class: 'badge s-before' }, stage.status),
    el('div', { class: 'small' }, stage.detail)));
}

// 측정값: 값이 있으면 언제·어디서 쟀는지를 함께, 없으면 "측정 중"
function measureNodes(measure) {
  const shown = measureText(measure);
  return [el('span', { class: shown.isPending ? 'pending' : '' }, shown.text), shown.detail ? el('div', { class: 'small' }, shown.detail) : null];
}

function renderAccuracy(rows) {
  const cell = (measure) => el('td', { class: measure === null ? 'pending' : '' }, ...measureNodes(measure));
  return el('table', { class: 'metrics' },
    el('caption', { class: 'sr-only' }, '방법별 정확도. 측정하지 않은 값은 측정 중'),
    el('thead', {}, el('tr', {},
      el('th', { scope: 'col' }, '방법'), el('th', { scope: 'col' }, '채운 항목 수'),
      el('th', { scope: 'col' }, '항목 정확도'), el('th', { scope: 'col' }, '위험 오판 (불가 → 가능)'))),
    el('tbody', {}, ...rows.map((row) => el('tr', {},
      el('th', { scope: 'row' }, row.method), cell(row.filled), cell(row.field_accuracy), cell(row.risky_misreads)))));
}

function renderPrompts(prompts) {
  return prompts.map((prompt) => el('div', {},
    el('b', {}, prompt.version), ` · ${prompt.date} · ${prompt.changes}`,
    el('div', { class: 'small' }, `이유: ${prompt.reason}`),
    el('div', { class: 'small' }, '정확도: ', ...measureNodes(prompt.accuracy)),
    prompt.path ? el('div', { class: 'small' }, repoLink(prompt.path, '원문과 바꾼 이유 보기')) : null));
}

function renderErrors(errors) {
  return el('table', { class: 'metrics' },
    el('caption', { class: 'sr-only' }, 'AI가 틀린 것과 잡은 방법'),
    el('thead', {}, el('tr', {},
      el('th', { scope: 'col' }, '날짜·작업'), el('th', { scope: 'col' }, 'AI가 틀린 것'),
      el('th', { scope: 'col' }, '잡은 방법'), el('th', { scope: 'col' }, '고친 것'))),
    el('tbody', {}, ...errors.map((error) => el('tr', {},
      el('th', { scope: 'row' }, error.date, el('div', { class: 'small' }, error.where)),
      el('td', {}, error.ai_did),
      el('td', {}, error.caught_by),
      el('td', {}, error.fix, error.path ? el('div', { class: 'small' }, repoLink(error.path, '근거 문서')) : null)))));
}

function renderQuiz(quiz) {
  // 결과 영역은 처음부터 보이게 두고 내용만 채운다 (숨겼다 보이면 화면 읽기 프로그램이 읽지 않는 경우가 많다)
  const result = el('div', { role: 'status' });
  const buttons = quiz.options.map((option, index) => {
    const button = el('button', { type: 'button', class: 'chip', 'aria-pressed': 'false' }, `${optionMark(index)} ${option}`);
    button.addEventListener('click', () => {
      const { isCorrect } = checkAnswer(quiz, index);
      buttons.forEach((other, otherIndex) => other.setAttribute('aria-pressed', String(otherIndex === index)));
      result.replaceChildren(el('div', { class: 'ev' },
        el('b', {}, isCorrect ? '명지레이더의 선택과 같습니다.' : `명지레이더의 선택: ${optionMark(quiz.answer)}`),
        el('div', {}, quiz.explanation),
        quiz.path ? el('div', { class: 'small' }, repoLink(quiz.path, '근거 문서')) : null));
    });
    return button;
  });
  return el('div', { class: 'quiz' },
    el('p', { class: 'small' }, quiz.kind),
    el('p', {}, quiz.question),
    ...quiz.quotes.map((quote) => el('div', { class: 'ev' },
      el('mark', {}, quote.text),
      el('div', { class: 'meta' }, el('span', { class: 'loc' }, quote.location),
        quote.url ? ' · ' : '', quote.url ? externalLink(quote.url, '원문 보기') : null))),
    el('div', { class: 'chips', role: 'group', 'aria-label': '답 고르기' }, ...buttons),
    result);
}

function renderTools(tools) {
  return el('table', { class: 'metrics' },
    el('caption', { class: 'sr-only' }, '사용한 AI 도구'),
    el('thead', {}, el('tr', {}, el('th', { scope: 'col' }, '도구'), el('th', { scope: 'col' }, '쓴 곳'), el('th', { scope: 'col' }, '상태'))),
    el('tbody', {}, ...tools.map((tool) => el('tr', {},
      el('th', { scope: 'row' }, tool.name),
      el('td', {}, tool.used_for),
      el('td', {}, tool.status === '사용' ? '사용' : el('span', { class: 'badge s-before' }, `예정${tool.issue ? ` (${tool.issue})` : ''}`))))));
}
