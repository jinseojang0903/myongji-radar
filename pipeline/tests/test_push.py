"""cards.json 올리기(#19, `publisher/push.py`) 시험. 임시 원격(bare)과 복제 저장소로 실제 git을 돌린다 (네트워크 없음)."""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from publisher import __main__ as publisher
from publisher import push
from publisher.push import CARDS, PushError, prepare, push_cards

# 개발자 PC의 전역 설정(서명·훅 경로)에 시험이 흔들리지 않게
ISOLATED = ["-c", "commit.gpgsign=false", "-c", "core.hooksPath=", "-c", "init.defaultBranch=main"]


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *ISOLATED, "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout


def subject(repo: Path, ref: str = "HEAD") -> str:
    return git(repo, "log", "-1", "--format=%s", ref).strip()


class PushTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        empty_config = Path(self.temp.name) / "empty.gitconfig"
        empty_config.write_text("", encoding="utf-8")
        # 시험 PC의 전역·시스템 git 설정(서명, 훅 경로 등)을 쓰지 않는다 (push 모듈이 부르는 git에도 적용)
        environment = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(empty_config), "GIT_CONFIG_NOSYSTEM": "1"})
        environment.start()
        self.addCleanup(environment.stop)
        self.remote = Path(self.temp.name) / "remote.git"
        subprocess.run(["git", *ISOLATED, "init", "--quiet", "--bare", str(self.remote)], check=True)
        git(self.remote, "symbolic-ref", "HEAD", "refs/heads/main")
        self.repo = self.clone("work", first=True)
        (self.repo / "README.md").write_text("시작\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "--quiet", "-m", "처음")
        git(self.repo, "push", "--quiet", "origin", "HEAD:main")
        git(self.repo, "branch", "--quiet", "--set-upstream-to=origin/main")

    def tearDown(self):
        self.temp.cleanup()

    def clone(self, name: str, first: bool = False) -> Path:
        path = Path(self.temp.name) / name
        if first:
            subprocess.run(["git", *ISOLATED, "init", "--quiet", str(path)], check=True)
            git(path, "remote", "add", "origin", str(self.remote))
            git(path, "checkout", "--quiet", "-b", "main")
        else:
            subprocess.run(["git", *ISOLATED, "clone", "--quiet", str(self.remote), str(path)], check=True, capture_output=True)
        for key, value in (("user.name", "시험"), ("user.email", "test@example.invalid"), ("commit.gpgsign", "false")):
            git(path, "config", key, value)
        return path

    def write_cards(self, repo: Path, value) -> None:
        (repo / CARDS).parent.mkdir(parents=True, exist_ok=True)
        (repo / CARDS).write_text(json.dumps({"cards": value}) + "\n", encoding="utf-8")

    def team_merge(self, text: str) -> None:
        """다른 사람이 원격 main에 머지하는 일"""
        other = self.clone(f"other-{len(text)}")
        (other / "README.md").write_text(text, encoding="utf-8")
        git(other, "commit", "--quiet", "-am", text.strip())
        git(other, "push", "--quiet", "origin", "main")

    def remote_count(self) -> int:
        return int(git(self.remote, "rev-list", "--count", "main").strip())

    def test_first_cards_file_and_only_that_file(self):
        prepare(self.repo)
        self.write_cards(self.repo, [1])  # 처음엔 추적되지 않는 파일
        (self.repo / "README.md").write_text("작업 중인 수정\n", encoding="utf-8")
        (self.repo / "notes.txt").write_text("스테이지된 다른 파일\n", encoding="utf-8")
        git(self.repo, "add", "notes.txt")
        self.assertEqual(push_cards(self.repo, "카드 갱신"), "pushed")
        self.assertEqual(git(self.remote, "diff", "--name-only", "main~1", "main").split(), [CARDS.as_posix()],
                         "다른 파일은 바뀌었거나 스테이지돼 있어도 올리지 않는다")
        self.assertIn("notes.txt", git(self.repo, "diff", "--cached", "--name-only"), "다른 사람의 스테이지는 그대로 둔다")
        self.assertEqual(push_cards(self.repo, "카드 갱신"), "unchanged", "내용이 그대로면 커밋·푸시하지 않는다")
        self.assertEqual(self.remote_count(), 2)

    def test_prepare_follows_the_team_and_refuses_development_commits(self):
        git(self.repo, "checkout", "--quiet", "-b", "dev")
        with self.assertRaises(PushError):
            prepare(self.repo)
        git(self.repo, "checkout", "--quiet", "main")
        self.team_merge("팀 머지\n")
        prepare(self.repo)
        self.assertEqual((self.repo / "README.md").read_text(encoding="utf-8"), "팀 머지\n", "뒤처졌으면 앞으로 감는다")
        (self.repo / "README.md").write_text("main에 남은 개발 커밋\n", encoding="utf-8")
        git(self.repo, "commit", "--quiet", "-am", "개발")
        with self.assertRaises(PushError) as caught:
            prepare(self.repo)
        self.assertIn("다른 파일을 바꾼 커밋", str(caught.exception))
        self.write_cards(self.repo, [2])
        with self.assertRaises(PushError):
            push_cards(self.repo, "카드 갱신")
        self.assertEqual(self.remote_count(), 2, "개발 커밋은 딸려 올라가지 않는다")

    def test_a_file_added_then_removed_in_between_still_blocks(self):
        (self.repo / "notes.txt").write_text("원문을 실수로 커밋\n", encoding="utf-8")
        git(self.repo, "add", "notes.txt")
        git(self.repo, "commit", "--quiet", "-m", "실수")
        git(self.repo, "rm", "--quiet", "notes.txt")
        git(self.repo, "commit", "--quiet", "-m", "실수 지움")
        self.write_cards(self.repo, [9])
        git(self.repo, "add", CARDS.as_posix())
        git(self.repo, "commit", "--quiet", "-m", "카드")  # 합산 차이는 cards.json뿐이지만 이력에는 원문이 있다
        with self.assertRaises(PushError):
            prepare(self.repo)
        self.team_merge("그 사이 팀 머지\n")
        with self.assertRaises(PushError):
            prepare(self.repo)  # 갈라져도 "카드뿐"으로 보고 버리지 않는다
        self.assertEqual(subject(self.repo), "카드", "사람이 보도록 그대로 둔다")
        self.assertEqual(self.remote_count(), 2)

    def test_team_merge_during_the_run_is_absorbed(self):
        prepare(self.repo)
        self.team_merge("실행 중 팀 머지\n")
        self.write_cards(self.repo, [3])
        self.assertEqual(push_cards(self.repo, "카드 갱신"), "pushed", "원격 위에 카드를 다시 커밋해 올린다")
        self.assertEqual(subject(self.remote, "main"), "카드 갱신")
        self.assertEqual(git(self.remote, "show", "main:README.md"), "실행 중 팀 머지\n", "팀의 변경을 덮지 않는다 (force 없음)")
        self.assertEqual(json.loads(git(self.remote, "show", f"main:{CARDS.as_posix()}"))["cards"], [3])

    def test_leftover_card_commit_after_a_failed_push_is_cleaned_next_time(self):
        prepare(self.repo)
        self.write_cards(self.repo, [4])
        with mock.patch.object(push, "_git", side_effect=_failing_push(push._git)):
            with self.assertRaises(PushError):
                push_cards(self.repo, "어제 카드")
        self.assertEqual(subject(self.repo), "어제 카드", "로컬 커밋은 남는다")
        self.team_merge("그 사이 팀 머지\n")
        prepare(self.repo)  # 갈라졌지만 로컬 전용 커밋이 카드뿐 → 버리고 원격에 맞춘다 (멈추지 않는다)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), git(self.repo, "rev-parse", "origin/main"))
        self.write_cards(self.repo, [5])
        self.assertEqual(push_cards(self.repo, "오늘 카드"), "pushed")
        self.assertEqual(json.loads(git(self.remote, "show", f"main:{CARDS.as_posix()}"))["cards"], [5])
        self.assertNotIn("어제 카드", git(self.remote, "log", "--format=%s", "main"))

    def test_a_push_rejected_by_a_race_is_retried_once(self):
        prepare(self.repo)
        self.write_cards(self.repo, [6])
        calls = []
        real = push._git

        def reject_first_push(repo, *args, check=True):
            if args and args[0] == "push" and not calls:
                calls.append("rejected")
                return subprocess.CompletedProcess(["git", *args], 1, "", "")
            return real(repo, *args, check=check)

        with mock.patch.object(push, "_git", side_effect=reject_first_push):
            self.assertEqual(push_cards(self.repo, "카드 갱신"), "pushed", "푸시와 팀 머지가 겹쳐 거절되면 한 번 다시")
        self.assertEqual(calls, ["rejected"])
        with mock.patch.object(push, "_git", side_effect=_failing_push(push._git)):
            self.write_cards(self.repo, [7])
            with self.assertRaises(PushError):
                push_cards(self.repo, "카드 갱신")

    def test_hooks_do_not_run_unattended(self):
        hooks = self.repo / ".git" / "hooks"
        for name in ("pre-commit", "pre-push"):
            (hooks / name).write_bytes(b"#!/bin/sh\nexit 1\n")
        (hooks / "post-merge").write_bytes(b"#!/bin/sh\ntouch hook-ran\n")
        self.team_merge("팀 머지\n")
        prepare(self.repo)  # 앞으로 감기(merge --ff-only) 뒤 post-merge 훅도 돌지 않는다
        self.assertFalse((self.repo / "hook-ran").exists())
        self.write_cards(self.repo, [8])
        self.assertEqual(push_cards(self.repo, "카드 갱신"), "pushed", "main에 들어온 훅이 운영 PC에서 돌지 않게")

    def test_git_never_waits_for_input(self):
        with mock.patch.object(push.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", push.GIT_TIMEOUT)):
            with self.assertRaises(PushError) as caught:
                prepare(self.repo)
        self.assertIn("초 안에 끝나지 않음", str(caught.exception))
        seen = {}

        def record(command, **kwargs):
            seen.update(kwargs)
            return subprocess.CompletedProcess(command, 0, "main\n", "")

        with mock.patch.object(push.subprocess, "run", side_effect=record):
            push._git(self.repo, "branch", "--show-current")
        self.assertEqual((seen["env"]["GIT_TERMINAL_PROMPT"], seen["env"]["GCM_INTERACTIVE"], seen["timeout"]),
                         ("0", "never", push.GIT_TIMEOUT), "자격 증명을 묻지 않고 시간 안에 끝낸다")


def _failing_push(real):
    def fake(repo, *args, check=True):
        if args and args[0] == "push":
            return subprocess.CompletedProcess(["git", *args], 1, "", "")
        return real(repo, *args, check=check)
    return fake


class PublisherPushTests(unittest.TestCase):
    def run_cli(self, *extra):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
            code = publisher.main(["--model", "sonnet", *extra])
        return code, stderr.getvalue()

    def test_push_guards(self):
        with tempfile.TemporaryDirectory() as folder:
            code, log = self.run_cli("--push", "--out", str(Path(folder) / "cards.json"), "--data-dir", folder)
            self.assertEqual(code, 2, "저장소의 site/data/cards.json이 아니면 올리지 않는다")
            self.assertIn("--out을 바꾸지 않기", log)
            with mock.patch.object(publisher, "prepare", side_effect=PushError("main 브랜치에서만 올린다")):
                code, log = self.run_cli("--push", "--data-dir", folder)
            self.assertEqual(code, 2, "준비가 안 되면 아무 단계도 돌리지 않는다")
            entry = json.loads((Path(folder) / "publish_logs").glob("*.jsonl").__next__().read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual((entry["stages"], entry["stopped"]), ([], "올리기 준비 실패: main 브랜치에서만 올린다"),
                             "준비 실패도 기록에 남긴다")
            with mock.patch.object(publisher, "prepare"), mock.patch.object(publisher, "check_analysis"), \
                    mock.patch.object(publisher, "run_stage", return_value=0), \
                    mock.patch.object(publisher, "push_cards", side_effect=PushError("git push 실패 (종료 코드 1)")):
                code, log = self.run_cli("--push", "--data-dir", folder)
            self.assertEqual(code, 3, "카드는 썼지만 올리지 못하면 3")
            self.assertIn("[올리기 실패]", log)
            with mock.patch.object(publisher, "prepare"), mock.patch.object(publisher, "check_analysis"), \
                    mock.patch.object(publisher, "run_stage", return_value=1), \
                    mock.patch.object(publisher, "push_cards", return_value="pushed"):
                code, log = self.run_cli("--push", "--data-dir", folder)
            self.assertEqual(code, 1, "올렸어도 내보내기 경고(1)는 그대로 알린다")
            self.assertIn("[올림] cards.json을 main에 올림", log)


if __name__ == "__main__":
    unittest.main()
