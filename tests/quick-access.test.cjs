const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { DEFAULTS, QuickAccess, SHORTCUTS, shortcutLabel } = require('../electron/quick-access.cjs');

// The preset offered first, which the tests choose where they need a shortcut.
const FIRST = 'CommandOrControl+Shift+Space';

// Electron's parts as far as the quick access uses them. `taken`: shortcuts another program holds; `trayThrows`: a
// system with no tray; `noIcon`: an icon file that cannot be read.
function electron({ taken = [], trayThrows = false, noIcon = false } = {}) {
  const trays = [];
  class Tray extends EventEmitter {
    constructor(image) {
      super();
      if (trayThrows) throw new Error('no tray on this system');
      this.image = image;
      this.destroyed = false;
      trays.push(this);
    }
    setToolTip(text) {
      this.tooltip = text;
    }
    setContextMenu(menu) {
      this.menu = menu;
    }
    destroy() {
      this.destroyed = true;
    }
  }
  const Menu = { buildFromTemplate: (items) => ({ items }) };
  // The shortcuts this app holds, with what each calls, and every shortcut it asked the system for.
  const held = new Map();
  const asked = [];
  const globalShortcut = {
    taken: new Set(taken),
    register(accelerator, callback) {
      asked.push(accelerator);
      if (this.taken.has(accelerator) || held.has(accelerator)) return false;
      held.set(accelerator, callback);
      return true;
    },
    unregister(accelerator) {
      held.delete(accelerator);
    },
  };
  const image = (file) => ({
    reps: [],
    isEmpty: () => !file,
    resize: ({ width, height }) => ({ toPNG: () => Buffer.from(`${width}x${height}`) }),
    addRepresentation(rep) {
      this.reps.push(rep);
    },
  });
  const nativeImage = {
    createFromPath: (file) => image(noIcon ? '' : file),
    createEmpty: () => image(''),
  };
  const app = Object.assign(new EventEmitter(), {
    quits: 0,
    focused: [],
    quit() {
      this.quits++;
    },
    focus(options) {
      this.focused.push(options);
    },
  });
  return { app, Tray, Menu, globalShortcut, nativeImage, trays, held, asked };
}

// The main window as far as the quick access uses it: what it was asked to do, and what its screen was sent.
class FakeWindow {
  destroyed = false;
  visible = true;
  minimized = false;
  calls = [];
  webContents = {
    sent: [],
    send: (...message) => this.webContents.sent.push(message),
    isDestroyed: () => false,
  };
  isDestroyed() {
    return this.destroyed;
  }
  isMinimized() {
    return this.minimized;
  }
  restore() {
    this.minimized = false;
    this.calls.push('restore');
  }
  show() {
    this.visible = true;
    this.calls.push('show');
  }
  hide() {
    this.visible = false;
    this.calls.push('hide');
  }
  focus() {
    this.calls.push('focus');
  }
}

function setup(options = {}, { file = null, platform = 'win32', window = new FakeWindow() } = {}) {
  const parts = electron(options);
  const errors = [];
  const quick = new QuickAccess({
    ...parts,
    window: () => window,
    file,
    icon: path.join('app', 'assets', 'icon.png'),
    platform,
    failed: (error) => errors.push(error),
  });
  return { ...parts, quick, window, errors };
}

// Started, with the preset offered first chosen, as a user turns the shortcut on.
async function withShortcut(options, settings) {
  const state = setup(options, settings);
  await state.quick.start();
  await state.quick.update({ shortcut: FIRST });
  return state;
}

// The window's close event as Electron gives it.
function closing() {
  return {
    prevented: false,
    preventDefault() {
      this.prevented = true;
    },
  };
}

const settingsOf = ({ shortcut, tray, keep_running }) => ({ shortcut, tray, keep_running });

async function settingsFile(t) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'agent-quick-test-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  return path.join(directory, 'quick-access.json');
}

test('by default no shortcut is held, the tray icon shows, and closing the window is left alone', async () => {
  const { quick, held, asked, trays, window } = setup();
  assert.deepEqual(DEFAULTS, { shortcut: '', tray: true, keep_running: false });
  await quick.start();
  // Started once, however often asked.
  await quick.start();
  const state = quick.get();
  assert.deepEqual(settingsOf(state), DEFAULTS);
  assert.equal(state.registered, false);
  assert.equal(state.tray_available, true);
  // The presets are offered all the same, the first one first.
  assert.deepEqual(
    state.presets.map((preset) => preset.shortcut),
    SHORTCUTS,
  );
  assert.deepEqual(state.presets[0], { shortcut: FIRST, label: 'Ctrl+Shift+Space' });
  assert.deepEqual(asked, []);
  assert.equal(held.size, 0);
  assert.equal(trays.length, 1);
  assert.equal(trays[0].tooltip, 'Private Agent');
  // Closing the window is the main process's as ever: nothing prevented, nothing hidden.
  const event = closing();
  assert.equal(quick.keepOnClose(event), false);
  assert.equal(event.prevented, false);
  assert.deepEqual(window.calls, []);
});

test('a fresh start registers nothing with the system; a shortcut is held only once the user chooses one', async (t) => {
  // No file yet, a file without a shortcut, one of an unknown shortcut, and a damaged one: none asks for a key.
  const file = await settingsFile(t);
  for (const contents of [
    null,
    JSON.stringify({ tray: true, keep_running: true }),
    JSON.stringify({ shortcut: 'Ctrl+Q' }),
    '{"shortcut": ',
  ]) {
    await fs.rm(file, { force: true });
    if (contents !== null) await fs.writeFile(file, contents);
    const { quick, asked, held } = setup({}, { file });
    await quick.start();
    assert.equal(quick.get().shortcut, '', String(contents));
    assert.deepEqual(asked, [], String(contents));
    assert.equal(held.size, 0, String(contents));
  }
  const { quick, asked, held } = setup({}, { file });
  await quick.start();
  assert.equal((await quick.update({ shortcut: FIRST })).registered, true);
  assert.deepEqual(asked, [FIRST]);
  assert.deepEqual([...held.keys()], [FIRST]);
});

test('the shortcut brings the window back in front and asks its screen for the chat, carrying nothing', async () => {
  const { held, window } = await withShortcut();
  window.visible = false;
  window.minimized = true;
  held.get(FIRST)();
  assert.deepEqual(window.calls, ['restore', 'show', 'focus']);
  assert.equal(window.visible, true);
  assert.deepEqual(window.webContents.sent, [['chat:focus']]);
  // A window shown already only comes to the front.
  window.calls = [];
  held.get(FIRST)();
  assert.deepEqual(window.calls, ['show', 'focus']);
  assert.deepEqual(window.webContents.sent, [['chat:focus'], ['chat:focus']]);
  // macOS hands the keyboard to the app as well.
  const mac = await withShortcut({}, { platform: 'darwin' });
  mac.held.get(FIRST)();
  assert.deepEqual(mac.app.focused, [{ steal: true }]);
  // Without a window (closed, destroyed) there is nothing to show, and nothing fails.
  const none = setup({}, { window: null });
  await none.quick.start();
  assert.equal(none.quick.open({ chat: true }), false);
  const gone = await withShortcut();
  gone.window.destroyed = true;
  assert.equal(gone.quick.open({ chat: true }), false);
  assert.deepEqual(gone.window.webContents.sent, []);
});

test('a shortcut changed is registered in place of the old one, and none is held when it is off', async () => {
  const { quick, held } = await withShortcut();
  assert.deepEqual([...held.keys()], [FIRST]);
  const changed = await quick.update({ shortcut: 'Alt+Space' });
  assert.equal(changed.shortcut, 'Alt+Space');
  assert.equal(changed.registered, true);
  assert.deepEqual([...held.keys()], ['Alt+Space']);
  const off = await quick.update({ shortcut: '' });
  assert.equal(off.shortcut, '');
  assert.equal(off.registered, false);
  assert.equal(held.size, 0);
  await quick.update({ shortcut: 'CommandOrControl+Alt+A' });
  assert.deepEqual([...held.keys()], ['CommandOrControl+Alt+A']);
  // The same one again keeps it as it is.
  assert.equal((await quick.update({ shortcut: 'CommandOrControl+Alt+A' })).registered, true);
  assert.deepEqual([...held.keys()], ['CommandOrControl+Alt+A']);
});

test('a shortcut another program holds is reported, and registered once chosen again after it is free', async () => {
  const { quick, held, globalShortcut, errors } = setup({ taken: [FIRST] });
  await quick.start();
  const state = await quick.update({ shortcut: FIRST });
  assert.equal(state.shortcut, FIRST);
  assert.equal(state.registered, false);
  assert.equal(held.size, 0);
  // Another one that is free works at once, and going back to the taken one gives it up.
  assert.equal((await quick.update({ shortcut: 'Alt+Space' })).registered, true);
  assert.equal((await quick.update({ shortcut: FIRST })).registered, false);
  assert.equal(held.size, 0);
  // The other program let it go: choosing it again (다시 시도) registers it.
  globalShortcut.taken.clear();
  assert.equal((await quick.update({ shortcut: FIRST })).registered, true);
  // A register that throws is told as one that failed, and logged.
  assert.equal(errors.length, 0);
  globalShortcut.register = () => {
    throw new Error('conversion failure');
  };
  assert.equal((await quick.update({ shortcut: 'Alt+Space' })).registered, false);
  assert.equal(held.size, 0);
  assert.match(errors.at(-1).message, /conversion failure/);
});

test('an unknown shortcut, a switch that is no boolean or a setting there is not is refused, and nothing changes', async (t) => {
  const file = await settingsFile(t);
  const { quick, held } = await withShortcut({}, { file });
  const kept = await fs.readFile(file, 'utf8');
  for (const [change, message] of [
    [{ shortcut: 'Ctrl+Q' }, /목록에서 골라/],
    [{ shortcut: 'CommandOrControl+Shift+Space ' }, /목록에서 골라/],
    [{ shortcut: 'commandorcontrol+alt+space' }, /목록에서 골라/],
    [{ shortcut: null }, /목록에서 골라/],
    [{ shortcut: 42 }, /목록에서 골라/],
    [{ tray: 'false' }, /켜기·끄기/],
    [{ keep_running: 1 }, /켜기·끄기/],
    // A change with one value refused changes none of the others.
    [{ shortcut: 'Alt+Space', keep_running: 'yes' }, /켜기·끄기/],
    [{ shortcuts: 'Alt+Space' }, /없는 설정/],
  ])
    await assert.rejects(quick.update(change), message, JSON.stringify(change));
  assert.deepEqual(settingsOf(quick.get()), { ...DEFAULTS, shortcut: FIRST });
  assert.deepEqual([...held.keys()], [FIRST]);
  assert.equal(await fs.readFile(file, 'utf8'), kept);
});

test('the tray menu opens the window, opens the chat and quits; a click on the icon opens the window', async () => {
  const { quick, trays, window, app } = setup();
  await quick.start();
  const { items } = trays[0].menu;
  assert.deepEqual(
    items.map((item) => item.label ?? item.type),
    ['열기', '채팅 열기', 'separator', '종료'],
  );
  window.visible = false;
  items[0].click();
  assert.equal(window.visible, true);
  assert.deepEqual(window.webContents.sent, []);
  window.visible = false;
  items[1].click();
  assert.equal(window.visible, true);
  assert.deepEqual(window.webContents.sent, [['chat:focus']]);
  window.visible = false;
  trays[0].emit('click');
  assert.equal(window.visible, true);
  assert.equal(window.webContents.sent.length, 1);
  assert.equal(app.quits, 0);
  items[3].click();
  assert.equal(app.quits, 1);
});

test('the tray icon is the app icon drawn at the tray size, for each scale factor', async () => {
  for (const [platform, sizes] of [
    ['win32', ['16x16', '24x24', '32x32']],
    ['darwin', ['16x16', '24x24', '32x32']],
    ['linux', ['24x24', '36x36', '48x48']],
  ]) {
    const { quick, trays } = setup({}, { platform });
    await quick.start();
    assert.deepEqual(
      trays[0].image.reps.map((rep) => [rep.scaleFactor, rep.buffer.toString()]),
      [
        [1, sizes[0]],
        [1.5, sizes[1]],
        [2, sizes[2]],
      ],
      platform,
    );
  }
});

test('without a tray the app goes on: the shortcut works, and closing the window still closes it', async () => {
  for (const options of [{ trayThrows: true }, { noIcon: true }]) {
    const { quick, held, trays, window, errors } = await withShortcut(options);
    const state = quick.get();
    assert.equal(state.tray_available, false);
    assert.equal(state.registered, true);
    assert.equal(trays.length, 0);
    assert.equal(errors.length, 1);
    // Keeping the app running needs the tray: the window is not hidden where nothing would bring it back.
    await quick.update({ keep_running: true });
    const event = closing();
    assert.equal(quick.keepOnClose(event), false);
    assert.equal(event.prevented, false);
    assert.equal(window.visible, true);
    // Not tried again in this run, nor logged again.
    assert.equal(errors.length, 1);
    window.visible = false;
    held.get(FIRST)();
    assert.equal(window.visible, true);
  }
});

test('keep running hides the window on close only while it is on and a tray icon exists', async () => {
  const { quick, window, app } = setup();
  await quick.start();
  assert.equal((await quick.update({ keep_running: true })).keep_running, true);
  const event = closing();
  assert.equal(quick.keepOnClose(event), true);
  assert.equal(event.prevented, true);
  assert.equal(window.visible, false);
  // macOS: the Dock icon brings back a window closed into the tray, and only such a window.
  app.emit('activate');
  assert.equal(window.visible, true);
  window.visible = false;
  app.emit('activate');
  assert.equal(window.visible, false);
  window.visible = true;
  // The tray icon turned off: the window closes again.
  await quick.update({ tray: false });
  assert.equal(quick.keepOnClose(closing()), false);
  await quick.update({ tray: true });
  assert.equal(quick.keepOnClose(closing()), true);
  // The option off.
  await quick.update({ keep_running: false });
  assert.equal(quick.keepOnClose(closing()), false);
  // Quitting (the tray's 종료, a restore's restart): the window closes, kept running or not.
  await quick.update({ keep_running: true });
  quick.stop();
  const quitting = closing();
  assert.equal(quick.keepOnClose(quitting), false);
  assert.equal(quitting.prevented, false);
});

test('quitting gives the shortcut back and removes the tray icon, and nothing is set up again after', async () => {
  const { quick, held, asked, trays, window } = await withShortcut();
  assert.deepEqual([...held.keys()], [FIRST]);
  quick.stop();
  assert.equal(held.size, 0);
  assert.equal(trays[0].destroyed, true);
  assert.equal(quick.get().registered, false);
  await quick.update({ shortcut: 'Alt+Space' });
  assert.equal(held.size, 0);
  assert.deepEqual(asked, [FIRST]);
  assert.equal(trays.length, 1);
  assert.equal(quick.open({ chat: true }), false);
  assert.deepEqual(window.webContents.sent, []);
  // A start the quit came before sets nothing up, a saved shortcut neither.
  const late = setup();
  await late.quick.update({ shortcut: FIRST });
  late.quick.stop();
  await late.quick.start();
  assert.deepEqual(late.asked, []);
  assert.equal(late.held.size, 0);
  assert.equal(late.trays.length, 0);
});

test('the settings file round-trips; a damaged one, or values not its own, fall back to the defaults', async (t) => {
  const file = await settingsFile(t);
  const first = setup({}, { file });
  await first.quick.start();
  await first.quick.update({ shortcut: 'Alt+Space', keep_running: true });
  assert.deepEqual(JSON.parse(await fs.readFile(file, 'utf8')), {
    shortcut: 'Alt+Space',
    tray: true,
    keep_running: true,
  });
  const second = setup({}, { file });
  await second.quick.start();
  assert.deepEqual(settingsOf(second.quick.get()), {
    shortcut: 'Alt+Space',
    tray: true,
    keep_running: true,
  });
  assert.deepEqual([...second.held.keys()], ['Alt+Space']);
  await second.quick.update({ shortcut: '', tray: false });
  const third = setup({}, { file });
  await third.quick.start();
  assert.deepEqual(settingsOf(third.quick.get()), {
    shortcut: '',
    tray: false,
    keep_running: true,
  });
  assert.equal(third.held.size, 0);
  assert.equal(third.trays.length, 0);
  // A value that is not one of its own (edited by hand, a later version) takes its default; the others are kept.
  await fs.writeFile(
    file,
    JSON.stringify({ shortcut: 'Ctrl+Q', tray: 'yes', keep_running: true, more: 1 }),
  );
  const odd = setup({}, { file });
  await odd.quick.start();
  assert.deepEqual(settingsOf(odd.quick.get()), { ...DEFAULTS, keep_running: true });
  assert.equal(odd.held.size, 0);
  // A damaged file is set aside (files.cjs), and saving starts a new one.
  await fs.writeFile(file, '{"shortcut": ');
  const damaged = setup({}, { file });
  await damaged.quick.start();
  assert.deepEqual(settingsOf(damaged.quick.get()), DEFAULTS);
  assert.equal(damaged.held.size, 0);
  const names = await fs.readdir(path.dirname(file));
  assert.equal(names.filter((name) => name.startsWith('quick-access.json.corrupt-')).length, 1);
  await damaged.quick.update({ shortcut: 'CommandOrControl+Alt+Space' });
  assert.deepEqual(JSON.parse(await fs.readFile(file, 'utf8')), {
    ...DEFAULTS,
    shortcut: 'CommandOrControl+Alt+Space',
  });
  assert.deepEqual([...damaged.held.keys()], ['CommandOrControl+Alt+Space']);
});

test('the settings are read before they are shown or changed', async (t) => {
  const file = await settingsFile(t);
  const kept = { shortcut: 'Alt+Space', tray: true, keep_running: true };
  await fs.writeFile(file, JSON.stringify(kept));
  // Asked before start() has read the file, as a window opened at once could.
  const { quick } = setup({}, { file });
  const [shown, changed] = await Promise.all([quick.read(), quick.update({ tray: false })]);
  assert.deepEqual(settingsOf(shown), kept);
  assert.deepEqual(settingsOf(changed), { ...kept, tray: false });
  assert.deepEqual(JSON.parse(await fs.readFile(file, 'utf8')), { ...kept, tray: false });
});

test('a settings file that cannot be read is not written over: the defaults this run, and no change', async (t) => {
  const file = await settingsFile(t);
  // A folder where the file should be: reading it fails, and it is no file to set aside.
  await fs.mkdir(file);
  const { quick, held, asked, errors } = setup({}, { file });
  await quick.start();
  assert.deepEqual(settingsOf(quick.get()), DEFAULTS);
  assert.deepEqual(asked, []);
  assert.equal(held.size, 0);
  assert.equal(errors.length, 1);
  await assert.rejects(quick.update({ shortcut: FIRST }), /quick-access\.json\)을 읽지 못했/);
  assert.equal(quick.get().shortcut, '');
  assert.equal(held.size, 0);
  assert.ok((await fs.stat(file)).isDirectory());
});

test('a change that cannot be saved is not applied', async (t) => {
  const file = await settingsFile(t);
  const { quick, held } = await withShortcut({}, { file });
  t.mock.method(fs, 'open', async () => {
    throw Object.assign(new Error('denied'), { code: 'EACCES' });
  });
  await assert.rejects(quick.update({ shortcut: 'Alt+Space' }), /저장하지 못했습니다/);
  assert.equal(quick.get().shortcut, FIRST);
  assert.deepEqual([...held.keys()], [FIRST]);
});

test('without a file the settings last for the run, as for a demo recording', async () => {
  const { quick } = setup();
  await quick.start();
  assert.equal((await quick.update({ keep_running: true })).keep_running, true);
  const next = setup();
  await next.quick.start();
  assert.equal(next.quick.get().keep_running, false);
});

test('the shortcuts are named as each system names their keys', () => {
  assert.equal(shortcutLabel('CommandOrControl+Shift+Space', 'win32'), 'Ctrl+Shift+Space');
  assert.equal(shortcutLabel('CommandOrControl+Alt+A', 'linux'), 'Ctrl+Alt+A');
  assert.equal(shortcutLabel('Alt+Space', 'win32'), 'Alt+Space');
  assert.equal(shortcutLabel('CommandOrControl+Shift+Space', 'darwin'), 'Cmd+Shift+Space');
  assert.equal(shortcutLabel('CommandOrControl+Alt+Space', 'darwin'), 'Cmd+Option+Space');
});
