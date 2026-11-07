"""새로 쓴 cards.json만 main에 올린다 (#19). Vercel이 main의 site/를 배포한다 (`site/README.md` "배포").

- main 브랜치에서만. 원격 main보다 뒤처졌으면 앞으로 감기만 한다 (합치기 없음).
- **cards.json 한 파일만 커밋한다.** 다른 파일이 바뀌었거나 스테이지돼 있어도 담지 않는다 (`git commit -- <경로>`).
- 올라갈 커밋이 cards.json 말고 다른 파일을 바꾸면 올리지 않는다 (main에 남은 개발 커밋이 딸려 공개되지 않게).
- **원격과 갈라졌을 때:** 로컬에만 있는 커밋이 모두 cards.json만 바꿨으면(지난번 못 올린 카드) 그 커밋을 버리고 원격
  main 위에 오늘 카드를 다시 커밋한다. 카드는 날마다 새로 만들므로 잃는 것이 없다. 그 밖의 갈라짐은 사람이 정리한다.
  실행 중에 팀이 main에 머지해 푸시가 거절되면 같은 방법으로 한 번 다시 올린다. force-push는 하지 않는다.
- 내용이 그대로면 커밋하지 않는다. 무인 실행이라 git은 자격 증명을 묻지 않고(입력 대기 없음), 정해진 시간 안에
  끝나지 않으면 실패로 본다. 훅은 하나도 돌리지 않는다 (main에 들어온 훅이 운영 PC에서 무인으로 돌지 않게).
- 커밋 작성자는 돌리는 사람의 git 설정이다. Vercel 무료 팀은 비공개 저장소에서 팀 멤버가 아닌 작성자의 커밋을
  배포하지 않으므로, 저장소를 공개로 바꾸기 전에는 팀원 PC에서 올린 카드가 바로 배포되지 않는다.
"""
import os
import subprocess
from pathlib import Path

CARDS = Path("site") / "data" / "cards.json"
GIT_TIMEOUT = 120  # 초. fetch·push가 자격 증명이나 네트워크를 기다리며 무기한 멈추지 않게
GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}  # 자격 증명을 묻는 창·입력을 띄우지 않는다


class PushError(Exception):
    """올리기 전 확인·커밋·푸시 실패. 메시지는 고정 문구 + git 종료 코드 (git 출력은 담지 않는다)."""


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    try:
        # 훅은 하나도 돌리지 않는다 (commit·push만이 아니라 merge 뒤 post-merge 등도): 없는 폴더를 훅 폴더로
        no_hooks = Path(repo).resolve() / ".git" / "publisher-no-hooks"
        completed = subprocess.run(["git", "-C", str(repo), "-c", f"core.hooksPath={no_hooks}", *args],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   stdin=subprocess.DEVNULL, timeout=GIT_TIMEOUT, env={**os.environ, **GIT_ENV})
    except subprocess.TimeoutExpired:
        raise PushError(f"git {args[0]}이 {GIT_TIMEOUT}초 안에 끝나지 않음 (자격 증명·네트워크 확인)") from None
    except OSError as error:
        raise PushError(f"git을 실행하지 못함 ({type(error).__name__})") from None
    if check and completed.returncode != 0:
        raise PushError(f"git {args[0]} 실패 (종료 코드 {completed.returncode})")
    return completed


def _only_card_commits(repo: Path, base: str) -> bool:
    """base..HEAD의 커밋이 하나하나 모두 cards.json만 바꿨는지. 푸시는 이력 전체를 올리므로 합산 차이가 아니라
    커밋별로 본다 (중간에 넣었다 지운 원문·.env도 이력에는 남는다). 머지 커밋이 있으면 아니다."""
    if _git(repo, "rev-list", "--merges", f"{base}..HEAD").stdout.strip():
        return False
    output = _git(repo, "log", "--no-renames", "--name-only", "--format=", f"{base}..HEAD").stdout
    return all(line == CARDS.as_posix() for line in output.splitlines() if line.strip())


def _is_ancestor(repo: Path, older: str, newer: str) -> bool:
    return _git(repo, "merge-base", "--is-ancestor", older, newer, check=False).returncode == 0


def _sync(repo: Path, upstream: str, keep_cards: bytes | None) -> None:
    """HEAD를 원격 main에 맞춘다. 앞서 있거나 갈라졌으면 로컬 전용 커밋이 cards.json만 바꿨을 때만 버리고 맞춘다.

    keep_cards: 맞춘 뒤 다시 써 둘 오늘 카드 내용 (None이면 원격 것 그대로. 카드는 곧 다시 만든다).
    """
    if _is_ancestor(repo, "HEAD", upstream):
        _git(repo, "merge", "--ff-only", "--quiet", upstream)  # 뒤처졌거나 같음: 앞으로 감기만
        return
    base = _git(repo, "merge-base", "HEAD", upstream).stdout.strip()
    if not _only_card_commits(repo, base):
        raise PushError(f"로컬 main에 cards.json 말고 다른 파일을 바꾼 커밋이 있음 (개발 커밋은 PR로, 사람이 정리)")
    if _is_ancestor(repo, upstream, "HEAD"):
        return  # 앞서 있고 카드 커밋뿐: 이번 푸시에 함께 올라간다
    # 갈라짐 + 로컬 전용 커밋이 카드뿐: 지난번 못 올린 카드다. 버리고 원격 위에 오늘 카드를 다시 커밋한다
    _git(repo, "reset", "--keep", "--quiet", upstream)
    if keep_cards is not None:
        target = repo / CARDS
        try:
            target.parent.mkdir(parents=True, exist_ok=True)  # 원격에 아직 카드가 없으면 폴더째 사라진다
            target.write_bytes(keep_cards)
        except OSError as error:
            raise PushError(f"원격에 맞춘 뒤 카드를 다시 쓰지 못함 ({type(error).__name__})") from None


def prepare(repo: Path, remote: str = "origin", branch: str = "main") -> None:
    """돌리기 전에: main인지 보고 원격 main에 맞춘다 (위 _sync 규칙).

    Raises: PushError
    """
    current = _git(repo, "branch", "--show-current").stdout.strip()
    if current != branch:
        raise PushError(f"{branch} 브랜치에서만 올린다 (지금 {current or '브랜치 없음'})")
    _git(repo, "fetch", "--quiet", remote, branch)
    _sync(repo, f"{remote}/{branch}", keep_cards=None)


def _commit_cards(repo: Path, message: str) -> None:
    path = CARDS.as_posix()
    if _git(repo, "status", "--porcelain", "--", path).stdout.strip():
        _git(repo, "add", "--", path)
        _git(repo, "commit", "--quiet", "--no-verify", "-m", message, "--", path)


def push_cards(repo: Path, message: str, remote: str = "origin", branch: str = "main") -> str:
    """cards.json이 바뀌었으면 그 파일만 커밋하고 올린다. Returns: "pushed" | "unchanged".

    실행 중에 원격 main이 바뀌었으면(팀의 머지) 원격 위에 카드를 다시 커밋해 한 번 더 올린다.
    Raises: PushError (커밋·푸시 실패. 로컬 커밋은 남고 다음 실행이 정리해 함께 올린다)
    """
    upstream = f"{remote}/{branch}"
    _commit_cards(repo, message)
    for attempt in range(2):
        _git(repo, "fetch", "--quiet", remote, branch)
        cards = (repo / CARDS).read_bytes() if (repo / CARDS).exists() else None
        _sync(repo, upstream, keep_cards=cards)
        _commit_cards(repo, message)
        if _git(repo, "rev-list", "--count", f"{upstream}..HEAD").stdout.strip() == "0":
            return "unchanged"
        if not _only_card_commits(repo, upstream):
            raise PushError("올라갈 커밋이 cards.json 말고 다른 파일을 바꿈 (개발 커밋은 PR로)")
        pushed = _git(repo, "push", "--quiet", "--no-verify", remote, f"HEAD:{branch}", check=False)  # force 없음
        if pushed.returncode == 0:
            return "pushed"
    raise PushError(f"git push 실패 (종료 코드 {pushed.returncode})")
