param(
    [string]$ProjectRoot = "d:\QualiBug-AI\QualiBug-AI-main",
    [int]$BackendPort = 8088,
    [int]$DebugPort = 7777,
    [int]$FrontendPort = 5174,
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"

function Stop-PortListener {
    param([int]$Port)
    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        try {
            Stop-Process -Id $listener.OwningProcess -Force -ErrorAction Stop
            Write-Output ("STOPPED port={0} pid={1}" -f $Port, $listener.OwningProcess)
        } catch {
            Write-Output ("STOP_FAILED port={0} pid={1} err={2}" -f $Port, $listener.OwningProcess, $_.Exception.Message)
        }
    }
}

function Wait-HttpOk {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 30
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -TimeoutSec 5
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                return $response
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    throw "Timed out waiting for $Url"
}

$root = (Resolve-Path $ProjectRoot).Path
$dbgDir = Join-Path $root ".dbg"
New-Item -ItemType Directory -Force -Path $dbgDir | Out-Null

Stop-PortListener -Port $DebugPort
Stop-PortListener -Port $BackendPort
if (-not $SkipFrontend) {
    Stop-PortListener -Port $FrontendPort
}

$debugOut = Join-Path $dbgDir "debug-server.out.log"
$debugErr = Join-Path $dbgDir "debug-server.err.log"
$serviceOut = Join-Path $dbgDir "private-service.out.log"
$serviceErr = Join-Path $dbgDir "private-service.err.log"
$frontendOut = Join-Path $dbgDir "frontend.out.log"
$frontendErr = Join-Path $dbgDir "frontend.err.log"
$summaryPath = Join-Path $dbgDir "acceptance-runtime-summary.json"

Remove-Item -Force -ErrorAction SilentlyContinue $debugOut, $debugErr, $serviceOut, $serviceErr, $frontendOut, $frontendErr, $summaryPath, (Join-Path $dbgDir "trae-debug-log-private-service-stability.ndjson"), (Join-Path $dbgDir "private-service-stability.env")

$debugProc = Start-Process -FilePath python -ArgumentList @(
    "c:\Users\Test\.trae\builtin_skills\TRAE-debugger\tools\debug-server\python\debug-server.py",
    "--session", "private-service-stability",
    "--outdir", ".dbg",
    "--clean",
    "--idle", "1200",
    "--port", "$DebugPort"
) -WorkingDirectory $root -RedirectStandardOutput $debugOut -RedirectStandardError $debugErr -PassThru

Wait-HttpOk -Url ("http://127.0.0.1:{0}/health" -f $DebugPort) | Out-Null

$env:QUALIBUG_JWT_SECRET = "dev-mode-only"
$env:QUALIBUG_DEBUG_REPORT = "1"
$env:QUALIBUG_DEBUG_SESSION_ID = "private-service-stability"
$env:QUALIBUG_DEBUG_SERVER_URL = ("http://127.0.0.1:{0}/event" -f $DebugPort)

$serviceProc = Start-Process -FilePath python -ArgumentList @(
    "-m", "ai_test_asset_center.private_pilot_entrypoint"
) -WorkingDirectory $root -RedirectStandardOutput $serviceOut -RedirectStandardError $serviceErr -PassThru

$health = Wait-HttpOk -Url ("http://127.0.0.1:{0}/api/health" -f $BackendPort)

$frontendProc = $null
if (-not $SkipFrontend) {
    $frontendRoot = Join-Path $root "frontend"
    $frontendProc = Start-Process -FilePath npm.cmd -ArgumentList @("run", "dev") -WorkingDirectory $frontendRoot -RedirectStandardOutput $frontendOut -RedirectStandardError $frontendErr -PassThru
    Wait-HttpOk -Url ("http://127.0.0.1:{0}" -f $FrontendPort) | Out-Null
}

$summary = [pscustomobject]@{
    project_root = $root
    debug_server_pid = $debugProc.Id
    private_service_pid = $serviceProc.Id
    frontend_pid = if ($frontendProc) { $frontendProc.Id } else { $null }
    debug_server_health = ("http://127.0.0.1:{0}/health" -f $DebugPort)
    backend_health = ("http://127.0.0.1:{0}/api/health" -f $BackendPort)
    dashboard_url = ("http://127.0.0.1:{0}/dashboard?project=benchmark_mall_v05_p0probe" -f $FrontendPort)
    evidence_url = ("http://127.0.0.1:{0}/evidence?project=benchmark_mall_v05_p0probe" -f $FrontendPort)
    backend_status = $health.StatusCode
    debug_log = (Join-Path $dbgDir "trae-debug-log-private-service-stability.ndjson")
    service_stdout = $serviceOut
    service_stderr = $serviceErr
    frontend_stdout = if ($frontendProc) { $frontendOut } else { $null }
    frontend_stderr = if ($frontendProc) { $frontendErr } else { $null }
}

$summary | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 $summaryPath
$summary | ConvertTo-Json -Depth 4
