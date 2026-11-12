const fs = require('node:fs/promises');
const path = require('node:path');
const { readJsonFile, writeFileSafely } = require('./files.cjs');

// The chat from anywhere on the desktop: a shortcut that works in every program (Electron's globalShortcut), once the
// user has chosen one, shows the main window with the chat and the caret in its message box; a tray icon opens the
// window, the chat or quits; and, when the user chose it, closing the window leaves the app running in the tray, so
// that the scheduled routines, the reminders and the messenger gateway go on. Electron's parts are handed in (app,
// Tray, Menu, globalShortcut, nativeImage), so tests/quick-access.test.cjs runs this with stand-ins. The settings are
// kept in the app's settings folder (quick-access.json). Nothing here opens a window, and the screen hears only
// 'chat:focus', which carries nothing.

// The shortcuts the user picks from, as Electron's accelerators, the first offered first; no other is taken.
const SHORTCUTS = [
  'CommandOrControl+Shift+Space',
  'CommandOrControl+Alt+Space',
  'Alt+Space',
  'CommandOrControl+Alt+A',
];
/** @typedef {{ shortcut: string, tray: boolean, keep_running: boolean }} Settings */
// No shortcut until the user chooses one: a shortcut the app holds is taken from every other program while it runs,
// where the same keys may already do something, so the app takes none unasked.
/** @type {Readonly<Settings>} */
const DEFAULTS = Object.freeze({ shortcut: '', tray: true, keep_running: false });
// The tray icon's size in points (Linux panels are taller), drawn at each scale factor so it is sharp on high-DPI
// screens.
/** @type {Record<string, number>} */
const TRAY_POINTS = { linux: 24 };
const SCALES = [1, 1.5, 2];

const SHORTCUT_REFUSED = '채팅 바로 가기 키는 목록에서 골라 주세요.';
const SWITCH_REFUSED = '트레이 아이콘과 계속 실행은 켜기·끄기로만 정할 수 있습니다.';
const UNKNOWN = '빠른 호출에 없는 설정입니다.';
const UNREADABLE =
  '빠른 호출 설정 파일(quick-access.json)을 읽지 못했습니다. 저장된 설정을 덮어쓰지 않도록 이번 실행에서는 바꿀 수 없습니다. 앱을 다시 실행해 주세요.';
const UNSAVED = '빠른 호출 설정을 저장하지 못했습니다. 앱 설정 폴더의 권한을 확인해 주세요.';

const validShortcut = (value) => value === '' || SHORTCUTS.includes(value);

// The settings a file holds; a value that is not one of these settings' own (a file edited by hand, or written by a
// later version) takes its default, the others are kept.
function settingsOf(record) {
  const saved = record ?? {};
  return {
    shortcut: validShortcut(saved.shortcut) ? saved.shortcut : DEFAULTS.shortcut,
    tray: typeof saved.tray === 'boolean' ? saved.tray : DEFAULTS.tray,
    keep_running:
      typeof saved.keep_running === 'boolean' ? saved.keep_running : DEFAULTS.keep_running,
  };
}

// How this system names a shortcut's keys: Ctrl on Windows and Linux, Cmd and Option on macOS.
function shortcutLabel(shortcut, platform) {
  /** @type {Record<string, string>} */
  const names =
    platform === 'darwin'
      ? { CommandOrControl: 'Cmd', Alt: 'Option' }
      : { CommandOrControl: 'Ctrl' };
  return shortcut
    .split('+')
    .map((key) => names[key] ?? key)
    .join('+');
}

class QuickAccess {
  /**
   * @param {object} options
   * @param {any} options.app Electron's app: quit() for the tray's 종료, 'activate' and focus() on macOS.
   * @param {any} options.Tray
   * @param {any} options.Menu
   * @param {any} options.globalShortcut
   * @param {any} options.nativeImage
   * @param {() => any} options.window The main window, or null once it has closed.
   * @param {string | null} [options.file] quick-access.json, or null to keep the settings for this run only.
   * @param {string} [options.icon] The app's icon (a PNG), drawn small for the tray.
   * @param {string} [options.platform]
   * @param {(error: Error) => void} [options.failed] Hears of a tray, a shortcut or a file that could not be had.
   */
  constructor({
    app,
    Tray,
    Menu,
    globalShortcut,
    nativeImage,
    window,
    file = null,
    icon = '',
    platform = process.platform,
    failed = () => {},
  }) {
    this.app = app;
    this.Tray = Tray;
    this.Menu = Menu;
    this.globalShortcut = globalShortcut;
    this.nativeImage = nativeImage;
    this.window = window;
    this.file = file;
    this.icon = icon;
    this.platform = platform;
    this.failed = failed;
    /** @type {Settings} */
    this.settings = { ...DEFAULTS };
    // The shortcut this app holds now: '' while it holds none (off, or another program has it).
    this.registered = '';
    this.tray = null;
    // Making the tray icon failed: this system shows none, and closing the window always quits.
    this.trayFailed = false;
    this.started = false;
    this.stopped = false;
    // The window was hidden by closing it (keep_running): the Dock brings it back on macOS.
    this.tucked = false;
    this.loaded = null;
    /** @type {Promise<unknown>} */
    this.queue = Promise.resolve();
    // Why the settings could not be read; while set, nothing is saved over quick-access.json.
    this.unavailable = '';
  }

  load() {
    this.loaded ??= (async () => {
      if (!this.file) return;
      try {
        // A damaged file is set aside (files.cjs) and the defaults apply.
        this.settings = settingsOf(await readJsonFile(this.file));
      } catch (error) {
        // Held by another program for good, or not readable: the defaults this run, and the file left as it is.
        this.unavailable = UNREADABLE;
        this.failed(error);
      }
    })();
    return this.loaded;
  }

  // Reads the settings and sets up what they ask for, once the main window exists (main.cjs). It never fails: a
  // shortcut or a tray that cannot be had is told by get() (registered, tray_available), and the app goes on.
  async start() {
    await this.load();
    if (this.stopped || this.started) return;
    this.started = true;
    this.app.on('activate', () => {
      if (this.tucked) this.open();
    });
    this.apply();
  }

  get() {
    return {
      ...this.settings,
      registered: !!this.settings.shortcut && this.registered === this.settings.shortcut,
      tray_available: !this.trayFailed,
      presets: SHORTCUTS.map((shortcut) => ({
        shortcut,
        label: shortcutLabel(shortcut, this.platform),
      })),
    };
  }

  // What the admin page shows (quick.get): the saved settings, never the defaults of a file not read yet.
  async read() {
    await this.load();
    return this.get();
  }

  // The admin page's change (quick.update): each value checked, saved, then applied. A shortcut chosen again is
  // registered again, should the program that held it have let it go.
  update(params) {
    const job = this.queue.then(async () => {
      // A change made over settings not read yet would write their defaults over them.
      await this.load();
      if (this.unavailable) throw new Error(this.unavailable);
      const next = { ...this.settings };
      for (const [key, value] of Object.entries(params ?? {})) {
        if (key === 'shortcut') {
          if (!validShortcut(value)) throw new Error(SHORTCUT_REFUSED);
          next.shortcut = value;
        } else if (key === 'tray' || key === 'keep_running') {
          if (typeof value !== 'boolean') throw new Error(SWITCH_REFUSED);
          next[key] = value;
        } else throw new Error(UNKNOWN);
      }
      if (this.file)
        try {
          await fs.mkdir(path.dirname(this.file), { recursive: true });
          await writeFileSafely(this.file, `${JSON.stringify(next, null, 2)}\n`);
        } catch {
          throw new Error(UNSAVED);
        }
      this.settings = next;
      this.apply();
      return this.get();
    });
    this.queue = job.catch(() => {});
    return job;
  }

  apply() {
    if (!this.started || this.stopped) return;
    this.applyShortcut();
    this.applyTray();
  }

  applyShortcut() {
    const wanted = this.settings.shortcut;
    if (this.registered && this.registered === wanted) return;
    this.release();
    if (!wanted) return;
    try {
      // false when another program holds it: the system gives each shortcut to one program.
      if (this.globalShortcut.register(wanted, () => this.open({ chat: true })))
        this.registered = wanted;
    } catch (error) {
      this.failed(error);
    }
  }

  release() {
    const held = this.registered;
    this.registered = '';
    if (!held) return;
    try {
      this.globalShortcut.unregister(held);
    } catch (error) {
      this.failed(error);
    }
  }

  applyTray() {
    if (!this.settings.tray) return this.removeTray();
    // Made already, or this system could make none: it is not tried again in this run.
    if (this.tray || this.trayFailed) return;
    let tray = null;
    try {
      const image = this.trayImage();
      if (!image) throw new Error(`트레이 아이콘의 그림을 읽지 못했습니다: ${this.icon}`);
      tray = new this.Tray(image);
      tray.setToolTip('Private Agent');
      tray.setContextMenu(
        this.Menu.buildFromTemplate([
          { label: '열기', click: () => this.open() },
          { label: '채팅 열기', click: () => this.open({ chat: true }) },
          { type: 'separator' },
          { label: '종료', click: () => this.app.quit() },
        ]),
      );
      // A click on the icon opens the window; its menu is on the other button (on Linux, on any click).
      tray.on('click', () => this.open());
      this.tray = tray;
      this.trayFailed = false;
    } catch (error) {
      try {
        tray?.destroy();
      } catch {
        /* Gone with the failure. */
      }
      this.trayFailed = true;
      this.failed(error);
    }
  }

  // The app's icon drawn at the tray's size; null when it cannot be read, as a tray with no picture would be no tray
  // the user can find.
  trayImage() {
    const source = this.icon ? this.nativeImage.createFromPath(this.icon) : null;
    if (!source || source.isEmpty()) return null;
    const points = TRAY_POINTS[this.platform] ?? 16;
    const image = this.nativeImage.createEmpty();
    for (const scaleFactor of SCALES) {
      const size = Math.round(points * scaleFactor);
      image.addRepresentation({
        scaleFactor,
        buffer: source.resize({ width: size, height: size, quality: 'best' }).toPNG(),
      });
    }
    return image;
  }

  removeTray() {
    const tray = this.tray;
    this.tray = null;
    try {
      tray?.destroy();
    } catch (error) {
      this.failed(error);
    }
  }

  // Shows the main window in front, restored when minimized and shown again when hidden; `chat` also asks its screen
  // for the chat with the caret in the message box. False when there is no window to show.
  open({ chat = false } = {}) {
    if (this.stopped) return false;
    const win = this.window();
    if (!win || win.isDestroyed()) return false;
    this.tucked = false;
    if (win.isMinimized()) win.restore();
    win.show();
    // macOS gives the keyboard to an app only once it is the active one.
    if (this.platform === 'darwin') this.app.focus?.({ steal: true });
    win.focus();
    if (chat && !win.webContents.isDestroyed()) win.webContents.send('chat:focus');
    return true;
  }

  // The main window's close (main.cjs): kept running in the tray, the window hides instead, with what is on it, and
  // the app goes on. False, with nothing done, while the option is off or no tray icon exists: the window closes as it
  // always has.
  keepOnClose(event) {
    if (this.stopped || !this.settings.keep_running || !this.tray) return false;
    const win = this.window();
    if (!win || win.isDestroyed()) return false;
    event.preventDefault();
    win.hide();
    this.tucked = true;
    return true;
  }

  // Quitting (main.cjs before-quit): the shortcut goes back to the system and the tray icon goes away; nothing is set
  // up again after this.
  stop() {
    this.stopped = true;
    this.release();
    this.removeTray();
  }
}

module.exports = { DEFAULTS, QuickAccess, SHORTCUTS, shortcutLabel };
