param([int]$Port = 8501)
. (Join-Path $PSScriptRoot 'common.ps1')

$owners = Get-PortOwner $Port
if ($owners.Count -eq 0) {
    Write-Host "No service listening on port $Port."
} else {
    foreach ($procId in $owners) {
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        Write-Host "Stopped PID $procId"
    }
}

# Remove a lock file only if it survived the shutdown (stale lock protection).
Start-Sleep -Seconds 1
$lock = Join-Path $DataDir '.write.lock'
if (Test-Path -LiteralPath $lock) {
    Remove-Item -LiteralPath $lock -Force -ErrorAction SilentlyContinue
    Write-Host 'Removed stale write lock.'
}
