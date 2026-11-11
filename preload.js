const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('mypics', {
  platform: process.platform,
  saveFile: (name, data) => ipcRenderer.invoke('save-file', name, data),
  beginSave: (dirs) => ipcRenderer.invoke('begin-save', dirs),
  writeFile: (rel, data) => ipcRenderer.invoke('write-file', rel, data),
  getVersion: () => ipcRenderer.invoke('get-version'),
  checkUpdate: () => ipcRenderer.invoke('check-update'),
  installUpdate: () => ipcRenderer.invoke('install-update'),
  openRelease: () => ipcRenderer.invoke('open-release'),
  onUpdate: (cb) => ipcRenderer.on('update-status', (e, s) => cb(s))
});
