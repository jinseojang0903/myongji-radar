// 자연어 해석기: 학생이 쓴 문장 → 조건 (docs/api-contract.md 2절).
// 표현 사전 기반이고 AI를 호출하지 않는다 (11/2 결정). 해석 규칙은 계약 2절이 기준이다.
// 사전을 바꾸면 `npm run eval:parser`로 시험 문장 정확도를 다시 잰다 (eval/parser/).
// 원칙: 뜻이 하나로 정해지는 표현만 값으로 바꾸고, 나머지는 버리지 않고 unknown_terms로 돌려준다.
// 확실하지 않으면 값을 비운다. 값이 비면 판정은 "확인 필요"가 되지만, 틀린 값은 "불가인데 가능"이 될 수 있다.

import { DELIVERABLE_TAGS } from './cards.js';

export const PARSER_VERSION = '0.2';

// 입력 문장 최대 길이 (계약 2절). 화면 입력창도 같은 값을 maxlength로 쓴다.
export const MAX_INPUT_LENGTH = 200;

// 학생이 고를 수 있는 제출물 태그. `기타`는 학생이 말할 수 있는 제출물이 아니라서 뺀다 (계약 2절).
export const STUDENT_DELIVERABLE_TAGS = DELIVERABLE_TAGS.filter((tag) => tag !== '기타');

// [조건, 값, 표현들]. 표현은 띄어쓰기를 뺀 소문자. 긴 표현부터 맞춰 보므로 순서는 상관없다.
const DICTIONARY = [
  ['campus', '인문', ['인문캠퍼스', '인문캠', '서울캠퍼스', '서울캠', '인캠']],
  ['campus', '자연', ['자연캠퍼스', '자연캠', '용인캠퍼스', '용인캠', '자캠']],
  ['grade', 1, ['신입생', '새내기']],
  ['is_transfer', true, ['편입생', '편입학', '편입']],
  ['enrollment', '재학', ['재학생', '재학']],
  ['enrollment', '휴학', ['휴학생', '휴학']],
  ['enrollment', '수료', ['수료생', '수료']],
  ['enrollment', '초과학기', ['초과학기생', '초과학기']],
  ['enrollment', '졸업유예', ['졸업유예생', '졸업유예']],
  ['participation', '개인', ['혼자서', '혼자', '개인전', '개인참가', '개인참여', '개인', '솔로', '단독']],
  ['participation', '팀', ['팀전', '팀참가', '팀참여', '팀플', '팀원', '팀', '친구', '같이', '함께', '둘이서', '둘이', '셋이서', '셋이', '넷이서', '넷이', '여럿이']],
  ['deliverables', '기획서·제안서', ['사업계획서', '기획서', '제안서', '기획안', '계획서', '아이디어', '기획']],
  ['deliverables', '영상', ['동영상', '영상', '유튜브', '숏폼', '쇼츠', '릴스', 'ucc', '브이로그', 'vlog', '영화']],
  ['deliverables', '에세이·수기', ['에세이', '체험수기', '수기', '수필', '글쓰기', '독후감', '작문', '글']],
  ['deliverables', '코드·개발', ['프로그래밍', '소프트웨어', '해커톤', '코딩', '코드', '개발', '어플', '앱', '웹', 'sw']],
  ['deliverables', '발표', ['프레젠테이션', '프리젠테이션', '스피치', '발표', '피티', 'ppt', 'pt']],
  ['deliverables', '디자인', ['일러스트', '카드뉴스', '디자인', '포스터', '웹툰', '그림', '로고']],
  // "현장" 하나만으로는 "현장 실습", "현장 심사"와 구분되지 않아 넣지 않는다
  ['deliverables', '현장 참가', ['현장참가', '현장참여', '오프라인']],
  ['period', '마감임박', ['마감임박']],
  ['period', '이번주', ['이번주말', '이번주', '금주']],
  ['period', '이번달', ['이번달', '이달', '금월']],
];

const STEMS = DICTIONARY
  .flatMap(([dim, value, stems]) => stems.map((stem) => ({ dim, value, stem })))
  .sort((a, b) => b.stem.length - a.stem.length);

const KOREAN_DIGITS = { 일: 1, 이: 2, 삼: 3, 사: 4, 오: 5, 육: 6 };
const GRADE_RE = /^([1-6]|일|이|삼|사|오|육)학년/;
// 팀 구성 인원("3명이서", "1인")만 본다. 두 자리 수("30명 규모")는 대회 규모일 때가 많아 읽지 않는다
const HEADCOUNT_RE = /^([1-9])(명|인)(?!칭)/;

// 표현 뒤에 붙어도 뜻이 바뀌지 않는 조사·어미·붙여 쓰는 말 ("자연캠인데", "영상공모전", "혼자서도").
// 여기에 없는 말이 붙으면 표현으로 보지 않는다 ("영상학과", "개인정보", "글로벌").
const PARTICLES = [
  '으로도', '으로는', '으로만', '에서는', '에서도', '이라서', '이라도', '이니까', '이에요', '입니다', '인데요',
  '이랑도', '이라는', '공모전', '콘테스트', '에게', '에서', '으로', '이랑', '하고', '이나', '이든', '이라', '라서',
  '라도', '인데', '이고', '이야', '예요', '에요', '이요', '이면', '니까', '까지', '부터', '끼리', '이서', '상태',
  '처럼', '같은', '관련', '위주', '제작', '공모', '대회', '분야', '부문', '작품', '형식', '제출',
  '이상', '이하', '미만', '초과', '안에', '내에', '이내', '안으로',
  '은', '는', '이', '가', '을', '를', '도', '만', '의', '에', '로', '랑', '과', '와', '나', '든', '라', '고',
  '야', '요', '인', '한', '면', '들', '서', '중', '생', '쪽', '류', '임',
].sort((a, b) => b.length - a.length);

// 부정 ("팀 말고", "편입생 아니고", "팀 없이"): 바로 옆이면 값을 뒤집는다. "아니면"은 "또는"이라서 부정이 아니다.
const FLIP_NEGATION_RE = /^(아니(?!면)|아닌|아님|아닙|아냐|말고|빼고|없이|없는|없어|없고|없음|없으|제외)/;
// 거절·곤란 ("팀플은 싫어", "발표는 별로", "편입생이라 힘들어요"): 참가 형태만 뒤집고(싫다 = 반대를 원함),
// 그 밖의 조건은 무엇을 말하는지 확실하지 않아 값을 비운다 (편입생이라 힘들다 ≠ 편입생이 아니다)
// 극혐·귀찮·사절: 시험 문장 첫 측정에서 실제 학생이 쓴 거절 표현 (#37). "사절단"은 거절이 아니다
const REJECTION_RE = /^(싫|별로|부담|힘들|어렵|어려|자신없|극혐|귀찮|사절(?!단))/;
const NEGATION_RE = new RegExp(`${FLIP_NEGATION_RE.source}|${REJECTION_RE.source}`);
const REJECTION_FLIP_DIMS = new Set(['participation', 'deliverables']);
// 절 안에서 어느 표현에도 붙지 않은 신분 부정·시간 표현은 그 앞(시간 표현은 절 전체)의 판정 조건을 모두 불확실하게 만든다
// ("자연캠 학생은 아니에요", "지난 학기에 휴학했는데 복학했어요"). "없어"는 "경험 없어서"처럼 흔해 넣지 않는다
const CLAUSE_IDENTITY_NEGATION_RE = /^(아니(?!면)|아닌|아님|아닙|아냐|말고|빼고|제외)/;
// "지난"·"저번"은 뒤에 무엇이 오든(지난해·지난 겨울·지난번·저번에) 지난 일이다. "전에"는 "전공"과 겹치지 않게 낱말 전체만 (#37)
// 한계: "졸업하기 전에", "방학 전에"의 앞으로의 "전에"도 걸려 그 절의 값이 빈다 (값이 비는 안전한 쪽. 시험 문장 측정 뒤 다시 본다)
const CLAUSE_TIME_RE = /^(지난|저번|작년|재작년|예전|옛날|원래|이전|그전|전에$|전엔$|전에는$|내년|내후년|다음학기|나중|복학)/;
const JUDGED_DIMS = new Set(['campus', 'grade', 'enrollment', 'is_transfer', 'participation']);
// "발표하는 거 말고", "편입생은 좀 아니고"처럼 표현과 부정 사이에 끼어도 부정이 그 표현에 걸리는 말
const NEGATION_BRIDGE = new Set(['거', '것', '건', '게', '거는', '쪽', '쪽은', '좀', '조금', '같은', '딱', '그냥', '너무', '진짜', '정말', '절대']);
// 참가 형태·제출물은 사이에 다른 말이 끼어도 부정이 걸릴 수 있다 ("같이 할 사람 없어서").
// 이때는 어디에 걸린 부정인지 확실하지 않으므로 값을 비운다. 그 밖의 조건은 바로 옆 부정만 본다.
const DISTANT_NEGATION_DIMS = new Set(['participation', 'deliverables']);
const NEGATION_WINDOW = 3;

// 지금 상태가 아닌 표현: "휴학 예정", "편입 준비 중", "내년에 3학년", "1학년 때" (계약 2절 1)
const FUTURE_SENSITIVE = new Set(['campus', 'grade', 'enrollment', 'is_transfer']);
// 붙여 쓴 "휴학할", "편입하고싶", "휴학해야", "편입해볼"도 지금 상태가 아니다 (지금 상태는 "휴학했어", "휴학한", "휴학 중").
// 맨 "하고"는 넣지 않는다: "인문캠하고 자연캠"의 하고는 "그리고"라서 계약 2절 2의 충돌로 다뤄야 한다
const FUTURE_TAIL_RE = /^(예정|하려|할|하고싶|해야|하면|해볼|해보|될|되면|되는|준비|고민|생각)/;
const FUTURE_NEXT_RE = /^(예정|하려|계획|고민|준비|생각|싶|때|해야|해볼|해보|하면|고려|거|건|수도|할게)/;
const FUTURE_AFTER_HAL_RE = /^(예정|계획|생각|거|건|까|지|수도)/;
const TIME_SHIFT_RE = /^(내년|내후년|다음학기|다음해|작년|재작년|예전|원래|이전|곧|나중|때)/;
const GRADE_SHIFT_RE = /^(될|되면|되는|올라|진급)/;
// "2학년 이상", "3학년부터": 학년 하나로 담을 수 없다
const GRADE_RANGE_RE = /(이상|이하|부터|까지|초과|미만)/;
const GRADE_RANGE_NEXT_RE = /^(이상|이하|부터|까지|초과|미만)/;
// "3명 이내", "5명 모집": 팀 구성이 아니라 대회 조건
const HEADCOUNT_SCOPE_RE = /^(규모|모집|정원|이내|이상|이하|선발|내외|까지|제한|미만|초과)/;

// 표현 뒤에 붙는 용언 어미 (닫힌 목록: "영상한국어"처럼 우연히 "한"으로 시작하는 낱말은 받지 않는다)
const VERB_RE = /^(했|했어|했어요|했는데|했고|했지만|했음|했다|한|한데|해|해요|해서|해도|하고|하는|하는데|하며|함|할|됐|됐어|된|돼|돼요|되고|되는|됨|될|되나|되나요|될까|될까요|되죠|되요|됩니다|됩니까|가능|가능해|가능한|었|었어|었어요|었는데|었고|였|였어|였어요|였는데|였고)$/;

// "친구랑", "친구들과"처럼 함께한다는 조사가 붙을 때만 팀 ("친구가 추천한 대회"는 아님)
const FRIEND_TAIL_RE = /^(랑|이랑|와|과|하고|끼리|들과|들이랑|들하고|들끼리|들랑)/;
// "같이 나갈", "함께 할"처럼 참가와 이어질 때만 팀 ("영상 같이 짧은 거", "같이 내용이 비슷한"은 아님)
const TOGETHER_NEXT_RE = /^(나가|나갈|참가|참여|할|하|해|내$|내고|내도|내면|내려|내볼|낼|만들|준비|출전|도전|팀|작업)/;
// "디자인과 2학년"의 "과"는 조사가 아니라 학과
const DEPARTMENT_NEXT_RE = /^(학생|학년|전공|재학|다니|다녀|졸업|교수|선배|후배|동기|학번|\d학년|[일이삼사오육]학년)/;

// 조건과 상관없는 말 (계약 2절 4). 낱말 전체 또는 두 글자 이상 남기고 조사를 뗀 형태로 비교한다.
const FILLER_WORDS = new Set([
  '대회', '공모전', '경진대회', '경연대회', '경연', '공모', '콘테스트', '대회들', '행사', '프로그램', '이벤트', '챌린지',
  '뭐', '뭐가', '뭔가', '뭐있어', '뭐있나', '뭐있나요', '어떤', '어떤거', '어떤게', '무슨', '무엇', '거', '것', '게', '걸', '건',
  '좀', '조금', '지금', '요즘', '현재', '당장', '이제', '혹시', '그냥', '그리고', '또는', '아니면', '근데', '그런데', '그럼',
  '저', '나', '내가', '제가', '저는', '나는', '난', '전', '우리', '저희', '수', '학생', '대학생', '학부생',
  '명지대', '명지대생', '명지대학교', '학교', '교내', '우리학교', 'mju', '다', '둘', '모든', '전부', '관련', '위주',
  '중', '중에', '중에서', '중인데', '중이야', '중이에요', '중이고', '중입니다', '중이라', '같은', '쪽', '분야',
  '아무거나', '아무', '아무나', '아무것', '데', '거야', '거예요', '건가요',
  '마감', '안', '안에', '내', '내에', '이내', '까지', '남은', '지난', '진행중', '진행', '모집', '모집중', '올라온',
  '새로', '최근', '정도', '대상', '등', '등등', '거나', '열린', '열려있는', '받는', '요', '네', '응', '딱', '너무', '진짜', '정말',
]);
const FILLER_PATTERNS = [
  /^(있|없)/,
  /^(할|하는|하고|해|해서|한|할래|해볼|해보고|해볼만한|할만한|할수|하기|하면|하려고|하는데|해요|합니다)$/,
  /^(낼|내|내고|내는|내려|내려고|내면|내도|내볼|낼만한|내볼만한)$/,
  /^(나갈|나가)/,
  /^(주는|주고|하나|구해|구하|구할|몰라|모르)/,
  // 초성 줄임말 ("ㅊㅊ", "ㅎㅇ"). NFKC를 거치면 한글 자모 영역으로 바뀐다
  /^[ᄀ-ᇿ㄰-㆏]+$/,
  /^(참가|참여|신청|지원|응모|출전|도전|제출)(할|하는|하고|해|해도|하려고|해볼|할만한|가능|하기|하면|할래|해요|했어|가능한)?$/,
  /^(찾|알려|추천|보여|싶|가능|되|돼|될|된|됨|괜찮|상관|다니|다녀|다님|만한|만들|만드)/,
  // 제출물을 만드는 동사 ("웹툰 그리는", "글 쓰는", "영상 찍을")
  /^(그리|그릴|그린|쓰는|쓸|쓰고|써서|찍|올리|올릴)/,
];

// 붙여 쓰거나 띄어 쓴 여러 낱말 표현을 한 낱말로 맞춘다 (해석 전 정리).
const JOINS = [
  [/이번\s*주\s*말/g, '이번주말'], [/이번\s*주/g, '이번주'], [/이번\s*달/g, '이번달'],
  [/다음\s*주/g, '다음주'], [/다음\s*달/g, '다음달'], [/(다음|지난|저번)\s*학기/g, '$1학기'],
  [/마감\s*(?:이\s*)?임박/g, '마감임박'], [/곧\s*마감/g, '마감임박'],
  [/마감\s*(?:이\s*)?(?:얼마\s*안\s*남은|코앞|직전)/g, '마감임박'],
  // "3일 안에 마감", "마감 일주일 남은": 계약 2절 5의 마감임박(7일 이내)
  [/(?:(?<!\d)[1-7]\s*일|일주일)\s*(?:이내|안에|안|내에|내)\s*(?:에\s*)?마감/g, '마감임박'],
  [/마감\s*(?:까지\s*)?(?:이\s*)?(?:(?<!\d)[1-7]\s*일|일주일)\s*(?:이내|안|남은|남음|전)/g, '마감임박'],
  [/초과\s*학기/g, '초과학기'], [/졸업\s*유예/g, '졸업유예'],
  [/(인문|자연|서울|용인)\s*캠/g, '$1캠'],
  [/(현장|개인|팀)\s*(참가|참여)/g, '$1$2'],
  [/체험\s*수기/g, '체험수기'], [/카드\s*뉴스/g, '카드뉴스'], [/사업\s*계획서/g, '사업계획서'], [/글\s*쓰기/g, '글쓰기'],
];

/**
 * 문장을 조건으로 바꾼다.
 * @param {string} text  학생이 쓴 문장
 * @returns {{campus: string|null, grade: number|null, is_transfer: boolean|null, enrollment: string|null,
 *   participation: string|null, deliverables: string[], period: string|null, unknown_terms: string[]}}
 *   계약 2절 조건. 말하지 않은 조건은 null(제출물은 빈 배열)
 * @throws {TypeError} text가 문자열이 아닐 때
 * @throws {RangeError} text가 MAX_INPUT_LENGTH자보다 길 때 (긴 붙여넣기·주소로 받은 문장이 화면을 멈추지 않게)
 */
export function parseConditions(text) {
  if (typeof text !== 'string') throw new TypeError('문장은 문자열이어야 합니다');
  if (text.length > MAX_INPUT_LENGTH) throw new RangeError(`문장은 ${MAX_INPUT_LENGTH}자까지입니다`);
  const tokens = tokenize(text);
  const hits = [];
  const unknownTerms = [];
  const consumed = new Set();

  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (token.isBoundary) continue;
    const matches = acceptMatches(matchToken(token.lower), tokens, index);
    if (!matches) {
      const term = leftoverTerm(token);
      if (term) unknownTerms.push(term);
      continue;
    }
    const last = matches[matches.length - 1];
    const context = readContext(tokens, index, last);
    const phraseTokens = tokens.slice(context.begin, context.end + 1).map((t) => t.raw);
    phraseTokens[phraseTokens.length - 1] = displayForm(phraseTokens[phraseTokens.length - 1]);
    const phrase = phraseTokens.join(' ');
    for (const match of matches) {
      const state = match === last ? context.state : 'plain';
      hits.push({ ...match, state, term: token.raw.slice(match.start, match.start + match.length), phrase, tokenIndex: index, clause: token.clause });
    }
    for (let used = index; used <= context.end; used += 1) consumed.add(used);
    index = context.end;
  }
  applyListNegation(tokens, hits);
  applyClauseRisks(tokens, hits, consumed);
  return assemble(hits, unknownTerms);
}

function tokenize(text) {
  let normalized = text.normalize('NFKC');
  normalized = normalized.replace(/([가-힣])(?=\d)/g, '$1 ');
  normalized = normalized.replace(/(\d)\s+(학년|명|일)/g, '$1$2');
  // "1, 2학년"·"1~2학년"·"1이나 2학년" → "1학년 2학년" (학년이 둘 이상이면 계약 2절 2로 null)
  for (let round = 0; round < 6; round += 1) {
    const before = normalized;
    normalized = normalized.replace(/(\d)\s*([,·~\-/]|이나|또는|아니면|이랑|랑|하고|와|과|및)\s*(?=\d학년)/g, '$1학년 ');
    if (normalized === before) break;
  }
  // "2~3명" → "3명". 숫자 길이에 상한을 둬 긴 숫자열에서 되돌림이 늘지 않게 한다
  normalized = normalized.replace(/(?<!\d)\d{1,3}\s*[~-]\s*(?=\d{1,3}\s*[명인])/g, '');
  for (const [pattern, replacement] of JOINS) normalized = normalized.replace(pattern, replacement);
  // 문장부호는 절의 경계: 부정·미래 표현이 경계를 넘어 다른 절의 조건에 걸리지 않게 한다
  const tokens = [];
  normalized.split(/[.,!?;:\n]+/).forEach((clauseText, clause) => {
    if (tokens.length > 0) tokens.push({ raw: '', lower: '', isBoundary: true, clause });
    const words = clauseText.replace(/[^\p{L}\p{N}\s]/gu, ' ').split(/\s+/).filter(Boolean);
    words.forEach((raw, position) => {
      tokens.push({ raw, lower: raw.toLowerCase(), isBoundary: false, clause, isClauseStart: position === 0 });
    });
  });
  return tokens;
}

// 낱말 하나를 사전 표현(+ 조사)으로 끝까지 나눌 수 있으면 표현 목록, 아니면 null.
// "앱개발로"처럼 표현 두 개가 붙은 낱말도 나눈다.
function matchToken(word, start = 0, depth = 0) {
  if (depth > 3 || word === '') return null;
  for (const candidate of candidatesFor(word)) {
    const rest = word.slice(candidate.length);
    const tail = readTail(rest);
    if (tail) return [{ ...candidate, ...tail, rest, start }];
    const more = matchToken(rest, start + candidate.length, depth + 1);
    if (more) return [{ ...candidate, tailNegated: false, tailFuture: false, rest: '', start }, ...more];
  }
  return null;
}

function candidatesFor(word) {
  const candidates = [];
  const grade = GRADE_RE.exec(word);
  if (grade) {
    const value = KOREAN_DIGITS[grade[1]] ?? Number(grade[1]);
    candidates.push({ dim: 'grade', value, stem: grade[0], length: grade[0].length });
  }
  const headcount = HEADCOUNT_RE.exec(word);
  if (headcount) {
    const value = Number(headcount[1]) <= 1 ? '개인' : '팀';
    candidates.push({ dim: 'participation', value, stem: headcount[0], length: headcount[0].length, isHeadcount: true });
  }
  for (const { dim, value, stem } of STEMS) {
    if (word.startsWith(stem)) candidates.push({ dim, value, stem, length: stem.length });
  }
  return candidates;
}

function readTail(rest, depth = 0) {
  if (rest === '') return { tailNegated: false, tailFuture: false };
  // 붙여 쓴 "안"·"못" ("편입안했어"). 뜻은 readAdverbNegation과 같다. "안에"는 기간이라 뺀다
  if (/^(안|못)(?!에|으로)/.test(rest)) {
    const adverb = readAdverbNegation(rest[0], rest.slice(1));
    return { tailNegated: adverb.state === 'negated', tailFuture: false, tailUncertain: adverb.state === 'uncertain' };
  }
  if (REJECTION_RE.test(rest)) return { tailNegated: false, tailFuture: false, tailRejected: true };
  if (FLIP_NEGATION_RE.test(rest)) return { tailNegated: true, tailFuture: false };
  if (FUTURE_TAIL_RE.test(rest)) return { tailNegated: false, tailFuture: true };
  if (VERB_RE.test(rest)) return { tailNegated: false, tailFuture: false };
  if (depth >= 4) return null;
  for (const particle of PARTICLES) {
    if (!rest.startsWith(particle)) continue;
    const tail = readTail(rest.slice(particle.length), depth + 1);
    if (tail) return tail;
  }
  return null;
}

// 사전 표현으로 나뉘었어도 문맥상 그 뜻이 아니면 버린다 (null이면 사전에 없는 낱말로 다룬다)
function acceptMatches(matches, tokens, index) {
  if (!matches) return null;
  const next = wordAt(tokens, index + 1) ?? '';
  const last = matches[matches.length - 1];
  // "웹디자인", "앱기획": 서로 다른 제출물이 한 낱말에 붙으면 어느 쪽인지 알 수 없다
  const tags = new Set(matches.filter((m) => m.dim === 'deliverables').map((m) => m.value));
  if (tags.size > 1) return null;
  // 학과 이름이 되는 것은 제출물 낱말뿐이다 ("디자인과"는 학과, "1학년과 2학년"의 과는 접속)
  if (last.dim === 'deliverables' && last.rest === '과' && DEPARTMENT_NEXT_RE.test(next)) return null;
  if (matches.length === 1) {
    if (last.stem === '친구' && !FRIEND_TAIL_RE.test(last.rest)) return null;
    if ((last.stem === '같이' || last.stem === '함께') && last.rest === '' && !TOGETHER_NEXT_RE.test(next)) return null;
  }
  return matches;
}

function wordAt(tokens, index) {
  const token = tokens[index];
  return token && !token.isBoundary ? token.lower : null;
}

/**
 * 표현 앞뒤 낱말을 보고 그 표현이 그대로 쓰였는지(plain), 부정됐는지(negated), 확실하지 않은지(uncertain) 정한다.
 * @returns {{state: 'plain'|'negated'|'uncertain', begin: number, end: number}} begin~end: 문구로 묶을 낱말 범위
 */
function readContext(tokens, index, hit) {
  const at = (offset) => wordAt(tokens, index + offset);
  const result = (state, end = index, begin = index) => ({ state, begin, end });
  const isTimeSensitive = FUTURE_SENSITIVE.has(hit.dim);

  // 거절은 참가 형태·제출물에서만 부정, 그 밖의 조건은 불확실
  const rejection = () => (REJECTION_FLIP_DIMS.has(hit.dim) ? 'negated' : 'uncertain');
  if (hit.tailNegated) return result('negated');
  if (hit.tailRejected) return result(rejection());
  if (hit.tailUncertain || (hit.tailFuture && isTimeSensitive)) return result('uncertain');
  if (hit.dim === 'grade' && GRADE_RANGE_RE.test(hit.rest)) return result('uncertain');
  if (hit.isHeadcount && HEADCOUNT_SCOPE_RE.test(hit.rest)) return result('uncertain');
  if (isTimeSensitive && TIME_SHIFT_RE.test(at(-1) ?? '')) return result('uncertain', index, index - 1);

  const next = at(1);
  if (next === null) return result('plain');
  if (isTimeSensitive) {
    if (FUTURE_NEXT_RE.test(next)) return result('uncertain', index + 1);
    if (next === '할' && FUTURE_AFTER_HAL_RE.test(at(2) ?? '')) return result('uncertain', index + 2);
    // 띄어 쓴 "편입 하고 싶은데"만 미래. "자연캠 하고 인문캠"의 하고는 접속이다
    if (next === '하고' && /^싶/.test(at(2) ?? '')) return result('uncertain', index + 2);
    if (hit.dim === 'grade' && GRADE_SHIFT_RE.test(next)) return result('uncertain', index + 1);
  }
  if (hit.dim === 'grade' && GRADE_RANGE_NEXT_RE.test(next)) return result('uncertain', index + 1);
  if (hit.isHeadcount && HEADCOUNT_SCOPE_RE.test(next)) return result('uncertain', index + 1);

  const adverb = readAdverbNegation(next, at(2));
  if (adverb) return result(adverb.state, index + adverb.length);

  let isBridgedOnly = true;
  for (let offset = 1; offset <= NEGATION_WINDOW; offset += 1) {
    const word = at(offset);
    if (word === null) break;
    // 떨어진 "안"·"못" ("팀으로 참가 못해"): 바로 옆이 아니면 무엇을 부정하는지 확실하지 않다
    const distantAdverb = readAdverbNegation(word, at(offset + 1));
    if (distantAdverb) {
      const end = index + offset + distantAdverb.length - 1;
      if (distantAdverb.state === 'plain') return result('plain', end);
      return result(isBridgedOnly ? distantAdverb.state : 'uncertain', end);
    }
    if (NEGATION_RE.test(word)) {
      if (!isBridgedOnly) return result('uncertain', index + offset);
      return result(REJECTION_RE.test(word) ? rejection() : 'negated', index + offset);
    }
    if (matchToken(word)) break;
    if (!NEGATION_BRIDGE.has(word)) {
      isBridgedOnly = false;
      if (!DISTANT_NEGATION_DIMS.has(hit.dim)) break;
    }
  }
  return result('plain');
}

// "안"·"못" 부정: "편입 안 했어"는 부정, "편입생은 안 돼?"는 자격을 묻는 말이라 부정이 아니다.
// "못 찍어", "안 짜도"처럼 그 밖의 말이 이어지면 무엇을 부정하는지 확실하지 않다.
function readAdverbNegation(next, after) {
  let adverb;
  let verb;
  let length;
  if (next === '안' || next === '못') {
    adverb = next;
    verb = after ?? '';
    length = after === null || after === undefined ? 1 : 2;
  } else if (/^(안|못)(했|해|한|함|하|돼|되|됨|될)/.test(next)) {
    adverb = next[0];
    verb = next.slice(1);
    length = 1;
  } else {
    return null;
  }
  if (/^(돼|되|됨|될)/.test(verb)) return { state: 'plain', length };
  // "안 해봤어"는 경험이 없다는 말이지 그 조건이 아니라는 말이 아니다
  if (adverb === '안' && /^(했|해(?!보|봐|봤|본)|한(?!국)|함|하(?!려))/.test(verb)) return { state: 'negated', length };
  return { state: 'uncertain', length };
}

// 나열을 잇는 말: "휴학이나 수료", "영상과 발표", "인문캠 또는 자연캠"
const LIST_JOIN_TAIL_RE = /(이나|나|과|와|이랑|랑|하고|이든|든)$/;
const LIST_JOIN_WORDS = new Set(['및', '또는', '혹은', '이나', '아니면']);

/**
 * 나열한 같은 조건의 뒤쪽이 부정·불확실이면 앞쪽도 비운다.
 * "휴학이나 수료는 아니에요"의 부정은 나열 전체에 걸리므로 "휴학"을 값으로 확정하면 안 된다.
 */
function applyListNegation(tokens, hits) {
  for (let i = hits.length - 2; i >= 0; i -= 1) {
    const first = hits[i];
    const second = hits[i + 1];
    if (first.state !== 'plain' || second.state === 'plain') continue;
    if (first.dim !== second.dim || first.clause !== second.clause || first.tokenIndex === second.tokenIndex) continue;
    const between = tokens.slice(first.tokenIndex + 1, second.tokenIndex).map((token) => token.lower);
    const isJoinedByTail = between.length === 0 && LIST_JOIN_TAIL_RE.test(first.rest);
    const isJoinedByWord = between.length === 1 && LIST_JOIN_WORDS.has(between[0]);
    if (!isJoinedByTail && !isJoinedByWord) continue;
    first.state = 'uncertain';
    first.phrase = `${first.phrase} … ${second.phrase}`;
  }
}

/**
 * 어느 표현에도 묶이지 않은 신분 부정·시간 표현이 절에 있으면 판정 조건을 불확실로 바꾼다.
 * - 신분 부정("자연캠 학생은 아니에요"): 같은 절에서 그 앞의 표현. 절 첫머리면("편입생, 아니고") 앞 절의 마지막 표현
 * - 시간 표현("지난 학기에 휴학했는데", "복학했어요"): 같은 절의 지금 상태 조건 전부
 */
function applyClauseRisks(tokens, hits, consumed) {
  const judged = hits.filter((hit) => JUDGED_DIMS.has(hit.dim));
  // 시간 표현은 부정으로 읽은 표현도 비운다 ("지난 학기에 편입 안 했어"는 지금 편입생이 아닌지 알 수 없다)
  const markUncertain = (hit, token, isNegationAffected = false) => {
    if (hit.state !== 'plain' && !(isNegationAffected && hit.state === 'negated')) return;
    hit.state = 'uncertain';
    hit.phrase = `${hit.phrase} … ${displayForm(token.raw)}`;
  };
  tokens.forEach((token, index) => {
    if (token.isBoundary || consumed.has(index)) return;
    if (CLAUSE_TIME_RE.test(token.lower)) {
      for (const hit of judged) {
        if (hit.clause === token.clause && FUTURE_SENSITIVE.has(hit.dim)) markUncertain(hit, token, true);
      }
    } else if (CLAUSE_IDENTITY_NEGATION_RE.test(token.lower)) {
      for (const hit of judged) {
        if (hit.clause === token.clause && hit.tokenIndex < index) markUncertain(hit, token);
      }
      if (token.isClauseStart) {
        const previous = judged.filter((hit) => hit.clause === token.clause - 1).at(-1);
        if (previous) markUncertain(previous, token);
      }
    }
  });
}

// 화면에 보여 줄 때 떼는 조사. PARTICLES와 달리 낱말의 일부일 수 있는 말("학과"의 과, "복학생"의 생)은 넣지 않는다.
const DISPLAY_PARTICLES = [
  '으로도', '에서는', '에서도', '이에요', '입니다', '인데요', '이라서', '인데', '이고', '이야', '이랑', '에서', '으로',
  '하고', '이나', '은', '는', '이', '가', '을', '를', '도', '만', '에', '로', '랑', '요',
].sort((a, b) => b.length - a.length);

function stripParticles(word, particles) {
  let stem = word;
  for (let round = 0; round < 2; round += 1) {
    const particle = particles.find((p) => stem.endsWith(p) && stem.length - p.length >= 2);
    if (!particle) break;
    stem = stem.slice(0, -particle.length);
  }
  return stem;
}

function displayForm(raw) {
  return raw.slice(0, stripParticles(raw.toLowerCase(), DISPLAY_PARTICLES).length);
}

function isFiller(word) {
  if (FILLER_WORDS.has(word)) return true;
  return FILLER_PATTERNS.some((pattern) => pattern.test(word));
}

// 사전에 없는 낱말: 조건과 상관없는 말이면 버리고, 아니면 unknown_terms에 넣을 형태를 돌려준다.
// 조사를 떼더라도 두 글자는 남긴다 ("게임"→"게", "나이"→"나"처럼 군말로 줄어들어 사라지지 않게).
// 한 글자로 줄이는 것은 두 글자 이상의 조사를 뗄 때만 ("쪽으로"→"쪽", "중인데"→"중").
function leftoverTerm(token) {
  const word = token.lower;
  if (NEGATION_RE.test(word) || PARTICLES.includes(word)) return null;
  if (isFiller(word) || isFiller(stripParticles(word, PARTICLES))) return null;
  const longParticle = PARTICLES.find((p) => p.length >= 2 && word.endsWith(p) && word.length > p.length);
  if (longParticle && isFiller(word.slice(0, -longParticle.length))) return null;
  return displayForm(token.raw);
}

function assemble(hits, unknownTerms) {
  const conditions = {
    campus: null, grade: null, is_transfer: null, enrollment: null,
    participation: null, deliverables: [], period: null, unknown_terms: [],
  };
  const unknown = [...unknownTerms];
  const byDim = (dim) => hits.filter((hit) => hit.dim === dim);
  // 확실하지 않은 표현은 값으로 쓰지 않고 문구를 보여 준다
  const usable = (dim, isNegationUsable) => byDim(dim).filter((hit) => {
    const isUsable = hit.state === 'plain' || (hit.state === 'negated' && isNegationUsable);
    if (!isUsable) unknown.push(hit.phrase);
    return isUsable;
  });

  const transfer = usable('is_transfer', true);
  pickSingle(conditions, 'is_transfer', transfer, (hit) => hit.state !== 'negated', unknown);

  for (const dim of ['campus', 'grade', 'enrollment', 'period']) {
    let plain = usable(dim, false);
    // "편입 신입생"은 1학년이 아닐 수 있다 (편입생은 보통 3학년으로 들어온다)
    if (dim === 'grade' && conditions.is_transfer === true) {
      plain = plain.filter((hit) => {
        const isFreshmanWord = hit.stem === '신입생' || hit.stem === '새내기';
        if (isFreshmanWord) unknown.push(hit.phrase);
        return !isFreshmanWord;
      });
    }
    pickSingle(conditions, dim, plain, (hit) => hit.value, unknown);
  }

  // 참가 형태는 둘 다 말하면 "상관없다"는 뜻이라 null만 둔다 (계약 2절 2)
  // 확실하지 않은 표현이 하나라도 있으면 다른 표현이 그대로 쓰였어도 비운다 ("친구랑 같이 하는 건 싫어")
  const isParticipationUncertain = byDim('participation').some((hit) => hit.state === 'uncertain');
  const participation = new Set(usable('participation', true)
    .map((hit) => (hit.state === 'negated' ? flipParticipation(hit.value) : hit.value)));
  if (participation.size === 1 && !isParticipationUncertain) conditions.participation = [...participation][0];

  const tags = new Set(usable('deliverables', false).map((hit) => hit.value));
  conditions.deliverables = STUDENT_DELIVERABLE_TAGS.filter((tag) => tags.has(tag));
  conditions.unknown_terms = [...new Set(unknown)];
  return conditions;
}

// 값이 하나로 모이면 그 값, 서로 다르면 null로 두고 표현을 unknown_terms에 넣는다 (계약 2절 2)
function pickSingle(conditions, dim, hits, valueOf, unknown) {
  const values = new Set(hits.map(valueOf));
  if (values.size === 1) conditions[dim] = [...values][0];
  else if (values.size > 1) unknown.push(...hits.map((hit) => (hit.state === 'negated' ? hit.phrase : hit.term)));
}

function flipParticipation(value) {
  return value === '개인' ? '팀' : '개인';
}
