const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { BACKGROUND, WindowTheme } = require('../electron/window-theme.cjs');

// Electron's nativeTheme as far as the window theme uses it: the setting decides, and 'system' follows the OS, which
// tests switch with `os`. Like Electron, it says 'updated' when either changes.
class NativeTheme extends EventEmitter {
  source = 'system';
  os = 'light';
  get themeSource() {
    return this.source;
  }
  set themeSource(value) {
    this.source = value;
    this.emit('updated');
  }
  get shouldUseDarkColors() {
    return (this.source === 'system' ? this.os : this.source) === 'dark';
  }
  switchOs(value) {
    this.os = value;
    this.emit('updated');
  }
}

// A window as far as the theme paints it.
class Window {
  colors = [];
  destroyed = false;
  setBackgroundColor(color) {
    this.colors.push(color);
  }
  isDestroyed() {
    return this.destroyed;
  }
  get color() {
    return this.colors.at(-1);
  }
}

function setup() {
  const nativeTheme = new NativeTheme();
  /** @type {(Window | null)[]} */
  const windows = [];
  const theme = new WindowTheme({ nativeTheme, windows: () => windows });
  return { nativeTheme, windows, theme };
}

test('a window opens on the background of the theme Windows shows', () => {
  const { nativeTheme, theme } = setup();
  assert.equal(theme.background, BACKGROUND.light);
  nativeTheme.os = 'dark';
  assert.equal(theme.background, BACKGROUND.dark);
});

test("the screen's setting becomes Electron's theme and paints the window", () => {
  const { nativeTheme, windows, theme } = setup();
  const window = new Window();
  windows.push(window);
  assert.deepEqual(theme.set('dark'), { theme: 'dark' });
  assert.equal(nativeTheme.themeSource, 'dark');
  assert.equal(window.color, BACKGROUND.dark);
  theme.set('light');
  assert.equal(nativeTheme.themeSource, 'light');
  assert.equal(window.color, BACKGROUND.light);
  // A fixed theme stays as Windows switches.
  nativeTheme.switchOs('dark');
  assert.equal(window.color, BACKGROUND.light);
});

test("'system' follows Windows as it switches between light and dark", () => {
  const { nativeTheme, windows, theme } = setup();
  const window = new Window();
  windows.push(window);
  theme.set('dark');
  theme.set('system');
  assert.equal(nativeTheme.themeSource, 'system');
  assert.equal(window.color, BACKGROUND.light);
  nativeTheme.switchOs('dark');
  assert.equal(window.color, BACKGROUND.dark);
  nativeTheme.switchOs('light');
  assert.equal(window.color, BACKGROUND.light);
});

test('a closed window or none at all is passed over', () => {
  const { nativeTheme, windows, theme } = setup();
  const closed = new Window();
  closed.destroyed = true;
  windows.push(null, closed);
  theme.set('dark');
  nativeTheme.switchOs('dark');
  assert.deepEqual(closed.colors, []);
});

test('only the three settings are taken, and a refused one changes nothing', () => {
  const { nativeTheme, windows, theme } = setup();
  const window = new Window();
  windows.push(window);
  for (const value of [
    'Dark',
    'auto',
    '',
    ' dark',
    undefined,
    null,
    1,
    ['dark'],
    { toString: () => 'dark' },
  ])
    assert.throws(() => theme.set(value), /화면 테마/, String(value));
  assert.equal(nativeTheme.themeSource, 'system');
  assert.deepEqual(window.colors, []);
});
