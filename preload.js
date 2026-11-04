const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('mypics', {
  saveFile: (name, data) => ipcRenderer.invoke('save-file', name, data),
  beginSave: (name) => ipcRenderer.invoke('begin-save', name),
  writeFile: (rel, data) => ipcRenderer.invoke('write-file', rel, data)
});
