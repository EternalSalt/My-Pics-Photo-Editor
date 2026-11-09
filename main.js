const { app, BrowserWindow, ipcMain, dialog, Menu, nativeTheme, shell, net } = require('electron');
const path = require('path');
const fs = require('fs');
let autoUpdater = null;
try { autoUpdater = require('electron-updater').autoUpdater; } catch (e) { /* falls back to the notice-only check */ }

let base = null;
let win = null;
let manualCheck = false;
let downloading = false;

// Windows installer builds can update themselves. Mac and the portable Windows app get a "new version" notice.
const portable = !!process.env.PORTABLE_EXECUTABLE_DIR;
const selfUpdate = app.isPackaged && process.platform === 'win32' && !portable && !!autoUpdater;
const msg = (e) => String((e && e.message) || e).split('\n')[0].slice(0, 160);

// Colors for the Windows minimize/maximize/close buttons, matched to the app background
const bar = () => nativeTheme.shouldUseDarkColors
  ? { color: '#0b0d12', symbolColor: '#e9eef6', height: 38 }
  : { color: '#eef1f6', symbolColor: '#111a2b', height: 38 };

function createWindow() {
  const mac = process.platform === 'darwin';
  win = new BrowserWindow({
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
    if (!mac && win && !win.isDestroyed()) win.setTitleBarOverlay(bar());
  });
}

/* ---------- updates ---------- */
const send = (s) => {
  if (win && !win.isDestroyed()) win.webContents.send('update-status', { ...s, manual: manualCheck });
};

// Which GitHub repository to check. The build writes update-config.json; app-update.yml is the backup.
function repoInfo() {
  try {
    const c = JSON.parse(fs.readFileSync(path.join(__dirname, 'update-config.json'), 'utf8'));
    if (c.owner && c.repo) return { owner: c.owner, repo: c.repo };
  } catch (e) { /* try the next one */ }
  try {
    const t = fs.readFileSync(path.join(process.resourcesPath, 'app-update.yml'), 'utf8');
    const owner = (t.match(/^owner:\s*(\S+)/m) || [])[1];
    const repo = (t.match(/^repo:\s*(\S+)/m) || [])[1];
    return owner && repo ? { owner, repo } : null;
  } catch (e) { return null; }
}

const newer = (a, b) => {
  const p = (s) => String(s).split('.').map((n) => parseInt(n, 10) || 0);
  const x = p(a), y = p(b);
  for (let i = 0; i < 3; i++) {
    if ((x[i] || 0) > (y[i] || 0)) return true;
    if ((x[i] || 0) < (y[i] || 0)) return false;
  }
  return false;
};

async function checkViaGitHub() {
  const r = repoInfo();
  if (!r) throw new Error('This copy was built without update settings');
  const res = await net.fetch(`https://api.github.com/repos/${r.owner}/${r.repo}/releases/latest`, {
    headers: { Accept: 'application/vnd.github+json', 'User-Agent': 'My-Pics' }
  });
  if (res.status === 404) throw new Error(`No public release found at github.com/${r.owner}/${r.repo} (is the repository public?)`);
  if (!res.ok) throw new Error('GitHub answered ' + res.status);
  const j = await res.json();
  const v = String(j.tag_name || '').replace(/^v/, '');
  if (newer(v, app.getVersion())) send({ state: 'manual', version: v });
  else send({ state: 'none' });
}

async function checkForUpdates() {
  if (!app.isPackaged) { send({ state: 'none' }); return; }
  let first = '';
  if (selfUpdate) {
    try { await autoUpdater.checkForUpdates(); return; }
    catch (e) { first = msg(e); }   // fall back to the notice-only check below
  }
  try { await checkViaGitHub(); }
  catch (e) { send({ state: 'error', message: msg(e) + (first ? ' (updater: ' + first + ')' : '') }); }
}

function setupUpdates() {
  if (selfUpdate) {
    autoUpdater.autoDownload = true;
    autoUpdater.autoInstallOnAppQuit = true;
    autoUpdater.on('update-available', (i) => { downloading = true; send({ state: 'downloading', version: i.version, percent: 0 }); });
    autoUpdater.on('update-not-available', () => send({ state: 'none' }));
    autoUpdater.on('download-progress', (p) => send({ state: 'downloading', percent: Math.round(p.percent) }));
    autoUpdater.on('update-downloaded', (i) => { downloading = false; send({ state: 'ready', version: i.version }); });
    autoUpdater.on('error', (e) => { if (downloading) { downloading = false; send({ state: 'error', message: msg(e) }); } });
  }
  if (!app.isPackaged) return;
  setTimeout(checkForUpdates, 6000);
  setInterval(checkForUpdates, 4 * 60 * 60 * 1000);
}

ipcMain.handle('get-version', () => app.getVersion());
ipcMain.handle('check-update', async () => {
  manualCheck = true;
  try { await checkForUpdates(); } finally { manualCheck = false; }
});
ipcMain.handle('install-update', () => { if (selfUpdate) autoUpdater.quitAndInstall(true, true); });
ipcMain.handle('open-release', () => {
  const r = repoInfo();
  if (r) shell.openExternal(`https://github.com/${r.owner}/${r.repo}/releases/latest`);
});

/* ---------- saving ---------- */
ipcMain.handle('save-file', async (e, name, data) => {
  const w = BrowserWindow.fromWebContents(e.sender);
  const r = await dialog.showSaveDialog(w, {
    defaultPath: String(name), filters: [{ name: 'JPEG image', extensions: ['jpg'] }]
  });
  if (r.canceled || !r.filePath) return { ok: false };
  await fs.promises.writeFile(r.filePath, Buffer.from(data));
  return { ok: true, path: r.filePath };
});

ipcMain.handle('begin-save', async (e, name) => {
  const w = BrowserWindow.fromWebContents(e.sender);
  const r = await dialog.showOpenDialog(w, {
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
  setupUpdates();
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
