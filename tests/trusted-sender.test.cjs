const { test } = require('node:test');
const assert = require('node:assert/strict');
const { ALLOWED, allowedRequest, trustedSender } = require('../electron/trusted-sender.cjs');

const DEV = 'http://127.0.0.1:3000';

// A frame, a webContents and a window as far as the checks read them.
const frame = (url) => ({ url });
function contents(url = 'agent://bundle/') {
  return {
    mainFrame: frame(url),
    destroyed: false,
    isDestroyed() {
      return this.destroyed;
    },
  };
}
function browserWindow(webContents = contents()) {
  return {
    webContents,
    destroyed: false,
    isDestroyed() {
      return this.destroyed;
    },
  };
}
// An IPC message's event: who sent it, and from which of its frames.
const message = (sender, senderFrame = sender.mainFrame) => ({ sender, senderFrame });

test("the main frame of the app's window at agent://bundle/ is trusted", () => {
  const window = browserWindow();
  for (const url of [
    'agent://bundle/',
    'agent://bundle/index.html',
    'agent://bundle/docs/?q=1#top',
  ]) {
    window.webContents.mainFrame.url = url;
    const event = message(window.webContents);
    assert.equal(trustedSender(event, { window }), true, url);
    assert.equal(allowedRequest(event, 'wiki.list', { window }), true, url);
  }
});

test('a subframe, another window or another page of the same window is refused', () => {
  const window = browserWindow();
  const screen = { window };
  // An iframe of the screen, even one at the bundle's own address.
  assert.equal(trustedSender(message(window.webContents, frame('agent://bundle/')), screen), false);
  // Another window's main frame, at the same address.
  const other = contents();
  assert.equal(trustedSender(message(other), screen), false);
  // The window's main frame once it shows anything but the bundle.
  for (const url of [
    'https://example.com/',
    'agent://other/',
    'agent://bundlex/',
    'agent://bundle.example.test/',
    'file:///C:/app/out/index.html',
    'about:blank',
    'devtools://devtools/bundled/inspector.html',
    '',
  ]) {
    window.webContents.mainFrame.url = url;
    assert.equal(trustedSender(message(window.webContents), screen), false, url);
  }
});

test('a destroyed sender, a closed window or a frame that has gone is refused', () => {
  const window = browserWindow();
  const event = message(window.webContents);
  assert.equal(trustedSender(event, { window }), true);
  // Electron's senderFrame is null once the frame has navigated away or been destroyed.
  assert.equal(trustedSender(message(window.webContents, null), { window }), false);
  window.webContents.destroyed = true;
  assert.equal(trustedSender(event, { window }), false);
  window.webContents.destroyed = false;
  window.destroyed = true;
  assert.equal(trustedSender(event, { window }), false);
  // The window variable is null once the window has closed, and a new window has a webContents of its own.
  assert.equal(trustedSender(event, { window: null }), false);
  assert.equal(trustedSender(event, { window: undefined }), false);
  assert.equal(trustedSender(event, { window: browserWindow() }), false);
  // A message without a sender at all.
  assert.equal(trustedSender({}, { window: browserWindow() }), false);
});

test('a source run trusts the development server instead of the bundle', () => {
  const window = browserWindow(contents(`${DEV}/`));
  const screen = { window, devUrl: DEV };
  assert.equal(trustedSender(message(window.webContents), screen), true);
  window.webContents.mainFrame.url = `${DEV}/docs/`;
  assert.equal(trustedSender(message(window.webContents), screen), true);
  for (const url of ['agent://bundle/', `${DEV}.example.test/`, 'http://127.0.0.1:30000/', DEV]) {
    window.webContents.mainFrame.url = url;
    assert.equal(trustedSender(message(window.webContents), screen), false, url);
  }
});

test('a method outside ALLOWED is refused, even from the trusted sender', () => {
  const window = browserWindow();
  const event = message(window.webContents);
  for (const method of ['wiki.list', 'settings.update', 'api.get', 'app.relaunch'])
    assert.equal(allowedRequest(event, method, { window }), true, method);
  for (const method of [
    // Secrets stay in the main process.
    'settings.secrets',
    // The AI tools' own methods and the backend's internal ones.
    'app.click',
    'documents.query',
    'routines.needs',
    'backend.shutdown',
    'confluence.forget',
    // Names that only look like members of a Set or an object.
    'has',
    'constructor',
    '__proto__',
    'toString',
    'WIKI.LIST',
    ' wiki.list',
    '',
    undefined,
    null,
    42,
    ['wiki.list'],
    { toString: () => 'wiki.list' },
  ])
    assert.equal(allowedRequest(event, method, { window }), false, String(method));
  // An allowed method from an untrusted sender is refused all the same.
  assert.equal(allowedRequest(message(contents()), 'wiki.list', { window }), false);
});

test('ALLOWED names methods only, and never one that returns secrets', () => {
  assert.ok(ALLOWED.size > 100);
  for (const method of ALLOWED) assert.match(method, /^[a-z]+\.[a-z_]+$|^health$|^stats$/, method);
  for (const method of ALLOWED) assert.doesNotMatch(method, /secret|password/, method);
});
