<#
.SYNOPSIS
  MVP 试用版启动：数据库注册登录 + 每用户独立数据目录 + 局域网监听。
  配合内网穿透或部署到 Streamlit Cloud 供真实用户免费试用。
.DESCRIPTION
  - EFA_USE_DB=1：启用注册/登录，用户自助注册。
  - 数据按用户隔离存于 data/users/<用户ID>/，重启不丢。
  - 上云时把 EFA_DATA_DIR 指向持久卷即可。
.EXAMPLE
  .\scripts\run_mvp.ps1
  .\scripts\run_mvp.ps1 -Port 8502 -AllowDemoSeed
#>
param(
    [int]$Port = 8501,
    [switch]$Foreground
)
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Python
Ensure-Dirs

$owners = Get-PortOwner $Port
if ($owners.Count -gt 0) {
    throw "Port $Port is already in use by PID $($owners -join ','). Run stop.ps1 first."
}

$env:EFA_USE_DB = '1'
$env:EFA_DEMO_USERS = (Join-Path $ProjectRoot 'demo_users_disabled.json')  # 关闭演示账号，用注册登录
$arguments = @('-m', 'streamlit', 'run', 'app.py',
               '--server.headless', 'true', '--server.port', "$Port",
               '--server.address', '0.0.0.0')

if ($Foreground) {
    Write-Host "Starting MVP foreground on port $Port (DB auth enabled)"
    & $PythonExe @arguments
    return
}

$outLog = Join-Path $LogDir 'mvp_out.log'
$errLog = Join-Path $LogDir 'mvp_err.log'
foreach ($f in @($outLog, $errLog)) {
    if (Test-Path -LiteralPath $f) { Remove-Item -LiteralPath $f -Force }
}
$proc = Start-Process -FilePath $PythonExe -ArgumentList $arguments `
    -WorkingDirectory $ProjectRoot -WindowStyle Hidden `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
Write-Host "MVP started PID=$($proc.Id) port=$Port address=0.0.0.0 db_auth=1"
Write-Host "Local: http://localhost:$Port"
Start-Sleep -Seconds 6
& (Join-Path $PSScriptRoot 'healthcheck.ps1') -Port $Port
