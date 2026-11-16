. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Python
Push-Location $ProjectRoot
try {
    & $PythonExe -m unittest discover -s tests -t . -v
    if ($LASTEXITCODE -ne 0) { throw "Regression tests failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}
