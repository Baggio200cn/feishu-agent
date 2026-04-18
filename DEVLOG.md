# 飞书智能 Agent — 开发日志

> 记录范围：技术文档改造 + 前端 UI 改造的完整过程
> 分支：`claude/feishu-agent-interface-HiudF`
> 时间：2026-04-17

---

## 一、项目背景

### 起始状态

仓库 `baggio200cn/feishu-agent` 原为纯 Python CLI 工具，基于 `lark-oapi` SDK，
功能聚焦三块：

1. **文档整理** — 扫描个人 + 企业两个飞书账号的 Wiki/文档，由豆包 AI 自动分类，
   整理到个人 Wiki 空间
2. **GitHub 导入** — 将 GitHub 优质仓库的 README + 核心代码写入飞书文档
3. **飞书管理** — 邮箱、联系人、IM、日历的查看与管理

所有能力仅通过 `python main.py <子命令>` 调用，无图形界面。

### 历史尝试（已回滚）

在本次之前，曾有一次 GUI 尝试（commit `76b3ec6`），基于 tkinter：
- `launcher.pyw`：Console 选项卡（捕获 stdout/stderr）+ Chat 选项卡（自然语言分发）
- `main.py`：新增 `daily-report` 子命令

因方向调整，该改动通过 PR #1 被 revert（commit `975e684`），回到纯 CLI 形态。

### 本次任务目标

用户提供了一份完整的飞书风格 HTML 模板，要求：

1. 作为桌面应用实际落地（不是静态原型）
2. 按钮点击能真正调用 Python 后端
3. **页面的文字显示要高清**（用户原话：反复强调的硬指标）
4. 桌面快捷方式一键启动

---

## 二、技术方案选型

### 为何选 Electron

用户模板自带 `window.feishuAgent.invoke` / `onStateUpdate` 这类 Electron IPC
风格的调用约定，且注释里直接出现 "wire this in preload.js with contextBridge"，
方向已经写死。另外 Python GUI（tkinter / PyQt）已被证明不是用户想要的路径。

最终架构：

```
┌─────────────────┐     IPC      ┌─────────────────┐    spawn    ┌──────────────┐
│ Electron 渲染进程 │ ───────────→ │ Electron 主进程  │ ─────────→ │ python main.py│
│  (index.html)   │   invoke()   │   (main.js)     │  子进程     │   子命令      │
│                 │ ←─────────── │                 │ ←────────── │              │
└─────────────────┘  Promise     └─────────────────┘  stdout     └──────────────┘
```

- **渲染进程**：纯 HTML/CSS/JS，负责界面
- **preload.js**：用 `contextBridge` 安全暴露 `feishuAgent` 对象给渲染进程
- **主进程**：接收 IPC，`spawn python3 main.py <args>`，把结果回传
- **Python**：保持原 CLI 不动，UI 只是触发器

好处：UI 与业务逻辑完全解耦，以后 CLI 新加子命令就在 `main.js` 的
`actionDispatch` 表里添一行即可，Python 代码不用动。

---

## 三、前端 UI 改造

### 3.1 文件结构

```
feishu-agent/
├── package.json         # Electron 项目声明 + start 脚本
├── start-ui.bat         # Windows 桌面快捷方式入口（本地生成，未入库）
└── ui/
    ├── index.html       # 全部 UI（CSS + JS 内联）
    ├── main.js          # Electron 主进程
    ├── preload.js       # contextBridge
    └── icon.png         # 头像图标（本地放置，未入库）
```

### 3.2 UI 结构（index.html）

由上至下的功能区：

| 区块 | 功能 |
|------|------|
| 标题栏 | 圆形头像 + "飞书智能 Agent"，`-webkit-app-region: drag` 可拖动窗口 |
| 连接状态栏 | 状态圆点 + 文案 + 账号类型（支持通过 IPC 推送刷新） |
| 对话助手卡片 | 主色块按钮，规划中（`open-chat` 动作） |
| 每日抓取（双列） | GitHub Trending + Reddit AI 日报，每张卡片带 badge + meta + 执行/查看 |
| Wiki 整理工作流 | 分类概览 + 统计条 + "预览/执行" 双按钮 |
| 调度开关 | 自定义 Toggle，绿色=运行中 |
| Toast | 底部居中弹层，2.2s 自动消失 |

### 3.3 高清文字渲染（重点）

用户多次强调"页面的文字显示要高清"。通用渲染通路做了五层优化，覆盖
Windows / macOS / 不同 DPR 屏幕：

```css
/* 1. HTML 根上打开排版微调 */
html {
  -webkit-text-size-adjust: 100%;
  text-size-adjust: 100%;
  font-feature-settings: "kern" 1, "liga" 1, "calt" 1;
}

/* 2. body 主体 —— 抗锯齿 + 连字 + GPU 合成层 */
body {
  -webkit-font-smoothing: antialiased;      /* macOS 灰度平滑 */
  -moz-osx-font-smoothing: grayscale;        /* macOS Firefox */
  text-rendering: optimizeLegibility;        /* 开启字距/连字 */
  font-feature-settings: "kern" 1, "liga" 1, "calt" 1, "ss01" 1;
  letter-spacing: 0.01em;
  transform: translateZ(0);                  /* 触发 GPU 层，避免亚像素模糊 */
  backface-visibility: hidden;
}

/* 3. 中文避免字距异常（latin 微调对 CJK 会导致字宽变形） */
:lang(zh), :lang(zh-CN) { letter-spacing: 0; }

/* 4. Retina / HiDPI 再覆盖一次 */
@media (-webkit-min-device-pixel-ratio: 1.5), (min-resolution: 144dpi) {
  body { -webkit-font-smoothing: subpixel-antialiased; }
}

/* 5. 图标/按钮单独加 antialiased，避免 transform:scale 时糊掉 */
button { -webkit-font-smoothing: antialiased; }
.toast { -webkit-font-smoothing: antialiased; }
```

**字体栈**也做了微调，在 Windows 下优先取 `Microsoft YaHei UI`（比普通雅黑更清晰的 UI 变体）：

```css
--font: -apple-system, BlinkMacSystemFont, "Segoe UI",
         "PingFang SC", "Hiragino Sans GB",
         "Microsoft YaHei UI", "Microsoft YaHei",
         sans-serif;
```

另在 Electron 主进程侧强制 zoom factor = 1.0，避免系统缩放把字体拉糊：

```js
mainWindow.webContents.on('did-finish-load', () => {
  mainWindow.webContents.setZoomFactor(1.0);
  mainWindow.webContents.setVisualZoomLevelLimits(1, 1);
});
```

### 3.4 IPC 桥（preload.js）

用 `contextBridge` 隔离渲染进程与 Node API，保持 `contextIsolation: true`、
`sandbox: true`、`nodeIntegration: false` 的安全默认：

```js
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('feishuAgent', {
  invoke: (action, params) => ipcRenderer.invoke('agent-action', action, params),
  onStateUpdate: (cb) => ipcRenderer.on('state-update', (_e, state) => cb(state)),
});
```

渲染进程里一律走 `window.feishuAgent.invoke('action-name', params)`，
拿到的是带 `{ message, stdout, stderr }` 的 Promise。

### 3.5 主进程动作分发（main.js）

核心是一张动作表，把前端动作名映射到 Python 子命令：

```js
const actionDispatch = {
  'open-chat':          async () => ({ message: '对话助手即将接入（规划中）' }),
  'run-github':         async () => runPython(['import-github']),
  'preview-wiki':       async () => runPython(['organize', '--dry-run']),
  'execute-wiki':       async () => runPython(['organize']),
  'toggle-scheduler':   async (p) => ({ message: p.enabled ? '调度已启动' : '调度已停止' }),
  // ...
};

ipcMain.handle('agent-action', async (_e, action, params) => {
  return actionDispatch[action] ? actionDispatch[action](params) : { message: `未知操作: ${action}` };
});
```

`runPython()` 用 `child_process.spawn`，强制 `PYTHONIOENCODING=utf-8`
（Windows 默认 GBK 会把中文日志乱码），完成后返回 `{ ok, code, stdout, stderr }`。

---

## 四、部署与调试（Windows 实战）

### 4.1 npm install 卡在 Electron 二进制下载

**问题**：国内网络从 `github.com/electron/releases` 下载二进制时频繁 `ECONNRESET`。

```
npm error RequestError: read ECONNRESET
npm error path C:\Users\Zhaol\feishu-agent\node_modules\electron
```

**解决**：切到国内镜像。

```powershell
Remove-Item -Recurse -Force node_modules, package-lock.json -ErrorAction SilentlyContinue
$env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
npm config set registry https://registry.npmmirror.com
npm install
```

永久生效可写入用户级环境变量：

```powershell
[Environment]::SetEnvironmentVariable("ELECTRON_MIRROR", "https://npmmirror.com/mirrors/electron/", "User")
```

### 4.2 start-ui.bat —— 桌面快捷方式入口

直接用快捷方式指向 `npm start` 需要 shell 环境解析，改用一层 `.bat` 更稳：

```bat
@echo off
cd /d "%~dp0"
node_modules\electron\dist\electron.exe .
```

PowerShell 一次性生成（注意 heredoc 语法）：

```powershell
Set-Content -Path start-ui.bat -Encoding ASCII -Value @'
@echo off
cd /d "%~dp0"
node_modules\electron\dist\electron.exe .
'@
```

**踩坑**：第一次把 `.bat` 内容直接粘进 PowerShell 导致解析失败 —
`@echo off` 在 PS 里被当成数组表达式。必须写进文件再运行。

### 4.3 桌面快捷方式配置

右键 "飞书Agent" 快捷方式 → 属性：

| 字段 | 值 |
|------|---|
| 目标 | `C:\Users\Zhaol\feishu-agent\start-ui.bat` |
| 起始位置 | `C:\Users\Zhaol\feishu-agent` |
| 运行方式 | 最小化（隐藏 cmd 黑窗） |
| 图标 | 保留用户自定义头像 |

### 4.4 自定义图标

初版标题栏用蓝底白字 `F` 占位。用户希望替换为桌面快捷方式同款头像图标：

**改动：**
1. 用户把头像存为 `ui/icon.png`（建议 256×256 PNG）
2. `index.html`：`<div class="titlebar-logo">F</div>` → `<img class="titlebar-logo" src="icon.png">`
3. 标题栏 logo 样式改圆形（`border-radius: 50%; object-fit: cover`）、
   22px、加 `image-rendering: -webkit-optimize-contrast` 避免缩放糊
4. `main.js` 的 `BrowserWindow` 加 `icon: path.join(__dirname, 'icon.png')`，
   任务栏 / Alt-Tab / 窗口图标同步

**注意**：`icon.png` 未入库（它是用户个人资源），别人 clone 时需自备。

---

## 五、技术文档改造

`README.md` 的增量：

1. 在 "3. 运行" 下新增 **桌面界面（Electron）** 小节，给出：
   - `npm install` / `npm start` 两步
   - 界面功能清单
   - 高清渲染说明

2. 保留原 **命令行** 小节不动，CLI 用户体验零破坏

改造原则：不重写旧内容、不改变原有章节顺序，只在合适位置插入新入口。

---

## 六、Git 提交记录

开发分支：`claude/feishu-agent-interface-HiudF`

```
285bd3f feat: use ui/icon.png for titlebar logo and window icon
e119a37 feat: add Electron desktop UI with HD text rendering
```

两次提交都附带了 session URL 便于追溯，提交消息遵循 commit 原有风格（`feat:` 前缀 + 要点列表）。

---

## 七、已知待办 / 后续方向

| 项 | 状态 | 备注 |
|---|-----|-----|
| 对话助手（`open-chat`） | 占位 | 需接入豆包或 Claude，支持检索 Wiki、调用 Agent |
| Reddit AI 日报模块 | 占位 | 需新增 `python main.py import-reddit` 子命令（步骤 B） |
| GitHub 日志查看 | 占位 | 需把 `logs/feishu_agent.log` 尾部推给渲染进程 |
| ~~调度器（toggle-scheduler）~~ | ✅ 已完成 | 见下方"步骤 C" |
| ~~状态实时刷新~~ | ✅ 已完成 | 主进程每 15s 读 state 文件并推 `state-update` 事件 |
| 打包为 .exe | 未实施 | `electron-builder` 一键构建，免去用户装 Node 的成本 |

---

## 九、步骤 C：调度器（2026-04-18）

### 背景

用户在第一版 UI 上线后核查发现：界面所有"已完成 / 抓取 32 项 / 运行中"等状态都是
HTML 写死的占位文本，不是真实数据。本轮目标是把"每日定时任务"开关变成**真的能拉起后台
守护进程**的功能。

### 设计

```
┌──────────┐  toggle  ┌──────────┐  spawn detached  ┌──────────────┐
│   UI     │ ───────→ │ Electron │ ───────────────→ │ python main  │
│          │          │  主进程   │                  │  .py schedule│
│          │ ←─────── │          │ ←── poll 15s ────│ (APScheduler)│
└──────────┘  state   └──────────┘  state.json      └──────────────┘
                                                           │ writes
                                                           ▼
                                              logs/scheduler_state.json
                                              logs/scheduler.pid
```

**关键决定**：调度器是**独立的 Python 守护进程**，用 `spawn(... detached: true)` +
`proc.unref()` 启动，关闭 Electron 窗口后调度器仍然运行。这符合"每日定时"的语义 ——
用户不可能 24 小时挂着 UI。

### 文件清单

| 文件 | 作用 |
|------|------|
| `requirements.txt` | 新增 `APScheduler>=3.10.0` |
| `src/scheduler.py` | `FeishuScheduler` 类（新建） |
| `main.py` | 新增 `schedule` / `schedule-status` 子命令 |
| `config/credentials.json.example` | 新增 `schedule.jobs` 配置块 |
| `ui/main.js` | 新增 `startScheduler` / `stopScheduler` / `pidAlive` / `buildUiState` / `pushState` |
| `ui/index.html` | 移除 mock 数据，改为"未执行 / 待开发"等真实初始状态 |

### `FeishuScheduler` 核心实现

```python
class FeishuScheduler:
    def __init__(self, jobs_config):
        self.scheduler = BlockingScheduler(timezone="Asia/Shanghai")
        self.state = {"running": False, "pid": os.getpid(),
                      "last_runs": {}, "next_runs": {}, "jobs": []}

    def add_jobs(self):
        for job in self.jobs_config:
            handler = self._get_handler(job["type"])  # github / reddit / organize
            self.scheduler.add_job(
                handler,
                CronTrigger(hour=job["hour"], minute=job["minute"], timezone="Asia/Shanghai"),
                id=job["type"], coalesce=True, misfire_grace_time=300,
            )

    def start(self):
        self.add_jobs()
        self._write_pid()
        self._save_state()
        # 监听 SCHEDULER_STARTED / JOB_EXECUTED 事件，每次重写状态文件
        self.scheduler.add_listener(
            lambda _e: self._save_state(),
            EVENT_SCHEDULER_STARTED | EVENT_JOB_EXECUTED | EVENT_JOB_ERROR,
        )
        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)
        self.scheduler.start()  # blocks
```

### 踩坑记录

#### 坑 1：`logs/` 目录不存在导致 logging FileHandler 崩溃

`main.py` 顶部用 `logging.basicConfig(handlers=[FileHandler("logs/feishu_agent.log")])`
配置日志，但 `os.makedirs("logs")` 写在 `main()` 函数内，**模块加载时就崩**。

**修复**：把 `os.makedirs("logs", exist_ok=True)` 提到 module-level，在
`logging.basicConfig` 之前。

```python
import logging
import os

os.makedirs("logs", exist_ok=True)   # ← 关键：必须在 basicConfig 之前
logging.basicConfig(...)
```

#### 坑 2：APScheduler 3.11 的 `Job.next_run_time` 在 start 之前不存在

启动顺序：
```
add_jobs()       → job 处于 "tentative" 状态，没有 next_run_time 属性
_save_state()    → AttributeError: 'Job' object has no attribute 'next_run_time'
scheduler.start()→ 此时才真正排期，job 才有 next_run_time
```

**修复**：
- `getattr(job, "next_run_time", None)` 防御性访问
- 注册 `EVENT_SCHEDULER_STARTED` 监听器，启动后再写一次状态，把 `next_runs` 持久化

#### 坑 3：跨平台 PID 检查

Node 端用 `process.kill(pid, 0)` 判断进程存活，Python 端用同样思路。但 Windows
没有 POSIX 信号 0 的概念，用 `OpenProcess` + `GetExitCodeProcess` 替代：

```python
def _pid_alive(pid):
    if sys.platform == "win32":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle: return False
        exit_code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return exit_code.value == 259  # STILL_ACTIVE
    else:
        try: os.kill(pid, 0); return True
        except (ProcessLookupError, PermissionError): return False
```

#### 坑 4：Windows 上 SIGTERM 信号处理不生效

`process.kill(pid, 'SIGTERM')` 在 Node Windows 实现会调 `TerminateProcess`，
绕过 Python 的 `signal.SIGTERM` handler，状态文件来不及更新。

**修复**：UI 端 `stopScheduler` 在 Windows 上改用 `taskkill /PID <pid> /T /F`，
同时主动删 `PID_FILE`，UI 通过 PID 存活检查反推真实状态，不强依赖 state 文件
是否被进程亲自更新。

### UI 改动

去除界面所有 mock 文案：

| 改动前（假数据） | 改动后（真实状态） |
|----------------|-------------------|
| GitHub: "就绪 · 昨天 09:00 · 抓取 32 项" | "未执行 · 配置好凭证后由调度器自动执行" |
| Reddit: "完成 · 刚刚执行 · 抓取 47 条" | "待开发 · 模块尚未实现（步骤 B）" |
| Wiki: "个人 142 篇 · 企业 38 篇 · 待整理 23 篇" | "个人 — 篇 · 企业 — 篇 · 待整理 — 篇" |
| 调度: "运行中"（绿色开关默认 ON） | "已停止"（开关默认 OFF） |
| 连接: "已连接 · 上次同步 14:23" | "本地模式 · 配置凭证后联通飞书" |
| `F` 蓝方块 | 圆形 `icon.png` 头像 |

调度卡片下方动态显示：`运行中 · PID xxxx · 已注册 N 个任务`。

### 验证流程

```bash
# 1. 装依赖
pip install -r requirements.txt

# 2. 验证调度器能启动并写状态文件
timeout 4 python main.py schedule
cat logs/scheduler_state.json
# 期望看到 next_runs 里有 organize / github 的下一次触发时间

# 3. 通过 schedule-status 子命令读状态
python main.py schedule-status

# 4. UI 端验证：双击 start-ui.bat → 切换"每日定时任务"开关
#    ON  → 后台多出一个 python main.py schedule 进程，UI 显示 PID
#    OFF → 进程消失，UI 显示"已停止"
```

### 未完成项

- 任务运行进度尚未实时推到 UI（每 15s 拉一次状态足够看，但不够"实时"）
- 调度器开机自启动（建议用 Windows 任务计划程序触发 start-ui.bat 或单独写一个无 UI 启动器）
- 任务失败重试策略（目前 `coalesce=True`，错过窗口会合并执行；失败仅记 error 状态）

下一步：步骤 A —— 让 GitHub 抓取真跑通（凭证配置、限频、错误重试）。

---

*步骤 C 日志生成：2026-04-18*

---

## 八、启动指南（给未来的自己）

**从零启动：**

```powershell
# 1. 克隆并切到开发分支
git clone <repo-url>
cd feishu-agent
git checkout claude/feishu-agent-interface-HiudF

# 2. Python 侧
pip install -r requirements.txt
cp config/credentials.json.example config/credentials.json
# 编辑 credentials.json 填入真实凭证

# 3. Electron 侧
$env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
npm install

# 4. 放置头像（可选）
copy <你的头像>.png ui\icon.png

# 5. 启动
npm start
# 或双击 start-ui.bat
```

**日常使用：** 双击桌面那个人像图标即可。

---

*日志生成：2026-04-17 · 分支 `claude/feishu-agent-interface-HiudF`*
