# CLAUDE.md — feishu-agent 项目档案

> 本文件供 AI 助手（Claude Code）在每次会话开始时快速恢复上下文。
> 每次会话结束时必须更新"当前进度"和"下次 TODO"两节。

---

## 一、AI 协作约定

### 会话开始时
1. 阅读本文件全部内容
2. 运行 `git log --oneline -10` 确认最新提交
3. 运行 `git status` 确认工作区干净
4. 询问用户"本次要做什么"，不要自己假设继续上次任务

### 进行中
- **禁止**在未阅读现有代码前自己新写同功能代码（"你不要乱搞"）
- **禁止**删除或重写 `launcher.pyw` 和 `main.py`（用户本地有修改）
- 单线程对话中出错时，按以下原则处理：
  1. 读取出错文件的完整内容，定位真正的错误行
  2. 最小化修改，只改出错的那一行/块
  3. 改完立即提交并 push，告知用户拉取
  4. 不要重写整个文件，不要引入未经讨论的新功能

### 会话结束时更新本文件
触发条件（任意一个）：
- 完成一个完整功能
- 发现并修复了重要 Bug
- 做出了影响后续开发的架构决策
- 上下文即将耗尽

---

## 二、项目背景

**飞书 Agent 智能管理中心** — 个人飞书空间的自动化运营工具。

### 核心功能
| 命令 | 说明 | 状态 |
|------|------|------|
| `python main.py organize` | AI 分类 + Wiki 目录整理 | ✅ 已完成 |
| `python main.py search --keyword X` | 跨空间文档搜索 | ✅ 已完成 |
| `python main.py daily-report` | 每日工作日报 | ✅ 已完成 |
| `python main.py daily-reddit` | Reddit 技术资讯日报 | 🚧 待迁移 AI |
| `python main.py daily-github` | GitHub Trending 日报 | 🚧 待迁移 AI |
| `python main.py import-github` | 导入 GitHub 项目到 Wiki | ✅ 已完成 |
| `python main.py list-spaces` | 列出所有 Wiki 空间 | ✅ 已完成 |
| `python main.py manage` | 交互式管理菜单 | ✅ 已完成 |
| `python cleanup_wiki.py` | 清理 Wiki 孤立节点 | ✅ 已完成 |
| 图形界面 `launcher.pyw` | tkinter 双标签页 GUI | ✅ 已完成 |
| 智能对话 `chat_agent.py` | 飞书 API 工具调用对话 | 🚧 待迁移 AI |

### 技术栈
- **语言**：Python 3.10+
- **飞书 SDK**：`lark-oapi >= 1.3.0`
- **AI**：豆包（火山方舟）OpenAI 兼容接口（已决定全面替换 Anthropic）
- **GUI**：tkinter（`launcher.pyw`）
- **数据库**：SQLite（`data/agent.db`）
- **配置**：`config/credentials.json`（不入库）

---

## 三、目录结构

```
feishu-agent/
├── main.py                     # CLI 入口（21KB，勿重写）
├── launcher.pyw                # tkinter GUI（15KB，勿重写，用户本地有修改）
├── cleanup_wiki.py             # Wiki 节点清理工具
├── CLAUDE.md                   # 本文件
├── requirements.txt
├── config/
│   ├── credentials.json        # 真实凭证（不入库）
│   ├── credentials.json.example
│   └── categories.json.example
├── data/
│   └── agent.db                # SQLite：doc_cache + organizer_folders
├── logs/                       # 运行日志 + 整理报告（自动生成）
└── src/
    ├── auth/                   # 飞书认证
    ├── chat/
    │   └── chat_agent.py       # 智能对话（待迁移 Anthropic→豆包）
    ├── importers/
    │   ├── feishu_doc_writer.py
    │   └── github_importer.py
    ├── managers/
    │   ├── calendar_manager.py  # 日历管理（已修复时间戳格式）
    │   ├── contact_manager.py
    │   ├── email_manager.py
    │   └── message_manager.py
    ├── organizer/
    │   └── doc_organizer.py     # Wiki 目录整理（已修复 Node 类和 docx 类型）
    ├── scrapers/
    │   ├── daily_writer.py
    │   ├── github_trending_scraper.py  # 待迁移 Anthropic→豆包
    │   └── reddit_scraper.py           # 待迁移 Anthropic→豆包
    └── utils/
        ├── config_loader.py
        └── feishu_client.py
```

---

## 四、当前进度

### ✅ 已完成
- 飞书 Wiki 文档扫描（双账号：personal + enterprise）
- AI 分类（豆包接口）+ 关键词规则匹配
- Wiki 分类目录创建 + 文档移动（原子操作 + rollback）
- **82 篇文档全部整理成功**（2026-04-18 验证）
- 文档搜索（`search` 命令，关键词跨空间匹配）
- GitHub Trending 爬取 + 导入 Wiki
- 日历事件查询（修复 Unix 时间戳格式）
- `cleanup_wiki.py` 孤立节点清理工具
- tkinter GUI（`launcher.pyw`）

### 🚧 进行中（下次会话优先）
- **AI 提供商迁移**：`reddit_scraper.py`、`github_trending_scraper.py`、`chat_agent.py` 将 Anthropic SDK 替换为豆包 OpenAI 兼容接口
  - 需要用户提供新的豆包模型名（用户正在选型中）
  - 注意：`tool_use` 格式（Anthropic）→ `function_calling` 格式（OpenAI）

### 📋 待处理
- 清理 20 个嵌套的旧分类目录节点（运行 organize 后，旧的分类文件夹被包含进了新分类文件夹）
  - 决策点：先移出子文档 or 直接删空文件夹？
- 测试 `daily-reddit`（迁移后）
- 测试 `daily-github`（迁移后）
- 测试智能对话（迁移后）

---

## 五、关键决策记录

| 日期 | 决策 | 原因 |
|------|------|------|
| 2026-04-18 | AI 提供商全部统一为豆包（火山方舟） | 用户有豆包 API Key，停用 Anthropic，减少依赖 |
| 2026-04-18 | Wiki 节点创建使用 `Node` 类替代 `CreateSpaceNodeRequestBody` | lark-oapi 新版本移除了 `CreateSpaceNodeRequestBody` |
| 2026-04-18 | `obj_type` 从 `"doc"` 改为 `"docx"` | 飞书 API 废弃了 `doc` 类型 |
| 2026-04-18 | 日历 API 时间戳使用 Unix 整数字符串 | 飞书 Calendar v4 不接受 ISO 格式，必须是 Unix timestamp |
| 2026-04-18 | 文档整理器使用 SQLite 缓存已创建文件夹 | 防止每次运行重复创建相同分类目录 |
| 2026-04-xx | 双账号架构（personal + enterprise） | 用户有两个飞书账号，Wiki 文档分布在两处 |

---

## 六、踩坑记录

### 飞书 API 坑
1. **`CreateSpaceNodeRequestBody` 已移除**
   - 新版 lark-oapi 用 `Node` 类替代
   - 修复：`from lark_oapi.api.wiki.v2 import CreateSpaceNodeRequest, Node`
   - 文件：`src/organizer/doc_organizer.py` L152, L251

2. **`obj_type("doc")` 被废弃**
   - 必须改为 `obj_type("docx")`，否则 API 返回错误
   - 文件：`src/organizer/doc_organizer.py` L157, L260

3. **Calendar v4 时间戳格式**
   - 必须是 Unix 时间戳字符串（如 `"1713456000"`），不接受 ISO 格式
   - 文件：`src/managers/calendar_manager.py` L49-50

4. **日历权限必须配置在"应用身份"标签下**
   - `calendar:calendar:readonly` 在飞书开放平台 → 权限管理 → 找到"应用身份"标签
   - 常见错误：错误地配置在"用户身份"标签下

5. **`logs/` 目录不存在时 logging 会崩溃**
   - `main.py` 在模块级别配置日志，但 `logs/` 在 `main()` 才创建
   - 解决：始终从项目根目录运行

### AI SDK 坑
6. **Anthropic `tool_use` 格式 ≠ OpenAI `function_calling` 格式**
   - Anthropic：`{"type": "tool_use", "name": ..., "input": {...}}`
   - OpenAI（豆包）：`{"role": "assistant", "tool_calls": [{"function": {"name": ..., "arguments": "..."}}]}`
   - 迁移时需要完整重写工具调用的解析逻辑

### Git 操作坑
7. **两个分支同时存在，launcher.pyw 有分歧**
   - 不能直接 cherry-pick，会有冲突
   - 解决：从用户分支新建临时分支 → 只 checkout 变更的文件 → 提交 → push → 删除临时分支

---

## 七、重要配置说明

### credentials.json 结构
```json
{
  "accounts": {
    "personal": { "app_id": "...", "app_secret": "...", "wiki_space_id": "..." },
    "enterprise": { "app_id": "...", "app_secret": "..." }
  },
  "ai": {
    "provider": "doubao",
    "api_key": "...",
    "model": "doubao-seed-2-0-mini-260215",
    "base_url": "https://ark.cn-beijing.volces.com/api/v3"
  },
  "github": { "token": "...", "repo_list": [...] }
}
```

### 豆包 API 调用方式（OpenAI 兼容）
```python
from openai import OpenAI
client = OpenAI(api_key=api_key, base_url="https://ark.cn-beijing.volces.com/api/v3")
resp = client.chat.completions.create(model=model, messages=[...])
```

### 飞书应用权限清单（personal 账号）
- `wiki:wiki:write` — Wiki 读写
- `docx:document:write` — 云文档读写
- `calendar:calendar:readonly` — 日历只读（应用身份）
- `im:message` — 消息发送
- `contact:contact:readonly` — 通讯录读取

---

## 八、下次会话 TODO

> 优先级从高到低：

1. **[ ] 完成 AI 迁移（Anthropic → 豆包）**
   - 用户需要先确认新的豆包模型名称
   - 改动文件：`src/scrapers/reddit_scraper.py`、`src/scrapers/github_trending_scraper.py`、`src/chat/chat_agent.py`
   - chat_agent 需要将 `tool_use` 改为 OpenAI `function_calling` 格式
   - requirements.txt 可以移除 `anthropic>=0.25.0`，添加 `openai>=1.0.0`

2. **[ ] 测试 daily-reddit 和 daily-github（迁移后）**

3. **[ ] 测试智能对话功能（迁移后）**

4. **[ ] 决策：20 个旧分类节点的处理方式**
   - 运行 `python cleanup_wiki.py --list` 可查看当前状态
   - 选项 A：直接删除（文档已经在正确位置了）
   - 选项 B：先确认没有子文档，再删除

---

*最后更新：2026-04-18 | 会话：continue-debugging-oql1O*
