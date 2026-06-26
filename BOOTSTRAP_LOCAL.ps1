[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Find-Python {
    foreach ($name in @("python.exe", "python")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    throw "Python 3.11+ was not found on PATH. Install Python or activate your Conda environment first."
}

$Python = Find-Python
& $Python --version
if ($LASTEXITCODE -ne 0) { throw "Python is not runnable." }

Write-Host "[1/3] Installing local runner dependencies..."
& $Python -m pip install -r .\requirements-local-run.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency install failed." }

Write-Host "[2/3] Installing QualiBug in editable local mode..."
& $Python -m pip install -e .
if ($LASTEXITCODE -ne 0) { throw "Editable install failed." }

Write-Host "[3/3] Checking target imports..."
& $Python -c "import fastapi, uvicorn; from ai_test_asset_center.discovery_engine import AutonomousDiscoveryEngine; print('BOOTSTRAP_OK')"
if ($LASTEXITCODE -ne 0) { throw "Import check failed." }

if (-not (Test-Path .\.env.local)) {
    Copy-Item .\.env.local.example .\.env.local
    Write-Host "Created .env.local from template. Add your local DeepSeek key and model, then run .\TEST_DEEPSEEK_CONNECTION.ps1." -ForegroundColor Yellow
} else {
    Write-Host ".env.local already exists; it was not changed." -ForegroundColor Green
}

Write-Host "Bootstrap complete." -ForegroundColor Green
