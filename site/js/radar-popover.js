// 홈 레이더의 점 안내 상자 (#178): 점에 마우스를 올리거나 누르면 그 대회를 읽기 좋게 보여 준다.
// - 올림(미리보기): 점 위에 있는 동안만 보이고 벗어나면 바로 사라진다. 상자가 마우스를 받지 않아 옆의 다른 점을 가리지 않는다.
// - 누름(잡아 둠): 상자 안의 "카드 보기"를 누를 수 있게 된다. 마우스는 점과 상자를 벗어나면 사라지고,
//   터치는 "벗어남"이 없으므로 같은 점을 다시 누르거나 다른 곳을 누르면 사라진다.
// 계속 떠 있으면 레이더가 복잡해지므로 Esc·창 폭 변경·다시 그리기에도 닫는다.
// 레이더는 보조 그림이다. 같은 대회가 아래 목록에 모두 있으므로 점은 Tab 순서에 넣지 않는다 (radar.js).
import { el } from './html.js';
import { boardNames, formatDeadline } from './cards.js';
import { badgeElements, ddayElement } from './page.js';

// 점과 상자 사이 간격(px)
const GAP = 14;
// 상자와 첫 화면(.hero) 가장자리 사이에 남기는 여백(px). 첫 화면은 넘친 부분을 잘라 낸다
const EDGE = 8;
// 잡아 둔 상자: 점에서 상자로 마우스를 옮기는 동안 사라지지 않게 두는 시간
export const HIDE_DELAY_MS = 180;

/** 손가락·펜처럼 "올림"이 없는 입력인지. click의 pointerType이 없는 브라우저는 마우스로 본다 */
export function isTouchPointer(pointerType) {
  return pointerType === 'touch' || pointerType === 'pen';
}

/**
 * 상자를 둘 자리. 점 위에 두고, 위가 모자라면 아래에 둔다. 좌우는 레이더 칸을 벗어나지 않게 민다.
 * 위아래는 레이더 칸 밖으로 slack만큼 나갈 수 있다 (칸 밖도 첫 화면 안이면 잘리지 않는다).
 * 어느 쪽에도 다 들어가지 않으면 더 넓은 쪽에 두고 허용 범위 안으로 당긴다. 이때는 상자가 점을 덮을 수 있다.
 * @param {{x: number, y: number}} anchor  점의 중심 (레이더 칸 왼쪽 위 기준, px)
 * @param {{width: number, height: number}} size  상자 크기
 * @param {{width: number, height: number}} bounds  레이더 칸 크기
 * @param {{gap?: number, slackTop?: number, slackBottom?: number}} [options]  점과 상자 사이 간격, 칸 위·아래로 나갈 수 있는 길이
 * @returns {{left: number, top: number, placement: 'above'|'below'}}
 */
export function popoverPosition(anchor, size, bounds, { gap = GAP, slackTop = 0, slackBottom = 0 } = {}) {
  const left = Math.max(0, Math.min(anchor.x - size.width / 2, bounds.width - size.width));
  // 상자가 쓸 수 있는 위·아래 끝 (레이더 칸 왼쪽 위 기준)
  const topLimit = 0 - Math.max(0, slackTop); // 단항 음수(-0)가 결과에 섞이지 않게 뺄셈으로
  const bottomLimit = bounds.height + Math.max(0, slackBottom);
  const roomAbove = anchor.y - gap - topLimit;
  const roomBelow = bottomLimit - anchor.y - gap;
  const isAbove = roomAbove >= size.height || (roomBelow < size.height && roomAbove >= roomBelow);
  const wanted = isAbove ? anchor.y - gap - size.height : anchor.y + gap;
  // 상자가 쓸 수 있는 높이보다 크면 위 끝에 맞춘다 (제목이 먼저 보이게)
  const maxTop = Math.max(topLimit, bottomLimit - size.height);
  return { left: Math.round(left), top: Math.round(Math.min(maxTop, Math.max(topLimit, wanted))), placement: isAbove ? 'above' : 'below' };
}

/**
 * 누른 뒤의 상태를 정한다.
 * @param {{id: string, isHeld: boolean}|null} active  지금 떠 있는 상자
 * @param {string} cardId  누른 점의 카드
 * @param {string|undefined} pointerType  click 이벤트의 pointerType
 * @returns {'hide'|'hold'|'keep'}  hide: 닫는다, hold: 이 점을 잡아 둔다, keep: 그대로 둔다
 */
export function pickAction(active, cardId, pointerType) {
  const isSameHeld = Boolean(active?.isHeld) && active.id === cardId;
  if (!isSameHeld) return 'hold';
  // 터치로 같은 점을 다시 누르면 닫는다. 마우스는 벗어나면 닫히므로 다시 눌러도 그대로 둔다
  return isTouchPointer(pointerType) ? 'hide' : 'keep';
}

/**
 * 안내 상자의 상태(미리보기 · 잡아 둠 · 닫힘)를 다룬다. 화면은 view가 그리므로 화면 없이 시험할 수 있다.
 * @param {{render: (item: object, blip: object, isHeld: boolean) => void, clear: (blip: object) => void, hasFocus: () => boolean}} view
 *   render: 상자를 그 점 옆에 그린다. clear: 상자를 지우고 그 점의 강조를 푼다. hasFocus: 키보드 포커스가 상자 안에 있는지
 * @returns {{enter: Function, leave: Function, pick: Function, boxEnter: Function, boxLeave: Function, focusLeave: Function, dismiss: Function, current: () => object|null}}
 */
export function createPopoverController(view) {
  // 지금 떠 있는 상자. isHeld: 눌러서 잡아 둠, isTouch: 터치로 잡아 둠(벗어나도 닫지 않는다)
  let active = null;
  let hideTimer = null;
  // 마우스가 지금 떠 있는 상자의 점 위·상자 위에 있는지 (포커스가 상자를 떠날 때 닫을지 정한다)
  let isOverBlip = false;
  let isOverBox = false;

  const cancelHide = () => {
    clearTimeout(hideTimer);
    hideTimer = null;
  };
  const dismiss = () => {
    cancelHide();
    isOverBlip = false;
    isOverBox = false;
    if (!active) return;
    const { blip } = active;
    active = null;
    view.clear(blip);
  };
  const scheduleHide = () => {
    cancelHide();
    hideTimer = setTimeout(() => {
      hideTimer = null;
      // 상자 안 링크에 포커스가 있으면 닫지 않는다 (닫으면 포커스가 문서 처음으로 돌아간다).
      // 포커스가 상자를 떠나면 focusLeave가 다시 닫기를 예약한다
      if (!view.hasFocus()) dismiss();
    }, HIDE_DELAY_MS);
  };
  const show = (item, blip, isHeld, isTouch) => {
    cancelHide();
    if (active && active.blip !== blip) view.clear(active.blip);
    // 레이더를 그리지 못해 상자가 화면에 없으면 view.render가 아무것도 그리지 않는다. 그때는 점도 없어 여기까지 오지 않는다
    active = { id: item.card.id, isHeld, isTouch, blip };
    isOverBox = false;
    view.render(item, blip, isHeld);
  };
  // 마우스로 잡아 둔 상자는 마우스와 포커스가 모두 떠나면 닫는다
  const closeIfLeft = () => {
    if (active?.isHeld && !active.isTouch && !isOverBlip && !isOverBox) scheduleHide();
  };

  return {
    /** 마우스가 점에 들어옴 */
    enter(item, blip) {
      isOverBlip = true;
      // 잡아 둔 점으로 돌아오면 그대로 둔다. 다른 점이면 그 점의 미리보기로 바꾼다
      if (active?.isHeld && active.id === item.card.id) cancelHide();
      else show(item, blip, false, false);
    },
    /** 마우스가 점에서 나감 */
    leave(item) {
      if (!active || active.id !== item.card.id) return;
      isOverBlip = false;
      if (!active.isHeld) dismiss();
      else closeIfLeft();
    },
    /** 점을 누름 */
    pick(item, blip, pointerType) {
      const action = pickAction(active, item.card.id, pointerType);
      if (action === 'hide') dismiss();
      else if (action === 'hold') {
        const isTouch = isTouchPointer(pointerType);
        isOverBlip = !isTouch; // 마우스로 눌렀다면 마우스가 그 점 위에 있다
        show(item, blip, true, isTouch);
      }
    },
    /** 마우스가 상자에 들어옴 (잡아 둔 상자만 마우스를 받는다) */
    boxEnter() {
      isOverBox = true;
      cancelHide();
    },
    /** 마우스가 상자에서 나감 */
    boxLeave() {
      isOverBox = false;
      closeIfLeft();
    },
    /** 키보드 포커스가 상자 밖으로 나감 */
    focusLeave: closeIfLeft,
    /** 바깥 누름 · Esc · 창 폭 변경 · 다시 그리기: 바로 닫는다 */
    dismiss,
    current: () => active,
  };
}

/**
 * 상자에 넣을 내용: 대회 이름, D-day와 마감, 주관·게시판, 확인 상태 배지, 카드 링크.
 * 공지에서 온 글자는 el()이 글자로만 넣는다.
 * @param {{card: object, deadline: object}} item  레이더 점 하나 (home.js가 만든다)
 * @param {object[]} boards  게시판 목록 (게시판 이름 표시용)
 * @returns {HTMLElement[]}
 */
export function popoverContent(item, boards) {
  const { card, deadline, opening } = item;
  const isOpen = deadline.state === 'open';
  const dday = ddayElement(deadline);
  // "마감일 없음"은 상시 모집처럼 읽혀서 홈 목록과 같이 확인이 필요하다고 쓴다
  if (!isOpen) dday.textContent = '마감일 확인 필요';
  const origin = [card.organizer, boardNames(card, boards).join(', ')].filter(Boolean).join(' · ');
  return [
    el('p', { class: 'rd-pop-title' }, card.title),
    opening ? el('span', { class: 'badge s-before' }, opening) : null,
    el('div', { class: 'row' }, dday,
      el('span', { class: 'small' }, isOpen ? `신청 마감 ${formatDeadline(deadline)}` : '공지에서 마감일을 확인하지 못했습니다')),
    origin && el('p', { class: 'small' }, origin),
    el('div', { class: 'row' }, ...badgeElements(card)),
    el('a', { class: 'rd-pop-link', href: `card.html?id=${encodeURIComponent(card.id)}` }, '카드 보기 →'),
    el('p', { class: 'rd-pop-hint small' }, '점을 누르면 카드 보기를 누를 수 있습니다'),
  ].filter(Boolean);
}

/**
 * 안내 상자를 만든다. element를 레이더 칸(#radar)에 SVG와 함께 넣고, handlers를 renderRadar에 넘긴다.
 * document와 window에 닫기용 리스너를 걸므로 화면당 한 번만 부른다 (home.js가 불러올 때 한 번).
 * @param {(item: object) => HTMLElement[]} describe  점 하나의 내용을 만드는 함수
 * @returns {{element: HTMLElement, hide: () => void,
 *   handlers: {onEnter: Function, onLeave: Function, onPick: Function}}}
 */
export function createRadarPopover(describe) {
  const box = el('div', { class: 'rd-pop', hidden: true });
  const controller = createPopoverController({
    render(item, blip, isHeld) {
      const host = box.parentElement;
      if (!host) return; // 레이더를 그리지 못해 상자가 화면에 없다
      blip.classList.add('is-active');
      box.replaceChildren(...describe(item));
      box.dataset.held = String(isHeld);
      box.hidden = false;
      const hostRect = host.getBoundingClientRect();
      const blipRect = blip.getBoundingClientRect();
      // 첫 화면(.hero)은 넘친 부분을 잘라 내므로, 레이더 칸 밖으로는 첫 화면 가장자리 앞까지만 나간다
      const frameRect = (host.closest('.hero') ?? host).getBoundingClientRect();
      const { left, top, placement } = popoverPosition(
        { x: blipRect.left + blipRect.width / 2 - hostRect.left, y: blipRect.top + blipRect.height / 2 - hostRect.top },
        { width: box.offsetWidth, height: box.offsetHeight },
        { width: hostRect.width, height: hostRect.height },
        { slackTop: hostRect.top - frameRect.top - EDGE, slackBottom: frameRect.bottom - hostRect.bottom - EDGE });
      // CSP(style-src 'self')는 style 속성을 막지만 CSSOM으로 값을 주는 것은 막지 않는다
      box.style.left = `${left}px`;
      box.style.top = `${top}px`;
      box.dataset.placement = placement;
    },
    clear(blip) {
      blip.classList.remove('is-active');
      box.hidden = true;
      box.replaceChildren();
    },
    hasFocus: () => box.contains(document.activeElement),
  });

  box.addEventListener('mouseenter', controller.boxEnter);
  box.addEventListener('mouseleave', controller.boxLeave);
  box.addEventListener('focusout', (event) => {
    if (!(event.relatedTarget instanceof Node) || !box.contains(event.relatedTarget)) controller.focusLeave();
  });
  // 상자 밖을 누르면 닫는다. 점을 누른 것은 onPick이 처리한다
  document.addEventListener('pointerdown', (event) => {
    const target = event.target;
    if (target instanceof Element && (box.contains(target) || target.closest('.rd-blip'))) return;
    controller.dismiss();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') controller.dismiss();
  });
  // 창 폭이 바뀌면 점의 자리가 달라진다. 다시 맞추지 않고 닫는다.
  // 높이만 바뀐 것(휴대폰에서 스크롤할 때 주소창이 접힘)에는 닫지 않는다
  let lastWidth = window.innerWidth;
  window.addEventListener('resize', () => {
    if (window.innerWidth === lastWidth) return;
    lastWidth = window.innerWidth;
    controller.dismiss();
  });

  return {
    element: box,
    hide: controller.dismiss,
    handlers: { onEnter: controller.enter, onLeave: controller.leave, onPick: controller.pick },
  };
}
