const { app, BrowserWindow, ipcMain, screen } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

const REPO_ROOT = path.join(__dirname, '..');
const PY_CMD = process.platform === 'win32' ? 'python' : 'python3';
const STATE_FILE = path.join(REPO_ROOT, 'logs', 'scheduler_state.json');
const PID_FILE = path.join(REPO_ROOT, 'logs', 'scheduler.pid');

let mainWindow;
let stateWatchTimer = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 460,
    height: 720,
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

function runPython(pyArgs) {
  return new Promise((resolve) => {
    const proc = spawn(PY_CMD, ['main.py', ...pyArgs], {
      cwd: REPO_ROOT,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
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

function pidAlive(pid) {
  if (!pid) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return e.code === 'EPERM';
  }
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

function buildUiState() {
  const sched = readSchedulerState();
  const meta = {};
  for (const [job, info] of Object.entries(sched.last_runs || {})) {
    meta[job] = info;
  }
  const nextRuns = sched.next_runs || {};

  const fmtMeta = (jobKey, defaultLabel) => {
    const last = meta[jobKey];
    const next = nextRuns[jobKey];
    if (!last && !next) return `尚未执行<br>${defaultLabel}`;
    const lastLine = last
      ? `上次 · ${last.at || '-'}（${last.status}）`
      : '尚未执行';
    const nextLine = next ? `下次 · ${next.replace('T', ' ').slice(0, 16)}` : '未排期';
    return `${lastLine}<br>${nextLine}`;
  };

  const statusLabel = (jobKey, fallback) => {
    const last = meta[jobKey];
    if (!last) return { label: fallback, type: 'idle' };
    if (last.status === 'success') return { label: '成功', type: 'success' };
    if (last.status === 'pending') return { label: '待开发', type: 'warning' };
    return { label: '失败', type: 'danger' };
  };

  const githubStatus = statusLabel('github', '就绪');
  const redditStatus = statusLabel('reddit', '待接入');

  return {
    scheduler: {
      enabled: !!sched.running,
      desc: sched.running
        ? `运行中 · PID ${sched.pid || '?'} · 已注册 ${(sched.jobs || []).filter(j => j.registered).length} 个任务`
        : '已停止 · 点击右侧开关启动',
    },
    github: {
      meta: fmtMeta('github', '配置好凭证后由调度器自动执行'),
      statusLabel: githubStatus.label,
      statusType: githubStatus.type,
    },
    reddit: {
      meta: fmtMeta('reddit', '模块尚未实现（步骤 B）'),
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

const actionDispatch = {
  'open-chat': async () => ({ message: '对话助手即将接入（规划中）' }),

  'run-github': async () => {
    const res = await runPython(['import-github']);
    pushState();
    return {
      message: res.ok ? 'GitHub 抓取完成' : `GitHub 抓取失败（退出码 ${res.code}）`,
      stdout: res.stdout,
      stderr: res.stderr,
    };
  },

  'view-github-log': async () => ({ message: '日志位于 logs/feishu_agent.log' }),

  'run-reddit': async () => ({ message: 'Reddit AI 日报模块待接入（步骤 B）' }),

  'view-reddit-result': async () => ({ message: 'Reddit 模块尚未开发' }),

  'preview-wiki': async () => {
    const res = await runPython(['organize', '--dry-run']);
    return {
      message: res.ok ? '预览完成，查看控制台输出' : `预览失败（退出码 ${res.code}）`,
      stdout: res.stdout,
      stderr: res.stderr,
    };
  },

  'execute-wiki': async () => {
    const res = await runPython(['organize']);
    return {
      message: res.ok ? 'Wiki 整理完成' : `整理失败（退出码 ${res.code}）`,
      stdout: res.stdout,
      stderr: res.stderr,
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
