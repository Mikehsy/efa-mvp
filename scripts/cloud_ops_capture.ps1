param(
    [string]$ConfigPath = ".ops\browser.env",
    [string]$SessionName = "efa-ops",
    [string]$AuthProfile = "efa-ops",
    [string]$EvidenceDir = "",
    [switch]$SkipLogin,
    [switch]$ManualLogin,
    [switch]$KeepEvidence
)

<#
浏览器自动巡检采集脚本。

登录策略（优先级从高到低）：
  1. Auth Vault：agent-browser auth login <AuthProfile>
     凭据由 scripts\ops_auth_setup.ps1 一次性写入并加密保存，
     登录选择器由 agent-browser 自动处理，不需要 @e1/@e2/@e3。
  2. 手动选择器兜底：读取 EFA_LOGIN_*_SELECTOR 后 fill/click。
     用 -ManualLogin 可强制走这条路径。

证据策略（默认不累积）：
  - 成功：删除本次证据目录，只把结论打到 stdout（LOGIN_METHOD / LOGIN_RESULT / PAGE_CHECK）；
  - 失败：证据保留在 ops-evidence\last-failure\（固定名，每次覆盖，不累积）；
  - 需要留档时加 -KeepEvidence，或显式指定 -EvidenceDir。

示例：
  powershell -ExecutionPolicy Bypass -File scripts\cloud_ops_capture.ps1
  powershell -ExecutionPolicy Bypass -File scripts\cloud_ops_capture.ps1 -KeepEvidence
  powershell -ExecutionPolicy Bypass -File scripts\cloud_ops_capture.ps1 -SkipLogin
  powershell -ExecutionPolicy Bypass -File scripts\cloud_ops_capture.ps1 -ManualLogin
#>

. (Join-Path $PSScriptRoot 'ops_browser_common.ps1')

Set-Location $script:ProjectRoot

$AgentBrowser = Get-AgentBrowserExe
Initialize-AgentBrowserEnv | Out-Null

function Fail {
    param([string]$Message)
    Write-Output "ERROR=$Message"
    exit 1
}

$ConfigPath = Resolve-OpsConfigPath -Path $ConfigPath
try {
    $cfg = Read-OpsEnvFile -Path $ConfigPath
} catch {
    Fail $_.Exception.Message
}

if (-not $PSBoundParameters.ContainsKey('AuthProfile') -and $cfg["EFA_AUTH_PROFILE"]) {
    $AuthProfile = $cfg["EFA_AUTH_PROFILE"]
}

$AppUrl = $cfg["EFA_APP_URL"]
$UserSelector = $cfg["EFA_LOGIN_USER_SELECTOR"]
$PassSelector = $cfg["EFA_LOGIN_PASS_SELECTOR"]
$SubmitSelector = $cfg["EFA_LOGIN_SUBMIT_SELECTOR"]
$Username = $cfg["EFA_OPS_USERNAME"]
$Password = $cfg["EFA_OPS_PASSWORD"]

if (-not $AppUrl) { Fail "EFA_APP_URL is required in $ConfigPath" }
try {
    Assert-AppUrl -Url $AppUrl
} catch {
    Fail $_.Exception.Message
}

if (-not $EvidenceDir) {
    $stamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
    $EvidenceDir = Join-Path $script:ProjectRoot ("ops-evidence\" + $stamp)
}
New-Item -ItemType Directory -Force -Path $EvidenceDir | Out-Null

$snapPath = Join-Path $EvidenceDir "00_login_snapshot.txt"
$homeShot = Join-Path $EvidenceDir "01_app_home.png"
$loggedShot = Join-Path $EvidenceDir "02_after_login.png"
$snapshotAfterPath = Join-Path $EvidenceDir "03_after_login_snapshot.txt"
$loginMethodPath = Join-Path $EvidenceDir "04_login_method.txt"
$runLog = Join-Path $EvidenceDir "agent_browser.log"

function AB {
    param([string[]]$ArgumentList, [string[]]$Sensitive)
    Invoke-AgentBrowser -Exe $AgentBrowser -SessionName $SessionName -ArgumentList $ArgumentList -Sensitive $Sensitive -LogPath $runLog
}

function Open-LoginPage {
    AB -ArgumentList @("open", $AppUrl) | Out-Null
    AB -ArgumentList @("wait", "--load", "load") | Out-Null
}

function Write-LoginRecord {
    param([string]$Result)
    Set-Content -Path $loginMethodPath -Value @(
        "LOGIN_METHOD=$loginMethod"
        "LOGIN_RESULT=$Result"
        "AUTH_PROFILE=$AuthProfile"
        "APP_URL=$AppUrl"
        "CAPTURED_AT=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    ) -Encoding UTF8
}

# 登录后判定：登录 tab 的用户名框仍在 = 没进去；同时读取应用自己的错误提示。
# 用 \u 转义避免中文经命令行传参时被 ANSI 编码破坏。
$loginStateJs = "(function(){var i=document.querySelectorAll('input');var n=0;" +
    "for(var k=0;k<i.length;k++){if(i[k].getAttribute('aria-label')==='\u7528\u6237\u540d'){n++;}}" +
    "var e=(document.body.innerText.match(/\u7528\u6237\u540d\u6216\u5bc6\u7801\u9519\u8bef|\u8bf7\u8f93\u5165\u7528\u6237\u540d|\u8d26\u53f7\u88ab\u9501\u5b9a/)||[''])[0];" +
    "return n+'|'+e;})()"

$loginMethod = "none"
$failed = $false
$vaultFailed = $false
$pageCheck = "n/a"

try {
    AB -ArgumentList @("close") | Out-Null
    Open-LoginPage
    (AB -ArgumentList @("snapshot", "-i")).Output | Set-Content -Path $snapPath -Encoding UTF8
    AB -ArgumentList @("screenshot", $homeShot) | Out-Null

    if (-not $SkipLogin) {
        $profile = $null
        if (-not $ManualLogin) {
            $profile = Get-AuthProfileInfo -Exe $AgentBrowser -Profile $AuthProfile
        }

        if ($profile) {
            $loginArgs = @("auth", "login", $AuthProfile)
            if (Test-SameOrigin -Left $profile.url -Right $AppUrl) {
                # 页面已在同一源上打开，避免重复导航。
                $loginArgs += "--no-navigate"
            }
            $r = AB -ArgumentList $loginArgs
            if ($r.Code -eq 0) {
                $loginMethod = "auth-vault:$AuthProfile"
            } else {
                Write-Warning "Auth Vault 登录失败（exit $($r.Code)），改用手动选择器兜底。"
                Open-LoginPage
                $vaultFailed = $true
                $profile = $null
            }
        }

        if (-not $profile) {
            if ($ManualLogin) {
                Write-Output "已指定 -ManualLogin，跳过 Auth Vault。"
            }
            if (-not $UserSelector -or -not $PassSelector -or -not $SubmitSelector) {
                $reason = if ($vaultFailed) {
                    "Auth profile '$AuthProfile' 登录失败，且未配置手动选择器兜底。"
                } else {
                    "没有可用的 Auth profile '$AuthProfile'，且手动选择器为空。"
                }
                throw ("未执行登录：$reason" +
                    " 先运行一次性配置：powershell -ExecutionPolicy Bypass -File scripts\ops_auth_setup.ps1" +
                    "；如需手工兜底再填 EFA_LOGIN_*_SELECTOR。")
            }
            if (-not $Username -or -not $Password) {
                throw "手动登录缺少凭据：请在 $ConfigPath 填写 EFA_OPS_USERNAME / EFA_OPS_PASSWORD。"
            }
            AB -ArgumentList @("fill", $UserSelector, $Username) | Out-Null
            AB -ArgumentList @("fill", $PassSelector, $Password) -Sensitive @($Password) | Out-Null
            AB -ArgumentList @("click", $SubmitSelector) | Out-Null
            $loginMethod = "manual-selectors"
        }

        AB -ArgumentList @("wait", "--load", "load") | Out-Null
        (AB -ArgumentList @("snapshot")).Output | Set-Content -Path $snapshotAfterPath -Encoding UTF8
        AB -ArgumentList @("screenshot", $loggedShot) | Out-Null

        $state = (AB -ArgumentList @("eval", $loginStateJs)).Output
        $stillOnForm = $false
        $appMessage = ''
        if ($state -match '(\d+)\s*\|\s*(.*)') {
            $stillOnForm = ([int]$Matches[1] -gt 0)
            $appMessage = $Matches[2].Trim().Trim('"')
        }
        if ($stillOnForm -or $appMessage) {
            $detail = if ($appMessage) { "页面提示「$appMessage」" } else { '登录表单仍然停留在页面上' }
            Write-LoginRecord 'rejected'
            throw ("登录未生效：$detail。请检查 .ops/browser.env 的 EFA_OPS_USERNAME / EFA_OPS_PASSWORD，" +
                "确认该账号已在应用里注册且未被锁定；凭据变更后重新运行 scripts\ops_auth_setup.ps1 -Force。")
        }

        # 证据默认不保留，所以页面级异常必须在这里判定，否则事后无从回看。
        $pageJs = "(function(){var t=document.body.innerText;" +
            "var m=t.match(/Traceback \(most recent call last\)|ModuleNotFoundError|ImportError|AttributeError|KeyError|TypeError|Uncaught/);" +
            "return m?('ERROR:'+m[0]):'ok'})()"
        $pageState = (AB -ArgumentList @("eval", $pageJs)).Output
        if ($pageState) { $pageCheck = $pageState.Trim().Trim('"') }
    }

    if ($SkipLogin) { Write-LoginRecord 'skipped' } else { Write-LoginRecord 'ok' }
}
catch {
    Write-Output "ERROR=$($_.Exception.Message)"
    $failed = $true
}
finally {
    AB -ArgumentList @("close") | Out-Null
}

# 证据目录处理：显式指定或 -KeepEvidence 才留档；失败留一份固定位置的现场；其余删除。
$explicitEvidence = $PSBoundParameters.ContainsKey('EvidenceDir')
if ($failed) {
    $lastFailure = Join-Path $script:ProjectRoot "ops-evidence\last-failure"
    if (-not $explicitEvidence) {
        if (Test-Path -LiteralPath $lastFailure) {
            Remove-Item -LiteralPath $lastFailure -Recurse -Force -ErrorAction SilentlyContinue
        }
        try {
            Move-Item -LiteralPath $EvidenceDir -Destination $lastFailure -Force -ErrorAction Stop
            $EvidenceDir = $lastFailure
        } catch {
            Write-Warning "无法移动失败证据到 $lastFailure，原目录保留：$EvidenceDir"
        }
    }
    Write-Output "EVIDENCE_DIR=$EvidenceDir"
    Write-Output "LOGIN_METHOD=$loginMethod"
    exit 1
}

if ($KeepEvidence -or $explicitEvidence) {
    Write-Output "EVIDENCE_DIR=$EvidenceDir"
} else {
    Remove-Item -LiteralPath $EvidenceDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Output "EVIDENCE_DIR=(已清理)"
}
Write-Output "LOGIN_METHOD=$loginMethod"
Write-Output "LOGIN_RESULT=$(if ($SkipLogin) { 'skipped' } else { 'ok' })"
Write-Output "PAGE_CHECK=$pageCheck"
