"""운영 설정 이관·오프라인 당일 건너뜀·Windows 하위 작업 종료. 실제 AI·네트워크 없음."""
import contextlib
import ctypes
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from collector import daily
from collector.local_publishing import migrate_publishing
from collector.locking import CollectorLock
from collector.publish_config import load_publishing, unset_local_options
from publisher import push
import runtime

ROOT = Path(__file__).resolve().parents[1]
BASE = 'version=1\nenabled=false\nmodel="opus"\npush=true\nmax_new=5\n'


class RuntimeFollowupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = self.root / 'data'

    def git(self, *args):
        return subprocess.run(['git', '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=', '-C', str(self.root), *args],
                              capture_output=True, text=True, encoding='utf-8', errors='replace', check=True).stdout

    def setup_repo(self):
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', '시험')
        self.git('config', 'user.email', 'test@example.invalid')
        config = self.root / 'pipeline/config/publishing.toml'
        config.parent.mkdir(parents=True)
        config.write_text(BASE, encoding='utf-8')
        (self.root / '.gitignore').write_text('pipeline/data/\npipeline/config/publishing.local.toml\n', encoding='utf-8')
        self.git('add', '.')
        self.git('commit', '-m', '초기 설정')
        return config

    def test_local_enabled_settings_survive_upstream_config_change(self):
        config = self.setup_repo()
        self.git('checkout', '-b', 'incoming')
        config.write_text(BASE + 'images=true\n', encoding='utf-8')
        self.git('commit', '-am', '포스터 설정 변경')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        self.git('checkout', 'main')
        original = BASE.replace('enabled=false', 'enabled=true').encode()
        config.write_bytes(original)
        real = push._git
        def offline_git(repo, *args, **kwargs):
            if args[0] == 'fetch': return subprocess.CompletedProcess(args, 0, '', '')
            return real(repo, *args, **kwargs)
        with patch.object(push, '_git', side_effect=offline_git):
            push.prepare(self.root)
        self.assertIn('images=true', config.read_text())
        self.assertEqual(config.with_suffix('.local.toml').read_bytes(), original)
        self.assertTrue(load_publishing(config).enabled)
        self.assertFalse(load_publishing(config).images)
        self.assertEqual(unset_local_options(config), ['images'], '이관본에 없는 새 칸은 알린다')
        self.assertEqual(self.git('status', '--porcelain').strip(), '')
        backups = list((self.root / 'pipeline/data/config_backups').glob('*.toml'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)

    def test_invalid_or_staged_settings_are_not_discarded(self):
        config = self.setup_repo()
        for text, staged in [('invalid', False), (BASE.replace('enabled=false', 'enabled=true'), True)]:
            config.write_text(text, encoding='utf-8')
            if staged: self.git('add', 'pipeline/config/publishing.toml')
            with self.assertRaises(push.PushError): migrate_publishing(self.root)
            self.assertEqual(config.read_text(), text)
            self.assertFalse(config.with_suffix('.local.toml').exists())

    def test_default_and_local_precedence_are_validated(self):
        config = self.root / 'publishing.toml'; config.write_text(BASE)
        self.assertFalse(load_publishing(config).enabled)
        local = config.with_suffix('.local.toml'); local.write_text(BASE.replace('enabled=false', 'enabled=true'))
        self.assertTrue(load_publishing(config).enabled)
        self.assertFalse(load_publishing(config, use_local=False).enabled)
        local.write_text('broken')
        with self.assertRaises(ValueError): load_publishing(config)
        self.assertFalse(load_publishing(config, use_local=False).enabled)

    def test_unset_local_options_only_counts_missing_keys(self):
        config = self.root / 'publishing.toml'; config.write_text(BASE + 'images=true\n')
        self.assertEqual(unset_local_options(config), [], '로컬 설정이 없으면 추적 설정 그대로')
        local = config.with_suffix('.local.toml')
        local.write_text(BASE)
        self.assertEqual(unset_local_options(config), ['images'])
        local.write_text(BASE + 'images=false\n')
        self.assertEqual(unset_local_options(config), [], '적어 둔 false는 운영자의 선택')
        config.write_text(BASE + 'images=true\ncross_model="sonnet"\n')
        local.write_text(BASE)
        self.assertEqual(unset_local_options(config), ['cross_model', 'images'])
        config.write_text(BASE + 'images=false\n')
        self.assertEqual(unset_local_options(config), [], '추적 값이 끔이면 로컬에 없어도 차이가 없다')
        config.write_text(BASE)
        local.write_text(BASE)
        self.assertEqual(unset_local_options(config), [])
        config.write_text('broken')
        self.assertEqual(unset_local_options(config), [], '로컬로 도는 실행을 알림 때문에 멈추지 않는다')
        self.assertTrue(load_publishing(config).max_new)

    def test_valid_local_does_not_hide_invalid_tracked_settings_during_migration(self):
        config = self.setup_repo()
        config.write_text('broken', encoding='utf-8')
        local = config.with_suffix('.local.toml'); local.write_text(BASE, encoding='utf-8')
        with self.assertRaises(push.PushError): migrate_publishing(self.root)
        self.assertEqual(config.read_text(), 'broken')
        self.assertEqual(local.read_text(), BASE)
        self.assertFalse((self.root / 'pipeline/data/config_backups').exists())

    def test_supervisor_pid_stays_in_parent_and_is_removed_from_worker_environment(self):
        with patch.dict(os.environ, {'MJU_RADAR_SUPERVISOR_PID': str(os.getpid())}), \
                patch.object(runtime, 'guard_process_tree'), patch.object(runtime, 'prepare'), \
                patch.object(runtime.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as worker:
            self.assertEqual(runtime.launch('publisher', [], self.data), 0)
            self.assertNotIn('MJU_RADAR_SUPERVISOR_PID', worker.call_args.kwargs['env'])
            self.assertEqual(os.environ['MJU_RADAR_SUPERVISOR_PID'], str(os.getpid()))

    def test_same_day_result_is_preserved_without_git_network_or_worker(self):
        self.data.mkdir()
        for status, code in [('success', 0), ('failed', 1), ('partial_failure', 3), ('collecting', 1)]:
            state = {'local_date': daily.current_time().date().isoformat(), 'collection_attempted': True, 'status': status}
            if status != 'collecting': state['exit_code'] = code
            path = self.data / 'daily_status.json'; path.write_text(json.dumps(state))
            original = path.read_bytes()
            with self.subTest(status=status), patch.object(runtime, 'prepare', side_effect=push.PushError('offline')) as prepare, \
                    patch.object(runtime.subprocess, 'run') as worker:
                self.assertEqual(runtime.launch('daily', [], self.data), code)
                prepare.assert_not_called(); worker.assert_not_called()
            self.assertEqual(path.read_bytes(), original)

    def test_busy_daily_writes_skip_record(self):
        with CollectorLock(self.data), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(daily.cli_main(['--data-dir', str(self.data), '--publish-config', 'unused.toml']), 0)
        log = self.data / 'logs' / (daily.current_time().date().isoformat() + '.jsonl')
        self.assertEqual(json.loads(log.read_text())['reason'], 'another_operation_running')


@unittest.skipUnless(os.name == 'nt', 'Windows Job Object 검증')
class ProcessTreeTests(unittest.TestCase):
    def test_supervisor_must_be_a_real_ancestor(self):
        from collector.process_tree import is_ancestor_process
        self.assertTrue(is_ancestor_process(os.getppid()))
        self.assertFalse(is_ancestor_process(os.getpid()))
        self.assertFalse(is_ancestor_process(0))

    def test_unrelated_live_supervisor_is_rejected(self):
        other = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            env = {**os.environ, 'MJU_RADAR_SUPERVISOR_PID': str(other.pid)}
            code = '''from collector.process_tree import guard_process_tree
try: guard_process_tree()
except OSError: raise SystemExit(7)
raise SystemExit(99)
'''
            self.assertEqual(subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env).returncode, 7)
        finally:
            other.kill(); other.wait()

    def test_real_supervisor_handle_is_accepted(self):
        env = {**os.environ, 'MJU_RADAR_SUPERVISOR_PID': str(os.getpid())}
        code = 'from collector.process_tree import guard_process_tree; guard_process_tree(); raise SystemExit(3)'
        self.assertEqual(subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env).returncode, 3)

    def alive(self, pid):
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]; k.OpenProcess.restype = ctypes.c_void_p
        k.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = k.OpenProcess(0x1000, 0, pid)
        if not handle: return False
        code = ctypes.c_ulong(); ok = k.GetExitCodeProcess(handle, ctypes.byref(code)); k.CloseHandle(handle)
        return bool(ok and code.value == 259)

    def test_killing_python_parent_also_stops_descendants(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / 'children.json'
            code = '''import json,subprocess,sys,time
from pathlib import Path
from collector.process_tree import guard_process_tree
guard_process_tree()
child = subprocess.Popen([sys.executable, '-c', "import time; time.sleep(60)"])
Path(sys.argv[1]).write_text(json.dumps([child.pid]))
time.sleep(60)
'''
            process = subprocess.Popen([sys.executable, '-c', code, str(marker)], cwd=ROOT)
            try:
                deadline = time.monotonic() + 10
                while not marker.exists() and process.poll() is None and time.monotonic() < deadline: time.sleep(.05)
                self.assertTrue(marker.exists())
                pids = json.loads(marker.read_text())
                self.assertTrue(all(self.alive(pid) for pid in pids))
                process.kill(); process.wait(timeout=10)
                deadline = time.monotonic() + 5
                while any(self.alive(pid) for pid in pids) and time.monotonic() < deadline: time.sleep(.05)
                self.assertFalse(any(self.alive(pid) for pid in pids))
            finally:
                if process.poll() is None: process.kill(); process.wait()

    def test_guard_keeps_normal_exit_code(self):
        code = 'from collector.process_tree import guard_process_tree; guard_process_tree(); raise SystemExit(3)'
        self.assertEqual(subprocess.run([sys.executable, '-c', code], cwd=ROOT).returncode, 3)
