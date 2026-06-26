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

New-Item -ItemType Directory -Force -Path $SuiteOut | Out-Null

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

$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($existing) {
  Stop-Process -Id $existing.OwningProcess -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 1
}

$env:QUALIBUG_BENCHMARK_SUITE_ROOT = $SuiteRoot
$server = Start-Process -FilePath python -ArgumentList @("-m", "uvicorn", "benchmark_runtime.runtime_target:app", "--host", "127.0.0.1", "--port", "$Port") -WorkingDirectory $Root -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 2

try {
  $health = Invoke-RestMethod "$BaseUrl/__health"
  Write-Host ("BENCHMARK_RUNTIME_SUITE_HEALTH loaded_runtime_bug_surfaces={0}" -f $health.loaded_runtime_bug_surfaces)

  $env:QUALIBUG_ALLOW_GROUNDED_WRITE_PROBES = "1"

  for ($i = 0; $i -lt $Projects.Count; $i++) {
    $project = $Projects[$i]
    $planDir = $PlanDirs[$i]
    if (!$planDir) {
      throw "Missing generated probe plan directory for project index $($i + 1)"
    }
    $probePlan = Join-Path $planDir.FullName "input_only_run\grounded_probe_plan.json"
    if (!(Test-Path $probePlan)) {
      throw "Missing probe plan: $probePlan"
    }
    $inputDir = Join-Path $project.FullName "input"
    $outDir = Join-Path $SuiteOut $project.Name
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    $logPath = Join-Path $outDir "executor_stdout.txt"

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
      throw "Runtime validation failed for $($project.Name). See $logPath"
    }
  }

  python -m benchmark_runtime.suite_summary --suite-out $SuiteOut --max-probes-per-project $MaxProbesPerProject
}
finally {
  if ($server -and !$server.HasExited) {
    Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
  }
}
