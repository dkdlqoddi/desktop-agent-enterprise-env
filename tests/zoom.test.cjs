const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { FACTORS, ScreenZoom, stepZoom, zoomKey } = require('../electron/zoom.cjs');

const press = (key, extra = {}) => ({ type: 'keyDown', key, code: '', control: true, ...extra });

// A window's webContents as far as the zoom uses it.
class Contents extends EventEmitter {
  factors = [];
  destroyed = false;
  setZoomFactor(factor) {
    this.factors.push(factor);
  }
  isDestroyed() {
    return this.destroyed;
  }
  // Presses a key as Electron's before-input-event has it; whether the screen still gets it.
  key(input) {
    let kept = false;
    this.emit('before-input-event', { preventDefault: () => (kept = true) }, input);
    return !kept;
  }
}

async function settings(t) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'agent-zoom-test-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  return path.join(directory, 'window.json');
}

test('Ctrl with =, +, - and 0, on the main keys or the keypad, are the zoom keys', () => {
  assert.equal(zoomKey(press('=')), 1);
  assert.equal(zoomKey(press('+', { shift: true })), 1);
  assert.equal(zoomKey(press('', { code: 'NumpadAdd' })), 1);
  assert.equal(zoomKey(press('-')), -1);
  assert.equal(zoomKey(press('_', { shift: true })), -1);
  assert.equal(zoomKey(press('', { code: 'NumpadSubtract' })), -1);
  assert.equal(zoomKey(press('0')), 0);
  assert.equal(zoomKey(press('Insert', { code: 'Numpad0' })), 0);
  // Cmd on macOS.
  assert.equal(zoomKey(press('=', { control: false, meta: true })), 1);
  // Not without Ctrl, not with Alt (AltGr), not on release, and not other shortcuts.
  assert.equal(zoomKey(press('=', { control: false })), null);
  assert.equal(zoomKey(press('-', { alt: true })), null);
  assert.equal(zoomKey(press('0', { type: 'keyUp' })), null);
  assert.equal(zoomKey(press(')', { shift: true })), null);
  for (const key of ['k', 'r', 'w', '9', 'ArrowUp']) assert.equal(zoomKey(press(key)), null);
});

test('steps follow the browsers’ factors between 50% and 200%, and 0 is 100%', () => {
  assert.equal(stepZoom(1, 1), 1.1);
  assert.equal(stepZoom(1, -1), 0.9);
  assert.equal(stepZoom(1.75, 0), 1);
  assert.equal(stepZoom(2, 1), 2);
  assert.equal(stepZoom(0.5, -1), 0.5);
  // A factor off the steps (an edited file) goes to the nearest step that way.
  assert.equal(stepZoom(1.33, 1), 1.5);
  assert.equal(stepZoom(1.33, -1), 1.25);
  let factor = 1;
  const seen = [factor];
  while (factor < 2) seen.push((factor = stepZoom(factor, 1)));
  assert.deepEqual(seen, FACTORS.slice(FACTORS.indexOf(1)));
});

test('the keys and Ctrl+wheel zoom the screen, which the keys never reach, and the factor is saved', async (t) => {
  const file = await settings(t);
  const zoom = new ScreenZoom({ file });
  const contents = new Contents();
  zoom.attach(contents);
  await zoom.load();
  assert.deepEqual(contents.factors, [1]);
  assert.equal(contents.key(press('=')), false);
  assert.equal(contents.key(press('k')), true);
  assert.equal(contents.key(press('=', { type: 'keyUp' })), true);
  contents.emit('zoom-changed', {}, 'in');
  assert.deepEqual(contents.factors, [1, 1.1, 1.25]);
  await zoom.save();
  assert.deepEqual(JSON.parse(await fs.readFile(file, 'utf8')), { zoom: 1.25 });
  contents.emit('zoom-changed', {}, 'out');
  assert.equal(contents.key(press('-')), false);
  assert.equal(zoom.factor, 1);
  for (let i = 0; i < 20; i++) contents.key(press('-'));
  assert.equal(zoom.factor, 0.5);
  contents.key(press('0'));
  assert.equal(zoom.factor, 1);
  await zoom.save();
  assert.deepEqual(JSON.parse(await fs.readFile(file, 'utf8')), { zoom: 1 });
});

test('the screen opens at the saved factor, and each page it loads does too', async (t) => {
  const file = await settings(t);
  await fs.writeFile(file, JSON.stringify({ zoom: 1.5 }));
  const zoom = new ScreenZoom({ file });
  const contents = new Contents();
  zoom.attach(contents);
  await zoom.load();
  assert.equal(contents.factors.at(-1), 1.5);
  contents.emit('did-navigate');
  assert.deepEqual(contents.factors.slice(-2), [1.5, 1.5]);
  // A closed window is left alone.
  contents.destroyed = true;
  contents.emit('destroyed');
  zoom.set(1.1);
  assert.equal(contents.factors.at(-1), 1.5);
});

test('a factor out of range is brought within it; a damaged file opens at 100%', async (t) => {
  const file = await settings(t);
  await fs.writeFile(file, JSON.stringify({ zoom: 9 }));
  const big = new ScreenZoom({ file });
  await big.load();
  assert.equal(big.factor, 2);
  await fs.writeFile(file, '{"zoom": ');
  const damaged = new ScreenZoom({ file });
  await damaged.load();
  assert.equal(damaged.factor, 1);
  // The damaged file was set aside (files.cjs), so saving starts a new one.
  await damaged.set(1.1);
  assert.deepEqual(JSON.parse(await fs.readFile(file, 'utf8')), { zoom: 1.1 });
});

test('a held key saves the last factor once the saves before it are done', async (t) => {
  const file = await settings(t);
  const zoom = new ScreenZoom({ file });
  await zoom.load();
  const writes = [];
  const contents = new Contents();
  zoom.attach(contents);
  const writeFile = fs.open;
  t.mock.method(fs, 'open', (...args) => {
    writes.push(args[0]);
    return writeFile(...args);
  });
  for (let i = 0; i < 6; i++) contents.key(press('=', { isAutoRepeat: i > 0 }));
  await zoom.save();
  assert.ok(writes.length < 6, `${writes.length} writes`);
  assert.deepEqual(JSON.parse(await fs.readFile(file, 'utf8')), { zoom: 2 });
});

test('without a file the zoom lasts for the run only; a failed save is reported', async (t) => {
  const zoom = new ScreenZoom();
  const contents = new Contents();
  zoom.attach(contents);
  await zoom.load();
  contents.key(press('='));
  assert.equal(zoom.factor, 1.1);
  await zoom.save();
  const file = await settings(t);
  const errors = [];
  const failing = new ScreenZoom({
    file: path.join(file, 'missing', 'window.json'),
    failed: (e) => errors.push(e),
  });
  await failing.load();
  await failing.set(1.25);
  assert.equal(errors.length, 1);
  assert.equal(failing.factor, 1.25);
});
