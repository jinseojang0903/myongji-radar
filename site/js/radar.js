// 홈 첫 화면 레이더: 중심까지의 거리 = 마감까지 남은 날. 마감일을 모르는 대회는 점선 궤도 밖에 '?'로 둔다.
// 훑어보기용 그림이다. 같은 정보가 아래 마감 구간 목록에 모두 있으므로 점은 Tab 순서에 넣지 않는다
// (키보드·화면 읽기 사용자는 목록으로 같은 대회에 닿는다). 그림 전체에는 요약 이름(role="img")을 붙인다.
// 점에 마우스를 올리거나 누르면 radar-popover.js의 안내 상자가 그 대회를 보여 준다 (#178).

const SVG_NS = 'http://www.w3.org/2000/svg';
// 마감 구간 기준(일). 홈 목록의 구간(home.js)과 레이더 고리가 같은 값을 쓴다.
// 7일은 cards.js의 "7일 이내 마감" 필터(URGENT_DAYS)와 같은 기준이다
export const WEEK_DAYS = 7;
export const MONTH_DAYS = 30;
// 반지름: 7일 고리 · 30일 고리 · 바깥(30일 뒤) · 궤도 밖(마감일 확인 필요)
const R = { week: 58, month: 112, outer: 172, unknown: 192 };

// 점의 각도는 카드 id로 정한다. 순번으로 정하면 검색·정렬을 바꿀 때마다 점이 통째로 움직인다
function angleOf(cardId) {
  let hash = 0;
  for (const char of cardId) hash = (hash * 31 + char.codePointAt(0)) >>> 0;
  return (hash % 360) * (Math.PI / 180);
}

function svg(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value !== null && value !== undefined) node.setAttribute(name, String(value));
  }
  for (const child of children) if (child) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
}

/** 남은 날 → 반지름. 구간마다 따로 늘려 7일 이내가 넓게 보이게 한다 */
function radiusFor(daysLeft) {
  if (daysLeft <= WEEK_DAYS) return 14 + (daysLeft / WEEK_DAYS) * (R.week - 14);
  if (daysLeft <= MONTH_DAYS) return R.week + ((daysLeft - WEEK_DAYS) / (MONTH_DAYS - WEEK_DAYS)) * (R.month - R.week);
  return Math.min(R.outer - 6, R.month + ((daysLeft - MONTH_DAYS) / 60) * (R.outer - R.month));
}

/**
 * 레이더 SVG를 만든다.
 * @param {{card: object, deadline: object, isUrgent: boolean, label: string}[]} items  마감 전 대회 (화면 순서대로)
 * @param {{onEnter?: Function, onLeave?: Function, onPick?: Function}} [handlers]  점에 마우스가 들어옴·나감·누름.
 *   모두 (item, 점 요소)를 받고, onPick은 누른 장치(pointerType)도 받는다 (radar-popover.js)
 * @returns {SVGSVGElement}
 */
export function renderRadar(items, handlers = {}) {
  const known = items.filter((item) => item.deadline.state === 'open');
  const unknown = items.filter((item) => item.deadline.state !== 'open');

  const blips = known.map((item) => {
    const angle = angleOf(item.card.id);
    const r = radiusFor(item.deadline.daysLeft);
    return blip(item, Math.cos(angle) * r, Math.sin(angle) * r, item.isUrgent ? 'urgent' : 'open', handlers);
  });
  // 궤도 밖: 아래쪽 호에 고르게 늘어놓는다
  const spread = Math.min(150, 22 * Math.max(unknown.length - 1, 1));
  const unknownBlips = unknown.map((item, index) => {
    const deg = 90 + (unknown.length === 1 ? 0 : -spread / 2 + (spread * index) / (unknown.length - 1));
    const rad = deg * (Math.PI / 180);
    return blip(item, Math.cos(rad) * R.unknown, Math.sin(rad) * R.unknown, 'unknown', handlers);
  });

  const ring = (r, label, cls) => svg('g', { class: `rd-ring ${cls}` },
    svg('circle', { r }),
    svg('text', cls === 'rd-ring-unknown' ? { x: 0, y: -r - 8, 'text-anchor': 'middle' } : { x: r - 4, y: -6, 'text-anchor': 'end' }, label));
  const sweepEdge = R.outer;
  return svg('svg', {
    class: 'rd', viewBox: '-215 -215 430 430', role: 'img', focusable: 'false',
    'aria-label': `마감 레이더: 마감일이 있는 대회 ${known.length}건, 마감일 확인 필요 ${unknown.length}건. 같은 내용이 아래 목록에 있습니다.`,
  },
  svg('defs', {},
    svg('radialGradient', { id: 'rd-glow' },
      svg('stop', { offset: '0%', 'stop-color': '#3ee6c1', 'stop-opacity': '.2' }),
      svg('stop', { offset: '100%', 'stop-color': '#3ee6c1', 'stop-opacity': '0' })),
    svg('linearGradient', { id: 'rd-sweep', x1: '0', y1: '0', x2: '1', y2: '0' },
      svg('stop', { offset: '0%', 'stop-color': '#3ee6c1', 'stop-opacity': '0' }),
      svg('stop', { offset: '100%', 'stop-color': '#3ee6c1', 'stop-opacity': '.35' }))),
  svg('circle', { r: R.outer, fill: 'url(#rd-glow)' }),
  ring(R.unknown, '궤도 밖 · 마감일 ?', 'rd-ring-unknown'),
  ring(R.outer, '30일 뒤', 'rd-ring-outer'),
  ring(R.month, '30일', 'rd-ring-month'),
  ring(R.week, '7일', 'rd-ring-week'),
  svg('line', { class: 'rd-axis', x1: -R.outer, y1: 0, x2: R.outer, y2: 0 }),
  svg('line', { class: 'rd-axis', x1: 0, y1: -R.outer, x2: 0, y2: R.outer }),
  svg('g', { class: 'rd-sweep' }, svg('path', {
    d: `M0 0 L${sweepEdge} 0 A${sweepEdge} ${sweepEdge} 0 0 0 ${(Math.cos(-0.6) * sweepEdge).toFixed(1)} ${(Math.sin(-0.6) * sweepEdge).toFixed(1)} Z`,
    fill: 'url(#rd-sweep)',
  })),
  svg('circle', { class: 'rd-core', r: 5 }),
  ...unknownBlips, ...blips);
}

function blip(item, x, y, tone, handlers) {
  const group = svg('g', { class: `rd-blip rd-blip-${tone}`, transform: `translate(${x.toFixed(1)} ${y.toFixed(1)})` },
    svg('circle', { class: 'rd-hit', r: 18 }),
    tone === 'unknown' ? null : svg('circle', { class: 'rd-ping', r: 9 }),
    svg('circle', { class: 'rd-pip', r: tone === 'unknown' ? 10 : 6.5 }),
    tone === 'unknown' ? svg('text', { class: 'rd-q', y: 4.5 }, '?') : null);
  // 브라우저 기본 말풍선(<title>)은 늦게 뜨고 글자가 작아 안내 상자로 바꿨다. 둘 다 두면 겹쳐 보인다
  // 올림은 마우스에만 반응한다. 터치가 만드는 가짜 마우스 이벤트로 상자를 띄우면 일부 휴대폰 브라우저가 첫 탭의 click을 보내지 않는다
  group.addEventListener('pointerenter', (event) => { if (event.pointerType === 'mouse') handlers.onEnter?.(item, group); });
  group.addEventListener('pointerleave', (event) => { if (event.pointerType === 'mouse') handlers.onLeave?.(item, group); });
  group.addEventListener('click', (event) => handlers.onPick?.(item, group, event.pointerType));
  return group;
}
