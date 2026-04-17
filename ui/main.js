const { app, BrowserWindow, ipcMain, screen } = require('electron');
const path = require('path');
const { spawn } = require('child_process');

let mainWindow;

function createWindow() {
  const primary = screen.getPrimaryDisplay();
  const scaleFactor = primary.scaleFactor || 1;

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
  });

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  mainWindow.loadFile(path.join(__dirname, 'index.html'));
}

function runPython(pyArgs) {
  return new Promise((resolve) => {
    const repoRoot = path.join(__dirname, '..');
    const pyCmd = process.platform === 'win32' ? 'python' : 'python3';
    const proc = spawn(pyCmd, ['main.py', ...pyArgs], {
      cwd: repoRoot,
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

const actionDispatch = {
  'open-chat': async () => ({ message: '对话助手即将接入（规划中）' }),

  'run-github': async () => {
    const res = await runPython(['import-github']);
    return {
      message: res.ok ? 'GitHub 抓取完成' : `GitHub 抓取失败（退出码 ${res.code}）`,
      stdout: res.stdout,
      stderr: res.stderr,
    };
  },

  'view-github-log': async () => ({ message: '已打开 GitHub 日志（占位）' }),

  'run-reddit': async () => ({ message: 'Reddit AI 日报模块待接入' }),

  'view-reddit-result': async () => ({ message: '查看 Reddit 日报（占位）' }),

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

  'toggle-scheduler': async (_params) => {
    return { message: _params && _params.enabled ? '调度已启动' : '调度已停止' };
  },
};

ipcMain.handle('agent-action', async (_evt, action, params) => {
  const handler = actionDispatch[action];
  if (!handler) {
    return { message: `未知操作: ${action}` };
  }
  try {
    return await handler(params || {});
  } catch (err) {
    return { message: `执行出错: ${err.message}` };
  }
});

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
