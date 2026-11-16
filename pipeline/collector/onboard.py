"""명지대 게시판 주소를 넣으면 수집 출처로 등록한다 (#99).

    python -m collector.onboard https://ince.mju.ac.kr/ince/5121/subview.do [다른 주소 ...] [--write]

게시판마다:
1. 명지대 공개 주소(https, *.mju.ac.kr)인지 본다. 아니면 "지원 안 됨".
2. K2Web 게시판 번호(/bbs/<사이트>/<번호>/)를 찾는다. 주소에 있으면 그대로, 목록 페이지면 페이지 안의 글 링크에서.
3. RSS(/bbs/<사이트>/<번호>/rssList.do)를 읽어 보고, 안 되면 목록 페이지(HTML)로 발견한다.
4. 공고 몇 건을 시험 수집해 제목·본문을 읽을 수 있는지 본다 (기본 선택자). 못 읽으면 등록하지 않는다.
5. 이미 등록된 게시판(같은 사이트·번호)이면 건너뛴다.
6. 설정 칸을 보여 주고, --write면 config/sources.toml에 더한 뒤 형식 검사(load_sources)를 통과할 때만 남긴다.

네트워크는 수집과 같은 Fetcher(허용 호스트·요청 간격·시간 제한)로만 쓴다. 로그인·쿠키를 넣지 않는다.
등록 뒤의 규칙 분석·AI 추출·근거 대조·카드 공개는 매일 실행이 그대로 이어서 한다.
게시판 이름은 공개 카드(boards)에 나가므로 --name으로 주거나, 페이지 제목에서 만든 이름을 확인해 고친다.

종료 코드: 0 모두 등록(또는 이미 있음)·제안만 함, 1 일부 주소를 등록하지 못함, 2 실행 오류.
"""
import argparse
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .adapters.k2web import K2WebAdapter, normalized_text
from .config import load_sources
from .fetcher import Fetcher
from .models import Source

ROOT = Path(__file__).resolve().parent.parent
HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+mju\.ac\.kr")
BOARD_PATH = re.compile(r"/bbs/([A-Za-z0-9_]+)/(\d+)/")
SAMPLE_DETAILS = 3
MAX_NAME_CHARS = 40
NAME_NOISE = re.compile(r"\s*[|\-–]\s*(명지대학교|MYONGJI UNIVERSITY).*$", re.IGNORECASE)


@dataclass
class Proposal:
    """주소 하나의 검사 결과. ok면 entry(설정 칸)를 등록할 수 있다."""
    url: str
    ok: bool | None = False  # None: 이미 등록돼 할 일 없음
    reason: str = ""
    entry: dict = field(default_factory=dict)
    sampled: int = 0
    readable: int = 0


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


def _page_name(html: bytes) -> str:
    soup = BeautifulSoup(html, "html.parser", from_encoding="utf-8")
    title = normalized_text(soup.title) if soup.title else ""
    return NAME_NOISE.sub("", title).strip()[:MAX_NAME_CHARS]


def _source_id(site: str, board: str, taken: set[str]) -> str:
    base = "mju_" + re.sub(r"[^a-z0-9]+", "_", site.lower()).strip("_")
    for candidate in (base[:40], f"{base}_{board}"[:40]):
        if candidate not in taken:
            return candidate
    raise ValueError("출처 id를 만들지 못함 (같은 사이트의 게시판이 이미 있음)")


def probe(url: str, fetcher, existing: list[Source], defaults: dict, name: str | None = None,
          taken: set[str] | None = None) -> Proposal:
    """주소 하나를 검사해 설정 칸을 제안한다. 네트워크 오류·구조 불일치는 ok=False와 이유로 돌려준다 (예외로 올리지 않음)."""
    proposal = Proposal(url)
    taken = taken if taken is not None else {source.id for source in existing}
    try:
        parts = urlsplit(url)
    except ValueError:
        proposal.reason = "주소 형식이 아님"
        return proposal
    host = parts.hostname or ""
    if parts.scheme != "https" or not HOST.fullmatch(host) or parts.username or parts.password or parts.port not in (None, 443):
        proposal.reason = "지원 안 됨: 명지대 공개 게시판(https, *.mju.ac.kr)만 등록한다"
        return proposal
    allowed = [host]
    page = None
    board = board_of(url)
    try:
        if board is None or name is None:
            page = fetcher.get(url, allowed)
        if board is None:
            links = BeautifulSoup(page, "html.parser", from_encoding="utf-8").select("a[href]")
            boards = [board_of(urljoin(url, link["href"])) for link in links if "artclView.do" in link["href"]]
            board = next((item for item in boards if item), None)
        if board is None:
            proposal.reason = "지원 안 됨: K2Web 게시판 글 링크를 찾지 못함 (다른 구조이거나 로그인이 필요한 페이지)"
            return proposal
        if (host, *board) in known_boards(existing):
            proposal.reason = "이미 등록된 게시판"
            proposal.ok = None  # 등록할 일 없음 (실패 아님)
            return proposal
        site, number = board
        adapter = K2WebAdapter()
        entry = {"id": _source_id(site, number, taken), "name": (name or _page_name(page or b"") or f"{host} 게시판 {number}"),
                 "priority": 100, "allowed_hosts": allowed}
        rss_url = f"https://{host}/bbs/{site}/{number}/rssList.do?row=50"
        refs = []
        for repair in (False, True):
            try:
                source = Source(rss_url=rss_url, repair_rss_ampersands=repair, **defaults, **entry)
                refs = adapter.discover(fetcher.get(rss_url, allowed), source)
                entry.update(rss_url=rss_url, **({"repair_rss_ampersands": True} if repair else {}))
                break
            except Exception:  # RSS가 없거나 깨짐: 다음 방법으로
                refs = []
        if not refs:
            list_url = url if page is not None and board_of(url) is None else f"https://{host}/bbs/{site}/{number}/artclList.do"
            source = Source(rss_url="", list_url=list_url, discovery="html", **defaults, **entry)
            try:
                refs = adapter.discover(fetcher.get(list_url, allowed), source)
            except Exception as error:
                proposal.reason = f"RSS와 목록 페이지에서 글을 찾지 못함 ({type(error).__name__})"
                return proposal
            entry.update(list_url=list_url, discovery="html")
        else:
            source = Source(**defaults, **entry)
        for ref in [ref for ref in refs if not ref.is_pinned][:SAMPLE_DETAILS] or refs[:SAMPLE_DETAILS]:
            proposal.sampled += 1
            try:
                adapter.parse_detail(fetcher.get(ref.url, allowed), ref, source)
                proposal.readable += 1
            except Exception:
                continue
        if proposal.readable == 0:
            proposal.reason = f"공고 {proposal.sampled}건 중 제목·본문을 읽은 글이 없음 (게시판 구조가 다름)"
            return proposal
        proposal.ok, proposal.entry = True, entry
        taken.add(entry["id"])
        return proposal
    except Exception as error:  # 네트워크·응답 이상: 이 주소만 실패로
        proposal.reason = f"읽지 못함 ({type(error).__name__})"
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
        return '"' + str(item).replace("\\", "\\\\").replace('"', '\\"') + '"'
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
    parser.add_argument("--name", action="append", default=[], help="게시판 이름 (주소 순서대로. 공개 카드에 나간다)")
    parser.add_argument("--config", type=Path, default=ROOT / "config/sources.toml")
    parser.add_argument("--write", action="store_true", help="검사를 통과한 게시판을 설정 파일에 더한다 (없으면 제안만)")
    args = parser.parse_args(argv)
    try:
        existing = load_sources(args.config)
    except (ValueError, OSError) as error:
        print(f"[오류] 출처 설정을 읽지 못함: {error}", file=sys.stderr)
        return 2
    fetcher = Fetcher()
    taken = {source.id for source in existing}
    defaults = read_defaults(args.config)
    proposals = [probe(url, fetcher, existing, defaults, args.name[index] if index < len(args.name) else None, taken)
                 for index, url in enumerate(args.urls)]
    accepted = [proposal for proposal in proposals if proposal.ok]
    for proposal in proposals:
        if proposal.ok:
            print(f"[등록 가능] {proposal.url} — 공고 {proposal.sampled}건 중 {proposal.readable}건 읽음")
            print(toml_entry(proposal.entry))
        elif proposal.ok is None:
            print(f"[건너뜀] {proposal.url} — {proposal.reason}")
        else:
            print(f"[등록 못 함] {proposal.url} — {proposal.reason}")
    if args.write and accepted:
        try:
            write_entries(args.config, [proposal.entry for proposal in accepted])
        except Exception as error:
            print(f"[오류] 설정 파일에 더하지 못함 (원래대로 둠): {error}", file=sys.stderr)
            return 2
        print(f"[더함] {len(accepted)}곳 → {args.config}. 다음 매일 수집부터 이 게시판도 읽는다")
    elif accepted:
        print("제안만 했습니다. 등록하려면 --write를 붙여 다시 실행하세요.")
    return 1 if any(proposal.ok is False for proposal in proposals) else 0


if __name__ == "__main__":
    sys.exit(main())
