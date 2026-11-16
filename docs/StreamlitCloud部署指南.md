# Streamlit Cloud 部署指南（免费云数据库版）

> 目标：把系统部署到 Streamlit Community Cloud（免费），数据存 Supabase PostgreSQL（免费），
> 得到公网网址 `https://xxx.streamlit.app`，用户在平板/电脑浏览器注册后即可试用，数据重启不丢。
> 前置：GitHub 仓库 `efa-mvp` 已建；Supabase 项目已建（Active）；本机已装 Git。

---

## 0. 原理解释（大白话）
```
用户浏览器 → https://xxx.streamlit.app（免费网页）
                  ↓ 读密钥
            Streamlit 应用（你 GitHub 上的代码）
                  ↓ 存/取数据
            Supabase PostgreSQL（免费云数据库，重启不丢）
```
密钥（数据库连接串）填在 Streamlit Cloud 的 Secrets 里，**不在代码里、不在 Git 里**。

---

## 1. 上传代码到 GitHub（你本机操作）

### 1.1 生成本地仓库并推送
在 PowerShell 中**逐条**执行（项目根目录）：

```powershell
cd "C:\Users\10692\CodeBuddy\20261113101802"

# 首次使用需配置提交身份（只做一次）
git config user.name "Mikehsy"
git config user.email "你的GitHub注册邮箱"

# 初始化并提交
git init
git add .
git commit -m "MVP: 注册登录 + 多用户数据隔离 + Supabase 支持"

# 关联远程仓库（仓库地址是你建仓库时页面显示的那条）
git remote add origin https://github.com/Mikehsy/efa-mvp.git

# 推送（会弹窗或命令行要求认证 → 用 Personal Access Token，不是密码）
git branch -M main
git push -u origin main
```

### 1.2 认证用 Token（不是 GitHub 密码）
GitHub 已禁止用密码推送。需生成 Personal Access Token（PAT）：
1. GitHub 右上角头像 → `Settings` → `Developer settings` → `Personal access tokens` → `Tokens (classic)`。
2. `Generate new token (classic)` → 勾选 `repo` 权限 → 生成，**立刻复制保存**（只显示一次）。
3. 推送时用户名填 `Mikehsy`，密码处**粘贴 token**。
> Windows 若弹出「Git Credential Manager」窗口，用户名填 Mikehsy，密码填 token。

### 1.3 确认推送成功
浏览器刷新 `https://github.com/Mikehsy/efa-mvp`，能看到 `app.py`、`utils.py`、`requirements.txt` 等文件即成功。
> 注意：`data/`、`logs/`、`demo_users.json`、`.streamlit/secrets.toml` 已被 .gitignore 排除，不会上传（正确）。

---

## 2. 拿到 Supabase 数据库连接串

1. 进入 Supabase 项目 → 左侧 `Project Settings`（齿轮）→ `Database`。
2. 找到 **Connection string** 区块，切到 **`Session pooler`** 标签（端口 6543，适合无公网 IP 的部署）。
3. 复制连接串，形如：
   ```
   postgresql://postgres.<项目ref>:<数据库密码>@<地区>.pooler.supabase.com:6543/postgres
   ```
   - `<数据库密码>` 是你**建项目时设的数据库密码**（不是账号密码）。
   - 若忘记，Supabase → Project Settings → Database → 可重置数据库密码。

---

## 3. 在 Streamlit Cloud 部署

1. 打开 https://share.streamlit.io/ ，用 GitHub 账号登录（授权 Streamlit 访问你的仓库）。
2. 点 `New app`：
   - **Repository**：`Mikehsy/efa-mvp`
   - **Branch**：`main`
   - **Main file path**：`app.py`
3. 点 **`Advanced settings`** → **`Secrets`**，粘贴以下内容（把连接串换成你第 2 步拿到的）：
   ```toml
   EFA_USE_DB = "1"
   DATABASE_URL = "postgresql://postgres.<项目ref>:<数据库密码>@<地区>.pooler.supabase.com:6543/postgres"
   ```
   > ⚠️ 这是你唯一需要填密钥的地方。填完保存。
4. 点 `Deploy`。等待 1–3 分钟构建（首次会装依赖，较慢）。
5. 构建成功后显示 `Your app is live!` 和网址 `https://xxx.streamlit.app`。

---

## 4. 验收

1. 打开生成的网址 → 应看到「登录 / 注册新账号」页面。
2. 点「注册新账号」，注册一个测试账号 → 登录 → 进入系统，能看到 62 个奥运项目、评分、图表。
3. 再注册第二个账号 → 两账号各自录入数据 → 确认互相看不到（隔离生效）。
4. 重启应用（Streamlit Cloud → App settings → Reboot），重新登录 → 数据还在（持久化生效）。

---

## 5. 更新已部署的应用

以后我改了代码，你只需在 PowerShell 执行：
```powershell
cd "C:\Users\10692\CodeBuddy\20261113101802"
git add .
git commit -m "update"
git push
```
Streamlit Cloud 检测到 push 会**自动重新部署**。Secrets 不用重填。

---

## 6. 常见问题

- **部署失败 `ModuleNotFoundError: psycopg2`**：确认 `requirements.txt` 已含 `psycopg2-binary>=2.9,<3` 并已 push。
- **连不上数据库**：检查 Secrets 里 DATABASE_URL 是否正确、用的是 Session pooler 连接串、数据库密码没填错。
- **打开是登录页但注册后报错**：看 Streamlit Cloud 的 Logs（右上角 `⋯` → `Manage app` → `Logs`），把报错发我。
- **数据没隔离/串号**：确认 Secrets 里 `EFA_USE_DB="1"`，且用户是注册账号登录（不是演示账号）。
- **免费版休眠**：闲置一段时间后首次打开会慢（冷启动），属正常；付费可关休眠。
- **密钥安全**：DATABASE_URL 只在 Secrets 里，绝不写进代码或发给我。

---

## 7. 你负责 vs 我负责

| 事项 | 谁做 |
| --- | --- |
| GitHub PAT、推送代码、Streamlit Cloud 建 app、填 Secrets | 你 |
| 代码改造、bug 修复、功能迭代 | 我 |
| Supabase 项目/数据库密码管理 | 你 |
| 验收测试、收集用户反馈 | 你 |
