param(
    [int]$Port = 8501,
    [switch]$AllowDemoData,
    [switch]$Foreground
)
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Python
Ensure-Dirs

$owners = Get-PortOwner $Port
if ($owners.Count -gt 0) {
    throw "Port $Port is already in use by PID $($owners -join ','). Run stop.ps1 first."
}

# Production default: demo data is disabled. Use -AllowDemoData only on a dev machine.
$env:EFA_ALLOW_DEMO_DATA = if ($AllowDemoData) { '1' } else { '0' }
$arguments = @('-m', 'streamlit', 'run', 'app.py',
               '--server.headless', 'true', '--server.port', "$Port")

if ($Foreground) {
    Write-Host "Starting foreground on port $Port (demo data: $($env:EFA_ALLOW_DEMO_DATA))"
    & $PythonExe @arguments
    return
}

$outLog = Join-Path $LogDir 'streamlit_out.log'
$errLog = Join-Path $LogDir 'streamlit_err.log'
foreach ($f in @($outLog, $errLog)) {
    if (Test-Path -LiteralPath $f) { Remove-Item -LiteralPath $f -Force }
}
$proc = Start-Process -FilePath $PythonExe -ArgumentList $arguments `
    -WorkingDirectory $ProjectRoot -WindowStyle Hidden `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
Write-Host "Started PID=$($proc.Id) port=$Port demo_data=$($env:EFA_ALLOW_DEMO_DATA)"
Start-Sleep -Seconds 6
& (Join-Path $PSScriptRoot 'healthcheck.ps1') -Port $Port
