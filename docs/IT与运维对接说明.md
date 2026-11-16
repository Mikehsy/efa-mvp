# IT 与运维对接说明（可直接转发）

> 用途：本系统（运动员体能与功能评估系统 / EFA，Streamlit，端口 8501）上线前，需要 **IT / 网络管理员** 与 **Windows 系统管理员** 各配合一件事。本文档可直接转发给对方，按其角色执行对应部分即可。
> 系统本身**无账号 / 权限体系**，因此访问安全完全依赖网络层限制，请务必按注意事项执行。

---

## 一、局域网访问（请转给 IT / 网络管理员）

### 背景（可转发原话）
> 我们准备在内网部署一个体育评估网页（Streamlit，端口 8501）。需要：① 把运行机器的 8501 端口在防火墙入站规则放行 TCP；② 只允许指定内网 IP 段访问（白名单），不要公网暴露。运行机器是指定电脑/服务器，系统本身没有登录账号，所以必须靠网络层限制访问。

### 前置条件
- 已确定运行 EFA 的机器及其**内网 IP**。
- 已确认允许访问该系统的**内网 IP 段 / 具体 IP**（白名单）。
- 该机器的 `.streamlit/config.toml` 已就位（项目根目录下）。

### 操作步骤
1. 编辑项目根目录 `.streamlit/config.toml`，将第 5 行改为：
   ```toml
   address = "0.0.0.0"
   ```
   （原值为 `"localhost"`，仅本机可访问。）
2. 在运行机器上以**管理员**启动 PowerShell，放行端口并限制来源（把网段换成你们实际内网段）：
   ```powershell
   New-NetFirewallRule -DisplayName "EFA-Streamlit-8501" -Direction Inbound `
     -Protocol TCP -LocalPort 8501 -RemoteAddress 10.0.0.0/24 -Action Allow
   ```
   > 不写 `-RemoteAddress` 即对所有来源开放，**不建议**。
3. 重启 EFA 服务（项目根目录执行 `scripts\run.ps1` 或对应启动方式）。
4. 在另一台同网段电脑访问 `http://<运行机内网IP>:8501` 验证。

### 验收标准
- 同网段其他机器可正常打开页面并操作。
- 非白名单 IP 无法访问 8501 端口。

### 注意事项
- ⚠️ **系统无登录账号**：任何能访问地址的人都能读写数据，只能内网 + IP 白名单。
- ⚠️ **禁止公网暴露**：勿将 8501 映射到公网；如需对外，须额外加反向代理 + 登录认证（属开发需求，不在本文范围）。
- 若运行机器 IP 为 DHCP 分配，建议改为静态 IP 或绑定域名，避免 IP 变动后白名单失效。

---

## 二、健康探活计划任务（请转给 Windows 系统管理员）

### 背景（可转发原话）
> 需要在运行 Streamlit 的机器上建一个 Windows 计划任务，每 5 分钟跑一次这个脚本 `scripts\healthcheck.ps1`（位于项目根目录下），把结果写入 `logs/health.log`。服务挂掉时脚本返回非零，便于告警。

### 前置条件
- EFA 服务已配置为**持续运行**（建议以服务/开机自启方式，而非临时命令行）。
- 管理员以**管理员身份**运行 PowerShell。
- `scripts\healthcheck.ps1` 与项目目录完整存在。

### 操作步骤
在运行机器上以管理员 PowerShell 执行（把路径换成实际项目目录）：
```powershell
$exe = "PowerShell.exe"
$arg = '-ExecutionPolicy Bypass -File "C:\你的项目路径\scripts\healthcheck.ps1"'
$act = New-ScheduledTaskAction -Execute $exe -Argument $arg
$trig = New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes 5) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
Register-ScheduledTask -TaskName "EFA-HealthCheck" -Action $act -Trigger $trig `
        -User "SYSTEM" -RunLevel Highest -Force
```
> ⚠️ **不要用 `-RepetitionDuration ([TimeSpan]::MaxValue)`**：它会被序列化成约 1067 万天，超出任务计划允许的时长，注册时报「任务 XML 包含格式不正确或超出范围的值」（HRESULT 0x80041318）。用 `-Days 3650`（10 年）即可，到期重新注册一次。
>
> 备选方案（更稳，绕开 XML 长度限制）：
> ```powershell
> schtasks /Create /TN "EFA-HealthCheck" /TR "powershell.exe -ExecutionPolicy Bypass -File \"C:\你的项目路径\scripts\healthcheck.ps1\"" /SC MINUTE /MO 5 /RU SYSTEM /RL HIGHEST /F
> ```
>
> 更完整的参数说明见 `docs/部署与运维手册.md` 第 7 节。

### 验收标准
- `logs/health.log` 每 5 分钟新增一行，内容形如 `2026-11-14 10:00:00 health=True port=8501`。
- 手动停止 EFA 服务后，`healthcheck.ps1` 退出码为非零（可在告警通道验证）。

### 注意事项
- 计划任务运行账户需对 `logs/` 目录有写权限；用 `SYSTEM` 账户通常满足。
- 脚本访问 `http://localhost:8501/_stcore/health`，本机回环地址不受防火墙白名单影响。
- 若项目目录搬迁，需同步更新上面命令中的路径并重新注册任务。

---

## 常见问题
- **Q：同事打不开页面？** 查运行机 IP 是否变化、防火墙规则与白名单是否生效、EFA 服务是否在运行（看 `logs/health.log`）。
- **Q：公网想让外队访问？** 当前不支持，需另加反向代理 + 认证，属开发需求。
- **Q：探活日志不更新？** 确认计划任务已启用、运行账户权限、项目路径正确。
