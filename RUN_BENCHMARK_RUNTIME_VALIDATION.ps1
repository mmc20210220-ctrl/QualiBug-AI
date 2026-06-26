$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$SuiteRoot = "D:\QualiBug-AI\benchmark_suite_v3\QualiBug_Benchmark_Suite_v3"
$Port = 8011
$BaseUrl = "http://127.0.0.1:$Port"
$ProjectOutput = Join-Path $Root "platform_outputs\qb_v3_latest_03_03_mes_work_order_quality_trace\input_only_run"
$ProbePlan = Join-Path $ProjectOutput "grounded_probe_plan.json"
$InputDir = Join-Path $SuiteRoot "projects\03_mes_work_order_quality_trace\input"
$ProbeConfig = Join-Path $Root "local_test_projects\benchmark_runtime_suite_v3\probe_config.json"
$OutDir = Join-Path $Root "platform_outputs\benchmark_runtime_suite_v3_mes"

if (!(Test-Path $ProbePlan)) {
  python -m aitestops.cli bug-engine-benchmark-blind --suite-root $SuiteRoot --root $Root --project-prefix qb_v3_latest
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
  Write-Host ("BENCHMARK_RUNTIME_HEALTH loaded_runtime_bug_surfaces={0}" -f $health.loaded_runtime_bug_surfaces)

  $env:QUALIBUG_ALLOW_GROUNDED_WRITE_PROBES = "1"
  python -m aitestops.cli bug-engine-grounded-execute `
    --probe-plan $ProbePlan `
    --out-dir $OutDir `
    --base-url $BaseUrl `
    --execute-readonly `
    --allow-write-sandbox `
    --approval-id benchmark-runtime-suite-v3 `
    --probe-config $ProbeConfig `
    --input-dir $InputDir `
    --max-probes 120 `
    --timeout-seconds 10

  $ReportPath = Join-Path $OutDir "grounded_probe_execution_report.json"
  python -c "import json, pathlib; p=pathlib.Path(r'$ReportPath'); d=json.loads(p.read_text(encoding='utf-8')); s=d['summary']; print('RUNTIME_VALIDATION_SUMMARY validated={0} protected={1} readonly={2} write={3} blocked={4} needs_more={5}'.format(s.get('validated_candidate_count'), s.get('protected_count'), s.get('executed_readonly_count'), s.get('executed_write_sandbox_count'), s.get('blocked_count'), s.get('needs_more_evidence_count')))"
}
finally {
  if ($server -and !$server.HasExited) {
    Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
  }
}
