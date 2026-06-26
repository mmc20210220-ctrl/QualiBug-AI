[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$EnvFile = Join-Path $Root ".env.local"

if (-not (Test-Path $EnvFile)) {
    throw ".env.local is missing. Run .\BOOTSTRAP_LOCAL.ps1, then fill in the local secret."
}

$cfg = @{}
Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) { return }
    $parts = $line.Split("=", 2)
    $cfg[$parts[0].Trim()] = $parts[1].Trim().Trim('"').Trim("'")
}

foreach ($key in @("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")) {
    if (-not $cfg.ContainsKey($key) -or [string]::IsNullOrWhiteSpace($cfg[$key]) -or $cfg[$key] -match "REPLACE_WITH") {
        throw "Missing usable $key in .env.local. The value was not printed."
    }
}

$baseUrl = $cfg["LLM_BASE_URL"].TrimEnd("/")
$headers = @{ Authorization = "Bearer " + $cfg["LLM_API_KEY"] }
$body = @{
    model = $cfg["LLM_MODEL"]
    messages = @(@{ role = "user"; content = "Reply exactly: OK" })
    temperature = 0
    max_tokens = 8
    stream = $false
} | ConvertTo-Json -Depth 8

try {
    $response = Invoke-RestMethod -Uri "$baseUrl/chat/completions" -Method Post -Headers $headers -ContentType "application/json" -Body $body -TimeoutSec 90
    $content = ""
    if ($response.choices -and $response.choices.Count -gt 0) { $content = [string]$response.choices[0].message.content }
    if ([string]::IsNullOrWhiteSpace($content)) { throw "LLM returned a response envelope without message content." }
    Write-Host "DEEPSEEK_CONNECTION_OK model=$($cfg['LLM_MODEL'])" -ForegroundColor Green
} catch {
    throw "DeepSeek test failed: $($_.Exception.Message)"
}
