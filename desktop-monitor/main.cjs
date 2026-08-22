const path = require('node:path');
const { app, BrowserWindow, dialog, ipcMain, Menu, screen } = require('electron');
const { DesktopControl } = require('./desktop-control.cjs');
const { MONITOR_URL, MonitorServer, resolveRepoRoot } = require('./server-process.cjs');
const { loadWindowState, resolveWindowState, saveWindowState } = require('./window-state.cjs');

const repoRoot = resolveRepoRoot(__dirname);
process.env.PHONE_HARNESS_DOCKVIEW_DIR ||= app.isPackaged
  ? path.join(process.resourcesPath, 'dockview')
  : path.join(repoRoot, 'desktop-monitor', 'node_modules', 'dockview', 'dist');
let mainWindow = null;
let quitting = false;
let windowStatePath = null;
const desktopControl = new DesktopControl(repoRoot);

const server = new MonitorServer(repoRoot, {
  onExit: ({ code, signal }) => {
    if (quitting || server.external || !mainWindow || mainWindow.isDestroyed()) return;
    dialog.showMessageBox(mainWindow, {
      type: 'error',
      title: 'Phone Harness Monitor',
      message: 'Monitor server stopped unexpectedly.',
      detail: `exit=${code ?? '-'} signal=${signal ?? '-'}`,
      buttons: ['Restart server', 'Close'],
      defaultId: 0,
      cancelId: 1,
    }).then(async ({ response }) => {
      if (response !== 0 || !mainWindow || mainWindow.isDestroyed()) {
        app.quit();
        return;
      }
      try {
        await server.start();
        await mainWindow.loadURL(MONITOR_URL);
      } catch (error) {
        dialog.showErrorBox('Monitor restart failed', error.message);
        app.quit();
      }
    });
  },
});

async function restartServer() {
  try {
    await server.restart();
    await mainWindow.loadURL(MONITOR_URL);
  } catch (error) {
    await dialog.showMessageBox(mainWindow, {
      type: 'warning',
      title: 'Phone Harness Monitor',
      message: 'Monitor server could not be restarted.',
      detail: error.message,
    });
  }
}

function assertMainRenderer(event) {
  if (!mainWindow || mainWindow.isDestroyed() || event.sender !== mainWindow.webContents) {
    throw new Error('Desktop control request rejected.');
  }
}

function installIpc() {
  ipcMain.handle('phone-harness:status', async (event) => {
    assertMainRenderer(event);
    return { monitor: server.status(), harness: desktopControl.status() };
  });
  ipcMain.handle('phone-harness:logs', async (event) => {
    assertMainRenderer(event);
    return { monitor: server.recentOutput, desktop: desktopControl.getLogs() };
  });
  ipcMain.handle('phone-harness:monitor', async (event, action) => {
    assertMainRenderer(event);
    if (!['start', 'stop', 'restart', 'reload'].includes(action)) throw new Error('Unsupported monitor action.');
    if (action === 'reload') {
      mainWindow.reload();
      return server.status();
    }
    if (action === 'start') await server.start();
    if (action === 'stop') server.stop();
    if (action === 'restart') await server.restart();
    if (action !== 'stop') await mainWindow.loadURL(MONITOR_URL);
    return server.status();
  });
  ipcMain.handle('phone-harness:harness', async (event, action) => {
    assertMainRenderer(event);
    await desktopControl.harness(action);
    return desktopControl.status();
  });
  ipcMain.handle('phone-harness:developer', async (event, action) => {
    assertMainRenderer(event);
    if (action === 'toggle-devtools') {
      mainWindow.webContents.toggleDevTools();
      return { ok: true };
    }
    return desktopControl.developer(action);
  });
}

function installMenu() {
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {
      label: 'Monitor',
      submenu: [
        { label: 'Restart Server', accelerator: 'CmdOrCtrl+Shift+R', click: restartServer },
        { label: 'Reload UI', accelerator: 'CmdOrCtrl+R', click: () => mainWindow?.reload() },
        { type: 'separator' },
        { role: 'quit' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'toggleDevTools' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
      ],
    },
  ]));
}

async function createWindow() {
  windowStatePath = path.join(app.getPath('userData'), 'window-state.json');
  const state = resolveWindowState(
    loadWindowState(windowStatePath),
    typeof screen?.getAllDisplays === 'function'
      ? screen.getAllDisplays().map((display) => display.workArea)
      : [],
  );
  const { maximized, ...bounds } = state;
  mainWindow = new BrowserWindow({
    title: 'Phone Harness Monitor',
    ...bounds,
    minWidth: 760,
    minHeight: 560,
    show: false,
    backgroundColor: '#090d12',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  if (maximized) mainWindow.maximize();
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  mainWindow.on('close', () => saveWindowState(windowStatePath, mainWindow));

  try {
    const mode = await server.start();
    await mainWindow.loadURL(MONITOR_URL);
    installMenu();
    mainWindow.show();
    if (mode === 'external') {
      mainWindow.setTitle('Phone Harness Monitor — external server');
    }
  } catch (error) {
    dialog.showErrorBox('Phone Harness Monitor failed to start', error.message);
    app.quit();
  }
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  });

  app.whenReady().then(() => {
    installIpc();
    return createWindow();
  });

  app.on('window-all-closed', () => app.quit());

  app.on('before-quit', () => {
    quitting = true;
    if (server.owned) server.stop();
  });
}
