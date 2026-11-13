// 접근성 점검 (#27) 가운데 코드로 고정할 수 있는 것: 글자·포커스·입력창 경계 대비, 페이지 기본 구조, 좁은 화면 규칙.
// 실기기 확인·키보드 동선·시연 리허설은 사람이 한다 (#27). 대비 기준은 WCAG 2.2: 글자 4.5:1, 포커스 표시·입력창 경계 3:1.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const CSS = readFileSync(new URL('../site/css/style.css', import.meta.url), 'utf8');
const LOG_PAGE = readFileSync(new URL('../site/js/log-page.js', import.meta.url), 'utf8');
const PAGES = ['index.html', 'recommend.html', 'card.html', 'log.html'];

const COLORS = Object.fromEntries([...CSS.match(/:root\s*{([^}]*)}/)[1].matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{6})/g)]
  .map(([, name, hex]) => [name, hex]));

function luminance(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(foreground, background) {
  const [a, b] = [luminance(COLORS[foreground]), luminance(COLORS[background])].sort((x, y) => y - x);
  return (a + 0.05) / (b + 0.05);
}

function cssRule(source, pattern, what) {
  const match = source.match(pattern);
  assert.ok(match, `CSS에서 ${what}을(를) 찾지 못함`);
  return match;
}

// [글자색, 배경색, 쓰는 곳]. 배지·안내처럼 작은 글자가 대부분이라 모두 4.5:1로 본다.
// 머리글의 반투명 흰 배경(.cond, 지금 화면 메뉴)과 opacity .85 메뉴 글자는 색 변수 쌍이 아니라 뺐다 (흰 글자 / 남색이 13:1이라 여유가 크다)
const TEXT_PAIRS = [
  ['text', 'bg', '본문'], ['text', 'card', '패널 본문'], ['sub', 'bg', '보조 글자'], ['sub', 'card', '보조 글자 (패널)'],
  ['blue', 'card', '링크'], ['blue', 'bg', '바탕 위 링크'], ['on-dark', 'navy', '머리글'], ['on-dark', 'blue', '눌린 칩·주 버튼'],
  ['ok', 'ok-bg', '신청 가능·검수 완료 배지'], ['team', 'team-bg', '팀 구성 시 가능 배지'], ['warn', 'warn-bg', '확인 필요 배지·안내'],
  ['no', 'no-bg', '불가 배지·오류'], ['no', 'card', 'D-day'], ['blue', 'blue-soft', '자동 검증 통과 배지'],
  ['muted', 'card', '확인 전 배지·정보 없음'], ['muted', 'muted-bg', '정보 없음 배지'], ['tag-text', 'tag-bg', '제출물 태그'],
  ['demo-text', 'demo-bg', '예시 데이터 안내'], ['sub', 'ev-bg', '근거 출처'], ['text', 'ev-bg', '근거 문장'],
  ['muted', 'ev-bg', '근거 안 인용'], ['blue', 'ev-bg', '근거 안 링크'], ['text', 'hl', '근거 강조'],
  ['text', 'note-bg', '문의 초안'], ['sub', 'note-bg', '문의 초안 보조 글자'], ['blue', 'note-bg', '안내 상자 안 링크'],
  ['warn', 'card', '확인 필요 안내 글자'], ['sub', 'warn-bg', '안내 상자 보조 글자'], ['sub', 'muted-bg', '회색 상자 보조 글자'],
];

test('글자와 배경의 대비가 4.5:1 이상이다', () => {
  for (const [foreground, background, place] of TEXT_PAIRS) {
    assert.ok(COLORS[foreground] && COLORS[background], `${place}: 색 변수 --${foreground}, --${background}`);
    const ratio = contrast(foreground, background);
    assert.ok(ratio >= 4.5, `${place}: --${foreground} / --${background} = ${ratio.toFixed(2)}:1`);
  }
});

test('포커스 표시가 보인다 (주변 색과 3:1 이상, 짙은 머리글 위에서도)', () => {
  cssRule(CSS, /:focus-visible\s*{\s*outline:\s*3px solid var\(--blue\)/, '기본 포커스 표시');
  for (const background of ['bg', 'card']) {
    assert.ok(contrast('blue', background) >= 3, `밝은 배경 --${background} 위 파란 포커스`);
  }
  // 남색 머리글 위 파란 테두리는 3:1에 못 미쳐(약 2.3:1) 머리글 안에서는 흰 테두리를 쓴다
  cssRule(CSS, /header :focus-visible\s*{\s*outline-color:\s*var\(--on-dark\)/, '머리글 안 포커스 색');
  assert.ok(contrast('on-dark', 'navy') >= 3);
});

test('입력창·칩·버튼 경계가 흰 배경과 3:1 이상이다', () => {
  cssRule(CSS, /\.ask input\s*{[^}]*border:\s*1px solid var\(--input-line\)/, '입력창 경계');
  cssRule(CSS, /\.title-search\s*{[^}]*border:\s*1px solid var\(--input-line\)/, '제목 검색칸 경계');
  // 누르지 않은 칩과 보조 버튼도 경계로 버튼임을 알아본다 (#104: 예전 색은 1.76:1, 1.30:1)
  cssRule(CSS, /\n\.chip\s*{[^}]*border:\s*1px solid var\(--input-line\)/, '칩 경계');
  cssRule(CSS, /\n\.btn\s*{[^}]*border:\s*1px solid var\(--input-line\)/, '버튼 경계');
  assert.ok(contrast('input-line', 'card') >= 3, `--input-line / --card = ${contrast('input-line', 'card').toFixed(2)}:1`);
});

test('네 화면 모두 언어·제목·확대 허용·랜드마크·이름 붙은 메뉴가 있다', () => {
  for (const page of PAGES) {
    const html = readFileSync(new URL(`../site/${page}`, import.meta.url), 'utf8');
    assert.match(html, /<html lang="ko">/, page);
    assert.match(html, /<title>[^<]+<\/title>/, page);
    assert.match(html, /<meta name="viewport" content="width=device-width, initial-scale=1">/, `${page}: 확대를 막지 않는다`);
    assert.doesNotMatch(html, /user-scalable=no|maximum-scale/, page);
    assert.match(html, /<main[\s>]/, page);
    assert.match(html, /<nav aria-label="[^"]+">/, page);
    assert.equal((html.match(/aria-current="page"/g) || []).length, page === 'card.html' ? 0 : 1, `${page}: 지금 화면 표시`);
    // HTML에 직접 쓴 입력만 본다 (JS가 만드는 체크박스는 label 안에 있다)
    for (const [, id] of html.matchAll(/<input[^>]*\sid="([^"]+)"/g)) {
      assert.match(html, new RegExp(`<label[^>]*for="${id}"`), `${page}: 입력 ${id}의 라벨`);
    }
  }
});

test('좁은 화면에서 넓은 표·버튼이 화면 전체를 가로로 밀지 않는다', () => {
  // 실제 폭 측정 절차와 결과는 site/README.md "접근성·휴대폰 점검". 여기서는 그 규칙이 남아 있는지만 본다
  const shrink = cssRule(CSS, /\n([^{}\n]*)\{\s*min-width:\s*0;\s*\}/, '격자 칸 min-width: 0 규칙')[1];
  for (const selector of ['main > *', '.split > *', '.side > *', '.panel > *']) {
    assert.ok(shrink.split(',').map((part) => part.trim()).includes(selector), `${selector}가 내용보다 좁아질 수 있다`);
  }
  cssRule(CSS, /\.table-scroll\s*{\s*overflow-x:\s*auto;?\s*}/, '표 감싸개의 가로 스크롤');
  const narrow = cssRule(CSS, /@media \(max-width: 520px\)\s*{([\s\S]*?)\n}/, '좁은 화면 규칙')[1];
  assert.match(narrow, /\.btn\s*{\s*white-space:\s*normal;?\s*}/, '긴 버튼 글자는 줄을 바꾼다');
  // 넓은 화면의 상태 칸 width: 1%가 남으면 칸 안 글자가 한 자씩 세로로 쪼개진다 (헤드리스 브라우저 360px로 확인, #104)
  assert.match(narrow, /\.fields td\.st\s*{[^}]*width:\s*auto/, '좁은 화면에서 상태 칸 너비를 푼다');
  assert.doesNotMatch(CSS, /\.metrics\s*{[^}]*display:\s*block/, '표를 block으로 바꾸면 화면 읽기 프로그램의 표 탐색이 사라진다');
});

test('공지에서 온 긴 낱말은 칸 안에서 줄을 바꾼다 (#104)', () => {
  const selectors = cssRule(CSS, /\n([^{}\n]*)\{\s*overflow-wrap:\s*anywhere;\s*\}/, '긴 낱말 줄바꿈 규칙')[1]
    .split(',').map((part) => part.trim());
  // 제목·주관·근거·항목 값·마감 이력·이해하지 못한 표현: 모두 공지나 학생 입력에서 그대로 온 글이다
  for (const selector of ['.item .title', '.item .org', '.ev', '.quote', '.fields td:not(.st)', '.timeline', '.term', 'h1']) {
    assert.ok(selectors.includes(selector), `${selector}의 긴 낱말이 넘칠 수 있다`);
  }
  // 너비 1%인 상태 칸에 anywhere를 주면 칸 안 글자가 한 자씩 세로로 쪼개진다 (헤드리스 브라우저로 확인)
  assert.ok(!selectors.includes('.fields td'), '상태 칸까지 anywhere를 주지 않는다');
});

test('근거 펼침 버튼 바로 뒤에 근거가 온다 (펼친 뒤 Tab으로 근거·원문 링크에 닿게, #104)', () => {
  const CARD_PAGE = readFileSync(new URL('../site/js/card-page.js', import.meta.url), 'utf8');
  assert.match(CARD_PAGE, /valueCell\.append\(el\('div', \{\}, button\), evidence\)/);
  assert.doesNotMatch(CARD_PAGE, /statusCell\.append\([^)]*button/, '버튼을 상태 칸에 두면 근거보다 뒤에 온다');
});

test('가로로 밀리는 표는 키보드로도 닿는다 (이름 붙은 영역 + tabindex)', () => {
  assert.match(LOG_PAGE, /el\('div', \{ class: 'table-scroll', tabindex: '0', role: 'region', 'aria-label': label \}/);
  const tables = (LOG_PAGE.match(/el\('table'/g) || []).length;
  assert.ok(tables > 0);
  assert.equal((LOG_PAGE.match(/scrollable\('[^']+', el\('table'/g) || []).length, tables, '모든 표를 감싼다');
});
