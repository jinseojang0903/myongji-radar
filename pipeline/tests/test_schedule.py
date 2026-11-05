import base64
import json
import os
import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts/register_daily_collect.ps1"


@unittest.skipUnless(os.name == "nt" and (ROOT / "pipeline/.venv/Scripts/python.exe").exists(),
                     "Windows 작업 스케줄러와 로컬 가상환경에서 확인")
class ScheduleTests(unittest.TestCase):
    def powershell(self, code):
        prelude = "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
        encoded = base64.b64encode((prelude + code).encode("utf-16-le")).decode("ascii")
        return subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                              capture_output=True, text=True, encoding="utf-8", timeout=30, cwd=ROOT)

    def test_dry_registration_builds_daily_hidden_interactive_task(self):
        # 실제 등록 함수를 막아 두고 -WhatIf로 정의만 만든다.
        code = f"""
if ((Get-TimeZone).Id -ne 'Korea Standard Time') {{ exit 77 }}
function Register-ScheduledTask {{ throw 'Registration must not run in this test' }}
. '{INSTALLER.as_posix()}' -WhatIf
@{{ start=$task.Triggers[0].StartBoundary; days=$task.Triggers[0].DaysInterval;
    catchup=$task.Settings.StartWhenAvailable; instances=[string]$task.Settings.MultipleInstances;
    limit=$task.Settings.ExecutionTimeLimit; logon=[string]$task.Principal.LogonType;
    level=[string]$task.Principal.RunLevel; arguments=$task.Actions[0].Arguments }} | ConvertTo-Json -Compress
"""
        result = self.powershell(code)
        if result.returncode == 77:
            self.skipTest("한국시간 작업 정의 검증은 Korea Standard Time 환경에서 실행")
        self.assertEqual(result.returncode, 0, result.stderr)
        task = json.loads(result.stdout.strip().splitlines()[-1])
        trigger = datetime.fromisoformat(task["start"].replace("Z", "+00:00"))
        self.assertEqual(trigger.astimezone(timezone(timedelta(hours=9))).hour, 21)
        self.assertEqual(task["days"], 1)
        self.assertTrue(task["catchup"])
        self.assertEqual(task["instances"], "IgnoreNew")
        self.assertEqual(task["limit"], "PT1H")
        self.assertEqual(task["logon"], "Interactive")
        self.assertEqual(task["level"], "Limited")
        self.assertIn('-WindowStyle Hidden', task["arguments"])
        self.assertIn('-File "', task["arguments"])

    def test_removal_refuses_an_unrelated_task(self):
        code = f"""
function Get-ScheduledTask {{ param($TaskPath,$ErrorAction)
    [pscustomobject]@{{ TaskName='Test-Daily'; Actions=@([pscustomobject]@{{Execute='other.exe';Arguments=''}});
        Principal=[pscustomobject]@{{UserId='other'}} }} }}
function Unregister-ScheduledTask {{ throw 'Deletion must not run in this test' }}
try {{ . '{INSTALLER.as_posix()}' -TaskName Test-Daily -Remove -WhatIf }}
catch {{ if ($_.Exception.Message -like '*같은 이름의 다른 작업*') {{ exit 24 }}; throw }}
"""
        result = self.powershell(code)
        self.assertEqual(result.returncode, 24, result.stderr)

    def test_same_user_short_full_and_sid_allow_reregistration_and_removal(self):
        for form in ("short", "full", "sid"):
            for remove in (False, True):
                with self.subTest(form=form, remove=remove):
                    code = rf"""
if ((Get-TimeZone).Id -ne 'Korea Standard Time') {{ exit 77 }}
$who=[System.Security.Principal.WindowsIdentity]::GetCurrent()
$ids=@{{short=$who.Name.Split('\')[-1];full=$who.Name;sid=$who.User.Value}}
$fixtureUser=$ids['{form}']
$fixtureExe=Join-Path $PSHOME 'powershell.exe'
$fixtureArgs='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{(ROOT / 'scripts/run_daily_collect.ps1').as_posix().replace('/', chr(92))}"'
function Get-ScheduledTask {{ param($TaskPath,$ErrorAction)
    [pscustomobject]@{{TaskName='Test-Daily';Actions=@([pscustomobject]@{{Execute=$fixtureExe;Arguments=$fixtureArgs}});
        Principal=[pscustomobject]@{{UserId=$fixtureUser}} }} }}
function Register-ScheduledTask {{ throw 'No actual registration in tests' }}
function Unregister-ScheduledTask {{ throw 'No actual removal in tests' }}
. '{INSTALLER.as_posix()}' -TaskName Test-Daily -WhatIf {'-Remove' if remove else ''}
"""
                    result = self.powershell(code)
                    if result.returncode == 77:
                        self.skipTest("KST 환경에서 현재 사용자 표현별 예약 정의 검증")
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_same_action_but_different_or_unresolved_user_is_refused(self):
        for user in ("S-1-5-18", "invalid\\no-such-user"):
            with self.subTest(user=user):
                code = f"""
$fixtureExe=Join-Path $PSHOME 'powershell.exe'
$fixtureArgs='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{(ROOT / 'scripts/run_daily_collect.ps1').as_posix().replace('/', chr(92))}"'
function Get-ScheduledTask {{ param($TaskPath,$ErrorAction)
    [pscustomobject]@{{TaskName='Test-Daily';Actions=@([pscustomobject]@{{Execute=$fixtureExe;Arguments=$fixtureArgs}});
        Principal=[pscustomobject]@{{UserId='{user}'}} }} }}
function Unregister-ScheduledTask {{ throw 'No removal of other tasks' }}
try {{ . '{INSTALLER.as_posix()}' -TaskName Test-Daily -Remove -WhatIf }}
catch {{ if ($_.Exception.Message -like '*같은 이름의 다른 작업*') {{ exit 24 }}; throw }}
"""
                result = self.powershell(code)
                self.assertEqual(result.returncode, 24, result.stderr)
