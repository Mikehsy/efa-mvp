# Shared helpers for all deployment scripts.
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$DataDir = Join-Path $ProjectRoot 'data'
$BackupDirRoot = Join-Path $ProjectRoot 'backups'
$LogDir = Join-Path $ProjectRoot 'logs'

function Assert-Python {
    if (-not (Test-Path -LiteralPath $PythonExe)) {
        throw "Virtual environment python not found: $PythonExe. Run setup.ps1 first."
    }
}

function Get-PortOwner([int]$Port) {
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($conns) { return @($conns.OwningProcess | Sort-Object -Unique) }
    return @()
}

function Invoke-ProjectPython([string]$Code) {
    Assert-Python
    & $PythonExe -c $Code
    if ($LASTEXITCODE -ne 0) { throw "Python command failed with exit code $LASTEXITCODE" }
}

function Ensure-Dirs {
    foreach ($dir in @($DataDir, $BackupDirRoot, $LogDir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
}
