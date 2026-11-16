param(
    [Parameter(Mandatory = $true)][string]$PreviousDir,
    [Parameter(Mandatory = $true)][string]$BackupDir,
    [int]$Port = 8501
)
. (Join-Path $PSScriptRoot 'common.ps1')

$previous = (Resolve-Path -LiteralPath $PreviousDir).Path
$previousPython = Join-Path $previous '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $previousPython)) {
    throw "Previous version has no virtual environment python: $previousPython"
}
if (-not (Test-Path -LiteralPath $BackupDir)) {
    throw "Backup directory not found: $BackupDir"
}

Write-Host 'Step 1/5: stop current service'
& (Join-Path $PSScriptRoot 'stop.ps1') -Port $Port

Write-Host 'Step 2/5: restore data backup'
& (Join-Path $PSScriptRoot 'restore.ps1') -BackupDir $BackupDir

Write-Host 'Step 3/5: start previous version'
$env:EFA_DATA_DIR = $DataDir
$env:EFA_BACKUP_DIR = $BackupDirRoot
$env:EFA_LOG_DIR = $LogDir
$env:EFA_ALLOW_DEMO_DATA = '0'
$arguments = @('-m', 'streamlit', 'run', 'app.py',
               '--server.headless', 'true', '--server.port', "$Port")
$outLog = Join-Path $LogDir 'rollback_out.log'
$errLog = Join-Path $LogDir 'rollback_err.log'
foreach ($f in @($outLog, $errLog)) {
    if (Test-Path -LiteralPath $f) { Remove-Item -LiteralPath $f -Force }
}
$proc = Start-Process -FilePath $previousPython -ArgumentList $arguments `
    -WorkingDirectory $previous -WindowStyle Hidden `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
Write-Host "Previous version started PID=$($proc.Id)"

Write-Host 'Step 4/5: health check'
Start-Sleep -Seconds 6
& (Join-Path $PSScriptRoot 'healthcheck.ps1') -Port $Port

Write-Host 'Step 5/5: rollback complete (RTO target <= 15 min)'
