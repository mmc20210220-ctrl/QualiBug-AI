[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$Project = if ($env:QUALIBUG_PROJECT) { $env:QUALIBUG_PROJECT } else { "real_project_demo" }
$OutDir = Join-Path $Root ("platform_outputs\" + $Project)
$PidFile = Join-Path $OutDir "local_launcher_pids.json"

$ids = New-Object System.Collections.Generic.List[int]
if (Test-Path $PidFile) {
    try {
        $state = Get-Content $PidFile -Raw | ConvertFrom-Json
        foreach ($name in @("supervisor_pid", "target_pid")) {
            if ($state.$name) { $ids.Add([int]$state.$name) }
        }
    } catch { Write-Warning "Could not parse local launcher pid file: $($_.Exception.Message)" }
}

$workerFile = Join-Path $OutDir ".discovery_pid.json"
if (Test-Path $workerFile) {
    try {
        $worker = Get-Content $workerFile -Raw | ConvertFrom-Json
        if ($worker.pid) { $ids.Add([int]$worker.pid) }
    } catch { Write-Warning "Could not parse worker pid file: $($_.Exception.Message)" }
}

$ids = $ids | Select-Object -Unique
foreach ($id in $ids) {
    $process = Get-Process -Id $id -ErrorAction SilentlyContinue
    if ($process) {
        Write-Host "Stopping pid=$id ($($process.ProcessName))..."
        Stop-Process -Id $id -Force
    }
}

Remove-Item $PidFile -ErrorAction SilentlyContinue
Write-Host "Local QualiBug processes requested to stop. Runtime logs and durable result files were retained." -ForegroundColor Green
