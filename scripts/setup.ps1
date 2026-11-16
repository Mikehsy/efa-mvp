param(
    [switch]$Offline,
    [switch]$Check
)
. (Join-Path $PSScriptRoot 'common.ps1')

$lock = Join-Path $ProjectRoot 'requirements.lock'
if ($Check) {
    Write-Host "Project root : $ProjectRoot"
    Write-Host "Python exe   : $PythonExe"
    Write-Host "Python found : $(Test-Path -LiteralPath $PythonExe)"
    Write-Host "Lock file    : $(Test-Path -LiteralPath $lock)"
    if (Test-Path -LiteralPath $PythonExe) {
        & $PythonExe -c "import sys, streamlit, pandas, plotly; print('python', sys.version.split()[0]); print('streamlit', streamlit.__version__); print('pandas', pandas.__version__); print('plotly', plotly.__version__)"
    }
    return
}

if (-not (Test-Path -LiteralPath $PythonExe)) {
    Write-Host 'Creating virtual environment...'
    & python -m venv (Join-Path $ProjectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create virtual environment.' }
}

Assert-Python
if ($Offline) {
    $vendor = Join-Path $ProjectRoot 'vendor'
    if (-not (Test-Path -LiteralPath $vendor)) {
        throw "Offline wheel folder missing: $vendor. On a networked machine run: pip download -r requirements.lock -d vendor"
    }
    & $PythonExe -m pip install --no-index --find-links $vendor -r $lock
} else {
    & $PythonExe -m pip install -r $lock
}
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }

Ensure-Dirs
Invoke-ProjectPython "import sys; sys.path.insert(0, r'$ProjectRoot'); import utils; utils.ensure_files(); print('data files ready')"
Write-Host 'Setup complete. Run run.ps1 to start the service.'
