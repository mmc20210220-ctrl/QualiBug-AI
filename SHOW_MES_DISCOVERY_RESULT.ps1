[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$Project = "real_project_demo"
$OutDir = Join-Path $Root ("platform_outputs\" + $Project)

function Read-JsonOrNull([string]$Path) {
    if (-not (Test-Path $Path)) { return $null }
    try { return (Get-Content $Path -Raw | ConvertFrom-Json) } catch { return $null }
}

Write-Host "`n=== QualiBug MES Bug Discovery Summary ===" -ForegroundColor Cyan
$result = Read-JsonOrNull (Join-Path $OutDir ".discovery_result.json")
if ($result) {
    Write-Host ("Terminal: {0}" -f $result.terminal)
    Write-Host ("Rounds: {0} | validated candidates: {1} | inconclusive rate: {2}" -f $result.rounds, $result.total_bugs, $result.inconclusive_rate)
    if ($result.error) { Write-Host ("Error: {0}" -f $result.error) -ForegroundColor Yellow }
} else {
    Write-Host "No durable .discovery_result.json yet. The worker may still be running." -ForegroundColor Yellow
}

$issues = Read-JsonOrNull (Join-Path $OutDir "real_project\discovered_issues.json")
if ($issues -and $issues.items) {
    Write-Host "`nCandidate findings (not automatically confirmed):" -ForegroundColor Cyan
    foreach ($item in @($issues.items | Select-Object -First 20)) {
        Write-Host ("- [{0}] {1} | {2} | confidence={3} | {4}" -f $item.severity, $item.title, $item.status, $item.confidence, $item.issue_id)
    }
    Write-Host ("Total candidate items: {0}" -f $issues.items.Count)
} else {
    Write-Host "`nNo candidate finding projection was produced for this run." -ForegroundColor Yellow
}

$ledger = Read-JsonOrNull (Join-Path $OutDir "agent_discovery_loop\agent_discovery_loop_report.json")
if ($ledger -and $ledger.next_best_actions) {
    Write-Host "`nNext best actions:" -ForegroundColor Cyan
    foreach ($item in @($ledger.next_best_actions | Select-Object -First 5)) {
        Write-Host ("- #{0}: {1} | {2}" -f $item.rank, $item.title, $item.action)
    }
}

Write-Host "`nArtifacts:" -ForegroundColor Cyan
Write-Host "- $OutDir"
Write-Host "- $OutDir\cron_worker.log"
Write-Host "- $OutDir\agent_discovery_loop\agent_discovery_loop_report.json"
Write-Host "- $OutDir\real_project\discovered_issues.json"
Write-Host "`nReminder: candidate findings require the existing Evidence, Adversarial, Schema and Human Review gates before confirmation." -ForegroundColor DarkYellow
