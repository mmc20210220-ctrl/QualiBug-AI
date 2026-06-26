[CmdletBinding()]
param(
    [switch]$NoRestartTarget,
    [int]$Port = 8000,
    [int]$MaxProbes = 80
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Project = "local_backend_runtime"
$AfterFixProject = "local_backend_runtime_after_fix"
$BaseUrl = "http://127.0.0.1:$Port"
$RuntimeDir = Join-Path $Root "platform_outputs\runtime"
$PidPath = Join-Path $RuntimeDir "backend_main_$Port.pid"
$InputDir = Join-Path $Root "local_test_projects\qualibug_backend\input"
$FixConfig = Join-Path $Root "local_test_projects\qualibug_backend\fix_verification_config.json"
$BeforeFixReport = Join-Path $Root "platform_outputs\local_backend_runtime\input_only_run\grounded_probe_execution_report.before_fix.json"
$ProbePlan = Join-Path $Root "platform_outputs\local_backend_runtime\input_only_run\grounded_probe_plan.json"
$RerunOut = Join-Path $Root "platform_outputs\local_backend_runtime_fix_rerun"

function Invoke-JsonHealth {
    param([string]$Url)
    try {
        return Invoke-RestMethod -Uri "$Url/health" -TimeoutSec 3
    } catch {
        return $null
    }
}

function Stop-TargetFromPidFile {
    if (Test-Path $PidPath) {
        try {
            $ExistingPid = [int](Get-Content $PidPath -Raw)
            Stop-Process -Id $ExistingPid -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 1
        } catch {}
    }
}

function Start-Target {
    New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
    $OutLog = Join-Path $RuntimeDir "backend_main_$Port.out.log"
    $ErrLog = Join-Path $RuntimeDir "backend_main_$Port.err.log"
    $Proc = Start-Process -FilePath python `
        -ArgumentList @("-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "$Port") `
        -WorkingDirectory $Root `
        -PassThru `
        -RedirectStandardOutput $OutLog `
        -RedirectStandardError $ErrLog `
        -WindowStyle Hidden
    Set-Content -Path $PidPath -Value $Proc.Id
    $Deadline = (Get-Date).AddSeconds(20)
    do {
        Start-Sleep -Milliseconds 500
        $Health = Invoke-JsonHealth $BaseUrl
        if ($Health -and $Health.status -eq "ok") {
            Write-Host "Target healthy: $BaseUrl pid=$($Proc.Id)" -ForegroundColor Green
            return
        }
    } while ((Get-Date) -lt $Deadline)
    throw "Local backend did not become healthy. Inspect $ErrLog"
}

if (-not (Test-Path $InputDir)) {
    throw "Local validation input missing: $InputDir"
}

python -c "import fastapi, uvicorn, backend.main; print('IMPORT_OK')"

if (-not $NoRestartTarget) {
    Stop-TargetFromPidFile
    Start-Target
} elseif (-not (Invoke-JsonHealth $BaseUrl)) {
    Start-Target
}

Write-Host "Running QualiBug read-only runtime validation..." -ForegroundColor Cyan
python -m aitestops.cli bug-engine-input-only `
    --input-dir $InputDir `
    --project $AfterFixProject `
    --root $Root `
    --base-url $BaseUrl `
    --execute-readonly `
    --max-probes $MaxProbes

$RegressionPytest = Join-Path $Root "platform_outputs\$AfterFixProject\input_only_run\grounded_probe_regression_pytest.py"
if (Test-Path $RegressionPytest) {
    Write-Host "Running generated regression pytest..." -ForegroundColor Cyan
    python -m pytest $RegressionPytest -q
}

if ((Test-Path $BeforeFixReport) -and (Test-Path $ProbePlan) -and (Test-Path $FixConfig)) {
    Write-Host "Running fix-verification rerun against before-fix evidence..." -ForegroundColor Cyan
    python -m aitestops.cli bug-engine-grounded-execute `
        --probe-plan $ProbePlan `
        --out-dir $RerunOut `
        --base-url $BaseUrl `
        --execute-readonly `
        --probe-config $FixConfig `
        --max-probes $MaxProbes
}

Write-Host "Local backend runtime validation completed." -ForegroundColor Green
Write-Host "Current report: platform_outputs\$AfterFixProject\input_only_run\grounded_probe_execution_report.json"
Write-Host "Fix rerun report: platform_outputs\local_backend_runtime_fix_rerun\grounded_probe_execution_report.json"

