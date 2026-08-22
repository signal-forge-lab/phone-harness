const { contextBridge, ipcRenderer } = require('electron');

const invoke = (channel, payload) => ipcRenderer.invoke(channel, payload);

contextBridge.exposeInMainWorld('phoneHarnessDesktop', Object.freeze({
  getStatus: () => invoke('phone-harness:status'),
  getLogs: () => invoke('phone-harness:logs'),
  monitor: Object.freeze({
    start: () => invoke('phone-harness:monitor', 'start'),
    stop: () => invoke('phone-harness:monitor', 'stop'),
    restart: () => invoke('phone-harness:monitor', 'restart'),
    reload: () => invoke('phone-harness:monitor', 'reload'),
  }),
  harness: Object.freeze({
    start: () => invoke('phone-harness:harness', 'start'),
    stop: () => invoke('phone-harness:harness', 'stop'),
    restart: () => invoke('phone-harness:harness', 'restart'),
  }),
  developer: Object.freeze({
    build: () => invoke('phone-harness:developer', 'build'),
    buildAndRestart: () => invoke('phone-harness:developer', 'build-and-restart'),
    buildExe: () => invoke('phone-harness:developer', 'build-exe'),
    toggleDevTools: () => invoke('phone-harness:developer', 'toggle-devtools'),
  }),
}));
