<#
.SYNOPSIS
  演示版一键启动：允许演示数据 + 局域网监听 + 多账号登录页。
  用于本机跑 demo，配合内网穿透工具生成公网网址给客户试用（无需云服务器）。
.DESCRIPTION
  等价于 run.ps1 -AllowDemoData，并把 server.address 设为 0.0.0.0 便于穿透。
.EXAMPLE
  .\scripts\run_demo.ps1
  .\scripts\run_demo.ps1 -Port 8502
#>
param(
    [int]$Port = 8501
)
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Python
Ensure-Dirs

$owners = Get-PortOwner $Port
if ($owners.Count -gt 0) {
    throw "Port $Port is already in use by PID $($owners -join ','). Run stop.ps1 first."
}

$env:EFA_ALLOW_DEMO_DATA = '1'
$arguments = @('-m', 'streamlit', 'run', 'app.py',
               '--server.headless', 'true',
               '--server.port', "$Port",
               '--server.address', '0.0.0.0')

$outLog = Join-Path $LogDir 'demo_out.log'
$errLog = Join-Path $LogDir 'demo_err.log'
foreach ($f in @($outLog, $errLog)) {
    if (Test-Path -LiteralPath $f) { Remove-Item -LiteralPath $f -Force }
}
$proc = Start-Process -FilePath $PythonExe -ArgumentList $arguments `
    -WorkingDirectory $ProjectRoot -WindowStyle Hidden `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
Write-Host "Demo started PID=$($proc.Id) port=$Port address=0.0.0.0 demo_data=1"
Write-Host "Local: http://localhost:$Port"
Write-Host "Accounts: see demo_users.json (default admin / demo1234) - CHANGE BEFORE SHARING!"
Start-Sleep -Seconds 6
& (Join-Path $PSScriptRoot 'healthcheck.ps1') -Port $Port
