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

**最后更新**：2026-05-11

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
- 也支持 `--node-tokens tok1,tok2` 精准删（2026-04-20 新增，给 Agent 用）
- lark-oapi 没包 DeleteSpaceNode，用 raw REST `DELETE /wiki/v2/spaces/:sid/nodes/:tk`
- **1061004 forbidden 兜底**（2026-04-20）：wiki DELETE 失败时自动切到
  `DELETE /drive/v1/files/:obj_token?type=docx` 删底层 docx（wiki 节点随之消失）
- 三重防误删：prefix 必填 · 默认 dry-run · UI confirm() 二次弹窗

### Wiki 管家 Agent（2026-04-20 新增，2026-04-21 大幅增强）
- `python main.py agent-chat --query "..." [--session sess-xxx] [--reset] --json`
- `python main.py scan-empty-wiki [--threshold 8] [--max 500] --json`
- 模块 `src/agent/wiki_agent.py`：意图路由（search / scan_empty / delete_empty /
  **mark_empty** / cleanup_prefix / suggest / confirm / cancel）+ 全树扫描
  + 空节点检测（读 docx raw_content 按字数判定，跳过 `has_child=True` 目录父页）
  + **真删只走 drive**（wiki DELETE 端点不存在）+ **🗑[空] 改名替代方案**
  + 会话持久化（logs/agent_sessions/{id}.json）+ **last_scan 5 分钟缓存**
  + **SSL 重试封装**（3 次 backoff 1/2.5/5s）
- **suggest 意图接豆包 AI**：扫全树 → 顶层目录+子节点计数+样本标题 JSON → 豆包返回
  4-7 条引用具体目录名的中文整理建议（规则版 fallback）
- **search 智能降级**：长句（>6 字）整句无命中时自动拆关键词搜；仍 0 命中
  时引导到 suggest（"这更像整理建议"）
- UI 弹层"对话 Agent · Wiki 管家"：多轮对话 + 预览卡片 + 底部确认栏

### 网络稳定性（2026-04-21 硬仗）
- `main.py` 顶部内置 NO_PROXY 豁免飞书 + 豆包域名（Veee VPN 开着也能跑）
- `src/importers/feishu_doc_writer.py` 4 个 SDK 调用点全部套 `_sdk_retry`
  （SSL / Connection / Timeout 3 次重试，backoff 1.5/3.5/7s）
- Wiki 写入失败时明确打印代理排查命令 + 缓存路径 + 续跑指引
- 豆包摘要每条完成都落盘 `logs/{trending,reddit}_cache_YYYY-MM-DD.json`，
  Wiki 写入失败不会烧 Token

### Reddit 评论分组（2026-04-21）
- `_fetch_top_comments` 返回带 `is_stickied` / `is_op` 标记，三类分组:
  📌 置顶 / ✍️ 作者 OP 补充 / 💬 高赞讨论+质疑
- 默认 limit 从 3 → 6；置顶和 OP 不占 limit 额外保留
- 豆包 prompt 五段输出：背景 / 核心观点 / 技术要点 / 置顶/作者补充 / 质疑与讨论
- 飞书 Wiki 子页按三组独立 H2 渲染

### 调度器补跑（2026-04-21）
- 启动时读上次 `logs/scheduler_state.json` 里的 last_runs
- 遍历所有 enabled job，若当天 cron 点已过 + 今天未跑过，自动安排
  一次性 `run_date=now+5s` 补跑
- 用户任意时间启动调度器都能当天至少跑一次；重复跑会按日期跳过

### 老巴疯啦灵感流（2026-05-11 新增）
**北极星定位**:
- A = 海运/跨境电商客户线索的 AI Agent（B2B Sales Intelligence + Web Scraping + Intent Signal）
- B = 颠覆人类内容消费的范式级 AI 形态，候选 B1/B2/B7:
  - B1 生活即内容（lifelog auto-narrative）
  - B2 平行人生（alternate life simulation）
  - B7 孤独消除（continuous AI companion 替代"内容产业"）

**老巴人设**: 50 岁是幽默（实际接近退休的体制内闲职），对 AI 极度风靡
（痴迷到老婆嫌弃），说话带点东北味儿，爱讲段子，判断犀利但不咬文嚼字。
看潮流是"老司机看小年轻"——激情但不端着。

**抓取源**:
- 7 个垂直 subreddit: Webscraping / DataHoarder / FulfillmentByAmazon /
  dropshipping / coldemail / sales / LocalLLaMA（保留 1 个技术池）
- GitHub Trending + 15 个关键词白名单过滤（scraper/crawler/lead/intent/
  b2b/clearbit/apollo/outreach/enrichment/prospecting/crm/linkedin/
  whois/shipping/customs）

**摘要五维度**（豆包用老巴人设做头脑风暴而非客观摘要）:
1. one_liner — 老巴一句话毒舌定性（封面页用）
2. dim_a_freight_bd — 对北极星 A 货代 BD agent 启发
3. dim_b_content_disruption — 对北极星 B（B1/B2/B7）启发
4. dim_zoom — 放大 100 倍 / 缩小 100 倍
5. dim_invert — 反过来做（核心假设取反）
6. laoba_verdict — 老巴的疯狂判断（能成/悬/纯炒作/必爆/已经过气）

**CLI**: `python main.py import-laoba-feng [--force] [--json]`
**调度**: 默认 09:30 触发（github/reddit 都跑完后再跑）
**Wiki 路径**: `老巴疯啦/老巴疯啦 YYYY-MM-DD/N. [r/sub或GH] 标题`
**封面页**: 🔥 老巴今日精选 Top 3（按 laoba_verdict 含"能成/有戏/必爆"过滤）
  + 📋 全部素材一句话清单
**缓存**: `logs/laoba_cache_YYYY-MM-DD.json` 同结构续跑（reddit 用 post.id 去重，
  github 用 full_name 去重）

### 诊断脚本四件套
- `scripts/diag_feishu.py` — Wiki 权限分 4 步测
- `scripts/diag_doubao.py` — 豆包 API Key / 模型名
- `scripts/diag_wiki_tree.py` — 列 Wiki 顶层节点
- `scripts/diag_proxy.py` — 扫 VPN 本地代理端口
- `scripts/diag_ui.py`（新）— 不开 UI 逐个验证 10 个按钮对应的 CLI 动作

### 已在用户本地端到端验证
- GitHub 日报（2026-04-18, 2026-04-19, **2026-04-21 VPN 开着无报错**）
- Reddit 日报（2026-04-19 首次，**2026-04-21 完整三分组评论 + NO_PROXY 豁免生效**）

## 🚧 进行中

无阻塞性遗留。2026-04-21 端到端打通：

- **GitHub 日报**：[2026-04-21 文件夹](https://open.feishu.cn/wiki/UXvHwZ0KoiGhyUkpJphcrqnknne) 10 页全新建
- **Reddit 日报**：[2026-04-21 文件夹](https://open.feishu.cn/wiki/L6aQwXuhki39Vvk1v6hclAXqnrf) 10 页全新建，含三组评论分区

## 📋 待办（低优先级）

- 打包 `.exe`（electron-builder），免用户装 Node
- Wiki 反向校验去重（手删 Wiki 后同步本地索引）
- Reddit 改为接入 Reddit OAuth 提升限频配额（目前匿名 10 req/min）
- 企业账号文档 organize 暂以"引用说明页"兜底，跨租户复制需额外授权
- Agent 接 LLM 意图分类（现在靠关键词规则，覆盖面不够时会误判为 search）

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
| 2026-04-20 | 对话助手升级为"Wiki 管家 Agent"（意图路由 + 多轮 + 确认执行） | 实战用户需要连续做"扫空 → 确认 → 删除"，单轮不够用；会话存文件够用 |
| 2026-04-20 | 空节点判定=读 docx raw_content 按字数阈值（≤8 视为空） | `/docx/v1/documents/:id/raw_content` 稳定，能直接拿纯文本；块遍历 API 复杂度高 |
| 2026-04-20 | ~~Wiki 节点删除失败（1061004）兜底到 drive DELETE~~ | **已废**。后续实测 wiki DELETE 本身就是 404（飞书根本没这个端点，我编的），drive DELETE 对个人 Wiki 的用户拥有的 docx 永远 forbidden。这是 API 硬限制。|
| 2026-04-20 | Agent 意图路由用关键词规则而非 LLM | 中文意图词集合小（删空/找空/前缀/建议/确认/取消），规则既快又不花 token |
| 2026-04-20 | 空节点删不了时改走"改名加 🗑[空] 前缀"作为替代方案 | 飞书 API 不给删用户个人 Wiki 空节点，但 UpdateTitleSpaceNode 权限宽得多。改名后用户在 Wiki UI 搜 🗑 可以一眼圈出批量选删。务实解。|
| 2026-04-20 | 空节点检测跳过 `has_child=True` 的节点 | 目录型父页本身正文也是空但子下还有内容，误删会丢一堆文档 |
| 2026-04-21 | `main.py` 启动时内置 NO_PROXY 豁免飞书 + 豆包域名 | 用户 Veee VPN 开着无法关（其他工作要用），走 VPN 导致 `open.feishu.cn` 和 `ark.cn-beijing.volces.com` SSLEOFError。这两个是大陆直连域名，NO_PROXY 绕代理即可；Reddit 仍走代理不豁免。|
| 2026-04-21 | FeishuDocWriter 所有 SDK 调用套 `_sdk_retry`（3 次 backoff 1.5/3.5/7s） | 豆包摘要 10 条全跑完，最后一步 Wiki 写入 SSL 被切整个流程崩；SDK 调用点加重试能扛大部分偶发抖动。摘要缓存保证不重烧 Token。|
| 2026-04-21 | Reddit 评论抓成 `stickied` + `is_op` + `regular` 三类，豆包 prompt 拆两变量、detail 五段化 | 用户要求"每个帖子需要加入置顶评论的介绍和其他评论的质疑内容"。置顶评论通常是版规/OP 补充，和高赞讨论性质完全不同，混一起让豆包摘要不够清晰。|
| 2026-04-21 | 调度器启动时补跑当日遗漏任务（date 触发器 now+5s） | 用户 18:34 启动调度器，cron=09:00 早过了，APScheduler `misfire_grace_time=300s` 也早超，导致全天没任何任务触发。补跑逻辑让任意时间启动都能当天至少跑一次。|
| 2026-04-21 | suggest 意图扩触发词 20+ / search 降级 / 豆包 AI 整理建议 | "你觉得目录分类如何"这种自然语言被当 search，整句 substring 匹配当然 0 命中。扩触发词路由到 suggest，失败时拆关键词搜，最终降级到豆包基于真实树结构给分析。|
| 2026-04-24 | `from typing import Dict, Optional` 漏 `List`，summarize_reddit_post 内嵌函数注解 `List[Dict]` NameError | 上版重构 _render 时引入的回归 bug。教训: 写新代码动了类型注解先看 import 顺手补 List/Tuple/Any 等常用。|
| 2026-05-11 | "老巴疯啦"灵感流：独立流水线 + 老巴人设 + 五维度头脑风暴 prompt | 用户两大北极星（A 货代 BD agent / B 内容消费颠覆）需要持续灵感喂养，但客观摘要式日报激发不了发散思维。独立 pipeline 用人设化 prompt 把外网素材"硬扯"到北极星上，宁可扯也比客观要点列表有启发。 |
| 2026-05-11 | 老巴的"北极星 B"从"AI 真人剧变种"转为范式级颠覆（B1/B2/B7） | 用户反馈早期 8 个候选都是"剧"这个媒介下的变种，不是颠覆。换框思路：AI 时代根本不需要"剧"这种形态了，颠覆是"生活即内容/平行人生/孤独消除"这种范式级重构。Prompt 里明确这三个方向让豆包对齐。 |
| 2026-05-11 | 老巴日报 GitHub 抓取走"关键词白名单过滤"而非全量 | 老巴流要的是 B2B sales intelligence / data mining 信号，原 trending 流是泛技术日报，混抓会稀释信号。白名单 15 个 keyword（scraper/crawler/lead/intent/b2b/clearbit/apollo/outreach/enrichment/prospecting/crm/linkedin/whois/shipping/customs）只保命中项。 |

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
19. ~~`1061004 forbidden` 删 Wiki 节点~~：**原假设错误**。实际情况：飞书 Wiki v2 API **根本没有 delete-node 方法**（lark-oapi 1.5.4 只有 Create/Get/List/Copy/Move/UpdateTitle，无任何 Delete 类 Request）。我当初以为的 `DELETE /wiki/v2/spaces/:sid/nodes/:tk` 端点纯属编造，实测返回 `HTTP 404 page not found`。唯一能删的路径是 `DELETE /drive/v1/files/:obj_token?type=docx` 删底层 docx，但这要求应用对该 docx 有管理权限；个人 Wiki 下由用户本人创建的 docx，tenant_access_token 返 `1061004 forbidden` 是必然。**结论：加权限也绕不过，这是飞书 API 硬限制。**替代方案：用 `UpdateTitleSpaceNode` 给空节点标题加 🗑[空] 前缀，让用户在 Wiki UI 里肉眼批量选删（2026-04-20 下午落地）。
20. **Agent 会话状态必须持久化**：CLI 无常驻进程，每次调用都是新 Python 进程。用 `logs/agent_sessions/{session_id}.json` 存 `{id, history, pending_action}`，前端 localStorage 保 session_id 跨窗口可继。
21. **空节点误判——把目录型父页算空节点**：`detect_empty_nodes` 最初只看 docx 正文字数，结果像"学习笔记""工作记录"这种纯目录页（本身无正文但有子节点）全被误判为空。66 个"空节点"里大半是父目录。修复：`has_child=True` 的节点一律跳过。
22. **Veee VPN 全局代理劫持大陆域名 TLS**：用户 Veee VPN `HTTP_PROXY=http://127.0.0.1:15236` + 系统代理 `ProxyEnable=1 ProxyServer=localhost:15236`，所有 HTTPS 走 Veee。飞书 `open.feishu.cn` / 豆包 `ark.cn-beijing.volces.com` 走过去 TLS 握手被切（`SSLEOFError _ssl.c:1018`）。这两个大陆直连，不该走 VPN。修复是在 `main.py` 顶部把它们加进 `NO_PROXY` 环境变量，requests 自动绕过代理直连；Reddit 不豁免保持走 VPN。
23. **调度器启动时间 > cron 时间 = 全天不触发**：APScheduler 的 cron 触发器配合默认 `misfire_grace_time=300s`，过了触发点 5 分钟就永远不补跑当天。用户 18:34 启动，09:00 的 job 再没机会跑。修复是启动时手动读 last_runs + 当天已过 cron 点 + 今天没跑过 → 安排 date 触发器 `now+5s` 补跑一次。
24. **长句自然语言被当关键词 substring 匹配**：用户问"你觉得知识库目录分类有没有问题"被路由到 search 意图，用整句去 `title.lower() in query.lower()` 匹配，必然 0 命中。修复：classify_intent 扩触发词（"你觉得/整理思路/怎么分类/给建议"等 20+ 条），加上 search 降级（拆关键词 + 长句提示走 suggest）。
25. **`typing.List` 漏 import 导致 Reddit 摘要全失败**：04-21 重构 `_render` 内嵌函数时用了 `List[Dict]` 注解，但 `from typing import Dict, Optional` 漏掉 `List`。Python 3 解析函数定义时立即评估注解，导致 `summarize_reddit_post` 一被调用就 NameError，用户 04-24 跑 import-reddit 全 10 条都失败。教训：动了类型注解习惯性补全常用 `List/Tuple/Any/Set/Union`。

---

# 重要文件位置

## 入口 & 配置

| 文件 | 作用 |
|------|------|
| `main.py` | CLI 入口。**顶部内置 NO_PROXY 豁免飞书+豆包**。子命令：`organize` / `import-github` / `import-reddit` / **`import-laoba-feng`** / `schedule` / `schedule-status` / `manage` / `chat` / `agent-chat` / `scan-empty-wiki` / `cleanup-wiki` |
| `config/credentials.json` | 用户凭证（gitignored）：飞书 app / 豆包 api_key / GitHub token / subreddits / 调度配置 |
| `config/credentials.json.example` | 模板，带 `_comment` 注释说明每个字段 |
| `requirements.txt` | Python 依赖 |
| `package.json` | Electron 依赖（只有 `electron` devDependency） |
| `start-ui.bat` | 桌面快捷方式入口（本地生成，未入库） |

## 业务代码

| 文件 | 作用 |
|------|------|
| `src/agent/wiki_agent.py` | Wiki 管家 Agent 核心：意图路由 · 全树扫描 · 空节点检测 · 带 drive 兜底的安全删除 · 会话持久化 |
| `src/utils/feishu_client.py` | lark-oapi Client 工厂（双账号：personal / enterprise） |
| `src/utils/config_loader.py` | `credentials.json` / `categories.json` 读取 |
| `src/scheduler.py` | `FeishuScheduler`：APScheduler 封装，cron 调度 4 个任务（含 laoba_feng）+ 启动时补跑当日遗漏 |
| `src/importers/github_trending.py` | GitHubTrending HTML 爬取 |
| `src/importers/github_importer.py` | GitHubImporter：单仓库元信息 + README + 核心文件抓取（含限频退避） |
| `src/importers/reddit_importer.py` | RedditImporter：`.json` 端点，评论分 📌 stickied / ✍️ OP / 💬 regular 三类 |
| `src/importers/ai_summarizer.py` | AISummarizer：豆包 REST 封装，Reddit prompt 拆 `{stickied_comments}` + `{top_comments_text}` 两变量 detail 五段化；新增 `summarize_laoba_feng_item` 老巴人设 + 五维 prompt（temperature 0.85） |
| `src/importers/feishu_doc_writer.py` | FeishuDocWriter：typed Block builder；4 个 SDK 调用点统一走 `_sdk_retry` SSL 重试；Reddit 子页评论三组分区渲染；**`write_daily_laoba_feng_report` 封面页精选 Top3 + 五维 H2 子页** |
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
| `logs/agent_sessions/{sess-xxx}.json` | Wiki 管家 Agent 会话状态：`{id, history, pending_action, last_scan}` |
| `logs/laoba_feng_last_run.json` | 老巴疯啦最近一次运行统计 |
| `logs/laoba_cache_YYYY-MM-DD.json` | 当日老巴五维摘要缓存（reddit + github 混合，按 source_type/key 去重）|

---

# 下次会话要做的事

1. **老巴疯啦实战验证**：本地拉新代码 → 跑 `python main.py import-laoba-feng` 看豆包产出的"老巴语气" 是不是真东北段子手味儿；五维度是否真扯到北极星 A/B；如果 prompt 调性不对，调 LAOBA_SYSTEM_PROMPT
2. **UI 加老巴疯啦卡片**：`ui/index.html` + `ui/main.js` 加一张"老巴疯啦"卡片（立即执行 / Wiki / 日志三按钮），读 `logs/laoba_feng_last_run.json` 展示状态
3. **老巴日报封面页"Top 3 精选"逻辑**：当前是关键词过滤（含"能成/有戏/必爆"），可能选不出来。如果实战中精选区常空，改为按 laoba_verdict 长度 + 关键词组合评分排序
4. **打包 `.exe`**：`electron-builder` 配置 + 内置 Python 运行时，用户不装 Node
5. **Agent 意图路由可选升级到 LLM**：关键词规则在中短句上效果好，长句仍可能误判
6. **豆包超时率优化**：老巴 prompt 比 reddit 长（system+user+JSON 都更复杂），可能加剧超时；测一下 timeout 是否需要从 180 → 240

---

# Git 工作流

- **开发分支**：`claude/continue-pr-6-fixes-Ptuu4`（2026-04-20 起；之前是 `claude/feishu-agent-interface-HiudF`，PR #6 已合并到 main）
- **活跃 PR**：[#7](https://github.com/Baggio200cn/feishu-agent/pull/7) draft，Wiki 管家 Agent + 网络稳定性全套
- **远程**：`baggio200cn/feishu-agent`（GitHub）
- 每次提交都附 `https://claude.ai/code/session_xxx` URL 以便追溯
- commit message 按 conventional commits：`feat(scope): ...` / `fix(scope): ...` / `chore: ...` / `docs: ...` / `refactor: ...`
- 用户未授权前不合并到 main、不创建 PR、不删除远程分支

---

*本文件由 Claude 在 2026-04-19 会话中创建，后续会话请按顶部 AI 协作指令维护。*
