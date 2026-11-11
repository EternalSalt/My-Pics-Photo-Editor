const { app, BrowserWindow, ipcMain, dialog, Menu, nativeTheme, shell, net } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
const crypto = require('crypto');
const { spawn } = require('child_process');

let base = null;
let win = null;
let manualCheck = false;
let busy = false;
let pending = null; // a finished download waiting to be installed: { file, version, mode }

// Set when the Windows portable app is running; it is the path of that .exe
const portableFile = process.env.PORTABLE_EXECUTABLE_FILE || null;
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
const updDir = () => path.join(app.getPath('temp'), 'mypics-update');

// Downloaded installers are removed the next time the app starts, so nothing piles up.
function cleanupOld() {
  const rm = (p) => { try { fs.rmSync(p, { recursive: true, force: true }); } catch (e) { /* in use, try next time */ } };
  rm(updDir());
  if (process.env.LOCALAPPDATA) rm(path.join(process.env.LOCALAPPDATA, 'my-pics-updater')); // leftovers from the older updater
}

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

// The file that matches this copy of the app (installer, portable app, or the right Mac disk image)
function pickAsset(assets) {
  const by = (re) => assets.find((a) => re.test(a.name));
  if (process.platform === 'win32') return portableFile ? by(/^My-Pics-Portable\.exe$/i) : by(/^My-Pics-Setup\.exe$/i);
  if (process.platform === 'darwin') return by(new RegExp('^My-Pics-Mac-' + process.arch + '\\.dmg$', 'i'));
  return null;
}
const modeFor = () => (process.platform === 'darwin' ? 'mac' : portableFile ? 'portable' : 'installer');

async function download(asset, version, mode) {
  fs.mkdirSync(updDir(), { recursive: true });
  const file = path.join(updDir(), asset.name);
  send({ state: 'downloading', version, percent: 0 });
  const res = await net.fetch(asset.browser_download_url, { headers: { 'User-Agent': 'My-Pics' } });
  if (!res.ok || !res.body) throw new Error('The download answered ' + res.status);
  const total = Number(res.headers.get('content-length')) || asset.size || 0;
  const out = fs.createWriteStream(file);
  const hash = crypto.createHash('sha256');
  const reader = res.body.getReader();
  let got = 0, last = -1;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      hash.update(value);
      got += value.length;
      if (!out.write(value)) await new Promise((r) => out.once('drain', r));
      const pc = total ? Math.floor((got / total) * 100) : 0;
      if (pc !== last) { last = pc; send({ state: 'downloading', percent: pc }); }
    }
  } finally {
    await new Promise((r) => out.end(r));
  }
  if (asset.size && got !== asset.size) throw new Error('The download was incomplete');
  const want = String(asset.digest || '').replace(/^sha256:/, '');
  if (want && hash.digest('hex') !== want) throw new Error('The download did not match its checksum');
  pending = { file, version, mode };
  send({ state: 'ready', version, mode });
}

// Installs the downloaded update. relaunch=false is used when the user is already closing the app.
function applyUpdate(relaunch) {
  if (!pending) return { quit: false };
  const p = pending;
  pending = null;
  try {
    if (p.mode === 'installer') {
      const args = ['/S', '--updated'];
      if (relaunch) args.push('--force-run');
      spawn(p.file, args, { detached: true, stdio: 'ignore' }).unref();
      return { quit: true };
    }
    if (p.mode === 'portable') {
      const target = portableFile;
      if (!target || /[%!^"]/.test(target + p.file)) { shell.showItemInFolder(p.file); return { quit: false }; }
      const script = path.join(os.tmpdir(), 'mypics-apply.cmd');
      fs.writeFileSync(script, [
        '@echo off',
        'set "NEW=' + p.file + '"',
        'set "OLD=' + target + '"',
        'set /a n=0',
        ':wait',
        'set /a n+=1',
        'if %n% gtr 90 goto done',
        'ping -n 2 127.0.0.1 >nul',
        'move /Y "%NEW%" "%OLD%" >nul 2>&1',
        'if errorlevel 1 goto wait',
        relaunch ? 'start "" "%OLD%"' : 'rem not relaunching',
        'rmdir /S /Q "' + updDir() + '" >nul 2>&1',
        ':done',
        '(goto) 2>nul & del "%~f0"',
        ''
      ].join('\r\n'));
      spawn('cmd.exe', ['/c', script], { detached: true, stdio: 'ignore', windowsHide: true }).unref();
      return { quit: true };
    }
    if (p.mode === 'mac') { shell.openPath(p.file); return { quit: false }; }
  } catch (e) {
    send({ state: 'error', message: msg(e) });
  }
  return { quit: false };
}

async function checkForUpdates() {
  if (busy) return;
  if (!app.isPackaged) { send({ state: 'none' }); return; }
  busy = true;
  let found = null;
  try {
    const r = repoInfo();
    if (!r) throw new Error('This copy was built without update settings');
    const res = await net.fetch(`https://api.github.com/repos/${r.owner}/${r.repo}/releases/latest`, {
      headers: { Accept: 'application/vnd.github+json', 'User-Agent': 'My-Pics' }
    });
    if (res.status === 404) throw new Error(`No public release found at github.com/${r.owner}/${r.repo} (is the repository public?)`);
    if (!res.ok) throw new Error('GitHub answered ' + res.status);
    const j = await res.json();
    const v = String(j.tag_name || '').replace(/^v/, '');
    if (!newer(v, app.getVersion())) { send({ state: 'none' }); return; }
    found = v;
    if (pending && pending.version === v) { if (manualCheck) send({ state: 'ready', version: v, mode: pending.mode }); return; }
    const asset = pickAsset(j.assets || []);
    if (!asset) { send({ state: 'manual', version: v }); return; }
    await download(asset, v, modeFor());
  } catch (e) {
    if (found) send({ state: 'manual', version: found, message: msg(e) });
    else send({ state: 'error', message: msg(e) });
  } finally {
    busy = false;
  }
}

function setupUpdates() {
  if (!app.isPackaged) return;
  setTimeout(checkForUpdates, 6000);
  setInterval(checkForUpdates, 4 * 60 * 60 * 1000);
}

ipcMain.handle('get-version', () => app.getVersion());
ipcMain.handle('check-update', async () => {
  manualCheck = true;
  try { await checkForUpdates(); } finally { manualCheck = false; }
});
ipcMain.handle('install-update', () => { if (applyUpdate(true).quit) app.quit(); });
ipcMain.handle('open-release', () => {
  const r = repoInfo();
  if (r) shell.openExternal(`https://github.com/${r.owner}/${r.repo}/releases/latest`);
});

/* ---------- saving ---------- */
ipcMain.handle('save-file', async (e, name, data) => {
  const w = BrowserWindow.fromWebContents(e.sender);
  const r = await dialog.showSaveDialog(w, {
    defaultPath: String(name), filters: [{ name: 'Image', extensions: ['jpg', 'png'] }]
  });
  if (r.canceled || !r.filePath) return { ok: false };
  await fs.promises.writeFile(r.filePath, Buffer.from(data));
  return { ok: true, path: r.filePath };
});

// Pick a location. The photos go straight into the folders you name (for example Edits and Watermark) inside it.
ipcMain.handle('begin-save', async (e, dirs) => {
  const w = BrowserWindow.fromWebContents(e.sender);
  const r = await dialog.showOpenDialog(w, { title: 'Choose where to save', properties: ['openDirectory', 'createDirectory'] });
  if (r.canceled || !r.filePaths[0]) return { ok: false };
  const chosen = r.filePaths[0];
  const names = (Array.isArray(dirs) ? dirs : []).map((d) => String(d).replace(/[\\/:*?"<>|]/g, '').trim()).filter(Boolean);
  const existing = names.filter((d) => { try { return fs.readdirSync(path.join(chosen, d)).length > 0; } catch (err) { return false; } });
  if (existing.length) {
    const c = await dialog.showMessageBox(w, {
      type: 'question', buttons: ['Replace files', 'Cancel'], defaultId: 1, cancelId: 1,
      title: 'Folder already has photos',
      message: existing.join(' and ') + (existing.length > 1 ? ' already have' : ' already has') + ' photos in this location.',
      detail: 'Files with the same names will be replaced.'
    });
    if (c.response !== 0) return { ok: false };
  }
  base = chosen;
  return { ok: true, dir: chosen };
});

ipcMain.handle('write-file', async (e, rel, data) => {
  if (!base) return { ok: false };
  const root = base.endsWith(path.sep) ? base : base + path.sep;
  const target = path.resolve(base, String(rel));
  if (!target.startsWith(root)) return { ok: false };
  await fs.promises.mkdir(path.dirname(target), { recursive: true });
  await fs.promises.writeFile(target, Buffer.from(data));
  return { ok: true };
});

app.whenReady().then(() => {
  Menu.setApplicationMenu(process.platform === 'darwin'
    ? Menu.buildFromTemplate([{ role: 'appMenu' }, { role: 'editMenu' }, { role: 'windowMenu' }])
    : null);
  cleanupOld();
  createWindow();
  setupUpdates();
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});
// If a downloaded update is waiting when the user closes the app, install it quietly then
app.on('will-quit', () => { if (pending && pending.mode !== 'mac') applyUpdate(false); });
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
