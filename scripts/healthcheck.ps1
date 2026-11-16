param(
    [int]$Port = 8501,
    [int]$Retries = 3
)
. (Join-Path $PSScriptRoot 'common.ps1')
Ensure-Dirs

$base = "http://localhost:$Port"
$ok = $false
for ($i = 1; $i -le $Retries; $i++) {
    try {
        $health = Invoke-WebRequest "$base/_stcore/health" -UseBasicParsing -TimeoutSec 5
        $page = Invoke-WebRequest "$base/" -UseBasicParsing -TimeoutSec 5
        if ($health.Content.Trim() -eq 'ok' -and $page.StatusCode -eq 200) {
            $ok = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 2
    }
}

$line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') health=$ok port=$Port"
Add-Content -Path (Join-Path $LogDir 'health.log') -Value $line
if (-not $ok) {
    Write-Host "Health check FAILED: $base (see logs/health.log)"
    exit 1
}
Write-Host "Health check OK: $base"
