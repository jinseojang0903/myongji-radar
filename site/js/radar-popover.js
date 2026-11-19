// 홈 레이더의 점 안내 상자 (#178): 점에 마우스를 올리거나 누르면 그 대회를 읽기 좋게 보여 준다.
// - 올림(미리보기): 점 위에 있는 동안만 보이고 벗어나면 바로 사라진다. 상자가 마우스를 받지 않아 옆의 다른 점을 가리지 않는다.
// - 누름(잡아 둠): 상자 안의 "카드 보기"를 누를 수 있게 된다. 마우스는 점과 상자를 벗어나면 사라지고,
//   터치는 "벗어남"이 없으므로 같은 점을 다시 누르거나 다른 곳을 누르면 사라진다.
// 계속 떠 있으면 레이더가 복잡해지므로 Esc·창 크기 변경·다시 그리기에도 닫는다.
// 레이더는 보조 그림이다. 같은 대회가 아래 목록에 모두 있으므로 점은 Tab 순서에 넣지 않는다 (radar.js).
import { el } from './html.js';
import { boardNames, formatDeadline } from './cards.js';
import { badgeElements, ddayElement } from './page.js';

// 점과 상자 사이 간격(px)
const GAP = 14;
// 잡아 둔 상자: 점에서 상자로 마우스를 옮기는 동안 사라지지 않게 두는 시간
const HIDE_DELAY_MS = 180;

/**
 * 상자를 둘 자리. 점 위에 두고, 위가 모자라면 아래에 둔다. 좌우는 레이더 칸을 벗어나지 않게 민다.
 * @param {{x: number, y: number}} anchor  점의 중심 (레이더 칸 왼쪽 위 기준, px)
 * @param {{width: number, height: number}} size  상자 크기
 * @param {{width: number, height: number}} bounds  레이더 칸 크기
 * @param {number} [gap]  점과 상자 사이 간격
 * @returns {{left: number, top: number, placement: 'above'|'below'}}
 */
export function popoverPosition(anchor, size, bounds, gap = GAP) {
  const left = Math.max(0, Math.min(anchor.x - size.width / 2, bounds.width - size.width));
  const roomAbove = anchor.y - gap;
  const roomBelow = bounds.height - anchor.y - gap;
  // 어느 쪽에도 다 들어가지 않으면 더 넓은 쪽에 둔다 (점을 덜 가린다)
  const isAbove = roomAbove >= size.height || (roomBelow < size.height && roomAbove >= roomBelow);
  const top = isAbove ? Math.max(0, roomAbove - size.height) : anchor.y + gap;
  return { left: Math.round(left), top: Math.round(top), placement: isAbove ? 'above' : 'below' };
}

/**
 * 누른 뒤의 상태를 정한다 (화면 없이 시험할 수 있게 따로 둔다).
 * @param {{id: string, isHeld: boolean, isTouch: boolean}|null} active  지금 떠 있는 상자
 * @param {string} cardId  누른 점의 카드
 * @param {string|undefined} pointerType  click 이벤트의 pointerType
 * @returns {'hide'|'hold'|'keep'}  hide: 닫는다, hold: 이 점을 잡아 둔다, keep: 그대로 둔다
 */
export function pickAction(active, cardId, pointerType) {
  const isTouch = pointerType === 'touch' || pointerType === 'pen';
  const isSameHeld = Boolean(active?.isHeld) && active.id === cardId;
  if (!isSameHeld) return 'hold';
  // 터치로 같은 점을 다시 누르면 닫는다. 마우스는 벗어나면 닫히므로 다시 눌러도 그대로 둔다
  return isTouch ? 'hide' : 'keep';
}

/**
 * 상자에 넣을 내용: 대회 이름, D-day와 마감, 주관·게시판, 확인 상태 배지, 카드 링크.
 * 공지에서 온 글자는 el()이 글자로만 넣는다.
 * @param {{card: object, deadline: object}} item  레이더 점 하나 (home.js가 만든다)
 * @param {object[]} boards  게시판 목록 (게시판 이름 표시용)
 * @returns {HTMLElement[]}
 */
export function popoverContent(item, boards) {
  const { card, deadline } = item;
  const isOpen = deadline.state === 'open';
  const dday = ddayElement(deadline);
  // "마감일 없음"은 상시 모집처럼 읽혀서 홈 목록과 같이 확인이 필요하다고 쓴다
  if (!isOpen) dday.textContent = '마감일 확인 필요';
  const origin = [card.organizer, boardNames(card, boards).join(', ')].filter(Boolean).join(' · ');
  return [
    el('p', { class: 'rd-pop-title' }, card.title),
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
 * @param {(item: object) => HTMLElement[]} describe  점 하나의 내용을 만드는 함수
 * @returns {{element: HTMLElement, hide: () => void,
 *   handlers: {onEnter: Function, onLeave: Function, onPick: Function}}}
 */
export function createRadarPopover(describe) {
  const box = el('div', { class: 'rd-pop', hidden: true });
  // 지금 떠 있는 상자. isHeld: 눌러서 잡아 둠(상자를 누를 수 있다), isTouch: 터치로 잡아 둠(벗어나도 닫지 않는다)
  let active = null;
  let hideTimer = null;

  const cancelHide = () => {
    clearTimeout(hideTimer);
    hideTimer = null;
  };
  const hide = () => {
    cancelHide();
    if (active) active.blip.classList.remove('is-active');
    active = null;
    box.hidden = true;
    box.replaceChildren();
  };
  const scheduleHide = () => {
    cancelHide();
    hideTimer = setTimeout(hide, HIDE_DELAY_MS);
  };
  const show = (item, blip, isHeld, isTouch) => {
    cancelHide();
    if (active) active.blip.classList.remove('is-active');
    active = { id: item.card.id, isHeld, isTouch, blip };
    blip.classList.add('is-active');
    box.replaceChildren(...describe(item));
    box.dataset.held = String(isHeld);
    box.hidden = false;
    const host = box.parentElement;
    if (!host) return;
    const hostRect = host.getBoundingClientRect();
    const blipRect = blip.getBoundingClientRect();
    const { left, top, placement } = popoverPosition(
      { x: blipRect.left + blipRect.width / 2 - hostRect.left, y: blipRect.top + blipRect.height / 2 - hostRect.top },
      { width: box.offsetWidth, height: box.offsetHeight },
      { width: hostRect.width, height: hostRect.height });
    // CSP(style-src 'self')는 style 속성을 막지만 CSSOM으로 값을 주는 것은 막지 않는다
    box.style.left = `${left}px`;
    box.style.top = `${top}px`;
    box.dataset.placement = placement;
  };

  // 잡아 둔 상자만 마우스를 받는다 (CSS). 상자 위에 있는 동안은 닫지 않는다
  box.addEventListener('mouseenter', cancelHide);
  box.addEventListener('mouseleave', () => {
    if (active?.isHeld && !active.isTouch) scheduleHide();
  });
  // 상자 밖을 누르면 닫는다. 점을 누른 것은 onPick이 처리한다
  document.addEventListener('pointerdown', (event) => {
    if (!active) return;
    const target = event.target;
    if (target instanceof Element && (box.contains(target) || target.closest('.rd-blip'))) return;
    hide();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && active) hide();
  });
  // 창 크기가 바뀌면 점의 자리가 달라진다. 다시 맞추지 않고 닫는다
  window.addEventListener('resize', hide);

  return {
    element: box,
    hide,
    handlers: {
      onEnter(item, blip) {
        // 잡아 둔 점으로 돌아오면 그대로 둔다. 다른 점이면 그 점의 미리보기로 바꾼다
        if (active?.isHeld && active.id === item.card.id) cancelHide();
        else show(item, blip, false, false);
      },
      onLeave(item) {
        if (!active || active.id !== item.card.id) return;
        if (!active.isHeld) hide();
        else if (!active.isTouch) scheduleHide();
      },
      onPick(item, blip, pointerType) {
        const action = pickAction(active, item.card.id, pointerType);
        if (action === 'hide') hide();
        else if (action === 'hold') show(item, blip, true, pointerType === 'touch' || pointerType === 'pen');
      },
    },
  };
}
