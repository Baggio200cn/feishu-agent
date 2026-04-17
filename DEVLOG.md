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
| Reddit AI 日报模块 | 占位 | 需新增 `python main.py import-reddit` 子命令 |
| GitHub 日志查看 | 占位 | 需把 `logs/feishu_agent.log` 尾部推给渲染进程 |
| 调度器（toggle-scheduler） | 仅 UI | 实际调度需接 `schedule` / `APScheduler` |
| 状态实时刷新 | 未启用 | 主进程可通过 `win.webContents.send('state-update', …)` 推送 |
| 打包为 .exe | 未实施 | `electron-builder` 一键构建，免去用户装 Node 的成本 |

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
