"""명지대 게시판 주소를 넣으면 수집 출처로 등록한다 (#99).

    python -m collector.onboard https://ince.mju.ac.kr/ince/5121/subview.do [다른 주소 ...] [--name 이름 ...] [--write]

게시판마다:
1. 명지대 공개 주소(https, *.mju.ac.kr, 443, 인증정보 없음)인지 본다. 아니면 요청하지 않고 "지원 안 됨".
2. K2Web 게시판 번호(/bbs/<사이트>/<번호>/)를 찾는다. 주소에 있으면 그대로, 목록 페이지면 목록 표의 글 링크
   (a.artclLinkView, 같은 호스트)에서. 사이드바·최신글 위젯이 다른 게시판을 가리켜도 목록 표의 번호를 쓴다.
3. RSS(/bbs/<사이트>/<번호>/rssList.do)를 읽어 보고, 없거나 깨졌으면 목록 페이지(HTML)로 발견한다.
4. 고정 공지가 아닌 공고 3건을 시험 수집해 제목·본문을 읽을 수 있는지 본다. 하나도 못 읽으면 등록하지 않는다.
5. 이미 등록된 게시판(같은 호스트·사이트·번호, 같은 실행에서 앞서 통과한 것 포함)이면 건너뛴다.
6. 설정 칸을 보여 주고, --write면 config/sources.toml에 더한 뒤 형식 검사(load_sources)를 통과할 때만 남긴다.

네트워크는 수집과 같은 Fetcher(허용 호스트·요청 간격·시간 제한)로만 쓴다. 로그인·쿠키를 넣지 않는다.
등록 뒤의 규칙 분석·AI 추출·근거 대조·카드 공개는 매일 실행이 그대로 이어서 한다.
게시판 이름은 공개 카드(boards)에 나가므로 --name으로 준다. 없으면 페이지 제목에서 만들고, "공지사항"처럼
흔한 이름이거나 이미 있는 이름이면 호스트를 덧붙인다.

종료 코드: 0 모두 등록 가능(또는 이미 있음), 1 일부 주소를 등록하지 못함, 2 실행 오류.
"""
import argparse
import json
import re
import sys
import tomllib
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from .adapters.k2web import K2WebAdapter, normalized_text
from .config import MJU_HOST, load_sources
from .fetcher import Fetcher, FetchPolicyError
from .models import Source

ROOT = Path(__file__).resolve().parent.parent
BOARD_PATH = re.compile(r"/bbs/([A-Za-z0-9_]+)/(\d+)/")
SAMPLE_DETAILS = 3
MAX_NAME_CHARS = 40
MAX_ID_CHARS = 40  # 공개 출처 id 규칙 (exporter.cards SOURCE_ID_PATTERN)
NAME_NOISE = re.compile(r"\s*[|\-–]\s*(명지대학교|MYONGJI UNIVERSITY).*$", re.IGNORECASE)
GENERIC_NAMES = {"공지사항", "공지", "알림", "알림마당", "게시판", "학과공지", "학부공지", "news", "notice"}


@dataclass
class Proposal:
    """주소 하나의 검사 결과. ok면 entry(설정 칸)를 등록할 수 있다."""
    url: str
    ok: bool | None = False  # None: 이미 등록돼 할 일 없음
    reason: str = ""
    entry: dict = field(default_factory=dict)
    sampled: int = 0
    readable: int = 0
    name_from_page: bool = False


def board_of(url: str) -> tuple[str, str] | None:
    match = BOARD_PATH.search(urlsplit(url).path + "/")
    return (match.group(1), match.group(2)) if match else None


def known_boards(sources: list[Source]) -> set[tuple[str, str, str]]:
    """이미 등록된 (호스트, 사이트, 게시판 번호)."""
    known = set()
    for source in sources:
        for url in (source.rss_url, source.list_url):
            board = board_of(url) if url else None
            if board:
                known.add((urlsplit(url).hostname, *board))
    return known


def clean_name(text) -> str:
    """공개 카드에 나갈 게시판 이름: 제어·보이지 않는 글자를 빼고 공백을 하나로, 40자까지. 비면 ""."""
    if not isinstance(text, str):
        return ""
    # 줄바꿈·탭은 띄어쓰기로, 그 밖의 제어·보이지 않는 글자는 지운다
    kept = "".join(" " if char.isspace() else char for char in unicodedata.normalize("NFC", text)
                   if char.isspace() or unicodedata.category(char)[0] != "C")
    return " ".join(kept.split())[:MAX_NAME_CHARS].strip()


def _why(error: BaseException) -> str:
    """사용자에게 보일 실패 이유 (내부 경로·응답 내용 없이)."""
    if isinstance(error, FetchPolicyError):
        return "허용되지 않은 주소·응답"
    if isinstance(error, requests.Timeout):
        return "시간 초과"
    if isinstance(error, requests.HTTPError) and error.response is not None:
        return f"HTTP {error.response.status_code}"
    if isinstance(error, (ET.ParseError, ValueError)):
        return "형식이 다름"
    if isinstance(error, (requests.RequestException, OSError)):
        return "연결 실패"
    return type(error).__name__


def _list_board(page: bytes, url: str, host: str) -> tuple[tuple[str, str] | None, str]:
    """목록 페이지 → (게시판, 이유). 목록 표의 글 링크(a.artclLinkView) 중 같은 호스트만 본다."""
    soup = BeautifulSoup(page, "html.parser", from_encoding="utf-8")
    boards = []
    for link in soup.select("a.artclLinkView[href]"):
        absolute = urljoin(url, link["href"])
        if urlsplit(absolute).hostname == host and urlsplit(absolute).path.endswith("/artclView.do"):
            boards.append(board_of(absolute))
    counts = Counter(board for board in boards if board)
    if not counts:
        return None, "지원 안 됨: 목록 표에서 K2Web 게시판 글 링크를 찾지 못함 (다른 구조이거나 로그인이 필요한 페이지)"
    (best, best_count), *rest = counts.most_common()
    if rest and rest[0][1] == best_count:
        return None, "목록 표에 여러 게시판의 글이 같은 수로 섞여 있어 고르지 못함 (게시판 주소(/bbs/...)를 직접 넣기)"
    return best, ""


def _source_id(site: str, board: str, taken: set[str]) -> str | None:
    base = "mju_" + (re.sub(r"[^a-z0-9]+", "_", site.lower()).strip("_") or "board")
    with_board = f"{base[:MAX_ID_CHARS - 1 - len(board)]}_{board}"
    for candidate in (base[:MAX_ID_CHARS], with_board):
        if candidate not in taken:
            return candidate
    return None


def _page_name(page: bytes | None, host: str, number: str, names: set[str]) -> str:
    """페이지 제목으로 이름을 만든다. 흔하거나 이미 있는 이름이면 호스트를 덧붙인다 (그래도 겹치면 probe가 거절한다)."""
    title = ""
    if page:
        soup = BeautifulSoup(page, "html.parser", from_encoding="utf-8")
        title = clean_name(NAME_NOISE.sub("", normalized_text(soup.title)) if soup.title else "")
    if not title:
        return clean_name(f"{host} 게시판 {number}")
    if title.lower() in GENERIC_NAMES or title in names:
        return clean_name(f"{title} ({host})")  # "공지사항"이 여러 개면 공개 카드에서 구분이 안 된다
    return title


def probe(url: str, fetcher, existing: list[Source], defaults: dict, name: str | None = None,
          taken: set[str] | None = None, known: set | None = None, names: set[str] | None = None) -> Proposal:
    """주소 하나를 검사해 설정 칸을 제안한다. 어떤 실패도 ok=False와 이유로 돌려준다 (예외로 올리지 않음).
    taken·known·names(출처 id, 등록된 게시판, 게시판 이름)는 같은 실행에서 통과한 제안을 더해 다음 주소의 중복을 막는다."""
    proposal = Proposal(url)
    taken = taken if taken is not None else {source.id for source in existing}
    known = known if known is not None else known_boards(existing)
    names = names if names is not None else {source.name for source in existing}
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port  # 잘못된 포트("abc")는 ValueError
    except ValueError:
        proposal.reason = "지원 안 됨: 주소 형식이 아님"
        return proposal
    if parts.scheme != "https" or not MJU_HOST.fullmatch(host) or parts.username or parts.password or port not in (None, 443):
        proposal.reason = "지원 안 됨: 명지대 공개 게시판(https, *.mju.ac.kr)만 등록한다"
        return proposal
    allowed = [host]
    page = None
    try:
        board = board_of(url)
        if board is None or not name:
            page = fetcher.get(url, allowed)
        if board is None:
            board, reason = _list_board(page, url, host)
            if board is None:
                proposal.reason = reason
                return proposal
    except Exception as error:
        proposal.reason = f"페이지를 읽지 못함 ({_why(error)})"
        return proposal
    if (host, *board) in known:
        proposal.ok, proposal.reason = None, "이미 등록된 게시판"
        return proposal
    site, number = board
    source_id = _source_id(site, number, taken)
    if source_id is None:
        proposal.reason = f"출처 id를 만들지 못함 (mju_{site} 계열 id가 이미 있음. 설정에 직접 다른 id로 넣기)"
        return proposal
    proposal.name_from_page = not name
    entry = {"id": source_id, "name": name or _page_name(page, host, number, names), "priority": 100,
             "allowed_hosts": allowed}
    adapter = K2WebAdapter()
    rss_url = f"https://{host}/bbs/{site}/{number}/rssList.do?row=50"
    refs, last_error = [], None
    raw = None
    for repair in (False, True):
        try:
            raw = raw if raw is not None else fetcher.get(rss_url, allowed)
        except Exception as error:  # 요청 실패(없음·시간 초과 등): 다시 요청하지 않고 목록 페이지로
            last_error = error
            break
        try:
            refs = adapter.discover(raw, Source(rss_url=rss_url, repair_rss_ampersands=repair, **defaults, **entry))
            entry.update(rss_url=rss_url, **({"repair_rss_ampersands": True} if repair else {}))
            break
        except Exception as error:  # RSS 형식이 깨짐: & 보정으로 한 번 더 (요청은 다시 하지 않는다)
            last_error, refs = error, []
            continue
    if not refs:
        list_url = url if board_of(url) is None else f"https://{host}/bbs/{site}/{number}/artclList.do"
        try:
            refs = adapter.discover(fetcher.get(list_url, allowed),
                                    Source(rss_url="", list_url=list_url, discovery="html", **defaults, **entry))
        except Exception as error:
            proposal.reason = f"RSS({_why(last_error) if last_error else '없음'})와 목록 페이지({_why(error)})에서 글을 찾지 못함"
            return proposal
        entry.update(list_url=list_url, discovery="html")
    source = Source(**{"rss_url": "", **defaults, **entry})
    sample_error = None
    for ref in [ref for ref in refs if not ref.is_pinned][:SAMPLE_DETAILS] or refs[:SAMPLE_DETAILS]:
        proposal.sampled += 1
        try:
            adapter.parse_detail(fetcher.get(ref.url, allowed), ref, source)
            proposal.readable += 1
        except Exception as error:
            sample_error = error
    if proposal.readable == 0:
        proposal.reason = (f"공고 {proposal.sampled}건 중 제목·본문을 읽은 글이 없음"
                           + (f" ({_why(sample_error)})" if sample_error else "") + ": 게시판 구조가 다르거나 로그인 필요")
        return proposal
    if entry["name"] in names:
        proposal.reason = f"게시판 이름 \"{entry['name']}\"이 이미 있음 (공개 카드에서 구분되지 않는다. --name으로 다른 이름을 주기)"
        return proposal
    proposal.ok, proposal.entry = True, entry
    taken.add(source_id)
    known.add((host, site, number))
    names.add(entry["name"])
    return proposal


def read_defaults(path: Path) -> dict:
    """설정 파일의 [defaults] 중 어댑터·선택자 (시험 수집에 쓴다)."""
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    return {key: config["defaults"][key] for key in ("adapter", "title_selector", "body_selector", "attachment_selector")}


def toml_entry(entry: dict) -> str:
    def value(item):
        if isinstance(item, bool):
            return "true" if item else "false"
        if isinstance(item, int):
            return str(item)
        if isinstance(item, list):
            return "[" + ", ".join(value(part) for part in item) + "]"
        # JSON 문자열은 TOML 기본 문자열 문법과 같다 (\" \\ \n \uXXXX). DEL은 JSON이 그대로 두므로 따로 적는다
        return json.dumps(str(item), ensure_ascii=False).replace("\x7f", "\\u007f")
    order = ("id", "name", "priority", "discovery", "list_url", "rss_url", "repair_rss_ampersands", "allowed_hosts")
    lines = ["[[sources]]"] + [f"{key} = {value(entry[key])}" for key in order if key in entry]
    return "\n".join(lines) + "\n"


def write_entries(config: Path, entries: list[dict]) -> None:
    """설정 파일 끝에 칸을 더하고, 형식 검사를 통과하지 못하면 원래대로 되돌린다.

    Raises: ValueError (형식 검사 실패. 파일은 원래대로)
    """
    original = config.read_bytes()
    addition = "\n# collector.onboard로 등록 (게시판 이름은 공개 카드에 나간다. 확인 후 고친다)\n" + "\n".join(map(toml_entry, entries))
    config.write_bytes(original + addition.encode("utf-8"))
    try:
        load_sources(config)
    except Exception:
        config.write_bytes(original)
        raise


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="명지대 게시판 주소를 수집 출처로 등록")
    parser.add_argument("urls", nargs="+", help="게시판 목록 주소 (예: https://ince.mju.ac.kr/ince/5121/subview.do)")
    parser.add_argument("--name", action="append", default=[], help="게시판 이름 (주소 순서대로 주소 수만큼. 공개 카드에 나간다)")
    parser.add_argument("--config", type=Path, default=ROOT / "config/sources.toml")
    parser.add_argument("--write", action="store_true", help="검사를 통과한 게시판을 설정 파일에 더한다 (없으면 제안만)")
    args = parser.parse_args(argv)
    if args.name and len(args.name) != len(args.urls):
        parser.error(f"--name은 주소 수({len(args.urls)})만큼 주거나 주지 않는다 (지금 {len(args.name)}개)")
    names = [clean_name(name) for name in args.name]
    if args.name and not all(names):
        parser.error("--name에 보이는 글자가 없는 이름이 있다")
    try:
        existing = load_sources(args.config)
        defaults = read_defaults(args.config)
    except KeyError as error:
        print(f"[오류] 출처 설정의 [defaults]에 {error} 값이 없음", file=sys.stderr)
        return 2
    except (ValueError, OSError) as error:
        print(f"[오류] 출처 설정을 읽지 못함: {error}", file=sys.stderr)
        return 2
    fetcher = Fetcher()
    taken, known, used_names = {source.id for source in existing}, known_boards(existing), {source.name for source in existing}
    proposals = [probe(url, fetcher, existing, defaults, names[index] if names else None, taken, known, used_names)
                 for index, url in enumerate(args.urls)]
    accepted = [proposal for proposal in proposals if proposal.ok]
    for proposal in proposals:
        if proposal.ok:
            print(f"[등록 가능] {proposal.url} — 공고 {proposal.sampled}건 중 {proposal.readable}건 읽음")
            if proposal.name_from_page:
                print(f"  이름 확인: 페이지 제목에서 만든 이름 \"{proposal.entry['name']}\" (공개 카드에 나간다. --name으로 바꿀 수 있다)")
            print(toml_entry(proposal.entry))
        elif proposal.ok is None:
            print(f"[건너뜀] {proposal.url} — {proposal.reason}")
        else:
            print(f"[등록 못 함] {proposal.url} — {proposal.reason}")
    if args.write and accepted:
        try:
            write_entries(args.config, [proposal.entry for proposal in accepted])
        except Exception as error:
            print(f"[오류] 설정 파일에 더하지 못함 (원래대로 둠): {type(error).__name__}: {error}", file=sys.stderr)
            return 2
        print(f"[더함] {len(accepted)}곳 → {args.config}. 다음 매일 수집부터 이 게시판도 읽는다")
    elif accepted:
        print("제안만 했습니다. 등록하려면 --write를 붙여 다시 실행하세요.")
    return 1 if any(proposal.ok is False for proposal in proposals) else 0


if __name__ == "__main__":
    sys.exit(main())
