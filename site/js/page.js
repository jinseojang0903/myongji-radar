// 화면 공통: 데이터 읽기 → 형식 검사 → 기준일 → 예시 안내·오래된 정보 경고, 읽기 실패 표시, 배지·D-day 요소.
// 홈(#21)·추천(#24)·상세(#25)·제작 기록(#26)이 같이 쓴다.
import { el } from './html.js';
import { validateCardData, referenceDate, ddayLabel, cardBadges, bannerText, deadlineOf, STALE_HOURS } from './cards.js';
import { kstDateString } from './dates.js';
import { calendarItems } from './ics.js';
import { loadCardData } from './data.js';
import { readSavedConditions, describeConditions } from './conditions.js';

const BADGE_CLASS = { human: 's-human', need: 's-need', before: 's-before', missing: 's-missing' };

/**
 * 화면에 필요한 카드 데이터를 준비한다. 실패하면 errorTarget에 사용자용 안내를 그리고 null을 돌려준다.
 * 다른 사이트의 프레임 안에서 열리면(클릭재킹) 그리지 않는다. meta CSP로는 frame-ancestors를 걸 수 없어서다.
 * @param {{errorTarget: HTMLElement, bannerTarget?: HTMLElement|null}} targets
 * @returns {Promise<{data: object, getNow: function(): Date, rejected: object[], isSample: boolean}|null>}
 */
export async function loadPage({ errorTarget, bannerTarget = document.getElementById('sample-banner') }) {
  if (window.top !== window.self) {
    errorTarget.replaceChildren(el('p', { class: 'error' }, '이 화면은 다른 사이트 안에서 열 수 없습니다. 주소창에서 직접 열어 주세요.'));
    return null;
  }
  try {
    const loaded = await loadCardData();
    const { data, rejected } = validateCardData(loaded.data);
    if (rejected.length > 0) console.warn('형식이 맞지 않아 뺀 카드·게시판', rejected);
    // 예시 파일을 읽었으면 파일 안의 표시와 상관없이 예시로 다룬다 (예시가 실제 데이터처럼 보이지 않게)
    const isSample = loaded.source === 'sample' || data.sample === true;
    const getNow = () => {
      const actual = new Date();
      const banner = bannerText(data, actual, isSample);
      if (bannerTarget) {
        bannerTarget.textContent = banner ?? '';
        bannerTarget.hidden = !banner;
      }
      return referenceDate(data, actual, isSample);
    };
    getNow();
    return { data, getNow, rejected, isSample };
  } catch (error) {
    console.error('카드 데이터를 준비하지 못함', error);
    errorTarget.replaceChildren(el('p', { class: 'error' },
      '대회 정보를 불러오지 못했습니다. 잠시 뒤 다시 열어 주세요. ',
      '(파일을 직접 열었다면 site/README.md의 "로컬에서 보기"대로 열어야 합니다.)'));
    return null;
  }
}

/**
 * 열린 화면의 시간 판정을 갱신한다. 날짜·마감·일정·경고가 바뀐 경우에만 다시 그린다.
 * 계약의 마감 시각은 분 단위라 매 분 경계 직후 확인한다. 36시간 경고는 초·밀리초도 보존한다.
 * 숨긴 탭에서는 타이머를 멈추고 탭 복귀·창 포커스·뒤로 가기 복원 시 즉시 확인한다.
 */
export function watchPageTime(page, render) {
  if (page.isSample) return () => {}; // 예시는 generated_at 기준을 유지한다.
  const key = (now) => JSON.stringify([
    kstDateString(now), bannerText(page.data, now, false),
    ...page.data.cards.map((card) => deadlineOf(card, now).state),
    ...page.data.cards.flatMap((card) => calendarItems(card, now).map((item) => item.isPast)),
  ]);
  let previous = key(page.getNow());
  let timer;
  const pause = () => clearTimeout(timer);
  const refresh = () => {
    pause();
    if (document.hidden) return;
    const now = page.getNow();
    const current = key(now);
    if (current !== previous) {
      previous = current;
      renderTimeChange(render, now);
    }
    const time = now.getTime();
    const staleAt = new Date(page.data.generated_at).getTime() + STALE_HOURS * 3600000 + 1;
    const nextMinute = time - time % 60000 + 60001;
    timer = setTimeout(refresh, Math.min(nextMinute, staleAt > time ? staleAt : Infinity) - time);
  };
  document.addEventListener('visibilitychange', refresh);
  window.addEventListener('focus', refresh);
  window.addEventListener('pageshow', refresh);
  window.addEventListener('pagehide', pause);
  refresh();
  return () => {
    pause();
    document.removeEventListener('visibilitychange', refresh);
    window.removeEventListener('focus', refresh);
    window.removeEventListener('pageshow', refresh);
    window.removeEventListener('pagehide', pause);
  };
}

// 자동 갱신으로 목록 요소를 교체해도 키보드 포커스를 잃지 않게 한다.
function renderTimeChange(render, now) {
  const active = document.activeElement;
  const cardId = active?.closest('[data-card-id]')?.dataset.cardId;
  render(now);
  if (!active || active.isConnected) return;
  const scope = cardId
    ? [...document.querySelectorAll('[data-card-id]')].find((node) => node.dataset.cardId === cardId)
    : document;
  const candidates = scope ? [scope, ...scope.querySelectorAll('a, button')] : [];
  const match = candidates.find((node) => node.tagName === active.tagName &&
    (active.tagName === 'A' ? node.getAttribute('href') === active.getAttribute('href')
      : node.textContent === active.textContent));
  if (match) match.focus({ preventScroll: true });
  else {
    const status = document.getElementById('list-status') ?? document.getElementById('result-heading');
    if (status) { status.tabIndex = -1; status.focus({ preventScroll: true }); }
  }
}

/** 카드 단위 배지 요소들 (화면설계.md 4절) */
export function badgeElements(card) {
  return cardBadges(card).map((badge) => el('span', { class: `badge ${BADGE_CLASS[badge.kind]}` }, badge.text));
}

/** D-day 요소. 7일 이내만 빨강, 마감일이 없거나 읽을 수 없으면 회색 (마감이 확실한 대회와 같은 색으로 보이지 않게) */
export function ddayElement(deadline, extraClass = '') {
  const dday = ddayLabel(deadline);
  const tone = deadline.state !== 'open' ? 'none' : dday.isUrgent ? '' : 'far';
  return el('span', { class: ['dday', tone, extraClass].filter(Boolean).join(' ') }, dday.text);
}

/** 목록 카드 왼쪽 띠 색. D-day 칩과 같은 기준(ddayLabel의 7일 이내)을 쓴다: 마감 임박·마감 전·마감일 없음 */
export function deadlineTone(deadline) {
  if (deadline.state !== 'open') return 'none';
  return ddayLabel(deadline).isUrgent ? 'soon' : 'open';
}

/**
 * 머리글의 "내 조건" 한 줄. 넘긴 조건이 없으면 저장된 조건(계약 5절)을 읽는다. 모든 화면이 같은 표시를 쓴다.
 * @param {object|null} [conditions]
 */
export function showConditionSummary(conditions = readSavedConditions()) {
  const target = document.getElementById('cond-summary');
  if (target) target.textContent = `내 조건: ${describeConditions(conditions) ?? '아직 고르지 않음'}`;
}
