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

1. Supabase 项目右上角点 **`Connect`**（或 `Project Settings` → `Database` → Connection string）。
2. 顶部选 **`Direct` / `Connection string`** → Connection Method 选 **`Session pooler`**
   → Type 选 **`URI`**（就是你截图那一页）。
3. 复制连接串，形如：
   ```
   postgresql://postgres.<项目ref>:[YOUR-PASSWORD]@<地区>.pooler.supabase.com:5432/postgres
   ```
   - **Session pooler 端口是 5432**（6543 是 Transaction pooler，不要混用）。
   - 把 `[YOUR-PASSWORD]` 替换成你**建项目时设的数据库密码**（不是账号密码）。
   - 忘记密码：点连接串旁边的 **`Reset database password`** 重置（截图里那个铅笔按钮）。
4. ⚠️ **密码含特殊字符必须 URL 编码**：`@ : / # ? %` 等要转成 `%40 %3A %2F %23 %3F %25`，
   否则连接会失败。最省事的做法：重置数据库密码时**只用字母和数字**，就不会有编码问题。

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
   DATABASE_URL = "postgresql://postgres.<项目ref>:<数据库密码>@<地区>.pooler.supabase.com:5432/postgres"
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
5. 打开「🔒 隐私与账号」：确认注册时须逐项勾选 4 项同意、导出 JSON 不含密码凭据、
   撤回同意后进入「重新同意」、注销后该账号无法登录。8 步验收见
   `docs/PIPL合规实施说明.md` 第七节。

---

## 5. 更新已部署的应用

> ⚠️ **网页上传时务必注意目录层级**：GitHub 的 `Upload files` 只会把文件传到
> **当前所在目录**。若 `pages/` 下的页面有改动，必须**先点进 `pages` 文件夹**再上传；
> 在仓库根目录上传同名文件会另建一份，真正的页面文件仍是旧版（表现：部分文字
> 已修复、子页面里的文字却仍是旧文案/带删除线）。

**需要同步的目录结构**
```
仓库根目录/  app.py  utils.py  db.py  auth.py  privacy.py  reports.py  requirements.txt
         └─ pages/  1_👥运动员管理.py  2_📏测试与评估.py  3_📊数据分析.py
                    4_⚙️评分标准.py  5_📝分析报告.py  6_🔒隐私与账号.py
         └─ .streamlit/  config.toml  secrets.toml.example
         └─ docs/  PIPL合规实施说明.md（文档，可不上传）
```

> PIPL 版本新增 `auth.py`、`privacy.py` 与 `pages/6_🔒隐私与账号.py`，并更新了
> `db.py`、`utils.py`。**新文件缺失会白屏**，务必确认仓库里确实存在。
> Secrets 还需新增 7 个 `EFA_*` 配置（见 `docs/PIPL合规实施说明.md` 第六节）；
> 未填真实运营主体信息时，注册页与政策页会持续显示警示。

**上传后自检**：在 GitHub 上逐个打开改动过的文件，用 `Ctrl+F` 搜本次特征串
（例如 `～`、`pg_advisory_xact_lock`、`RETURNING id`），确认云端内容与本地一致，
再 `Reboot app`。


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

- **首个用户注册就报错 / 白屏**：已修复——数据库连接时会自动幂等建表，云端无需手工初始化。
  若仍报错，看 Logs 确认 DATABASE_URL 是否指向可写的 Supabase 项目。
- **报 `psycopg2.errors.UniqueViolation`**（旧版会出现）：原因是并发会话同时执行
  `CREATE TABLE IF NOT EXISTS`，PostgreSQL 系统目录撞唯一索引。已改为「进程内只建一次 +
  `pg_advisory_xact_lock` 串行化 + 冲突兜底」，并支持残留半张表时自动补齐。
  看到此报错说明云端仍是旧代码，重新 push 并 `Reboot`。
- **注册时抛 `AttributeError` / `'NoneType' object has no attribute 'fetchone'`**：
  原因是 `sqlite3.execute()` 返回 cursor，而 `psycopg2.execute()` 返回 `None`，旧代码
  `return cur.execute(...)` 在 PG 下拿到 None。已改为显式返回 cursor，并新增
  `tests/test_db_pg_compat.py`（模拟 psycopg2 行为的 3 项回归测试，含变异验证）。
  看到此报错说明云端 `db.py` 仍是旧版，需重新上传/推送并 `Reboot`。
- **本地测试全绿但云端崩**：本地默认跑 SQLite，无法覆盖 PG 差异。改动 `db.py` 时
  必须同时跑 `python -m unittest tests.test_db_pg_compat -v`。
- **每次操作都很慢 / 页面变灰很久**：原因是每次读表都新建跨洋数据库连接、且同一页面
  重复查库。已做三层优化：psycopg2 连接池复用、初始化按会话只做一次、按
  `(用户ID, 表类型)` 缓存读表（写入立即失效）。实测 9 次读表只查库 3 次。
  页面变灰是 Streamlit 重跑脚本的正常表现，优化后应明显变快。
- **文字被横线划掉 / `4~6` 显示成 `46`**：半角 `~` 被 Markdown 解析成删除线定界符。
  显示文本已统一改为全角 `～`（代码里的位运算 `~df` 保留）。由
  `tests/test_display_tilde.py` 锁定防回归。
- **打开显示 host/CORS 相关错误**：`.streamlit/config.toml` 已移除写死的 `localhost` 绑定并收紧 CORS；
  如平台仍报 host 不允许，在该文件 `[server]` 段加 `allowedHosts = ["你的 app 域名"]`。
- **页面提示"数据保存在 data 文件夹"**：那是旧版本文案；现版本在 DB 模式下会显示
  "数据按账号独立保存在云端数据库"。若看到旧文案，说明云端拉的是旧代码，重新 push 并 Reboot。
- **部署失败 `ModuleNotFoundError: psycopg2`**：确认 `requirements.txt` 已含 `psycopg2-binary>=2.9,<3` 并已 push。
- **连不上数据库**：检查 Secrets 里 DATABASE_URL 是否正确、用的是 Session pooler 连接串、数据库密码没填错。
- **打开是登录页但注册后报错**：看 Streamlit Cloud 的 Logs（右上角 `⋯` → `Manage app` → `Logs`），把报错发我。
- **数据没隔离/串号**：确认 Secrets 里 `EFA_USE_DB="1"`，且用户是注册账号登录（不是演示账号）。
- **免费版休眠**：闲置一段时间后首次打开会慢（冷启动），属正常；付费可关休眠。
- **密钥安全**：DATABASE_URL 只在 Secrets 里，绝不写进代码或发给我。
- **升级后所有老账号首次登录被要求「重新同意」**：这是隐私政策首次生效 / 版本变更的
  预期行为，不是 bug。政策版本由 `EFA_POLICY_VERSION` 控制，改一次就全员重新同意一次。
- **注册页 / 政策页显示「【待填写：运营主体全称】」警示**：说明 Secrets 里
  `EFA_OPERATOR_NAME`、`EFA_PRIVACY_CONTACT` 尚未填真实值。试用不受影响，
  但正式商用前必须由运营方填写并经法务审阅，见 `docs/PIPL合规实施说明.md` 第八节。
- **新增页面 6 打开白屏 / 报 ModuleNotFoundError: privacy**：`privacy.py` 或
  `auth.py` 没上传，或 `pages/6_🔒隐私与账号.py` 被误传到仓库根目录。按第 5 节目录结构核对。

---

## 7. 你负责 vs 我负责

| 事项 | 谁做 |
| --- | --- |
| GitHub PAT、推送代码、Streamlit Cloud 建 app、填 Secrets | 你 |
| 代码改造、bug 修复、功能迭代 | 我 |
| 技术运维保障方案、巡检表、备份恢复演练模板、安全事件应急预案模板、问题登记表 | 我 |
| Supabase 项目/数据库密码管理 | 你 |
| 验收测试、收集用户反馈 | 你 |
| 按巡检表执行日常巡检、按模板执行备份恢复演练、按模板演练安全事件 | 你（运维/运营方） |
| 填写真实运营主体名称、个人信息保护负责人联系方式、保存期限与去标识化盐值 | 你（运营方） |
| 法务审阅、PIA、出境合法路径、未成年人监护人同意留档、应急预案责任签字 | 你（运营方 / 法务） |
