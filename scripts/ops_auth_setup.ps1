param(
    [string]$ConfigPath = ".ops\browser.env",
    [string]$AuthProfile = "efa-ops",
    [switch]$Force,
    [switch]$Show,
    [switch]$Delete
)

<#
一次性安全登录配置脚本。

作用：把专用巡检账号写入 agent-browser 的 Auth Vault（加密保存在
      %USERPROFILE%\.agent-browser\auth\<AuthProfile>.json）。
之后 scripts\cloud_ops_capture.ps1 直接 `auth login` 即可登录，
不需要再手工去登录页快照里找 @e1 / @e2 / @e3。

密码只通过 stdin 传入，不出现在命令行、进程列表或本脚本输出中。

示例：
  powershell -ExecutionPolicy Bypass -File scripts\ops_auth_setup.ps1
  powershell -ExecutionPolicy Bypass -File scripts\ops_auth_setup.ps1 -Show
  powershell -ExecutionPolicy Bypass -File scripts\ops_auth_setup.ps1 -Force
  powershell -ExecutionPolicy Bypass -File scripts\ops_auth_setup.ps1 -Delete
#>

. (Join-Path $PSScriptRoot 'ops_browser_common.ps1')

Set-Location $script:ProjectRoot

$AgentBrowser = Get-AgentBrowserExe
Initialize-AgentBrowserEnv | Out-Null

$ConfigPath = Resolve-OpsConfigPath -Path $ConfigPath
$cfg = @{}
if (Test-Path -LiteralPath $ConfigPath) {
    $cfg = Read-OpsEnvFile -Path $ConfigPath
}
if (-not $PSBoundParameters.ContainsKey('AuthProfile') -and $cfg["EFA_AUTH_PROFILE"]) {
    $AuthProfile = $cfg["EFA_AUTH_PROFILE"]
}

if ($Delete) {
    if (-not (Test-AuthProfile -Exe $AgentBrowser -Profile $AuthProfile)) {
        Write-Output "Auth profile '$AuthProfile' 不存在，无需删除。"
        return
    }
    $r = Invoke-AgentBrowser -Exe $AgentBrowser -ArgumentList @('auth', 'delete', $AuthProfile)
    if ($r.Code -ne 0) {
        Write-Output "ERROR=删除 Auth profile 失败（exit $($r.Code)）：$($r.Error)"
        exit 1
    }
    Write-Output "已删除 Auth profile：$AuthProfile"
    return
}

if ($Show) {
    if (-not (Test-AuthProfile -Exe $AgentBrowser -Profile $AuthProfile)) {
        Write-Output "Auth profile '$AuthProfile' 不存在。运行本脚本（不带 -Show）即可创建。"
        return
    }
    $r = Invoke-AgentBrowser -Exe $AgentBrowser -ArgumentList @('auth', 'show', $AuthProfile)
    Write-Output $r.Output.TrimEnd()
    Write-Output ''
    Write-Output "存储位置：$(Join-Path $script:AuthVaultDir ($AuthProfile + '.json'))"
    return
}

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    Write-Output "ERROR=缺少配置文件 $ConfigPath。先复制 ops\browser.env.example 到 .ops\browser.env 并填写。"
    exit 1
}

$AppUrl = $cfg["EFA_APP_URL"]
$Username = $cfg["EFA_OPS_USERNAME"]
$Password = $cfg["EFA_OPS_PASSWORD"]
$UserSelector = $cfg["EFA_LOGIN_USER_SELECTOR"]
$PassSelector = $cfg["EFA_LOGIN_PASS_SELECTOR"]
$SubmitSelector = $cfg["EFA_LOGIN_SUBMIT_SELECTOR"]

$missing = @()
if (-not $AppUrl) { $missing += 'EFA_APP_URL' }
if (-not $Username) { $missing += 'EFA_OPS_USERNAME' }
if (-not $Password) { $missing += 'EFA_OPS_PASSWORD' }
if ($missing.Count -gt 0) {
    Write-Output "ERROR=配置文件缺少字段：$($missing -join ', ')（$ConfigPath）"
    exit 1
}
try {
    Assert-AppUrl -Url $AppUrl
} catch {
    Write-Output "ERROR=$($_.Exception.Message)"
    exit 1
}

if (Test-AuthProfile -Exe $AgentBrowser -Profile $AuthProfile) {
    if (-not $Force) {
        $info = Get-AuthProfileInfo -Exe $AgentBrowser -Profile $AuthProfile
        $current = if ($info) {
            "URL=$($info.url)  USER=$($info.username)"
        } else {
            '(读取元数据失败)'
        }
        Write-Output "Auth profile '$AuthProfile' 已存在：$current"
        Write-Output '如需覆盖，请加 -Force 重新运行。'
        return
    }
}

Write-Output "正在写入 Auth profile '$AuthProfile' ..."
try {
    $save = Save-AuthProfile `
        -Exe $AgentBrowser `
        -Profile $AuthProfile `
        -Url $AppUrl `
        -Username $Username `
        -Password $Password `
        -UsernameSelector $UserSelector `
        -PasswordSelector $PassSelector `
        -SubmitSelector $SubmitSelector
} catch {
    Write-Output "ERROR=写入 Auth profile 失败：$($_.Exception.Message)"
    exit 1
}

if ($save.Code -ne 0) {
    Write-Output "ERROR=写入 Auth profile 失败（exit $($save.Code)）：$($save.Error)$($save.Output)"
    exit 1
}

if (-not (Test-AuthProfile -Exe $AgentBrowser -Profile $AuthProfile)) {
    Write-Output "ERROR=写入后校验失败：Auth profile '$AuthProfile' 仍不可用。"
    exit 1
}

$shown = Invoke-AgentBrowser -Exe $AgentBrowser -ArgumentList @('auth', 'show', $AuthProfile)
Write-Output $shown.Output.TrimEnd()
Write-Output ''

$customSelectors = @()
if ($UserSelector) { $customSelectors += 'username' }
if ($PassSelector) { $customSelectors += 'password' }
if ($SubmitSelector) { $customSelectors += 'submit' }
if ($customSelectors.Count -gt 0) {
    Write-Output "已附带自定义选择器：$($customSelectors -join ', ')"
} else {
    Write-Output '未指定选择器：由 agent-browser 自动识别登录表单字段（推荐）。'
}

Write-Output ''
Write-Output '下一步：'
Write-Output '  powershell -ExecutionPolicy Bypass -File scripts\cloud_ops_capture.ps1'
Write-Output ''
Write-Output "凭据存储：$(Join-Path $script:AuthVaultDir ($AuthProfile + '.json'))（加密，密钥在同目录上一级 .encryption-key）"
Write-Output '该目录不在项目内，不会被 .gitignore 之外的仓库或 GitHub 收录。'
