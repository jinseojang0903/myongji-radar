// 화면 도우미. 공지에서 온 문자열은 모두 외부 입력이다.
// - 요소는 el()로 만들고 글자는 textContent로만 넣는다 (innerHTML을 쓰지 않는다 → HTML로 해석되지 않음).
// - 링크 주소는 safeUrl()을 거친다 (https와 허용 도메인만).

// 링크를 허용하는 도메인. 학교 도메인과, 공지가 신청 경로로 자주 쓰는 구글 폼.
const ALLOWED_HOSTS = ['mju.ac.kr', 'forms.gle', 'docs.google.com'];

/**
 * 외부 링크 주소를 검사한다.
 * @param {unknown} url
 * @returns {string|null} https이고 허용 도메인(또는 그 하위 도메인)이면 정규화한 주소, 아니면 null
 */
export function safeUrl(url) {
  if (typeof url !== 'string' || url.trim() === '') return null;
  let parsed;
  try {
    parsed = new URL(url.trim());
  } catch {
    return null; // 주소 형식이 아니면 링크로 쓰지 않는다 (호출한 쪽이 글자만 보여 줌)
  }
  if (parsed.protocol !== 'https:' || parsed.username || parsed.password) return null;
  const host = parsed.hostname.toLowerCase();
  const isAllowed = ALLOWED_HOSTS.some((allowed) => host === allowed || host.endsWith(`.${allowed}`));
  return isAllowed ? parsed.href : null;
}

/**
 * 요소를 만든다. children의 문자열은 글자(텍스트 노드)로 들어간다.
 * @param {string} tag
 * @param {Record<string, string|boolean|null|undefined>} [attrs]  class, href 등. false·null·undefined는 생략
 * @param {...(Node|string|number|null|undefined|false)} children
 */
export function el(tag, attrs = {}, ...children) {
  const element = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value === false || value === null || value === undefined) continue;
    if (name === 'href' && tag === 'a' && !isInternalHref(String(value))) {
      throw new Error(`외부 링크는 externalLink()로 만든다: ${value}`);
    }
    element.setAttribute(name, value === true ? '' : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    element.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return element;
}

/** 사이트 안 링크(같은 폴더의 html, #앵커)인지 */
function isInternalHref(href) {
  // 쿼리에는 encodeURIComponent가 남기는 문자(! ' ( ) * ~)까지 허용. 큰따옴표·꺾쇠·콜론·슬래시는 인코딩되므로 들어올 수 없다
  return /^(#[\w-]*|[\w-]+\.html(\?[\w=%&.!'()*~-]*)?(#[\w-]*)?)$/.test(href);
}

/**
 * 외부 링크. 허용되지 않는 주소면 링크 대신 글자만 돌려준다.
 * @returns {HTMLAnchorElement|HTMLSpanElement}
 */
export function externalLink(url, text) {
  const href = safeUrl(url);
  if (!href) return el('span', {}, text);
  const link = el('a', {}, text);
  link.href = href;
  link.rel = 'noopener noreferrer';
  link.target = '_blank';
  return link;
}
