// 홈 레이더 점 안내 상자(site/js/radar-popover.js) 테스트 (#178): 상자를 둘 자리 계산과, 코드로 고정할 수 있는 동작 규칙.
// 마우스·터치 동작 자체는 브라우저에서 확인한다 (site/README.md "레이더 점 안내 상자").
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { popoverPosition, pickAction } from '../site/js/radar-popover.js';

const RADAR = readFileSync(new URL('../site/js/radar.js', import.meta.url), 'utf8');
const POPOVER = readFileSync(new URL('../site/js/radar-popover.js', import.meta.url), 'utf8');
const CSS = readFileSync(new URL('../site/css/style.css', import.meta.url), 'utf8');

const BOUNDS = { width: 500, height: 500 };
const SIZE = { width: 300, height: 140 };

test('안내 상자는 점 위 가운데에 두고, 위가 모자라면 아래에 둔다', () => {
  assert.deepEqual(popoverPosition({ x: 250, y: 300 }, SIZE, BOUNDS, 14), { left: 100, top: 146, placement: 'above' });
  // 점이 위쪽에 있어 위로는 들어가지 않는다
  assert.deepEqual(popoverPosition({ x: 250, y: 60 }, SIZE, BOUNDS, 14), { left: 100, top: 74, placement: 'below' });
  // 딱 맞게 들어가면 위에 둔다
  assert.equal(popoverPosition({ x: 250, y: 154 }, SIZE, BOUNDS, 14).placement, 'above');
  assert.equal(popoverPosition({ x: 250, y: 153 }, SIZE, BOUNDS, 14).placement, 'below');
});

test('안내 상자는 레이더 칸의 좌우를 벗어나지 않는다', () => {
  assert.equal(popoverPosition({ x: 20, y: 300 }, SIZE, BOUNDS).left, 0);
  assert.equal(popoverPosition({ x: 490, y: 300 }, SIZE, BOUNDS).left, 200);
  // 휴대폰처럼 상자가 칸만큼 넓으면 왼쪽 끝에 맞춘다
  assert.equal(popoverPosition({ x: 300, y: 300 }, { width: 340, height: 140 }, { width: 340, height: 340 }).left, 0);
  assert.equal(popoverPosition({ x: 100, y: 300 }, { width: 400, height: 140 }, { width: 340, height: 340 }).left, 0, '칸보다 넓어도 음수가 되지 않는다');
});

test('위아래 어디에도 다 들어가지 않으면 더 넓은 쪽에 두고, 칸 위로 나가지 않는다', () => {
  const tall = { width: 300, height: 320 };
  const bounds = { width: 340, height: 340 };
  const upper = popoverPosition({ x: 170, y: 120 }, tall, bounds, 14);
  assert.equal(upper.placement, 'below');
  assert.equal(upper.top, 134);
  const lower = popoverPosition({ x: 170, y: 250 }, tall, bounds, 14);
  assert.equal(lower.placement, 'above');
  assert.equal(lower.top, 0);
  for (const y of [0, 40, 170, 300, 340]) {
    const { top, left } = popoverPosition({ x: 170, y }, tall, bounds, 14);
    assert.ok(top >= 0 && left >= 0 && Number.isInteger(top) && Number.isInteger(left), `y=${y}`);
  }
});

test('누르면 잡아 두고, 터치로 같은 점을 다시 누르면 닫는다', () => {
  const preview = { id: 'a', isHeld: false, isTouch: false };
  const heldByMouse = { id: 'a', isHeld: true, isTouch: false };
  const heldByTouch = { id: 'a', isHeld: true, isTouch: true };
  // 아무것도 없거나 미리보기만 떠 있으면 누른 점을 잡아 둔다
  for (const pointerType of ['mouse', 'touch', 'pen', '', undefined]) {
    assert.equal(pickAction(null, 'a', pointerType), 'hold', String(pointerType));
    assert.equal(pickAction(preview, 'a', pointerType), 'hold', String(pointerType));
    assert.equal(pickAction(heldByTouch, 'b', pointerType), 'hold', `다른 점: ${pointerType}`);
  }
  // 터치에는 "벗어남"이 없어 같은 점을 다시 누르는 것이 닫는 방법이다
  assert.equal(pickAction(heldByTouch, 'a', 'touch'), 'hide');
  assert.equal(pickAction(heldByMouse, 'a', 'pen'), 'hide');
  // 마우스는 벗어나면 닫히므로 다시 눌러도 그대로 둔다 (누를 때마다 깜빡이지 않게)
  assert.equal(pickAction(heldByMouse, 'a', 'mouse'), 'keep');
  assert.equal(pickAction(heldByMouse, 'a', undefined), 'keep', 'pointerType이 없는 브라우저는 마우스로 본다');
});

test('점은 Tab 순서에 넣지 않고, 브라우저 기본 말풍선과 겹치지 않게 한다', () => {
  assert.doesNotMatch(RADAR, /svg\('title'/, '기본 말풍선(<title>)을 두면 안내 상자와 겹쳐 뜬다');
  assert.doesNotMatch(RADAR, /tabindex/i, '같은 대회가 아래 목록에 있어 점은 Tab으로 가지 않는다');
  assert.match(RADAR, /role: 'img'/);
  for (const event of ['mouseenter', 'mouseleave', 'click']) assert.match(RADAR, new RegExp(`addEventListener\\('${event}'`), event);
});

test('안내 상자는 벗어나면 닫히고, 공지 글자는 글자로만 넣는다', () => {
  // 닫히는 길: 점·상자에서 벗어남, 상자 밖 누름, Esc, 창 크기 변경
  for (const pattern of [/onLeave\(item\)/, /addEventListener\('mouseleave'/, /addEventListener\('pointerdown'/, /event\.key === 'Escape'/, /addEventListener\('resize', hide\)/]) {
    assert.match(POPOVER, pattern);
  }
  // 미리보기는 마우스를 받지 않는다: 상자가 옆의 점을 가려 다른 점으로 옮겨 갈 수 없게 되는 것을 막는다
  assert.match(CSS, /\.rd-pop:not\(\[data-held="true"\]\)\s*{\s*pointer-events:\s*none/);
  assert.match(POPOVER, /if \(!active\.isHeld\) hide\(\);/, '미리보기는 점에서 벗어나면 바로 닫는다');
  assert.doesNotMatch(POPOVER, /innerHTML|insertAdjacentHTML|setAttribute\('style'/);
  // 어두운 첫 화면 안의 흰 상자: 첫 화면용 흰 글자·흰 포커스 테두리를 그대로 물려받으면 보이지 않는다
  assert.match(CSS, /\.hero \.rd-pop \.small\s*{\s*color:\s*var\(--sub\)/);
  assert.match(CSS, /\.hero \.rd-pop a:not\(\.btn\)\s*{\s*color:\s*var\(--blue\)/);
  assert.match(CSS, /\.hero \.rd-pop :focus-visible\s*{\s*outline-color:\s*var\(--blue\)/);
  assert.match(CSS, /\.rd-pop\[hidden\]\s*{\s*display:\s*none/, 'display: grid가 hidden을 이기지 않게');
});
