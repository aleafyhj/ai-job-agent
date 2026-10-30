param([string]$MousePython = 'D:\app\Anaconda3\python.exe')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath $MousePython)) {
    throw 'Mouse Python not found. Check the interpreter path in scripts/start-mouse.ps1.'
}
$scriptPath = Join-Path $PSScriptRoot 'windows_mouse_bridge.py'
& $MousePython $scriptPath --status
if ($LASTEXITCODE -eq 0) { return }
$stateDir = Join-Path $projectRoot 'artifacts\boss-desktop'
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
$process = Start-Process -FilePath $MousePython -ArgumentList @(('"' + $scriptPath + '"'), '--serve') -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardError (Join-Path $stateDir 'mouse-service-error.log')
for ($attempt = 0; $attempt -lt 10; $attempt++) {
    Start-Sleep -Milliseconds 300
    & $MousePython $scriptPath --status
    if ($LASTEXITCODE -eq 0) { return }
    if ($process.HasExited) { break }
}
throw 'Mouse worker failed to start. See artifacts/boss-desktop/mouse-service-error.log.'
