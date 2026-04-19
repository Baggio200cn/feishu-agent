# AI 协作指令（请先读完）

如果你是 Claude Code 读到这个文件：

1. **启动时**：完整读一遍本文件，告诉用户：
   "已了解 feishu-agent 项目状态。最近进展是 XXX。今天继续吗？"

2. **工作中**：发现关键决策、架构变更、踩到的坑，主动追加到对应章节。

3. **会话结束前**（识别 "今天到此" / "保存进展" / "总结" / "记录一下" 等触发词）：
   - 更新「当前进度」章节（✅已完成 / 🚧进行中 / 📋待办）
   - 把今天的决策追加到「关键决策记录」表
   - 列出「下次会话要做的事」
   - 如果踩了新坑，补进「已踩过的坑」
   - 完成后告知用户已保存

4. **安全铁律**：
   - 绝不要求用户把 `config/credentials.json` 的值贴到对话里。已知泄漏过一个 App Secret（`ViAwj...`），该密钥必须视为永久作废。
   - 本项目的所有凭证（飞书 app_secret、豆包 api_key、GitHub token）**只存于用户本地** `config/credentials.json`（已在 `.gitignore`），沙箱里不应出现真实值。
   - 凡需要用户填配置，给占位模板让他自己填；凡用户已贴出的密钥，提醒他立即重置。

5. **沙箱边界**：
   - Claude Code 运行在临时 Linux 容器里，读写本仓库文件、`git push`、调 HTTP 都 OK。
   - **不能**访问用户 Windows 桌面或他本地其他文件。凡需"放到桌面"，先写进仓库，让用户本地 `git pull` + `Copy-Item`。
   - 跨会话不保留任何状态，所有上下文来自本文件 + 仓库代码 + 当次会话。

---

# 项目背景

**一句话**：基于 lark-oapi Python SDK 的飞书智能管理 Agent，核心能力是**每天自动抓 GitHub Trending + Reddit AI 讨论 → 豆包 AI 生成中文摘要 → 按日期分目录归档到飞书 Wiki**；辅以文档分类整理、飞书邮件/日历/联系人管理。Electron 桌面界面一键触发。

**技术栈**：
- 后端：Python 3.8+，`lark-oapi>=1.5.3`（飞书 SDK）、`requests`、`beautifulsoup4`（Trending HTML 解析）、`APScheduler`（定时任务）
- AI：豆包 Seed 2.0 mini（`doubao-seed-2-0-mini-260215`），REST 调用，火山方舟 Ark API
- 前端：Electron 桌面壳（HTML+JS，高 DPI 字体优化），IPC 调 Python CLI
- 配置：JSON 单文件（`config/credentials.json`），gitignore
- 存储产出：飞书 Wiki（docx 节点 + 子页面树）

**主要功能模块**：
1. **GitHub Trending 日报**（步骤 A）：爬 `/trending` → 抓 README → 豆包摘要 → 写 Wiki 的 `github专区/日期文件夹/仓库子页`
2. **Reddit AI 日报**（步骤 B）：4 个订阅 subreddit 的 top-of-day 帖子 + 高赞评论 → 豆包摘要 → `reddit专区/日期文件夹/帖子子页`
3. **调度器**（步骤 C）：APScheduler 守护进程，cron 每日 09:00/23:00 触发
4. **文档整理**（原功能）：扫飞书 Wiki → AI 分类 → 归档到个人 Wiki
5. **飞书管理**（原功能）：邮件、IM、日历、联系人的 CLI 管理
6. **桌面 UI**：Electron 窗口按钮触发后台 Python 任务，卡片读状态文件展示真实数据

---

# 当前进度

**最后更新**：2026-04-19 晚

## ✅ 已完成

### 核心日报流（步骤 A + B）
- GitHub Trending 日报：`/trending` HTML → 豆包摘要 → 飞书 `github专区/日期/仓库子页`
- Reddit AI 日报：4 subreddits top-of-day → 豆包 4 段式摘要 → 飞书 `reddit专区/日期/帖子子页`
- 封面页显示每个帖子完整中文摘要（不止一句话标题）
- 缓存续跑：`logs/{trending,reddit}_cache_YYYY-MM-DD.json`，失败项自动重试（不用 `--force`）
- 父节点 `github专区` / `reddit专区` 不存在自动在空间根下创建

### 调度器（步骤 C）
- APScheduler BlockingScheduler + cron（Asia/Shanghai）
- 守护进程独立于 UI，关 UI 不停调度
- `logs/scheduler_state.json` 实时状态 + PID 文件

### UI 全按钮真联通（生产化冲刺）
| 按钮 | 状态 |
|-----|-----|
| 对话助手「打开」| ✅ modal 弹层，Wiki 搜索 + 豆包回答 |
| GitHub 立即执行 / Wiki / 日志 | ✅ 三按钮全真调 |
| Reddit 立即执行 / Wiki / 日志 | ✅ 三按钮全真调 |
| Wiki 整理 预览 / 执行 | ✅ dry-run 路径真通 |
| Wiki 清理 预览 / 执行 | ✅ 新加：前缀搜 + 二次确认删除 |
| 调度开关 | ✅ spawn/taskkill |

### 对话助手（MVP）
- `python main.py chat "关键词" --json` → Wiki v1 SearchNode + 豆包 200 字回答
- UI 弹层带 spinner + 相关页面链接
- 豆包未配置时降级为仅列搜索结果

### Wiki 清理（新）
- `python main.py cleanup-wiki --prefix X [--confirm] [--json]`
- lark-oapi 没包 DeleteSpaceNode，用 raw REST `DELETE /wiki/v2/spaces/:sid/nodes/:tk`
- 三重防误删：prefix 必填 · 默认 dry-run · UI confirm() 二次弹窗

### 诊断脚本四件套
- `scripts/diag_feishu.py` — Wiki 权限分 4 步测
- `scripts/diag_doubao.py` — 豆包 API Key / 模型名
- `scripts/diag_wiki_tree.py` — 列 Wiki 顶层节点
- `scripts/diag_proxy.py` — 扫 VPN 本地代理端口
- `scripts/diag_ui.py`（新）— 不开 UI 逐个验证 10 个按钮对应的 CLI 动作

### 已在用户本地端到端验证
- GitHub 日报（2026-04-18, 2026-04-19 各一次，格式 OK）
- Reddit 日报（2026-04-19，Veee VPN 15236 端口 + 换节点 + 浏览器 UA 后成功，10 条帖子 + 中文摘要全部写入飞书）

## 🚧 进行中

生产化冲刺已全部 commit 推送，代码层面无遗留。用户本地需要：
1. `git pull` 最新代码（commits `8208962` / `50b2707` / 含 Phase C 的待推）
2. 如要 UI 里跑 Reddit：必须在**启动 UI 前**的终端里设 `$env:HTTP_PROXY = "http://127.0.0.1:15236"`，否则 UI spawn 出来的 python 继承不到代理
3. 测试 UI 所有按钮，确认无回归

## 📋 待办（低优先级，生产后再推）

- 打包 `.exe`（electron-builder），免用户装 Node
- Wiki 反向校验去重（手删 Wiki 后同步本地索引）
- 对话助手升级为"对话历史"模式（现在是单轮）
- Reddit 改为接入 Reddit OAuth 提升限频配额（目前匿名 10 req/min）
- 企业账号文档 organize 暂以"引用说明页"兜底，跨租户复制需额外授权

---

# 关键决策记录

| 日期 | 决策 | 原因 |
|------|------|------|
| 2026-04-17 | UI 用 Electron 而非 tkinter | 用户 HTML 模板本身就是 Electron IPC 风格；之前 tkinter 尝试（commit `76b3ec6`）已被回滚 |
| 2026-04-17 | 图标改为用户头像 `ui/icon.png` | 用户桌面快捷方式就用这张，标题栏保持一致 |
| 2026-04-17 | 飞书侧全用 lark-oapi SDK 不迁 REST | 用户提出"是不是该用 REST"，自查发现 SDK 覆盖齐全且自带 token 刷新/错误码映射，REST 等价复杂度但无额外收益 |
| 2026-04-18 | 调度器独立守护进程（非内嵌 Electron） | 关 UI 后定时任务仍要运行；通过 `spawn detached + proc.unref()` + PID 文件做跨进程控制 |
| 2026-04-18 | GitHub 抓取改为 Trending HTML（爬 + BS4）| 用户要求，静态 `repo_list` 信息密度差；GitHub 无官方 Trending API |
| 2026-04-18 | AI 用豆包 Seed 2.0 mini 而非 Pro | 每日成本 < 0.01 元（40K token × 0.2 元/百万），中文摘要质量够用，Pro 待必要时再切 |
| 2026-04-18 | 豆包模型 ID 直接用 `doubao-seed-2-0-mini-260215`（不走推理接入点） | 用户控制台截图显示该模型支持直接模型名调用，省创建 `ep-xxx` 的步骤 |
| 2026-04-18 | GitHub 日报改为"文件夹 + 子页"布局 | 用户原 reference 就是这种结构；搜仓库名可直达单页，不用在长页里翻 |
| 2026-04-18 | 父节点找不到时自动在空间根下创建 | 用户清理 Wiki 时会误删 github专区；不能硬依赖用户手动维护 |
| 2026-04-18 | Block 构造改用 typed `Block.builder()` | 错误码 1770001 "invalid param" 实测是 divider block 缺 `divider:{}` 字段；typed builder 自动补齐 |
| 2026-04-19 | Reddit 用 `.json` 端点而非 RSS | 虽然用户提老版本用 RSS，但 JSON 能一次拿到 score/num_comments/selftext 等排序依据，RSS 只有摘要 |
| 2026-04-19 | Reddit 每 sub 抓 10 条再按 score 降序取 top 10 | 保信息密度 + 全局热度排序（不均匀分配给每个 sub） |
| 2026-04-19 | Reddit 抓评论只取 t1 顶层前 3 条 | 深度 1 + limit 3，评论体截断 800 字，防 token 炸 |
| 2026-04-19 | `reddit_cache` 和 `trending_cache` 共用加载/保存函数 | 格式同构（`[{item, summary_ok: bool}, ...]`），续跑逻辑可复用 |
| 2026-04-19 | Reddit UA 从 `feishu-agent/0.1` 改为 Chrome 120 浏览器 UA + `Accept: application/json` | 简陋 UA 被 Reddit 重定向到反爬 HTML 页（status 200 但 content-type=text/html）|
| 2026-04-19 | AI 缓存读取时过滤 `summary_ok=false` | 失败项原本会被当"已缓存"永久跳过，现改为自动重试 |
| 2026-04-19 | Wiki 清理用 raw REST 而非 SDK | lark-oapi 1.5.3 未包 `DeleteSpaceNode`；飞书 REST 的 `DELETE /wiki/v2/spaces/:sid/nodes/:tk` 存在且工作 |
| 2026-04-19 | Wiki 清理的三重防误删（prefix 必填 / dry-run 默认 / UI confirm 弹窗）| 删 Wiki 节点不可恢复；单一保障不够 |
| 2026-04-19 | 对话助手 MVP 先做"搜+总结"单轮，不做对话历史 | 多轮需要状态持久化，目前 JSON 单次调用够用 |

---

# 已踩过的坑

1. **Revert 过一次 tkinter GUI**（`76b3ec6` → `975e684`）：方向不对，用户要的是 Electron。
2. **Windows npm 装 Electron 二进制 ECONNRESET**：国内网络从 github.com/electron/releases 下载被墙；解决：`$env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"`。
3. **`.bat` 内容当 PowerShell 命令粘贴导致解析失败**：`@echo off`、`%~dp0` 是批处理语法，必须写进文件运行，不能逐行敲进 PS。
4. **logs 目录不存在导致 logging.FileHandler 在模块加载时崩溃**：`os.makedirs("logs", exist_ok=True)` 必须提到 `logging.basicConfig()` 之前。
5. **APScheduler 3.11 `Job.next_run_time` 在 start 前不存在**：defensive `getattr(job, "next_run_time", None)` + 注册 `EVENT_SCHEDULER_STARTED` 监听器再补写 state。
6. **Windows 下 `process.kill(pid, 'SIGTERM')` 实际是 TerminateProcess**：Python 信号处理器不会触发，改用 `taskkill /PID <pid> /T /F`。
7. **lark-oapi 1.5.3 类名变更**：`CreateSpaceNodeRequestBody` 改叫 `Node`，`BatchCreateDocumentBlockChildren` 改叫 `CreateDocumentBlockChildren`，`obj_type` 从 `"doc"` 改 `"docx"`。
8. **App ID 给错了**（`cli_a921...` vs 实际 `cli_a943...`）：诊断脚本 ListSpace 全部拒绝，才定位到用户填的 app_id 根本不对应他手里配权限的应用。
9. **用户把一个 App Secret 粘贴到了会话里**：`ViAwj...`（对应僵尸 app `cli_a921...`），必须永久作废并重置；已在 CLAUDE.md 顶部作为安全铁律记录。
10. **飞书权限是"版本绑定"的**：权限管理页显示"✅已开通"只代表草稿，真正生效依赖"版本管理与发布"里在线版本声明了什么。勾权限后必须重新创建版本并发布。
11. **飞书 docx 写块 1770001 "invalid param" 误导**：错误 body 完全没有字段名，必须打印 resp.raw 才定位到 divider block 缺 `divider:{}`；根因是 dict 构造 block 省了"空对象子字段"。
12. **豆包 Seed 2.0 响应慢**：reasoning 模式下响应常超 90 秒，默认 timeout 已调到 180s + 1 次重试，仍有 10% 左右超时率。
13. **Reddit 中国大陆被墙**：必须挂 VPN，代码依赖 `HTTP_PROXY` / `HTTPS_PROXY` 环境变量。用户首次试时代理端口写错（7890 vs 实际 12536），`WinError 10061 目标计算机积极拒绝`。
14. **飞书 Wiki 节点标题含特殊字符**：Reddit 帖子标题常含 `/` `:` `*` `?`，直接用作 Wiki 节点标题会被拒；已在 `write_daily_reddit_report` 里 `re.sub(r"[\\/:*?\"<>|]", " ", ...)` 清洗，且截断到 80 字。
15. **Reddit 反爬**：2026-04-19 日实测 `User-Agent: test/0.1` 或 `feishu-agent/0.1` 触发 Reddit 反爬，响应变成 HTML（`<body class=theme-beta>...`），code 仍是 200 但不是 JSON。必须用浏览器 UA + `Accept: application/json` 才拿得到正常 JSON。
16. **Veee VPN 的诡异端口**：用户的 VPN 客户端（Veee）监听在 `15235` / `15236`，不在常见代理端口列表里。`scripts/diag_proxy.py` 扫 14 个常见端口全空时，让用户跑 `Get-NetTCPConnection -State Listen` 按进程名定位（找到 `Veee` 进程名）。
17. **Reddit 节点 IP 被拉黑**：即使 UA 对了，部分 VPN 节点的出口 IP 在 Reddit 黑名单，返回 403 + HTML。换 Veee 里的节点（美国/日本/新加坡）即可。
18. **`open-chat` IPC 要异步返回 JSON**：UI 里对话助手的 modal 期待 `result.chat = {answer, sources, status, message}`，`ui/main.js::actionDispatch['open-chat']` 用 `extractJsonFromStdout` 从 Python 的 `--json` 输出尾部拎 JSON 对象。Python 日志（开头的 INFO 行）不影响解析。

---

# 重要文件位置

## 入口 & 配置

| 文件 | 作用 |
|------|------|
| `main.py` | CLI 入口，所有子命令（`organize` / `import-github` / `import-reddit` / `schedule` / `schedule-status` / `manage`） |
| `config/credentials.json` | 用户凭证（gitignored）：飞书 app / 豆包 api_key / GitHub token / subreddits / 调度配置 |
| `config/credentials.json.example` | 模板，带 `_comment` 注释说明每个字段 |
| `requirements.txt` | Python 依赖 |
| `package.json` | Electron 依赖（只有 `electron` devDependency） |
| `start-ui.bat` | 桌面快捷方式入口（本地生成，未入库） |

## 业务代码

| 文件 | 作用 |
|------|------|
| `src/utils/feishu_client.py` | lark-oapi Client 工厂（双账号：personal / enterprise） |
| `src/utils/config_loader.py` | `credentials.json` / `categories.json` 读取 |
| `src/scheduler.py` | `FeishuScheduler`：APScheduler 封装，cron 调度 3 个任务 |
| `src/importers/github_trending.py` | GitHubTrending HTML 爬取 |
| `src/importers/github_importer.py` | GitHubImporter：单仓库元信息 + README + 核心文件抓取（含限频退避） |
| `src/importers/reddit_importer.py` | RedditImporter：多 subreddit top-of-day + 高赞评论（用 `.json` 端点） |
| `src/importers/ai_summarizer.py` | AISummarizer：豆包 REST 封装，`summarize_repo` + `summarize_reddit_post` |
| `src/importers/feishu_doc_writer.py` | FeishuDocWriter：`find_node_by_title` / `write_daily_trending_report` / `write_daily_reddit_report`，typed Block builder |
| `src/organizer/*.py` | 文档扫描、AI 分类、归档（原功能） |
| `src/managers/*.py` | 邮件、IM、日历、联系人管理（原功能） |

## UI

| 文件 | 作用 |
|------|------|
| `ui/index.html` | 界面 + 高 DPI 样式 + 前端 JS |
| `ui/main.js` | Electron 主进程：`spawn python main.py`，调度器进程管理，状态文件轮询 |
| `ui/preload.js` | contextBridge 暴露 `feishuAgent.invoke` / `onStateUpdate` |
| `ui/icon.png` | 用户自定义头像（本地放置，未入库） |

## 诊断

| 文件 | 作用 |
|------|------|
| `scripts/diag_feishu.py` | 分 4 步测飞书权限（ListSpace / GetSpace / CreateSpaceNode）|
| `scripts/diag_doubao.py` | 测豆包 API Key + 模型名 |
| `scripts/diag_wiki_tree.py` | 列 Wiki 顶层节点，精确/模糊匹配 `parent_folder` |

## 运行时产出（gitignored）

| 文件 | 作用 |
|------|------|
| `logs/feishu_agent.log` | 全局日志 |
| `logs/scheduler_state.json` | 调度器状态（running / PID / 下次触发时间 / 上次运行结果）|
| `logs/scheduler.pid` | 守护进程 PID |
| `logs/trending_cache_YYYY-MM-DD.json` | 当日 GitHub AI 摘要缓存（续跑避免重复烧豆包）|
| `logs/reddit_cache_YYYY-MM-DD.json` | 同上 for Reddit |
| `logs/github_last_run.json` | 最近一次 GitHub 运行统计（UI 读这个展示）|
| `logs/reddit_last_run.json` | 同上 for Reddit |
| `logs/github_imported.json` | Legacy 流程的去重索引（trending 流程已不用）|

---

# 下次会话要做的事

1. **解决 VPN 代理问题跑通 Reddit**：用户本地端口实际是 `12536` 还是别的待确认；确认后一条 `python main.py import-reddit` 验证端到端，贴飞书子页截图确认 AI 摘要质量
2. **UI 卡片对接新数据源**：`ui/main.js::buildUiState` 读 `reddit_last_run.json` 补 Reddit 真实状态；GitHub 卡片文案里加 `wiki_folder_token` 链接
3. **豆包超时率优化**：README 截断从 8K → 4K 再试；或测试其他豆包模型的响应时延
4. **打包 `.exe`**：`electron-builder` 配置 + 内置 Python 运行时，用户不用装 Node
5. **对话助手 MVP**：`open-chat` 按钮目前是占位；最小实现 = 用户输入关键词 → 豆包 + Wiki ListSpaceNode 检索 → 返回相关页 URL

---

# Git 工作流

- **开发分支**：`claude/feishu-agent-interface-HiudF`
- **远程**：`baggio200cn/feishu-agent`（GitHub）
- 每次提交都附 `https://claude.ai/code/session_xxx` URL 以便追溯
- commit message 按 conventional commits：`feat(scope): ...` / `fix(scope): ...` / `chore: ...` / `docs: ...` / `refactor: ...`
- 用户未授权前不合并到 main、不创建 PR、不删除远程分支

---

*本文件由 Claude 在 2026-04-19 会话中创建，后续会话请按顶部 AI 协作指令维护。*
