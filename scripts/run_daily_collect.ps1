[CmdletBinding()]
param([switch]$ValidateOnly)

$ErrorActionPreference = 'Stop'
$pipelineRoot = Join-Path (Split-Path -Parent $PSScriptRoot) 'pipeline'
$pythonPath = Join-Path $pipelineRoot '.venv\Scripts\python.exe'
$publishConfigPath = Join-Path $pipelineRoot 'config\publishing.toml'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'pipeline 폴더에서 가상환경을 만들고 requirements.lock.txt를 먼저 설치해주세요.'
}
if (-not $ValidateOnly) {
    $branchName = & git -C (Split-Path -Parent $pipelineRoot) branch --show-current
    if ($LASTEXITCODE -ne 0 -or $branchName -ne 'main') {
        throw '일일 운영은 main 브랜치의 전용 저장소에서 실행해주세요. 개발 브랜치는 -ValidateOnly로 확인합니다.'
    }
}
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:NoDefaultCurrentDirectoryInExePath = '1'
Push-Location -LiteralPath $pipelineRoot
try {
    $dailyArguments = @('-m', 'collector.daily', '--publish-config', $publishConfigPath)
    if ($ValidateOnly) { $dailyArguments += '--dry-run' }
    & $pythonPath @dailyArguments
    $resultCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $resultCode
