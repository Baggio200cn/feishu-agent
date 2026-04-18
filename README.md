# 飞书智能管理 Agent

基于 [lark-oapi](https://github.com/larksuite/oapi-sdk-python) Python SDK 构建的飞书自动化管理工具，支持：

1. **文档整理** — 扫描个人 + 企业两个飞书账号的文档，由豆包 AI 自动分类，整理到个人 Wiki 空间
2. **GitHub 导入** — 将 GitHub 优质仓库的 README + 核心代码导入飞书文档
3. **飞书管理** — 邮箱、联系人、IM 消息、工作日历的查看与管理

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置凭证

```bash
cp config/credentials.json.example config/credentials.json
# 编辑 credentials.json，填入真实的 app_id / app_secret 等信息
```

#### 飞书应用创建步骤

1. 访问 [飞书开放平台](https://open.feishu.cn/app) 创建**企业自建应用**
2. 个人应用需要以下权限：
   - `wiki:wiki:write` — Wiki 读写
   - `docx:document:write` — 文档读写
   - `mail:mail:read` — 邮件读取
   - `im:message` — IM 消息
   - `calendar:calendar` — 日历
3. 企业应用需要以下权限（只读）：
   - `wiki:wiki:read`
   - `docx:document:read`
4. 发布应用并安装到工作台
5. 在 Wiki 空间设置中找到 `space_id`（URL 中的数字）

### 3. 运行

#### 桌面界面（Electron）

```bash
npm install
npm start
```

界面包含：连接状态栏、对话助手入口、GitHub / Reddit 每日抓取卡片、Wiki 整理工作流、每日定时任务开关。按钮会通过 IPC 调用 `main.py` 的对应子命令。文字已针对 Retina / HiDPI 屏幕做字体平滑和抗锯齿优化。

#### 命令行

```bash
# 预览文档分类（不实际移动）
python main.py organize --dry-run

# 执行文档整理
python main.py organize

# GitHub Trending 日报：抓取 /trending → 豆包 AI 中文摘要 → 写入飞书 Wiki
# 默认模式（credentials.json 里 github.trending.enabled=true 时）：
#   - 按日期生成一页「GitHub Trending 日报 YYYY-MM-DD」，放在 github专区 子节点下
#   - 当日已存在则跳过（加 --force 强刷）
python main.py import-github

# 强刷（忽略当日去重）
python main.py import-github --force

# 查看邮件
python main.py manage email

# 查看未来 7 天日历
python main.py manage calendar

# 搜索联系人
python main.py manage contacts --query 张三

# 查看群聊消息
python main.py manage messages --chat-id oc_xxxxxx

# 启动定时任务调度器（每天 09:00 抓 GitHub、23:00 整理 Wiki，可在 credentials.json 调整）
python main.py schedule

# 查询调度器当前状态（输出 JSON，UI 内部使用）
python main.py schedule-status
```

#### 定时任务

调度器配置在 `config/credentials.json` 的 `schedule.jobs` 数组里：

```json
"schedule": {
  "jobs": [
    {"type": "github",   "hour": 9,  "minute": 0, "enabled": true},
    {"type": "reddit",   "hour": 9,  "minute": 5, "enabled": false},
    {"type": "organize", "hour": 23, "minute": 0, "enabled": true}
  ]
}
```

- `type`：任务类型，目前支持 `github` / `reddit` / `organize`
- `hour` / `minute`：cron 触发时间（Asia/Shanghai 时区）
- `enabled`：是否启用
- 任务运行状态写入 `logs/scheduler_state.json`，UI 通过该文件展示
- 调度器进程 PID 写入 `logs/scheduler.pid`

桌面界面右下角的"每日定时任务"开关会调用 `python main.py schedule` 启动后台守护进程，关闭时通过 PID 信号终止。守护进程独立于 Electron，关闭 UI 不会停止调度。

#### GitHub 导入的数据文件

运行 `import-github` 后会在 `logs/` 下产生：

- `logs/github_imported.json` — 已导入仓库索引（去重依据，下次跳过）
- `logs/github_last_run.json` — 最近一次运行统计，UI 卡片从这里读真实数据
- `logs/feishu_agent.log` — 完整日志（含限频警告、失败原因）

去重策略：索引记录 `repo_full_name → wiki_url`，下次再跑时同名仓库自动跳过。想强刷就用 `--force`。

---

## 项目结构

```
feishu-agent/
├── config/
│   ├── credentials.json.example   # 凭证模板
│   └── categories.json.example    # 自定义分类规则（可选）
├── src/
│   ├── auth/
│   │   └── feishu_auth.py         # Token 管理
│   ├── organizer/
│   │   ├── doc_scanner.py         # 扫描 Wiki/云盘文档
│   │   ├── ai_categorizer.py      # AI 自动分类
│   │   └── doc_organizer.py       # 整理到目标 Wiki 空间
│   ├── importers/
│   │   ├── github_importer.py     # GitHub API 拉取仓库内容
│   │   └── feishu_doc_writer.py   # Markdown → 飞书文档 Block
│   ├── managers/
│   │   ├── email_manager.py       # 邮箱管理
│   │   ├── message_manager.py     # IM 消息管理
│   │   ├── calendar_manager.py    # 日历管理
│   │   └── contact_manager.py     # 联系人管理
│   └── utils/
│       ├── config_loader.py       # 配置读取
│       └── feishu_client.py       # Client 工厂（双账号）
├── main.py                        # CLI 入口
├── requirements.txt
└── logs/                          # 运行日志和整理报告
```

---

## 分类规则

默认由豆包 AI 自动判断分类。也可在 `config/categories.json` 中定义关键词规则（优先级高于 AI）：

```json
{
  "categories": [
    { "name": "技术文档", "keywords": ["API", "架构", "开发"], "icon": "💻" },
    { "name": "AI 工具", "keywords": ["AI", "ChatGPT", "Claude"], "icon": "🤖" }
  ],
  "default_category": "其他"
}
```

---

## 依赖

- Python 3.8+
- [lark-oapi](https://github.com/larksuite/oapi-sdk-python) ≥ 1.3.0
- requests
- 豆包 API Key（豆包 AI 分类，可选）
- GitHub Token（GitHub 导入，可选）
