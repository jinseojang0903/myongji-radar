// 홈 레이더 테스트: 점 안내 상자(site/js/radar-popover.js, #178)의 자리 계산과 상태(미리보기·잡아 둠·닫힘) 전이,
// 겹친 점을 떼어 놓는 자리 잡기(site/js/radar.js layoutBlips, #184).
// 실제 마우스·터치 입력과 화면 배치는 브라우저에서 확인한다 (PR #178의 확인 방법).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { popoverPosition, pickAction, isTouchPointer, createPopoverController, HIDE_DELAY_MS } from '../site/js/radar-popover.js';
import { layoutBlips, BLIP_GAP } from '../site/js/radar.js';

const RADAR = readFileSync(new URL('../site/js/radar.js', import.meta.url), 'utf8');
const POPOVER = readFileSync(new URL('../site/js/radar-popover.js', import.meta.url), 'utf8');
const CSS = readFileSync(new URL('../site/css/style.css', import.meta.url), 'utf8');

const BOUNDS = { width: 500, height: 500 };
const SIZE = { width: 300, height: 140 };

test('안내 상자는 점 위 가운데에 두고, 위가 모자라면 아래에 둔다', () => {
  assert.deepEqual(popoverPosition({ x: 250, y: 300 }, SIZE, BOUNDS, { gap: 14 }), { left: 100, top: 146, placement: 'above' });
  // 점이 위쪽에 있어 위로는 들어가지 않는다
  assert.deepEqual(popoverPosition({ x: 250, y: 60 }, SIZE, BOUNDS, { gap: 14 }), { left: 100, top: 74, placement: 'below' });
  // 딱 맞게 들어가면 위에 둔다
  assert.equal(popoverPosition({ x: 250, y: 154 }, SIZE, BOUNDS, { gap: 14 }).placement, 'above');
  assert.equal(popoverPosition({ x: 250, y: 153 }, SIZE, BOUNDS, { gap: 14 }).placement, 'below');
});

test('안내 상자는 레이더 칸의 좌우를 벗어나지 않는다', () => {
  assert.equal(popoverPosition({ x: 20, y: 300 }, SIZE, BOUNDS).left, 0);
  assert.equal(popoverPosition({ x: 490, y: 300 }, SIZE, BOUNDS).left, 200);
  // 휴대폰처럼 상자가 칸만큼 넓으면 왼쪽 끝에 맞춘다
  assert.equal(popoverPosition({ x: 300, y: 300 }, { width: 340, height: 140 }, { width: 340, height: 340 }).left, 0);
  assert.equal(popoverPosition({ x: 100, y: 300 }, { width: 400, height: 140 }, { width: 340, height: 340 }).left, 0, '칸보다 넓어도 음수가 되지 않는다');
});

test('위아래 어디에도 다 들어가지 않으면 더 넓은 쪽에 두되, 허용 범위 밖으로 나가지 않는다', () => {
  const tall = { width: 300, height: 320 };
  const bounds = { width: 340, height: 340 };
  // 칸 밖으로 나갈 수 없을 때: 아래가 넓지만 다 들어가지 않으므로 칸 아래 끝에 맞춘다 (잘려서 링크를 못 누르는 것보다 점을 덮는 편이 낫다)
  assert.deepEqual(popoverPosition({ x: 170, y: 120 }, tall, bounds, { gap: 14 }), { left: 20, top: 20, placement: 'below' });
  assert.deepEqual(popoverPosition({ x: 170, y: 250 }, tall, bounds, { gap: 14 }), { left: 20, top: 0, placement: 'above' });
  for (const y of [0, 40, 170, 300, 340]) {
    const { top, left } = popoverPosition({ x: 170, y }, tall, bounds, { gap: 14 });
    assert.ok(top >= 0 && top + tall.height <= bounds.height, `y=${y}: 위아래로 칸 안 (top=${top})`);
    assert.ok(left >= 0 && Number.isInteger(top) && Number.isInteger(left), `y=${y}`);
  }
});

test('레이더 칸 밖이라도 첫 화면 안이면 그만큼은 나갈 수 있다', () => {
  const tall = { width: 300, height: 320 };
  const bounds = { width: 340, height: 340 };
  // 휴대폰: 레이더 아래에 첫 화면 글이 이어진다. 아래로 400px까지 나갈 수 있으면 점 아래에 그대로 둔다
  assert.deepEqual(popoverPosition({ x: 170, y: 120 }, tall, bounds, { gap: 14, slackBottom: 400 }), { left: 20, top: 134, placement: 'below' });
  // PC: 위아래 여백이 40px씩. 그 안에서만 나간다
  const pc = popoverPosition({ x: 170, y: 170 }, tall, bounds, { gap: 14, slackTop: 40, slackBottom: 40 });
  assert.ok(pc.top >= -40 && pc.top + tall.height <= 340 + 40, `top=${pc.top}`);
  // 위로 나갈 수 있으면 위에 다 들어간다
  assert.deepEqual(popoverPosition({ x: 170, y: 300 }, tall, bounds, { gap: 14, slackTop: 40 }), { left: 20, top: -34, placement: 'above' });
  // 음수 여유는 0으로 본다
  assert.deepEqual(popoverPosition({ x: 250, y: 300 }, SIZE, BOUNDS, { gap: 14, slackTop: -5, slackBottom: -5 }),
    popoverPosition({ x: 250, y: 300 }, SIZE, BOUNDS, { gap: 14 }));
});

test('누르면 잡아 두고, 터치로 같은 점을 다시 누르면 닫는다', () => {
  assert.deepEqual(['touch', 'pen', 'mouse', '', undefined].map(isTouchPointer), [true, true, false, false, false]);
  const preview = { id: 'a', isHeld: false };
  const held = { id: 'a', isHeld: true };
  for (const pointerType of ['mouse', 'touch', 'pen', '', undefined]) {
    assert.equal(pickAction(null, 'a', pointerType), 'hold', String(pointerType));
    assert.equal(pickAction(preview, 'a', pointerType), 'hold', String(pointerType));
    assert.equal(pickAction(held, 'b', pointerType), 'hold', `다른 점: ${pointerType}`);
  }
  // 터치에는 "벗어남"이 없어 같은 점을 다시 누르는 것이 닫는 방법이다
  assert.equal(pickAction(held, 'a', 'touch'), 'hide');
  assert.equal(pickAction(held, 'a', 'pen'), 'hide');
  // 마우스는 벗어나면 닫히므로 다시 눌러도 그대로 둔다 (누를 때마다 깜빡이지 않게)
  assert.equal(pickAction(held, 'a', 'mouse'), 'keep');
  assert.equal(pickAction(held, 'a', undefined), 'keep', 'pointerType이 없는 브라우저는 마우스로 본다');
});

// 화면 대신 무엇을 그리고 지웠는지 적어 두는 가짜 view
function fakeView() {
  const view = {
    shown: null, log: [], focusInside: false,
    render(item, blip, isHeld) {
      view.shown = { id: item.card.id, blip, isHeld };
      view.log.push(`render ${item.card.id} ${isHeld ? 'held' : 'preview'}`);
    },
    clear(blip) {
      if (view.shown?.blip === blip) view.shown = null;
      view.log.push(`clear ${blip}`);
    },
    hasFocus: () => view.focusInside,
  };
  return view;
}
const item = (id) => ({ card: { id } });

test('올리면 미리보기가 뜨고, 점에서 벗어나면 바로 닫힌다', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const view = fakeView();
  const popover = createPopoverController(view);
  popover.enter(item('a'), 'blip-a');
  assert.deepEqual(view.shown, { id: 'a', blip: 'blip-a', isHeld: false });
  popover.leave(item('a'));
  assert.equal(view.shown, null, '미리보기는 기다리지 않고 닫는다');
  assert.equal(popover.current(), null);
  // 이미 닫힌 뒤 늦게 온 벗어남, 다른 점의 벗어남은 무시한다
  popover.enter(item('b'), 'blip-b');
  popover.leave(item('a'));
  assert.equal(view.shown.id, 'b');
});

test('다른 점으로 옮기면 그 점의 미리보기로 바뀌고 앞 점의 강조가 풀린다', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const view = fakeView();
  const popover = createPopoverController(view);
  popover.enter(item('a'), 'blip-a');
  popover.enter(item('b'), 'blip-b'); // 겹친 점에서는 벗어남 없이 들어옴이 먼저 올 수 있다
  assert.deepEqual(view.log, ['render a preview', 'clear blip-a', 'render b preview']);
  assert.deepEqual(view.shown, { id: 'b', blip: 'blip-b', isHeld: false });
});

test('마우스로 누르면 잡아 두고, 점과 상자를 모두 벗어난 뒤에야 닫힌다', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const view = fakeView();
  const popover = createPopoverController(view);
  popover.enter(item('a'), 'blip-a');
  popover.pick(item('a'), 'blip-a', 'mouse');
  assert.equal(view.shown.isHeld, true);

  // 점 → 상자로 옮기는 동안은 닫지 않는다
  popover.leave(item('a'));
  t.mock.timers.tick(HIDE_DELAY_MS - 1);
  assert.equal(view.shown?.id, 'a');
  popover.boxEnter();
  t.mock.timers.tick(HIDE_DELAY_MS * 5);
  assert.equal(view.shown?.id, 'a', '상자 위에 있는 동안은 그대로');

  // 상자에서 점으로 돌아와도 그대로
  popover.boxLeave();
  popover.enter(item('a'), 'blip-a');
  t.mock.timers.tick(HIDE_DELAY_MS * 5);
  assert.deepEqual(view.shown, { id: 'a', blip: 'blip-a', isHeld: true }, '잡아 둔 점으로 돌아오면 미리보기로 바꾸지 않는다');

  // 둘 다 벗어나면 잠시 뒤 닫힌다
  popover.leave(item('a'));
  t.mock.timers.tick(HIDE_DELAY_MS);
  assert.equal(view.shown, null);
  assert.equal(popover.current(), null);
});

test('잡아 둔 채 다른 점에 올리면 그 점의 미리보기로 바뀐다 (늦게 온 닫기가 새 상자를 닫지 않는다)', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const view = fakeView();
  const popover = createPopoverController(view);
  popover.pick(item('a'), 'blip-a', 'mouse');
  popover.leave(item('a'));
  popover.enter(item('b'), 'blip-b');
  t.mock.timers.tick(HIDE_DELAY_MS * 2);
  assert.deepEqual(view.shown, { id: 'b', blip: 'blip-b', isHeld: false });
});

test('터치로 잡아 둔 상자는 벗어나도 닫히지 않고, 같은 점을 다시 누르거나 바깥을 누르면 닫힌다', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const view = fakeView();
  const popover = createPopoverController(view);
  popover.pick(item('a'), 'blip-a', 'touch');
  assert.deepEqual(view.shown, { id: 'a', blip: 'blip-a', isHeld: true });
  popover.leave(item('a'));
  popover.boxLeave();
  t.mock.timers.tick(HIDE_DELAY_MS * 5);
  assert.equal(view.shown?.id, 'a');

  popover.pick(item('a'), 'blip-a', 'touch');
  assert.equal(view.shown, null, '같은 점을 다시 누르면 닫힌다');

  popover.pick(item('a'), 'blip-a', 'touch');
  popover.pick(item('b'), 'blip-b', 'touch');
  assert.deepEqual(view.shown, { id: 'b', blip: 'blip-b', isHeld: true }, '다른 점을 누르면 그 점으로 바뀐다');
  popover.dismiss();
  assert.equal(view.shown, null, '바깥 누름·Esc·다시 그리기');
  popover.dismiss(); // 닫힌 뒤 한 번 더 불려도 문제없다
  assert.equal(view.log.filter((line) => line === 'clear blip-b').length, 1);
});

test('상자 안 링크에 포커스가 있으면 벗어나도 닫지 않는다', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const view = fakeView();
  const popover = createPopoverController(view);
  popover.pick(item('a'), 'blip-a', 'mouse');
  view.focusInside = true;
  popover.boxLeave();
  t.mock.timers.tick(HIDE_DELAY_MS * 2);
  assert.equal(view.shown?.id, 'a', '닫으면 포커스가 문서 처음으로 돌아간다');
  popover.dismiss(); // Esc나 바깥 누름으로는 닫힌다
  assert.equal(view.shown, null);
});

test('포커스가 상자를 떠나면, 마우스도 떠나 있을 때 닫힌다', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const view = fakeView();
  const popover = createPopoverController(view);
  // 링크를 새 탭으로 열어 포커스가 상자에 남은 채 마우스가 떠난 경우
  popover.enter(item('a'), 'blip-a');
  popover.pick(item('a'), 'blip-a', 'mouse');
  popover.leave(item('a'));
  popover.boxEnter();
  view.focusInside = true;
  popover.boxLeave();
  t.mock.timers.tick(HIDE_DELAY_MS * 2);
  assert.equal(view.shown?.id, 'a', '포커스가 있는 동안은 남는다');
  view.focusInside = false;
  popover.focusLeave();
  t.mock.timers.tick(HIDE_DELAY_MS - 1);
  assert.equal(view.shown?.id, 'a');
  t.mock.timers.tick(1);
  assert.equal(view.shown, null, '포커스까지 떠나면 닫힌다');

  // 마우스가 아직 상자 위에 있으면 포커스가 떠나도 닫지 않는다
  popover.enter(item('a'), 'blip-a');
  popover.pick(item('a'), 'blip-a', 'mouse');
  popover.leave(item('a'));
  popover.boxEnter();
  popover.focusLeave();
  t.mock.timers.tick(HIDE_DELAY_MS * 2);
  assert.equal(view.shown?.id, 'a');
  // 마우스가 점 위에 있을 때도 마찬가지
  popover.boxLeave();
  popover.enter(item('a'), 'blip-a');
  popover.focusLeave();
  t.mock.timers.tick(HIDE_DELAY_MS * 2);
  assert.equal(view.shown?.id, 'a');

  // 터치로 잡아 둔 상자는 포커스가 떠나도 닫지 않는다 (바깥을 눌러 닫는다)
  popover.dismiss();
  popover.pick(item('b'), 'blip-b', 'touch');
  popover.focusLeave();
  t.mock.timers.tick(HIDE_DELAY_MS * 2);
  assert.equal(view.shown?.id, 'b');
});

test('다시 그리며 닫을 때 예약된 닫기도 함께 지운다', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const view = fakeView();
  const popover = createPopoverController(view);
  popover.pick(item('a'), 'blip-a', 'mouse');
  popover.leave(item('a'));
  popover.dismiss(); // 목록을 다시 그림
  popover.enter(item('a'), 'new-blip-a'); // 새로 만들어진 점
  t.mock.timers.tick(HIDE_DELAY_MS * 2);
  assert.deepEqual(view.shown, { id: 'a', blip: 'new-blip-a', isHeld: false }, '앞서 예약한 닫기가 새 미리보기를 닫지 않는다');
});

test('점은 Tab 순서에 넣지 않고, 올림은 마우스에만 반응한다', () => {
  assert.doesNotMatch(RADAR, /svg\('title'/, '기본 말풍선(<title>)을 두면 안내 상자와 겹쳐 뜬다');
  assert.doesNotMatch(RADAR, /tabindex/i, '같은 대회가 아래 목록에 있어 점은 Tab으로 가지 않는다');
  assert.match(RADAR, /role: 'img'/);
  // 터치의 가짜 마우스 이벤트로 미리보기를 띄우면 일부 휴대폰 브라우저가 첫 탭의 click을 보내지 않는다
  assert.match(RADAR, /addEventListener\('pointerenter'[^\n]*pointerType === 'mouse'/);
  assert.match(RADAR, /addEventListener\('pointerleave'[^\n]*pointerType === 'mouse'/);
  assert.doesNotMatch(RADAR, /addEventListener\('mouse(enter|leave|over|out)'/);
});

test('미리보기는 마우스를 받지 않고, 공지 글자는 글자로만 넣는다', () => {
  // 상자가 옆의 점을 가려 다른 점으로 옮겨 갈 수 없게 되는 것을 막는다
  assert.match(CSS, /\.rd-pop:not\(\[data-held="true"\]\)\s*{\s*pointer-events:\s*none/);
  assert.match(CSS, /\.rd-pop\[hidden\]\s*{\s*display:\s*none/, 'display: grid가 hidden을 이기지 않게');
  assert.doesNotMatch(POPOVER, /innerHTML|insertAdjacentHTML|setAttribute\('style'/);
  // 어두운 첫 화면 안의 흰 상자: 첫 화면용 흰 글자·흰 포커스 테두리를 그대로 물려받으면 보이지 않는다
  assert.match(CSS, /\.hero \.rd-pop \.small\s*{\s*color:\s*var\(--sub\)/);
  assert.match(CSS, /\.hero \.rd-pop a:not\(\.btn\)\s*{\s*color:\s*var\(--blue\)/);
  assert.match(CSS, /\.hero \.rd-pop :focus-visible\s*{\s*outline-color:\s*var\(--blue\)/);
});

// ── 겹친 점 떼어 놓기 (#184) ─────────────────────────────
const radiusOf = (spot) => Math.hypot(spot.x, spot.y);
function closestPair(spots) {
  let closest = Infinity;
  for (let a = 0; a < spots.length; a += 1) {
    for (let b = a + 1; b < spots.length; b += 1) closest = Math.min(closest, Math.hypot(spots[a].x - spots[b].x, spots[a].y - spots[b].y));
  }
  return closest;
}
// 시험마다 같은 값이 나오는 난수 (Math.random을 쓰면 실패를 다시 볼 수 없다)
function seeded(seed) {
  let state = seed;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 2 ** 32;
  };
}

test('겹치지 않는 점은 제자리에 둔다 (혼자 있을 때와 같은 자리)', () => {
  const dots = [{ id: 'contest-a', radius: 30 }, { id: 'contest-q', radius: 90 }, { id: 'festival-2026', radius: 150 }];
  const together = layoutBlips(dots);
  assert.ok(closestPair(dots.map((dot) => layoutBlips([dot])[0])) >= BLIP_GAP, '이 시험의 전제: 원래 자리가 서로 떨어져 있다');
  dots.forEach((dot, index) => {
    assert.deepEqual(together[index], layoutBlips([dot])[0], dot.id);
    assert.ok(Math.abs(radiusOf(together[index]) - dot.radius) < 1e-9, `${dot.id}: 반지름`);
  });
  assert.deepEqual(layoutBlips([]), []);
});

test('공지 번호가 이어진 두 대회처럼 각도가 거의 같으면 같은 거리의 원을 따라 떼어 놓는다', () => {
  // 11/19 공개 데이터에서 실제로 겹친 두 카드. id가 끝자리만 달라 각도가 1도 차이였다
  const dots = [{ id: 'mju-student-mjukr-853-236710', radius: 67.4 }, { id: 'mju-student-mjukr-853-236711', radius: 69.7 }];
  const alone = dots.map((dot) => layoutBlips([dot])[0]);
  assert.ok(closestPair(alone) < 5, '이 시험의 전제: 그대로 두면 겹친다');
  const spots = layoutBlips(dots);
  assert.ok(closestPair(spots) >= BLIP_GAP, `떨어진 거리 ${closestPair(spots).toFixed(1)}`);
  spots.forEach((spot, index) => assert.ok(Math.abs(radiusOf(spot) - dots[index].radius) < 1e-9, '남은 날을 나타내는 거리는 그대로'));
  assert.deepEqual(spots[0], alone[0], 'id가 앞선 점은 제자리');
});

test('같은 데이터면 화면 순서가 바뀌어도 같은 자리에 찍힌다', () => {
  const random = seeded(7);
  const dots = Array.from({ length: 25 }, (_, index) => ({ id: `card-${index}`, radius: 14 + random() * 150 }));
  const expected = new Map(layoutBlips(dots).map((spot, index) => [dots[index].id, spot]));
  const shuffled = [...dots].sort((a, b) => (a.radius < b.radius ? -1 : 1));
  layoutBlips(shuffled).forEach((spot, index) => assert.deepEqual(spot, expected.get(shuffled[index].id), shuffled[index].id));
  // 넘긴 배열은 건드리지 않는다
  assert.deepEqual(shuffled.map((dot) => dot.id), [...dots].sort((a, b) => (a.radius < b.radius ? -1 : 1)).map((dot) => dot.id));
});

test('점이 많아도 어떤 두 점도 최소 간격보다 가깝지 않고, 거리는 한도 안에서만 옮긴다', () => {
  for (const seed of [1, 2, 3, 4, 5]) {
    const random = seeded(seed);
    const dots = Array.from({ length: 40 }, (_, index) => ({ id: `mju-board-${seed}-${1000 + index}`, radius: 14 + random() * 152 }));
    const spots = layoutBlips(dots);
    assert.equal(spots.length, dots.length);
    assert.ok(closestPair(spots) >= BLIP_GAP - 1e-9, `seed ${seed}: 가장 가까운 두 점 ${closestPair(spots).toFixed(2)}`);
    spots.forEach((spot, index) => {
      assert.ok(Math.abs(radiusOf(spot) - dots[index].radius) <= 24 + 1e-9, `seed ${seed}: ${dots[index].id} 거리 이동`);
      assert.ok(radiusOf(spot) >= 14 - 1e-9 && radiusOf(spot) <= 166 + 1e-9, `seed ${seed}: 마감일이 있는 점의 범위 안`);
    });
  }
});

test('오늘 마감이 여러 건이면 중심 근처에서 거리를 조금 옮겨서라도 떼어 놓는다', () => {
  // 반지름 14의 원에는 간격 26으로 두 점밖에 못 놓는다
  const dots = Array.from({ length: 6 }, (_, index) => ({ id: `today-${index}`, radius: 14 }));
  const spots = layoutBlips(dots);
  assert.ok(closestPair(spots) >= BLIP_GAP - 1e-9, `가장 가까운 두 점 ${closestPair(spots).toFixed(2)}`);
  assert.ok(spots.every((spot) => radiusOf(spot) >= 14 - 1e-9 && radiusOf(spot) <= 14 + 24 + 1e-9), '7일 이내 구간을 벗어나지 않는다');
  assert.ok(spots.filter((spot) => Math.abs(radiusOf(spot) - 14) < 1e-9).length >= 1, '적어도 하나는 제 거리에 있다');
  // id가 같은 카드 두 장이 들어와도 떼어 놓는다
  assert.ok(closestPair(layoutBlips([{ id: 'same', radius: 80 }, { id: 'same', radius: 80 }])) >= BLIP_GAP - 1e-9);
});

test('자리가 끝내 없으면 겹치더라도 모든 점을 그린다', () => {
  const dots = Array.from({ length: 80 }, (_, index) => ({ id: `crowd-${index}`, radius: 14 }));
  const spots = layoutBlips(dots);
  assert.equal(spots.length, 80);
  assert.ok(spots.every((spot) => Number.isFinite(spot.x) && Number.isFinite(spot.y)));
  assert.ok(spots.every((spot) => radiusOf(spot) <= 14 + 24 + 1e-9), '한도 밖으로 밀어내지는 않는다');
});
