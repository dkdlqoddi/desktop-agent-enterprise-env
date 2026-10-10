const fs = require('node:fs');
const fsp = require('node:fs/promises');
const path = require('node:path');
const { Readable } = require('node:stream');
const { BrowserWindow, ipcMain, screen, session, webContents } = require('electron');

// Records the app's window for a demo, then makes the video with captions. Both run in pages of their own
// (electron/recorder/) in a session of their own: the app's session grants no permission at all, this one only the
// capture of the app's window to its capture page. The capture page records the window's page itself (tab capture),
// so other windows on the screen never get into the video; it also shows a small "recording" panel with a stop
// button, which the capture leaves out. The render page plays the recording into a canvas with the captions, cards
// and sped-up waits drawn on it, and records that canvas.

const SCHEME = 'recorder';
const ORIGIN = `${SCHEME}://app`;
// For protocol.registerSchemesAsPrivileged before the app is ready; `stream` lets the render page play a recording
// read in parts.
const PRIVILEGES = {
  scheme: SCHEME,
  privileges: { standard: true, secure: true, supportFetchAPI: true, stream: true },
};
const PAGES = {
  'page.html': 'text/html; charset=utf-8',
  'page.js': 'text/javascript; charset=utf-8',
  'page.css': 'text/css; charset=utf-8',
};
const CSP =
  "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-src 'none'; object-src 'none'";
// MP4 with H.264 first: what PowerPoint and Windows players take. The Baseline profile is OpenH264's, the same on
// every PC; Windows' own encoder, which the High profile chose, wrote frames other players could not decode
// (2026-10-05).
const TYPES = [
  'video/mp4;codecs=avc1.42E028',
  'video/mp4;codecs=avc1.42E01E',
  'video/webm;codecs=vp9',
  'video/webm;codecs=vp8',
  'video/webm',
];
const CAPTURE = { maxWidth: 1920, maxHeight: 1080, frameRate: 30, bitrate: 6_000_000 };
const RENDER = { frameRate: 30, bitrate: 6_000_000 };
const PANEL = { width: 380, height: 92, margin: 16 };

class Stopped extends Error {
  constructor(message = '영상 만들기를 중지했습니다.') {
    super(message);
    this.name = 'Stopped';
  }
}

// The recording of a file, served to the render page in the parts it asks for.
async function fileResponse(file, range) {
  const { size } = await fsp.stat(file);
  const headers = { 'Content-Type': 'video/mp4', 'Accept-Ranges': 'bytes' };
  const match = /^bytes=(\d*)-(\d*)$/.exec(range || '');
  if (!match || (!match[1] && !match[2]))
    return new Response(Readable.toWeb(fs.createReadStream(file)), {
      headers: { ...headers, 'Content-Length': String(size) },
    });
  let start = match[1] ? Number(match[1]) : Math.max(0, size - Number(match[2]));
  let end = match[1] && match[2] ? Math.min(Number(match[2]), size - 1) : size - 1;
  if (start >= size || start > end)
    return new Response('', { status: 416, headers: { 'Content-Range': `bytes */${size}` } });
  return new Response(Readable.toWeb(fs.createReadStream(file, { start, end })), {
    status: 206,
    headers: {
      ...headers,
      'Content-Length': String(end - start + 1),
      'Content-Range': `bytes ${start}-${end}/${size}`,
    },
  });
}

class Recorder {
  constructor({ directory = path.join(__dirname, 'recorder') } = {}) {
    this.directory = directory;
    this.jobs = new Map(); // webContents id → job
    this.source = null; // The recording the render page plays.
    this.session = null;
  }

  setup() {
    if (this.session) return;
    // In memory: nothing of these pages is kept on disk.
    const own = session.fromPartition('recorder');
    own.protocol.handle(SCHEME, (request) => this.serve(request));
    own.setPermissionRequestHandler((contents, permission, callback) =>
      callback(this.mayCapture(contents, permission)),
    );
    own.setPermissionCheckHandler((contents, permission) => this.mayCapture(contents, permission));
    own.setDisplayMediaRequestHandler((request, callback) => this.grant(request, callback));
    ipcMain.handle('recorder:job', (event) => this.jobOf(event)?.spec ?? null);
    ipcMain.on('recorder:started', (event, info) => this.jobOf(event)?.started(info));
    ipcMain.on('recorder:chunk', (event, data) => this.jobOf(event)?.write(data));
    ipcMain.on('recorder:progress', (event, value) => this.jobOf(event)?.progress(value));
    ipcMain.on('recorder:finished', (event, info) => this.jobOf(event)?.finish(info));
    ipcMain.on('recorder:failed', (event, message) => this.jobOf(event)?.fail(message));
    ipcMain.on('recorder:abort', (event) => this.jobOf(event)?.abort());
    this.session = own;
  }

  jobOf(event) {
    const job = this.jobs.get(event.sender.id);
    return job && event.senderFrame === event.sender.mainFrame ? job : null;
  }

  mayCapture(contents, permission) {
    return (
      (permission === 'media' || permission === 'display-capture') &&
      !!contents &&
      this.jobs.get(contents.id)?.spec.mode === 'capture'
    );
  }

  // The capture page asks once for a screen to record: it gets the app's window, never anything else.
  grant(request, callback) {
    const contents = request.frame ? webContents.fromFrame(request.frame) : null;
    const job = contents ? this.jobs.get(contents.id) : null;
    const target = job?.spec.mode === 'capture' && !job.granted ? job.target : null;
    if (!target || target.isDestroyed()) return callback({});
    job.granted = true;
    callback({ video: target.webContents.mainFrame });
  }

  async serve(request) {
    const url = new URL(request.url);
    if (url.host !== 'app' || request.method !== 'GET') return new Response('', { status: 403 });
    const name = url.pathname.replace(/^\//, '');
    try {
      if (Object.hasOwn(PAGES, name))
        return new Response(await fsp.readFile(path.join(this.directory, name)), {
          headers: {
            'Content-Type': PAGES[name],
            'Content-Security-Policy': CSP,
            'X-Content-Type-Options': 'nosniff',
          },
        });
      if (name === 'media/source' && this.source)
        return await fileResponse(this.source, request.headers.get('range'));
    } catch {
      /* Not found below. */
    }
    return new Response('', { status: 404 });
  }

  // A window of one of the pages, with its job: what the page asks for (spec), the file its chunks are written to, and
  // how it ends.
  async open(options, spec, file, handlers = {}) {
    this.setup();
    const win = new BrowserWindow({
      ...options,
      show: false,
      autoHideMenuBar: true,
      backgroundColor: '#1c2433',
      webPreferences: {
        session: this.session,
        preload: path.join(__dirname, 'recorder-preload.cjs'),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        spellcheck: false,
        // The pages keep drawing and recording while hidden or behind other windows.
        backgroundThrottling: false,
      },
    });
    win.removeMenu();
    win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    win.webContents.on('will-navigate', (event) => event.preventDefault());
    const id = win.webContents.id;
    const output = fs.createWriteStream(file);
    let failure = null;
    output.on('error', (/** @type {NodeJS.ErrnoException} */ error) => {
      failure = new Error(
        `영상 파일을 쓰지 못했습니다. 디스크 공간과 폴더 권한을 확인해 주세요. (${error.code || error.message})`,
      );
      job.fail(failure.message);
    });
    let resolveStart;
    let rejectStart;
    let resolveDone;
    let rejectDone;
    const started = new Promise((resolve, reject) => {
      resolveStart = resolve;
      rejectStart = reject;
    });
    const done = new Promise((resolve, reject) => {
      resolveDone = resolve;
      rejectDone = reject;
    });
    // A start that was never awaited must not be an unhandled rejection.
    started.catch(() => {});
    done.catch(() => {});
    // Settles once the file is closed, so it can be read whole at once.
    const close = () =>
      new Promise((resolve) => {
        if (output.closed) return resolve(undefined);
        output.once('close', () => resolve(undefined));
        output.end();
      });
    // However the job ends (finished, failed, its window closed), the file is closed once it is over.
    const closed = new Promise((resolve) => output.once('close', () => resolve(undefined)));
    const job = {
      spec,
      target: handlers.target,
      granted: false,
      settled: false,
      /** @type {any} */
      info: null,
      started: (info) => {
        job.info = info;
        resolveStart({ ...info, at: Date.now() });
      },
      write: (data) => {
        if (!job.settled && !failure) output.write(Buffer.from(data));
      },
      progress: (value) => handlers.onProgress?.(value),
      finish: async (info) => {
        if (job.settled) return;
        job.settled = true;
        await close();
        if (failure) rejectDone(failure);
        else resolveDone({ file, mime: info?.mime || job.info?.mime || '' });
      },
      fail: async (message) => {
        if (job.settled) return;
        job.settled = true;
        await close();
        const error = new Error(String(message || '화면을 녹화하지 못했습니다.'));
        rejectStart(error);
        rejectDone(error);
      },
      abort: () => handlers.onAbort?.(),
    };
    this.jobs.set(id, job);
    win.on('closed', () => {
      this.jobs.delete(id);
      if (!job.settled) {
        job.settled = true;
        void close().then(() => {
          const error = handlers.closed?.() ?? new Stopped('녹화 창이 닫혔습니다.');
          rejectStart(error);
          rejectDone(error);
        });
      }
    });
    try {
      await win.loadURL(`${ORIGIN}/page.html`);
    } catch (error) {
      win.destroy();
      throw new Error(`녹화 화면을 열지 못했습니다: ${error.message}`);
    }
    return { win, job, started, done, closed };
  }

  // Starts recording the page of `target` into `file`, an MP4 written in fragments as it goes, and shows the
  // recording panel near the window's corner. `onAbort` is the panel's stop button.
  /**
   * @param {Electron.BrowserWindow} target
   * @param {string} file
   * @param {{ onAbort?: () => void }} [options]
   */
  async record(target, file, { onAbort } = {}) {
    const display = screen.getDisplayMatching(target.getBounds()).workArea;
    const { win, started, done, closed } = await this.open(
      {
        width: PANEL.width,
        height: PANEL.height,
        x: display.x + display.width - PANEL.width - PANEL.margin,
        y: display.y + display.height - PANEL.height - PANEL.margin,
        title: 'Private Agent 녹화',
        frame: false,
        resizable: false,
        minimizable: false,
        maximizable: false,
        fullscreenable: false,
        skipTaskbar: true,
        // Never takes the focus from the app, whose screen the demo is working.
        focusable: false,
        alwaysOnTop: true,
      },
      { mode: 'capture', types: TYPES, ...CAPTURE },
      file,
      { target, onAbort },
    );
    // Above the app's full-screen window.
    win.setAlwaysOnTop(true, 'screen-saver');
    win.showInactive();
    let info;
    try {
      info = await Promise.race([
        started,
        new Promise((_, reject) =>
          setTimeout(
            () => reject(new Error('화면 녹화가 30초 안에 시작되지 않았습니다.')),
            30_000,
          ).unref(),
        ),
      ]);
    } catch (error) {
      if (!win.isDestroyed()) win.destroy();
      throw error;
    }
    return {
      info,
      // What the panel shows: the step and caption of the moment.
      status: (value) => {
        if (!win.isDestroyed()) win.webContents.send('recorder:status', value);
      },
      // Ends the recording; the file is complete once this settles, and closed even when it rejects, so what the
      // recording wrote before it failed can still be kept.
      stop: async () => {
        if (!win.isDestroyed()) win.webContents.send('recorder:stop');
        try {
          return await Promise.race([
            done,
            new Promise((_, reject) =>
              setTimeout(() => reject(new Error('녹화를 마무리하지 못했습니다.')), 30_000).unref(),
            ),
          ]);
        } finally {
          if (!win.isDestroyed()) win.destroy();
          await Promise.race([
            closed,
            new Promise((resolve) => setTimeout(resolve, 10_000).unref()),
          ]);
        }
      },
      // Settles when the recording ends by itself: the page failed, or the app's window closed.
      done,
    };
  }

  // Makes the video of `spec` (the recording in `spec.sourceFile`, its captions, cards and sped-up parts) into `file`,
  // showing it as it is drawn. Closing that window stops it.
  /**
   * @param {Record<string, any>} spec
   * @param {string} file
   * @param {{ onProgress?: (value: number) => void }} [options]
   */
  async render(spec, file, { onProgress } = {}) {
    const { sourceFile, ...page } = spec;
    this.source = sourceFile;
    let win;
    try {
      const opened = await this.open(
        {
          width: 980,
          height: 660,
          minWidth: 640,
          minHeight: 460,
          title: 'Private Agent 시연 영상 만들기',
        },
        { mode: 'render', types: TYPES, ...RENDER, ...page, source: `${ORIGIN}/media/source` },
        file,
        {
          onProgress,
          onAbort: () => win?.destroy(),
          closed: () => new Stopped(),
        },
      );
      win = opened.win;
      win.show();
      return await opened.done;
    } catch (error) {
      await fsp.rm(file, { force: true });
      throw error;
    } finally {
      this.source = null;
      if (win && !win.isDestroyed()) win.destroy();
    }
  }
}

module.exports = { Recorder, Stopped, PRIVILEGES, TYPES };
