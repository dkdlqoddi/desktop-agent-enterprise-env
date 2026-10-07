const { contextBridge, ipcRenderer } = require('electron');

// What the recorder's pages (electron/recorder/) may say to the main process: their job, the video they make in
// order, and how it goes.
contextBridge.exposeInMainWorld(
  'recorder',
  Object.freeze({
    job: () => ipcRenderer.invoke('recorder:job'),
    started: (info) => ipcRenderer.send('recorder:started', info),
    chunk: (data) => ipcRenderer.send('recorder:chunk', data),
    progress: (value) => ipcRenderer.send('recorder:progress', value),
    finished: (info) => ipcRenderer.send('recorder:finished', info),
    failed: (message) => ipcRenderer.send('recorder:failed', String(message || '')),
    abort: () => ipcRenderer.send('recorder:abort'),
    onStop: (callback) => ipcRenderer.on('recorder:stop', () => callback()),
    onStatus: (callback) => ipcRenderer.on('recorder:status', (_event, status) => callback(status)),
  }),
);
