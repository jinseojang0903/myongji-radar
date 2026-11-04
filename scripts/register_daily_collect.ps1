[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidatePattern('^([01][0-9]|2[0-3]):[0-5][0-9]$')]
    [string]$At = '21:00',
    [ValidatePattern('^[A-Za-z0-9_.-]+$')]
    [string]$TaskName = "MyongjiRadar-DailyCollect-$env:USERNAME",
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $PSScriptRoot 'run_daily_collect.ps1'
$powershellPath = Join-Path $PSHOME 'powershell.exe'
$actionArguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $runner
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$existing = Get-ScheduledTask -TaskPath '\' -ErrorAction Stop |
    Where-Object { $_.TaskName -eq $TaskName }
if ($existing) {
    $owned = ($existing.Actions.Count -eq 1 -and
        $existing.Actions[0].Execute -eq $powershellPath -and
        $existing.Actions[0].Arguments -eq $actionArguments -and
        $existing.Principal.UserId -in @($identity.Name, $identity.User.Value))
    if (-not $owned) { throw '같은 이름의 다른 작업이 있습니다. 덮어쓰거나 해제하지 않습니다.' }
}
if ($Remove) {
    if (-not $existing) { Write-Output '등록된 일일 수집 작업이 없습니다.'; return }
    if ($PSCmdlet.ShouldProcess($TaskName, '명지레이더 일일 수집 예약 해제')) {
        Unregister-ScheduledTask -TaskName $TaskName -TaskPath '\' -Confirm:$false -ErrorAction Stop
        Write-Output '일일 수집 예약을 해제했습니다. 기존 데이터는 유지됩니다.'
    }
    return
}
if ((Get-TimeZone).Id -ne 'Korea Standard Time') {
    throw '한국시간 운영을 위해 이 PC의 시간대를 Korea Standard Time으로 확인해주세요. 스크립트가 시스템 시간대를 변경하지는 않습니다.'
}
$pythonPath = Join-Path $repositoryRoot 'pipeline\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw '먼저 pipeline의 가상환경과 의존성을 설치해주세요.' }
if (-not $WhatIfPreference) {
    $branchName = & git -C $repositoryRoot branch --show-current
    if ($LASTEXITCODE -ne 0 -or $branchName -ne 'main') {
        throw 'PR 병합 후 전용 운영 저장소의 main 브랜치에서 등록해주세요. 개발 중에는 -WhatIf로만 확인합니다.'
    }
}
$time = [DateTime]::ParseExact($At, 'HH:mm', [Globalization.CultureInfo]::InvariantCulture)
$trigger = New-ScheduledTaskTrigger -Daily -At ([DateTime]::Today.Add($time.TimeOfDay))
$action = New-ScheduledTaskAction -Execute $powershellPath -Argument $actionArguments -WorkingDirectory $repositoryRoot
$principal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description '명지레이더 공개 공지 수집과 규칙 분석. 로그인 상태 필요, 비공개 데이터는 pipeline/data에 보관.'
if ($PSCmdlet.ShouldProcess($TaskName, "매일 한국시간 $At 수집·분석 예약 등록")) {
    Register-ScheduledTask -TaskName $TaskName -TaskPath '\' -InputObject $task -Force -ErrorAction Stop | Out-Null
    Write-Output "매일 한국시간 $At 예약을 등록했습니다. PC 전원과 사용자 로그인 상태가 필요합니다."
}
