const { app, BrowserWindow, ipcMain, dialog, Menu, nativeTheme } = require('electron');
const path = require('path');
const fs = require('fs');

let base = null;

// Colors for the Windows minimize/maximize/close buttons, matched to the app background
const bar = () => nativeTheme.shouldUseDarkColors
  ? { color: '#0b0d12', symbolColor: '#e9eef6', height: 38 }
  : { color: '#eef1f6', symbolColor: '#111a2b', height: 38 };

function createWindow() {
  const mac = process.platform === 'darwin';
  const win = new BrowserWindow({
    width: 1440, height: 920, minWidth: 820, minHeight: 560,
    title: 'My Pics', backgroundColor: bar().color,
    icon: path.join(__dirname, 'build', 'icon.ico'),
    titleBarStyle: 'hidden',
    ...(mac ? { trafficLightPosition: { x: 14, y: 12 } } : { titleBarOverlay: bar() }),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true, sandbox: true, nodeIntegration: false
    }
  });
  win.loadFile('index.html');
  nativeTheme.on('updated', () => {
    if (!mac && !win.isDestroyed()) win.setTitleBarOverlay(bar());
  });
}

ipcMain.handle('save-file', async (e, name, data) => {
  const win = BrowserWindow.fromWebContents(e.sender);
  const r = await dialog.showSaveDialog(win, {
    defaultPath: String(name), filters: [{ name: 'JPEG image', extensions: ['jpg'] }]
  });
  if (r.canceled || !r.filePath) return { ok: false };
  await fs.promises.writeFile(r.filePath, Buffer.from(data));
  return { ok: true, path: r.filePath };
});

ipcMain.handle('begin-save', async (e, name) => {
  const win = BrowserWindow.fromWebContents(e.sender);
  const r = await dialog.showOpenDialog(win, {
    title: 'Choose where to save', properties: ['openDirectory', 'createDirectory']
  });
  if (r.canceled || !r.filePaths[0]) return { ok: false };
  const safe = String(name).replace(/[\\/:*?"<>|]/g, '').trim() || 'My Pics';
  base = path.join(r.filePaths[0], safe);
  await fs.promises.mkdir(base, { recursive: true });
  return { ok: true, dir: base };
});

ipcMain.handle('write-file', async (e, rel, data) => {
  if (!base) return { ok: false };
  const target = path.resolve(base, String(rel));
  if (!target.startsWith(base + path.sep)) return { ok: false };
  await fs.promises.mkdir(path.dirname(target), { recursive: true });
  await fs.promises.writeFile(target, Buffer.from(data));
  return { ok: true };
});

app.whenReady().then(() => {
  Menu.setApplicationMenu(process.platform === 'darwin'
    ? Menu.buildFromTemplate([{ role: 'appMenu' }, { role: 'editMenu' }, { role: 'windowMenu' }])
    : null);
  createWindow();
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
