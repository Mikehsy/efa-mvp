param(
    [string]$ConfigPath = ".ops\browser.env",
    [string]$SessionName = "efa-ops-deep",
    [string]$AuthProfile = "efa-ops",
    [string]$EvidenceDir = ""
)

<#
只读深度巡检（cloud_ops_capture.ps1 的补充）。
覆盖：6 个业务页访问与页面错误体检、三段式评分阈值抽检、本地/远端 Git 基线核对。
严格只读：只做登录、点侧边栏链接、筛选下拉框、读快照；不点保存/删除/载入演示数据，
不访问 Supabase / Streamlit 控制台，不触碰其他账号数据。
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
try { $cfg = Read-OpsEnvFile -Path $ConfigPath } catch { Fail $_.Exception.Message }
if (-not $PSBoundParameters.ContainsKey('AuthProfile') -and $cfg['EFA_AUTH_PROFILE']) {
    $AuthProfile = $cfg['EFA_AUTH_PROFILE']
}
$AppUrl = $cfg['EFA_APP_URL']
if (-not $AppUrl) { Fail "EFA_APP_URL is required in $ConfigPath" }
try { Assert-AppUrl -Url $AppUrl } catch { Fail $_.Exception.Message }

if (-not $EvidenceDir) {
    $stamp = Get-Date -Format 'yyyy-MM-dd'
    $EvidenceDir = Join-Path $script:ProjectRoot ("ops-evidence\" + $stamp + "_deep")
}
New-Item -ItemType Directory -Force -Path $EvidenceDir | Out-Null
$runLog = Join-Path $EvidenceDir 'agent_browser.log'
$reportPath = Join-Path $EvidenceDir 'deep_check_report.txt'

function AB {
    param([string[]]$ArgumentList)
    Invoke-AgentBrowser -Exe $AgentBrowser -SessionName $SessionName -ArgumentList $ArgumentList -LogPath $runLog
}

function Wait-Page {
    param([int]$Milliseconds = 2800)
    AB -ArgumentList @('wait', "$Milliseconds") | Out-Null
}

function Get-SnapshotText {
    (AB -ArgumentList @('snapshot')).Output
}

function Get-LinkRef {
    param([string]$SnapshotText, [string]$Label)
    $m = [regex]::Match($SnapshotText, 'link\s+"([^"]*' + [regex]::Escape($Label) + '[^"]*)"\s+\[[^\]]*ref=(e\d+)[^\]]*\]')
    if ($m.Success) { return $m.Groups[2].Value }
    return $null
}

function Get-ComboRef {
    param([string]$SnapshotText, [string]$Label)
    $m = [regex]::Match($SnapshotText, 'combobox\s+"' + [regex]::Escape($Label) + '"\s+\[[^\]]*ref=(e\d+)[^\]]*\]')
    if ($m.Success) { return $m.Groups[1].Value }
    return $null
}

function Select-Combo {
    param([string]$Label, [string]$OptionText)
    $snap = Get-SnapshotText
    $ref = Get-ComboRef -SnapshotText $snap -Label $Label
    if (-not $ref) { return $false }
    AB -ArgumentList @('click', "@$ref") | Out-Null
    AB -ArgumentList @('wait', '800') | Out-Null
    $js = "(function(){var o=[].slice.call(document.querySelectorAll('[role=option]'));for(var i=0;i<o.length;i++){if(o[i].innerText.indexOf('$OptionText')>=0){o[i].click();return 'ok'}}return 'no'})()"
    $r = AB -ArgumentList @('eval', $js)
    Wait-Page
    return ($r.Output -match 'ok')
}

function Get-RowCells {
    param([string]$Item)
    $js = "(function(){var rows=[].slice.call(document.querySelectorAll('[role=row]'));return rows.map(function(r){return [].slice.call(r.querySelectorAll('[role=gridcell]')).map(function(c){return c.innerText.trim()}).join('|')}).filter(function(t){return t.indexOf('$Item')>=0}).join(' || ');})()"
    $out = (AB -ArgumentList @('eval', $js)).Output
    if (-not $out) { return $null }
    return ($out.Trim().Trim('"') -split '\s*\|\|\s*')[0]
}

function Get-Score {
    param([double]$Value, [double]$Low, [double]$Pass, [double]$Excellent, [string]$Direction)
    if ($Direction -eq '越小越好') {
        if ($Value -ge $Low) { return 0 }
        if ($Value -gt $Pass) { return [math]::Round(($Low - $Value) / ($Low - $Pass) * 60, 1) }
        if ($Value -gt $Excellent) { return [math]::Round(60 + ($Pass - $Value) / ($Pass - $Excellent) * 40, 1) }
        return 100
    }
    if ($Value -le $Low) { return 0 }
    if ($Value -lt $Pass) { return [math]::Round(($Value - $Low) / ($Pass - $Low) * 60, 1) }
    if ($Value -lt $Excellent) { return [math]::Round(60 + ($Value - $Pass) / ($Excellent - $Pass) * 40, 1) }
    return 100
}

$results = New-Object System.Collections.Generic.List[string]
$pageChecks = New-Object System.Collections.Generic.List[string]
$scoreChecks = New-Object System.Collections.Generic.List[string]

try {
    AB -ArgumentList @('close') | Out-Null
    AB -ArgumentList @('open', $AppUrl) | Out-Null
    AB -ArgumentList @('wait', '--load', 'load') | Out-Null

    $profile = Get-AuthProfileInfo -Exe $AgentBrowser -Profile $AuthProfile
    if (-not $profile) { throw "缺少 Auth profile '$AuthProfile'，请先运行 ops_auth_setup.ps1。" }
    $loginArgs = @('auth', 'login', $AuthProfile)
    if (Test-SameOrigin -Left $profile.url -Right $AppUrl) { $loginArgs += '--no-navigate' }
    $login = AB -ArgumentList $loginArgs
    if ($login.Code -ne 0) { throw "Auth Vault 登录失败（exit $($login.Code)）。" }
    Wait-Page

    $errorJs = "(function(){var t=document.body.innerText;var m=t.match(/Traceback \(most recent call last\)|ModuleNotFoundError|ImportError|AttributeError|KeyError|TypeError|Uncaught|ERROR:/);return m?('ERROR:'+m[0]):'ok'})()"

    $pages = @('运动员管理', '测试与评估', '数据分析', '评分标准', '分析报告', '隐私与账号')
    foreach ($page in $pages) {
        $snap = Get-SnapshotText
        $ref = Get-LinkRef -SnapshotText $snap -Label $page
        if (-not $ref) {
            $pageChecks.Add("PAGE_${page}=NO_LINK")
            continue
        }
        AB -ArgumentList @('click', "@$ref") | Out-Null
        Wait-Page
        $pageSnap = Get-SnapshotText
        $safe = $page -replace '[^\w\u4e00-\u9fff]', '_'
        Set-Content -Path (Join-Path $EvidenceDir "page_$safe.txt") -Value $pageSnap -Encoding UTF8
        $state = (AB -ArgumentList @('eval', $errorJs)).Output.Trim().Trim('"')
        if (-not $state) { $state = 'NO_OUTPUT' }
        $pageChecks.Add("PAGE_${page}=$state")
    }

    # 回到评分标准页做只读阈值抽检
    $snap = Get-SnapshotText
    $ref = Get-LinkRef -SnapshotText $snap -Label '评分标准'
    if ($ref) {
        AB -ArgumentList @('click', "@$ref") | Out-Null
        Wait-Page

        [void](Select-Combo -Label '性别' -OptionText '男')
        [void](Select-Combo -Label '年龄组' -OptionText '成年')
        [void](Select-Combo -Label '类别' -OptionText '力量')
        $grip = Get-RowCells -Item '握力'
        if ($grip) {
            $c = $grip -split '\|'
            if ($c.Count -ge 7) {
                $low = [double]$c[4]; $pass = [double]$c[5]; $exc = [double]$c[6]
                foreach ($pair in @(@(22, 0), @(33, 60), @(38.5, 80), @(44, 100))) {
                    $got = Get-Score -Value ([double]$pair[0]) -Low $low -Pass $pass -Excellent $exc -Direction $c[3]
                    $ok = ($got -eq [double]$pair[1])
                    $scoreChecks.Add("GRIP_ADULT_$($pair[0])=$got (expected $($pair[1])) $(if ($ok) { 'PASS' } else { 'REVIEW' })")
                }
            } else {
                $scoreChecks.Add("GRIP_ADULT=PARSE_FAIL:$grip")
            }
        } else {
            $scoreChecks.Add('GRIP_ADULT=NO_ROW')
        }

        [void](Select-Combo -Label '类别' -OptionText '全部')
        $sprintAdult = Get-RowCells -Item '30米冲刺'
        if ($sprintAdult) {
            $c = $sprintAdult -split '\|'
            if ($c.Count -ge 7) {
                $got = Get-Score -Value 4.34 -Low ([double]$c[4]) -Pass ([double]$c[5]) -Excellent ([double]$c[6]) -Direction $c[3]
                $ok = ($got -eq 88.1)
                $scoreChecks.Add("SPRINT_ADULT_4.34=$got (expected 88.1) $(if ($ok) { 'PASS' } else { 'REVIEW' })")
            } else {
                $scoreChecks.Add("SPRINT_ADULT=PARSE_FAIL:$sprintAdult")
            }
        } else {
            $scoreChecks.Add('SPRINT_ADULT=NO_ROW')
        }

        [void](Select-Combo -Label '年龄组' -OptionText 'U18')
        $sprintU18 = Get-RowCells -Item '30米冲刺'
        if ($sprintU18) {
            $c = $sprintU18 -split '\|'
            if ($c.Count -ge 7) {
                $got = Get-Score -Value 4.34 -Low ([double]$c[4]) -Pass ([double]$c[5]) -Excellent ([double]$c[6]) -Direction $c[3]
                $ok = ($got -eq 80)
                $scoreChecks.Add("SPRINT_U18_4.34=$got (expected 80) $(if ($ok) { 'PASS' } else { 'REVIEW' })")
            } else {
                $scoreChecks.Add("SPRINT_U18=PARSE_FAIL:$sprintU18")
            }
        } else {
            $scoreChecks.Add('SPRINT_U18=NO_ROW')
        }
    } else {
        $scoreChecks.Add('SCORE_SAMPLE=NO_NAV')
    }
} catch {
    $results.Add("ERROR=$($_.Exception.Message)")
} finally {
    AB -ArgumentList @('close') | Out-Null
}

$localHead = (git rev-parse --short HEAD 2>$null)
$remoteMain = (git ls-remote origin refs/heads/main 2>$null | Select-Object -First 1)
$dirty = @(git status --porcelain 2>$null).Count

$results.AddRange($pageChecks)
$results.AddRange($scoreChecks)
$results.Add("GIT_LOCAL_HEAD=$localHead")
if ($remoteMain) { $results.Add("GIT_REMOTE_MAIN=$($remoteMain.Split([char]9)[0].Substring(0,7))") } else { $results.Add('GIT_REMOTE_MAIN=UNAVAILABLE') }
$results.Add("GIT_DIRTY_FILES=$dirty")
Set-Content -Path $reportPath -Value $results -Encoding UTF8

$results | ForEach-Object { Write-Output $_ }
Write-Output "EVIDENCE_DIR=$EvidenceDir"
