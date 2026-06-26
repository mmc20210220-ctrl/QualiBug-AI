[CmdletBinding()]
param([switch]$Force)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$Project = "real_project_demo"
$OutDir = Join-Path $Root ("platform_outputs\" + $Project)
$TargetData = Join-Path $Root "mes_target\mes-buglab-target\data"
$Seed = Join-Path $TargetData "mes_buglab.seed.db"
$Db = Join-Path $TargetData "mes_buglab.db"

if (-not $Force) {
    throw "This resets ONLY the bundled local MES BugLab test database. Re-run with .\RESET_MES_BUGLAB.ps1 -Force."
}
if (-not (Test-Path $Seed)) { throw "Seed database is missing: $Seed" }

$workerPidPath = Join-Path $OutDir ".discovery_pid.json"
if (Test-Path $workerPidPath) {
    try {
        $pid = (Get-Content $workerPidPath -Raw | ConvertFrom-Json).pid
        if ($pid -and (Get-Process -Id $pid -ErrorAction SilentlyContinue)) {
            throw "A discovery worker is active (pid=$pid). Stop it with .\STOP_LOCAL_QUALIBUG.ps1 before resetting the MES test database."
        }
    } catch { throw }
}

$launcherPath = Join-Path $OutDir "local_launcher_pids.json"
if (Test-Path $launcherPath) {
    try {
        $targetPid = (Get-Content $launcherPath -Raw | ConvertFrom-Json).target_pid
        if ($targetPid -and (Get-Process -Id $targetPid -ErrorAction SilentlyContinue)) {
            Stop-Process -Id $targetPid -Force
            Start-Sleep -Seconds 1
        }
    } catch {}
}

Copy-Item -Path $Seed -Destination $Db -Force
Write-Host "Bundled MES BugLab test database reset completed." -ForegroundColor Green
