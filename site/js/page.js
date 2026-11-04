// 화면 공통: 데이터 읽기 → 형식 검사 → 기준일 → 예시 안내, 읽기 실패 표시, 배지·D-day 요소.
// 홈(#21)·추천(#24)·상세(#25)·제작 기록(#26)이 같이 쓴다.
import { el } from './html.js';
import { validateCardData, referenceDate, ddayLabel, cardBadges } from './cards.js';
import { loadCardData } from './data.js';

const BADGE_CLASS = { human: 's-human', need: 's-need', before: 's-before', missing: 's-missing' };

/**
 * 화면에 필요한 카드 데이터를 준비한다. 실패하면 errorTarget에 사용자용 안내를 그리고 null을 돌려준다.
 * 다른 사이트의 프레임 안에서 열리면(클릭재킹) 그리지 않는다. meta CSP로는 frame-ancestors를 걸 수 없어서다.
 * @param {{errorTarget: HTMLElement, bannerTarget?: HTMLElement|null}} targets
 * @returns {Promise<{data: object, now: Date, rejected: object[], isSample: boolean}|null>}
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
    if (isSample && bannerTarget) {
      bannerTarget.textContent = typeof data.sample_note === 'string' && data.sample_note !== '' ? data.sample_note : '예시 데이터입니다.';
      bannerTarget.hidden = false;
    }
    return { data, now: referenceDate(data, new Date(), isSample), rejected, isSample };
  } catch (error) {
    console.error('카드 데이터를 준비하지 못함', error);
    errorTarget.replaceChildren(el('p', { class: 'error' },
      '대회 정보를 불러오지 못했습니다. 잠시 뒤 다시 열어 주세요. ',
      '(파일을 직접 열었다면 site/README.md의 "로컬에서 보기"대로 열어야 합니다.)'));
    return null;
  }
}

/** 카드 단위 배지 요소들 (화면설계.md 4절) */
export function badgeElements(card) {
  return cardBadges(card).map((badge) => el('span', { class: `badge ${BADGE_CLASS[badge.kind]}` }, badge.text));
}

/** D-day 요소. 7일 이내만 빨강 */
export function ddayElement(deadline, extraClass = '') {
  const dday = ddayLabel(deadline);
  return el('span', { class: ['dday', dday.isUrgent ? '' : 'far', extraClass].filter(Boolean).join(' ') }, dday.text);
}
