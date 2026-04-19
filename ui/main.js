const { app, BrowserWindow, ipcMain, screen, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

const REPO_ROOT = path.join(__dirname, '..');
const PY_CMD = process.platform === 'win32' ? 'python' : 'python3';
const STATE_FILE = path.join(REPO_ROOT, 'logs', 'scheduler_state.json');
const PID_FILE = path.join(REPO_ROOT, 'logs', 'scheduler.pid');
const GITHUB_LAST_RUN = path.join(REPO_ROOT, 'logs', 'github_last_run.json');
const GITHUB_INDEX = path.join(REPO_ROOT, 'logs', 'github_imported.json');
const REDDIT_LAST_RUN = path.join(REPO_ROOT, 'logs', 'reddit_last_run.json');
const FEISHU_AGENT_LOG = path.join(REPO_ROOT, 'logs', 'feishu_agent.log');

let mainWindow;
let stateWatchTimer = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 460,
    height: 760,
    minWidth: 420,
    minHeight: 640,
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    frame: process.platform !== 'darwin',
    backgroundColor: '#F5F6F8',
    icon: path.join(__dirname, 'icon.png'),
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      zoomFactor: 1.0,
      spellcheck: false,
    },
  });

  mainWindow.webContents.on('did-finish-load', () => {
    mainWindow.webContents.setZoomFactor(1.0);
    mainWindow.webContents.setVisualZoomLevelLimits(1, 1);
    pushState();
  });

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  mainWindow.loadFile(path.join(__dirname, 'index.html'));

  stateWatchTimer = setInterval(pushState, 15000);
}

function runPython(pyArgs, { env } = {}) {
  return new Promise((resolve) => {
    const proc = spawn(PY_CMD, ['main.py', ...pyArgs], {
      cwd: REPO_ROOT,
      env: { ...process.env, ...(env || {}), PYTHONIOENCODING: 'utf-8' },
    });
    let stdout = '';
    let stderr = '';
    proc.stdout.on('data', (d) => { stdout += d.toString('utf-8'); });
    proc.stderr.on('data', (d) => { stderr += d.toString('utf-8'); });
    proc.on('error', (err) => {
      resolve({ ok: false, code: -1, stdout, stderr: stderr + err.message });
    });
    proc.on('close', (code) => {
      resolve({ ok: code === 0, code, stdout, stderr });
    });
  });
}

/** 从 stdout 里拎出最后一个合法 JSON 对象（忽略前面的日志行） */
function extractJsonFromStdout(stdout) {
  if (!stdout) return null;
  const lines = stdout.split(/\r?\n/);
  for (let i = lines.length - 1; i >= 0; i--) {
    const t = lines[i].trim();
    if (t.startsWith('{') && t.endsWith('}')) {
      try { return JSON.parse(t); } catch { /* continue */ }
    }
  }
  return null;
}

function pidAlive(pid) {
  if (!pid) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return e.code === 'EPERM';
  }
}

function readJsonFile(file, fallback) {
  if (!fs.existsSync(file)) return fallback;
  try { return JSON.parse(fs.readFileSync(file, 'utf-8')); }
  catch { return fallback; }
}

function readSchedulerState() {
  if (!fs.existsSync(STATE_FILE)) {
    return { running: false, last_runs: {}, next_runs: {}, jobs: [] };
  }
  try {
    const state = JSON.parse(fs.readFileSync(STATE_FILE, 'utf-8'));
    if (state.running && state.pid && !pidAlive(state.pid)) {
      state.running = false;
    }
    return state;
  } catch {
    return { running: false, last_runs: {}, next_runs: {}, jobs: [] };
  }
}

function startScheduler() {
  const existing = readSchedulerState();
  if (existing.running) {
    return { ok: true, message: '调度器已经在运行', pid: existing.pid };
  }
  fs.mkdirSync(path.dirname(PID_FILE), { recursive: true });
  const out = fs.openSync(path.join(REPO_ROOT, 'logs', 'scheduler.out.log'), 'a');
  const err = fs.openSync(path.join(REPO_ROOT, 'logs', 'scheduler.err.log'), 'a');
  const proc = spawn(PY_CMD, ['main.py', 'schedule'], {
    cwd: REPO_ROOT,
    detached: true,
    stdio: ['ignore', out, err],
    env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
  });
  proc.unref();
  return { ok: true, message: '调度器已启动', pid: proc.pid };
}

function stopScheduler() {
  const state = readSchedulerState();
  const pid = state.pid;
  if (!pid || !pidAlive(pid)) {
    try { fs.unlinkSync(PID_FILE); } catch {}
    return { ok: true, message: '调度器未运行' };
  }
  try {
    if (process.platform === 'win32') {
      spawn('taskkill', ['/PID', String(pid), '/T', '/F']);
    } else {
      process.kill(pid, 'SIGTERM');
    }
  } catch (e) {
    return { ok: false, message: `停止失败: ${e.message}` };
  }
  try { fs.unlinkSync(PID_FILE); } catch {}
  return { ok: true, message: '调度器已停止' };
}

function fmtTime(iso) {
  if (!iso) return '-';
  return iso.replace('T', ' ').slice(0, 16);
}

function buildUiState() {
  const sched = readSchedulerState();
  const nextRuns = sched.next_runs || {};

  // ---------- GitHub 卡片 ----------
  const githubLast = readJsonFile(GITHUB_LAST_RUN, null);
  const githubIndex = readJsonFile(GITHUB_INDEX, {});
  const totalImported = Object.keys(githubIndex).length;

  let githubMeta;
  let githubStatus;
  if (githubLast) {
    const atLine = `上次 · ${fmtTime(githubLast.at)}`;
    let countLine;
    if (githubLast.status === 'error') {
      countLine = `失败：${(githubLast.message || '').slice(0, 60) || '未知错误'}`;
    } else if (githubLast.mode === 'trending') {
      // 新字段：trending 模式
      const fetched = githubLast.fetched || 0;
      const summarized = githubLast.summarized || 0;
      const aiFailed = githubLast.ai_failed || 0;
      const created = githubLast.pages_created || 0;
      countLine = `抓 ${fetched} · 摘要 ${summarized} · 失败 ${aiFailed} · 新建页 ${created}`;
    } else {
      // 老字段：legacy 模式
      countLine = `新增 ${githubLast.imported || 0} · 重复跳过 ${githubLast.skipped_dup || 0} · 累计 ${totalImported} 个`;
    }
    const nextLine = nextRuns.github ? `下次 · ${fmtTime(nextRuns.github)}` : '';
    githubMeta = nextLine ? `${atLine}<br>${countLine}<br>${nextLine}` : `${atLine}<br>${countLine}`;
    if (githubLast.status === 'success') githubStatus = { label: '成功', type: 'success' };
    else if (githubLast.status === 'partial') githubStatus = { label: '部分完成', type: 'warning' };
    else if (githubLast.status === 'skipped') githubStatus = { label: '当日已跑', type: 'idle' };
    else githubStatus = { label: '失败', type: 'danger' };
  } else {
    const nextLine = nextRuns.github ? `下次 · ${fmtTime(nextRuns.github)}` : '未排期';
    githubMeta = `尚未执行<br>${nextLine}`;
    githubStatus = { label: '未执行', type: 'idle' };
  }

  // ---------- Reddit 卡片 ----------
  const redditLast = readJsonFile(REDDIT_LAST_RUN, null);
  let redditMeta;
  let redditStatus;
  if (redditLast) {
    const atLine = `上次 · ${fmtTime(redditLast.at)}`;
    let countLine;
    if (redditLast.status === 'error') {
      countLine = `失败：${(redditLast.message || '').slice(0, 60) || '未知错误'}`;
    } else if (redditLast.status === 'disabled') {
      countLine = '已禁用（credentials.json 里 reddit.enabled=false）';
    } else {
      const fetched = redditLast.fetched || 0;
      const summarized = redditLast.summarized || 0;
      const aiFailed = redditLast.ai_failed || 0;
      const created = redditLast.pages_created || 0;
      countLine = `抓 ${fetched} · 摘要 ${summarized} · 失败 ${aiFailed} · 新建页 ${created}`;
    }
    const nextLine = nextRuns.reddit ? `下次 · ${fmtTime(nextRuns.reddit)}` : '';
    redditMeta = nextLine ? `${atLine}<br>${countLine}<br>${nextLine}` : `${atLine}<br>${countLine}`;
    if (redditLast.status === 'success') redditStatus = { label: '成功', type: 'success' };
    else if (redditLast.status === 'partial') redditStatus = { label: '部分完成', type: 'warning' };
    else if (redditLast.status === 'disabled') redditStatus = { label: '已禁用', type: 'idle' };
    else redditStatus = { label: '失败', type: 'danger' };
  } else {
    const nextLine = nextRuns.reddit ? `下次 · ${fmtTime(nextRuns.reddit)}` : '未排期';
    redditMeta = `尚未执行<br>${nextLine}<br>注：需先挂 VPN 设 HTTP_PROXY`;
    redditStatus = { label: '未执行', type: 'idle' };
  }

  return {
    scheduler: {
      enabled: !!sched.running,
      desc: sched.running
        ? `运行中 · PID ${sched.pid || '?'} · 已注册 ${(sched.jobs || []).filter(j => j.registered).length} 个任务`
        : '已停止 · 点击右侧开关启动',
    },
    github: {
      meta: githubMeta,
      statusLabel: githubStatus.label,
      statusType: githubStatus.type,
    },
    reddit: {
      meta: redditMeta,
      statusLabel: redditStatus.label,
      statusType: redditStatus.type,
    },
  };
}

function pushState() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  try {
    mainWindow.webContents.send('state-update', buildUiState());
  } catch {}
}

function openExternalUrl(url) {
  if (!url) return;
  shell.openExternal(url);
}

function openLocalFile(filePath) {
  if (!fs.existsSync(filePath)) return false;
  shell.openPath(filePath);
  return true;
}

/**
 * 触发需要 VPN 的命令时：透传当前父进程的 HTTP_PROXY（若 PowerShell 里设了的话）。
 * Electron 从 start-ui.bat 启动时，bat 里如果也 set 了 HTTP_PROXY，Python 子进程
 * 会自动继承。UI 不再强制任何值，让用户在启动前控制即可。
 */
const actionDispatch = {
  // 对话助手：把 query 发给 Python，等 JSON 回来
  'open-chat': async (params) => {
    const query = (params && params.query) ? String(params.query) : '';
    if (!query.trim()) {
      return { ok: false, message: '请输入查询关键词' };
    }
    const res = await runPython(['chat', query, '--limit', '5', '--json']);
    if (!res.ok) {
      return { ok: false, message: `对话助手调用失败（退出码 ${res.code}）`, stderr: res.stderr };
    }
    const data = extractJsonFromStdout(res.stdout);
    if (!data) {
      return { ok: false, message: '未解析到 JSON 输出', stdout: res.stdout };
    }
    return {
      ok: data.status === 'success' || data.status === 'partial',
      message: data.status === 'success' ? '回答已生成' : (data.message || '部分完成'),
      chat: data,
    };
  },

  'run-github': async () => {
    const res = await runPython(['import-github']);
    pushState();
    return {
      message: res.ok ? 'GitHub 抓取完成，刷新看新状态' : `GitHub 抓取失败（退出码 ${res.code}）`,
      stdout: res.stdout,
      stderr: res.stderr,
    };
  },

  'view-github-log': async () => {
    if (openLocalFile(FEISHU_AGENT_LOG)) {
      return { message: '日志已在系统默认编辑器打开' };
    }
    return { message: `日志文件不存在: ${FEISHU_AGENT_LOG}` };
  },

  'view-github-wiki': async () => {
    const data = readJsonFile(GITHUB_LAST_RUN, null);
    const url = data && data.wiki_url;
    if (url) { openExternalUrl(url); return { message: '已打开 Wiki 日报' }; }
    return { message: 'GitHub 日报还没生成，先点"立即执行"' };
  },

  'run-reddit': async () => {
    const res = await runPython(['import-reddit']);
    pushState();
    return {
      message: res.ok ? 'Reddit 抓取完成' : `Reddit 抓取失败（退出码 ${res.code}，可能 VPN 未启用）`,
      stdout: res.stdout,
      stderr: res.stderr,
    };
  },

  'view-reddit-result': async () => {
    const data = readJsonFile(REDDIT_LAST_RUN, null);
    const url = data && data.wiki_url;
    if (url) { openExternalUrl(url); return { message: '已打开 Reddit 日报' }; }
    return { message: 'Reddit 日报还没生成，先点"立即执行"' };
  },

  'preview-wiki': async () => {
    const res = await runPython(['organize', '--dry-run']);
    pushState();
    return {
      message: res.ok ? '预览完成，查看 logs/organize_report_*.json' : `预览失败（退出码 ${res.code}）`,
      stdout: res.stdout,
      stderr: res.stderr,
    };
  },

  'execute-wiki': async () => {
    const res = await runPython(['organize']);
    pushState();
    return {
      message: res.ok ? 'Wiki 整理完成' : `整理失败（退出码 ${res.code}）`,
      stdout: res.stdout,
      stderr: res.stderr,
    };
  },

  // Wiki 清理：dry-run 模式列出匹配项，让前端展示待删列表
  'cleanup-wiki-preview': async (params) => {
    const prefix = (params && params.prefix) ? String(params.prefix) : '';
    if (!prefix.trim()) {
      return { ok: false, message: '请输入要匹配的标题前缀（避免误删）' };
    }
    const res = await runPython(['cleanup-wiki', '--prefix', prefix, '--json']);
    if (!res.ok) return { ok: false, message: `预览失败（退出码 ${res.code}）`, stderr: res.stderr };
    const data = extractJsonFromStdout(res.stdout);
    if (!data) return { ok: false, message: '未解析到 JSON', stdout: res.stdout };
    return { ok: true, message: data.message || `匹配到 ${(data.matched || []).length} 个`, cleanup: data };
  },

  // Wiki 清理：真删（需要前端二次确认后触发）
  'cleanup-wiki-execute': async (params) => {
    const prefix = (params && params.prefix) ? String(params.prefix) : '';
    if (!prefix.trim()) {
      return { ok: false, message: '前缀不能为空' };
    }
    const res = await runPython(['cleanup-wiki', '--prefix', prefix, '--confirm', '--json']);
    if (!res.ok) return { ok: false, message: `删除失败（退出码 ${res.code}）`, stderr: res.stderr };
    const data = extractJsonFromStdout(res.stdout);
    if (!data) return { ok: false, message: '未解析到 JSON', stdout: res.stdout };
    pushState();
    return {
      ok: data.status === 'success' || data.status === 'partial',
      message: data.message || '删除完成',
      cleanup: data,
    };
  },

  'toggle-scheduler': async (params) => {
    const enabled = !!(params && params.enabled);
    const result = enabled ? startScheduler() : stopScheduler();
    setTimeout(pushState, 500);
    return { message: result.message };
  },

  'get-state': async () => {
    pushState();
    return { message: '状态已刷新' };
  },
};

ipcMain.handle('agent-action', async (_evt, action, params) => {
  const handler = actionDispatch[action];
  if (!handler) return { message: `未知操作: ${action}` };
  try {
    return await handler(params || {});
  } catch (err) {
    return { message: `执行出错: ${err.message}` };
  }
});

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  if (stateWatchTimer) clearInterval(stateWatchTimer);
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
