"""프로세스 종료 시 OS가 해제하는 데이터 폴더 단위 잠금."""
import errno
import os
from pathlib import Path


class AlreadyRunning(Exception):
    pass


class CollectorLock:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "collector.lock"
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        try:
            if self.path.stat().st_size == 0:
                self.file.write(b"0")
                self.file.flush()
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            self.file = None
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise AlreadyRunning("같은 데이터 폴더에서 다른 작업이 실행 중입니다") from exc
            raise
        return self

    def __exit__(self, *_):
        # 파일 자체는 삭제하지 않는다. 삭제하면 다른 inode를 잠그는 경쟁이 생긴다.
        if self.file is not None:
            self.file.close()
            self.file = None
