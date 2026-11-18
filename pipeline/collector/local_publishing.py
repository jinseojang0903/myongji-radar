"""Git 추적 설정의 로컬 변경을 비공개 운영 설정 파일로 보존한다."""
import tempfile
import uuid
from pathlib import Path

from .publish_config import load_publishing


def atomic_write(path, content):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(content)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def migrate_publishing(repo):
    """main 갱신 전 실행한다. 유효한 운영 설정을 보존하고 추적 파일만 HEAD로 되돌린다."""
    from publisher.push import PushError, _git
    config = Path(repo) / 'pipeline/config/publishing.toml'
    if not config.is_file():
        return
    path = 'pipeline/config/publishing.toml'
    changed = _git(repo, 'diff', '--quiet', 'HEAD', '--', path, check=False).returncode
    if changed == 0:
        return
    if changed != 1 or _git(repo, 'diff', '--cached', '--quiet', '--', path, check=False).returncode:
        raise PushError('공개 설정의 Git 상태를 확인할 수 없거나 스테이지된 수정이 있음 (사람이 정리)')
    try:
        load_publishing(config)  # 설정을 검증하기 전에는 어느 파일도 바꾸지 않는다.
        original = config.read_bytes()
        backup = config.parents[1] / 'data/config_backups'
        backup.mkdir(parents=True, exist_ok=True)
        with (backup / ('publishing-' + uuid.uuid4().hex + '.toml')).open('xb') as file:
            file.write(original)
        local = config.with_suffix('.local.toml')
        if not local.exists():
            atomic_write(local, original)
        # 기존 로컬 파일은 검증만 하고 유지한다. 원래 추적 파일의 수정도 위 백업에 남긴다.
        # 원본·운영 사본을 보존한 이 파일만 복구한다. Git의 줄바꿈·인덱스 처리도 유지한다.
        _git(repo, 'restore', '--source=HEAD', '--worktree', '--', path)
    except (ValueError, OSError) as error:
        raise PushError('로컬 공개 설정을 보존하지 못함 (' + type(error).__name__ + ')') from None
