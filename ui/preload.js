const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('feishuAgent', {
  invoke: (action, params) => ipcRenderer.invoke('agent-action', action, params),
  onStateUpdate: (cb) => ipcRenderer.on('state-update', (_evt, state) => cb(state)),
});
