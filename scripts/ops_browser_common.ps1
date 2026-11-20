# Shared helpers for agent-browser based ops automation (capture + auth vault).
# Usage: . (Join-Path $PSScriptRoot 'ops_browser_common.ps1')
#
# Design notes:
# - Resolve the agent-browser *executable* instead of the .cmd shim. The .cmd
#   wrapper breaks stdin piping, which is required by `auth save --password-stdin`.
# - Native calls redirect to temp files, so stderr never turns into a terminating
#   PowerShell error while $ErrorActionPreference is 'Stop'.

$ErrorActionPreference = 'Stop'

$script:ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$script:NodeDir = 'C:\Users\10692\.workbuddy\binaries\node\versions\22.22.2-6'
$script:AuthVaultDir = Join-Path $env:USERPROFILE '.agent-browser\auth'

function Get-AgentBrowserExe {
    $binDir = Join-Path $script:NodeDir 'node_modules\agent-browser\bin'
    if (Test-Path -LiteralPath $binDir) {
        $exe = Get-ChildItem -LiteralPath $binDir -Filter 'agent-browser-*.exe' -File -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($exe) { return $exe.FullName }
    }
    $cmd = Join-Path $script:NodeDir 'agent-browser.cmd'
    if (Test-Path -LiteralPath $cmd) { return $cmd }
    throw "agent-browser not found under $script:NodeDir. Update `$NodeDir in scripts/ops_browser_common.ps1."
}

function Initialize-AgentBrowserEnv {
    $env:PATH = "$script:NodeDir;$env:PATH"
    $browser = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
    if (-not (Test-Path -LiteralPath $browser)) {
        $browser = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
    }
    if (-not (Test-Path -LiteralPath $browser)) {
        throw 'Chrome/Edge executable not found.'
    }
    $env:AGENT_BROWSER_EXECUTABLE_PATH = $browser
    return $browser
}

function Resolve-OpsConfigPath {
    param([Parameter(Mandatory)][string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return (Join-Path $script:ProjectRoot $Path)
}

function Read-OpsEnvFile {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Missing config: $Path. Copy ops/browser.env.example to .ops/browser.env and fill values."
    }
    $cfg = @{}
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith('#')) { continue }
        if ($t -match '^([^=]+)=(.*)$') {
            $cfg[$Matches[1].Trim()] = $Matches[2].Trim().Trim('"').Trim("'")
        }
    }
    return $cfg
}

function Format-AgentBrowserArgs {
    param([string[]]$ArgumentList, [string[]]$Sensitive)
    $safe = New-Object System.Collections.Generic.List[string]
    for ($i = 0; $i -lt $ArgumentList.Count; $i++) {
        $a = $ArgumentList[$i]
        if ($a -eq '--password' -and ($i + 1) -lt $ArgumentList.Count) {
            $safe.Add('--password')
            $safe.Add('***')
            $i++
        } elseif ($Sensitive -and $Sensitive.Count -gt 0 -and $Sensitive -ccontains $a) {
            $safe.Add('***')
        } else {
            $safe.Add($a)
        }
    }
    return ($safe -join ' ')
}

function Write-AgentBrowserLog {
    param(
        [string]$LogPath,
        [string[]]$ArgumentList,
        [string[]]$Sensitive,
        [string]$Out,
        [string]$Err,
        [int]$Code
    )
    if (-not $LogPath) { return }
    $entry = New-Object System.Collections.Generic.List[string]
    $entry.Add("===== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') agent-browser $(Format-AgentBrowserArgs -ArgumentList $ArgumentList -Sensitive $Sensitive)")
    if ($Out) { $entry.Add($Out.TrimEnd()) }
    if ($Err) { $entry.Add('[stderr] ' + $Err.TrimEnd()) }
    $entry.Add("exit=$Code")
    Add-Content -LiteralPath $LogPath -Value $entry -Encoding UTF8
}

function Invoke-AgentBrowser {
    param(
        [Parameter(Mandatory)][string]$Exe,
        [string]$SessionName,
        [Parameter(Mandatory)][string[]]$ArgumentList,
        [string[]]$Sensitive,
        [string]$LogPath
    )
    $full = New-Object System.Collections.Generic.List[string]
    if ($SessionName) {
        $full.Add('--session')
        $full.Add($SessionName)
    }
    foreach ($a in $ArgumentList) { $full.Add($a) }

    $outFile = [System.IO.Path]::GetTempFileName()
    $errFile = [System.IO.Path]::GetTempFileName()
    $out = ''
    $err = ''
    $previousEap = $ErrorActionPreference
    try {
        # PowerShell 5.1 turns native stderr into a terminating error while
        # $ErrorActionPreference is 'Stop', even when it is redirected to a file.
        $ErrorActionPreference = 'Continue'
        & $Exe @($full.ToArray()) 1> $outFile 2> $errFile
        $code = $LASTEXITCODE
        $ErrorActionPreference = $previousEap
        $out = Get-Content -LiteralPath $outFile -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
        $err = Get-Content -LiteralPath $errFile -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
    } finally {
        $ErrorActionPreference = $previousEap
        Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue
    }

    Write-AgentBrowserLog -LogPath $LogPath -ArgumentList $full.ToArray() -Sensitive $Sensitive -Out $out -Err $err -Code $code
    return [pscustomobject]@{
        Output = $out
        Error  = $err
        Code   = $code
    }
}

function Test-AuthProfile {
    param(
        [Parameter(Mandatory)][string]$Exe,
        [Parameter(Mandatory)][string]$Profile
    )
    $r = Invoke-AgentBrowser -Exe $Exe -ArgumentList @('auth', 'show', $Profile)
    return ($r.Code -eq 0)
}

function Get-AuthProfileInfo {
    param(
        [Parameter(Mandatory)][string]$Exe,
        [Parameter(Mandatory)][string]$Profile
    )
    $r = Invoke-AgentBrowser -Exe $Exe -ArgumentList @('auth', 'show', $Profile, '--json')
    if ($r.Code -ne 0 -or -not $r.Output) { return $null }
    try {
        $o = $r.Output | ConvertFrom-Json
    } catch {
        return $null
    }
    if (-not $o.success -or -not $o.data.profile) { return $null }
    return $o.data.profile
}

function Save-AuthProfile {
    param(
        [Parameter(Mandatory)][string]$Exe,
        [Parameter(Mandatory)][string]$Profile,
        [Parameter(Mandatory)][string]$Url,
        [Parameter(Mandatory)][string]$Username,
        [Parameter(Mandatory)][string]$Password,
        [string]$UsernameSelector,
        [string]$PasswordSelector,
        [string]$SubmitSelector,
        [string]$LogPath
    )
    # Windows 上 --password-stdin 实测会把密码多存一个字符（10 位存成 11 位，
    # 20 位存成 21 位），登录时必然报“用户名或密码错误”。
    # 管道、StreamWriter、裸字节流三种写法结果都一样，是 CLI 侧行为，绕不过去。
    # 因此改用 --password 传参，并在日志里把密码值替换成 ***。
    $argList = New-Object System.Collections.Generic.List[string]
    foreach ($a in @('auth', 'save', $Profile, '--url', $Url, '--username', $Username, '--password', $Password)) { $argList.Add($a) }
    if ($UsernameSelector) { $argList.Add('--username-selector'); $argList.Add($UsernameSelector) }
    if ($PasswordSelector) { $argList.Add('--password-selector'); $argList.Add($PasswordSelector) }
    if ($SubmitSelector) { $argList.Add('--submit-selector'); $argList.Add($SubmitSelector) }

    return Invoke-AgentBrowser -Exe $Exe -ArgumentList $argList.ToArray() -Sensitive @($Password) -LogPath $LogPath
}

function Assert-AppUrl {
    param([string]$Url)
    if (-not $Url) { throw 'EFA_APP_URL 为空。' }
    if ($Url -match '[^\x21-\x7E]') {
        throw "EFA_APP_URL 含空格或非 ASCII 字符，不是可访问的网址：$Url。请填写形如 https://efa-mvp.streamlit.app 的真实地址（不要粘贴带分支名的界面文字）。"
    }
    if ($Url -notmatch '^https?://[^/\s]+') {
        throw "EFA_APP_URL 必须以 http:// 或 https:// 加主机名开头：$Url"
    }
}

function Test-SameOrigin {
    param([string]$Left, [string]$Right)
    try {
        $a = [System.Uri]$Left
        $b = [System.Uri]$Right
    } catch {
        return $false
    }
    if (-not $a.IsAbsoluteUri -or -not $b.IsAbsoluteUri) { return $false }
    return ($a.Scheme -eq $b.Scheme -and $a.Host -eq $b.Host -and $a.Port -eq $b.Port)
}
