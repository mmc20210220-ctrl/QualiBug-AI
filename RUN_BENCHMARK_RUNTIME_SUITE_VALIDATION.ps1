param(
  [int]$MaxProbesPerProject = 40
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$SuiteRoot = "D:\QualiBug-AI\benchmark_suite_v3\QualiBug_Benchmark_Suite_v3"
$Port = 8011
$BaseUrl = "http://127.0.0.1:$Port"
$ProbeConfig = Join-Path $Root "local_test_projects\benchmark_runtime_suite_v3\probe_config.json"
$SuiteOut = Join-Path $Root "platform_outputs\benchmark_runtime_suite_v3_full"
$RunId = Get-Date -Format "yyyyMMdd_HHmmss"
$RunOut = Join-Path $SuiteOut ("runs\" + $RunId)
$ManifestPath = Join-Path $RunOut "suite_runtime_run_manifest.json"

New-Item -ItemType Directory -Force -Path $SuiteOut | Out-Null
New-Item -ItemType Directory -Force -Path $RunOut | Out-Null

$PlanDirs = @(Get-ChildItem (Join-Path $Root "platform_outputs") -Directory |
  Where-Object { $_.Name -like "qb_v3_latest_*" } |
  Sort-Object Name)

if ($PlanDirs.Count -lt 15) {
  python -m aitestops.cli bug-engine-benchmark-blind --suite-root $SuiteRoot --root $Root --project-prefix qb_v3_latest
  $PlanDirs = @(Get-ChildItem (Join-Path $Root "platform_outputs") -Directory |
    Where-Object { $_.Name -like "qb_v3_latest_*" } |
    Sort-Object Name)
}

$Projects = @(Get-ChildItem (Join-Path $SuiteRoot "projects") -Directory | Sort-Object Name)
if ($Projects.Count -eq 0) {
  throw "No benchmark projects found under $SuiteRoot"
}

function Write-RunManifest([string]$Status, [array]$ProjectStatuses, [string]$ErrorMessage = "") {
  $payload = [ordered]@{
    mode = "benchmark_runtime_suite_validation"
    run_id = $RunId
    status = $Status
    started_at = $script:RunStartedAt
    completed_at = (Get-Date).ToUniversalTime().ToString("o")
    max_probes_per_project = $MaxProbesPerProject
    suite_root = $SuiteRoot
    base_url = $BaseUrl
    error = $ErrorMessage
    projects = $ProjectStatuses
  }
  $json = $payload | ConvertTo-Json -Depth 8
  Set-Content -Path $ManifestPath -Value $json -Encoding UTF8
  Set-Content -Path (Join-Path $SuiteOut "latest_run.json") -Value $json -Encoding UTF8
}

function Stop-BenchmarkServer() {
  $existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($existing) {
    Stop-Process -Id $existing.OwningProcess -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
  }
}

function Start-BenchmarkServer() {
  Stop-BenchmarkServer
  $env:QUALIBUG_BENCHMARK_SUITE_ROOT = $SuiteRoot
  $script:server = Start-Process -FilePath python -ArgumentList @("-m", "uvicorn", "benchmark_runtime.runtime_target:app", "--host", "127.0.0.1", "--port", "$Port") -WorkingDirectory $Root -PassThru -WindowStyle Hidden
  Start-Sleep -Seconds 2
}

function Ensure-BenchmarkHealth() {
  for ($attempt = 1; $attempt -le 2; $attempt++) {
    try {
      return Invoke-RestMethod "$BaseUrl/__health" -TimeoutSec 5
    }
    catch {
      if ($attempt -eq 2) {
        throw "Benchmark runtime target is not healthy after restart: $($_.Exception.Message)"
      }
      Start-BenchmarkServer
    }
  }
}

$script:RunStartedAt = (Get-Date).ToUniversalTime().ToString("o")
$ProjectStatuses = @()
foreach ($project in $Projects) {
  $ProjectStatuses += [ordered]@{
    project = $project.Name
    status = "pending"
    out_dir = (Join-Path $RunOut $project.Name)
    log_path = (Join-Path (Join-Path $RunOut $project.Name) "executor_stdout.txt")
    error = ""
  }
}
Write-RunManifest -Status "running" -ProjectStatuses $ProjectStatuses

try {
  Start-BenchmarkServer
  $health = Ensure-BenchmarkHealth
  Write-Host ("BENCHMARK_RUNTIME_SUITE_HEALTH loaded_runtime_bug_surfaces={0}" -f $health.loaded_runtime_bug_surfaces)

  $env:QUALIBUG_ALLOW_GROUNDED_WRITE_PROBES = "1"
  $HadFailures = $false

  for ($i = 0; $i -lt $Projects.Count; $i++) {
    $project = $Projects[$i]
    $planDir = $PlanDirs[$i]
    $outDir = Join-Path $RunOut $project.Name
    $logPath = Join-Path $outDir "executor_stdout.txt"
    if (Test-Path $outDir) {
      Remove-Item -Recurse -Force $outDir
    }
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    $ProjectStatuses[$i].out_dir = $outDir
    $ProjectStatuses[$i].log_path = $logPath
    $ProjectStatuses[$i].status = "running"
    $ProjectStatuses[$i].error = ""
    Write-RunManifest -Status "running" -ProjectStatuses $ProjectStatuses

    if (!$planDir) {
      $ProjectStatuses[$i].status = "failed"
      $ProjectStatuses[$i].error = "Missing generated probe plan directory for project index $($i + 1)"
      $HadFailures = $true
      Write-RunManifest -Status "partial_failed" -ProjectStatuses $ProjectStatuses
      continue
    }
    $probePlan = Join-Path $planDir.FullName "input_only_run\grounded_probe_plan.json"
    if (!(Test-Path $probePlan)) {
      $ProjectStatuses[$i].status = "failed"
      $ProjectStatuses[$i].error = "Missing probe plan: $probePlan"
      $HadFailures = $true
      Write-RunManifest -Status "partial_failed" -ProjectStatuses $ProjectStatuses
      continue
    }
    $inputDir = Join-Path $project.FullName "input"

    $null = Ensure-BenchmarkHealth
    Write-Host ("RUN_PROJECT index={0} project={1} max_probes={2}" -f ($i + 1), $project.Name, $MaxProbesPerProject)
    & python -m aitestops.cli bug-engine-grounded-execute `
      --probe-plan $probePlan `
      --out-dir $outDir `
      --base-url $BaseUrl `
      --execute-readonly `
      --allow-write-sandbox `
      --approval-id benchmark-runtime-suite-v3 `
      --probe-config $ProbeConfig `
      --input-dir $inputDir `
      --max-probes $MaxProbesPerProject `
      --timeout-seconds 10 *> $logPath

    if ($LASTEXITCODE -ne 0) {
      $ProjectStatuses[$i].status = "failed"
      $ProjectStatuses[$i].error = "Runtime validation failed for $($project.Name). See $logPath"
      $HadFailures = $true
    }
    elseif (Test-Path (Join-Path $outDir "grounded_probe_execution_report.json")) {
      $ProjectStatuses[$i].status = "completed"
      $ProjectStatuses[$i].error = ""
    }
    else {
      $ProjectStatuses[$i].status = "failed"
      $ProjectStatuses[$i].error = "Execution finished without grounded_probe_execution_report.json"
      $HadFailures = $true
    }
    Write-RunManifest -Status $(if ($HadFailures) { "partial_failed" } else { "running" }) -ProjectStatuses $ProjectStatuses
  }

  $FinalStatus = if ($HadFailures) { "partial_failed" } else { "completed" }
  Write-RunManifest -Status $FinalStatus -ProjectStatuses $ProjectStatuses
  python -m benchmark_runtime.suite_summary --suite-out $RunOut --manifest $ManifestPath --max-probes-per-project $MaxProbesPerProject
  Copy-Item -Force (Join-Path $RunOut "suite_runtime_validation_summary.json") (Join-Path $SuiteOut "suite_runtime_validation_summary.json")
  Copy-Item -Force (Join-Path $RunOut "suite_runtime_validation_summary.md") (Join-Path $SuiteOut "suite_runtime_validation_summary.md")
  Copy-Item -Force $ManifestPath (Join-Path $SuiteOut "suite_runtime_run_manifest.json")
  if ($HadFailures) {
    throw "Benchmark runtime suite validation completed with failed projects. See $ManifestPath"
  }
}
finally {
  if ($server -and !$server.HasExited) {
    Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
  }
}
