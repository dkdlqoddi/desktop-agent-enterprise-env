const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { AppControl } = require('../electron/app-control.cjs');

function fakeWindow() {
  const state = {
    minimized: false,
    maximized: false,
    fullscreen: false,
    visible: true,
    focused: false,
  };
  const sent = [];
  const log = [];
  let bounds = { x: 0, y: 0, width: 1440, height: 960 };
  let zoom = 1;
  const image = (width, height) => ({
    isEmpty: () => width === 0,
    getSize: () => ({ width, height }),
    resize: ({ width: next }) => image(next, Math.round((height * next) / width)),
    toPNG: () => Buffer.from(`png ${width}x${height}`),
  });
  const win = {
    isDestroyed: () => false,
    isVisible: () => state.visible,
    isFocused: () => state.focused,
    isMinimized: () => state.minimized,
    isMaximized: () => state.maximized,
    isFullScreen: () => state.fullscreen,
    getBounds: () => bounds,
    setBounds: (next) => (bounds = next),
    restore: () => (log.push('restore'), (state.minimized = false)),
    show: () => (log.push('show'), (state.visible = true)),
    moveTop: () => log.push('moveTop'),
    focus: () => (log.push('focus'), (state.focused = true)),
    minimize: () => (state.minimized = true),
    maximize: () => (state.maximized = true),
    unmaximize: () => (state.maximized = false),
    setFullScreen: (on) => (state.fullscreen = on),
    webContents: {
      send: (channel, payload) => sent.push({ channel, payload }),
      getZoomFactor: () => zoom,
      setZoomFactor: (value) => (zoom = value),
      capturePage: async () => image(2560, 1600),
      reload: () => log.push('reload'),
    },
  };
  return { win, state, sent, log };
}

class FakeNotification extends EventEmitter {
  static shown = [];
  static isSupported() {
    return true;
  }
  constructor(options) {
    super();
    this.options = options;
  }
  show() {
    FakeNotification.shown.push(this.options);
  }
  close() {
    this.emit('close');
  }
}

function setup(options = {}) {
  const fake = fakeWindow();
  const control = new AppControl({
    window: () => fake.win,
    Notification: FakeNotification,
    timeout: 200,
    ...options,
  });
  return { control, ...fake };
}

// The commands sent so far, by their type.
const types = (sent) => sent.map((item) => item.payload.type);
// Lets the promises in flight settle, such as a capture's turn coming.
const settled = () => new Promise((resolve) => setImmediate(resolve));

test('sends commands to the screen once it is ready, and settles them with its reply', async () => {
  const { control, sent } = setup();
  await assert.rejects(control.command('state'), (error) => error.status === 503);
  control.attach();
  const answer = control.command('state');
  assert.equal(sent.at(-1).channel, 'control:command');
  const { id, type } = sent.at(-1).payload;
  assert.equal(type, 'state');
  control.settle({ id: 'someone-else', ok: true, result: 1 });
  control.settle({ id, ok: true, result: { view: 'home' } });
  assert.deepEqual(await answer, { view: 'home' });
  const refused = control.command('click', { name: 'x' });
  control.settle({ id: sent.at(-1).payload.id, ok: false, error: '없습니다' });
  await assert.rejects(refused, /없습니다/);
  await assert.rejects(control.command('state'), /응답하지 않습니다/);
  const lost = control.command('state');
  control.detach();
  await assert.rejects(lost, /다시 불러오고/);
  assert.equal(await control.screen(), null);
});

test('sends one command at a time, and never one its caller has given up on', async () => {
  const { control, sent } = setup();
  control.attach();
  // A batch of an AI tool's calls: the click waits until the fill (which submits its form) is done.
  const fill = control.command('fill', { label: '이름', submit: true });
  const click = control.command('click', { name: '지금 실행' });
  assert.deepEqual(
    sent.map((item) => item.payload.type),
    ['fill'],
  );
  control.settle({ id: sent[0].payload.id, ok: true, result: { filled: '이름' } });
  assert.deepEqual(await fill, { filled: '이름' });
  assert.deepEqual(
    sent.map((item) => item.payload.type),
    ['fill', 'click'],
  );
  // Its time ran out while it waited: it is never sent.
  const late = control.command('state', {}, 50);
  await assert.rejects(late, /응답하지 않습니다/);
  control.settle({ id: sent[1].payload.id, ok: false, error: '전체' });
  await assert.rejects(click, /전체/);
  const next = control.command('read_screen');
  assert.deepEqual(
    sent.map((item) => item.payload.type),
    ['fill', 'click', 'read_screen'],
  );
  // A reload refuses the ones sent and the ones waiting.
  const waiting = control.command('state');
  control.detach();
  await assert.rejects(next, /다시 불러오고/);
  await assert.rejects(waiting, /다시 불러오고/);
  assert.equal(sent.length, 3);
});

test('captures with passwords as dots and what the user alone may use left blank, or not at all', async () => {
  const { control, sent, win } = setup();
  const captured = [];
  const capture = win.webContents.capturePage;
  win.webContents.capturePage = () => {
    captured.push(types(sent));
    return capture();
  };
  control.attach();
  const reply = (result) => control.settle({ id: sent.at(-1).payload.id, ok: true, result });
  const shot = control.screenshot({ max_width: 1280 });
  await settled();
  assert.equal(sent.at(-1).payload.type, 'mask_capture');
  reply({ secrets: 2, user_only: ['채팅'], painted: true });
  const taken = await shot;
  assert.equal(taken.width, 1280);
  // The areas left blank are named with the picture.
  assert.deepEqual(taken.user_only, ['채팅']);
  // Captured once the screen was drawn so, and drawn as before after.
  assert.deepEqual(captured, [['mask_capture']]);
  assert.equal(sent.at(-1).payload.type, 'unmask_capture');
  reply({ shown: true });
  // A window that could not draw a frame so (covered, throttled) is not captured: neither the dots nor the blank
  // areas, nor a screen with nothing to leave out now, as the window would show a frame from before (the chat a tool
  // has just moved away from).
  for (const mask of [
    { secrets: 1, user_only: [], painted: false },
    { secrets: 0, user_only: ['웹 기록'], painted: false },
    { secrets: 0, user_only: [], painted: false },
  ]) {
    const covered = control.screenshot({});
    await settled();
    reply(mask);
    await assert.rejects(covered, /가린 화면/);
    assert.equal(sent.at(-1).payload.type, 'unmask_capture');
    reply({ shown: true });
  }
  assert.equal(captured.length, 1);
  // Nothing to leave out on the screen: the screen is told the capture is over all the same, since it leaves out an
  // area that comes meanwhile too.
  const plain = control.screenshot({});
  await settled();
  reply({ secrets: 0, user_only: [], painted: true });
  assert.deepEqual((await plain).user_only, []);
  assert.deepEqual(types(sent).slice(-2), ['mask_capture', 'unmask_capture']);
  reply({ shown: true });
  // A screen that does not answer is not captured, nor one still loading, while the window may show the one before.
  await assert.rejects(control.screenshot({}), /응답하지 않습니다/);
  control.detach();
  await assert.rejects(control.screenshot({}), /준비되지 않았습니다/);
  assert.equal(captured.length, 2);
});

test('takes one capture at a time, so that one ending draws nothing back while another is taken', async () => {
  const { control, sent, win } = setup();
  const releases = [];
  const capture = win.webContents.capturePage;
  win.webContents.capturePage = () =>
    new Promise((resolve) => releases.push(() => resolve(capture())));
  control.attach();
  const reply = (result) => control.settle({ id: sent.at(-1).payload.id, ok: true, result });
  const first = control.screenshot({});
  const second = control.screenshot({});
  await settled();
  reply({ secrets: 0, user_only: ['채팅'], painted: true });
  await settled();
  // The first is being captured: the second has not asked the screen for anything yet.
  assert.deepEqual(types(sent), ['mask_capture']);
  releases.shift()();
  assert.deepEqual((await first).user_only, ['채팅']);
  // The first's end reaches the screen before the second's beginning.
  assert.deepEqual(types(sent), ['mask_capture', 'unmask_capture']);
  reply({ shown: true });
  await settled();
  assert.deepEqual(types(sent), ['mask_capture', 'unmask_capture', 'mask_capture']);
  reply({ secrets: 0, user_only: ['채팅'], painted: true });
  await settled();
  releases.shift()();
  assert.deepEqual((await second).user_only, ['채팅']);
  assert.equal(sent.at(-1).payload.type, 'unmask_capture');
});

test('throws a capture away that took so long that the screen may have drawn all again', async () => {
  let clock = 0;
  const { control, sent, win } = setup({ now: () => clock });
  const capture = win.webContents.capturePage;
  win.webContents.capturePage = async () => {
    clock += 3500;
    return capture();
  };
  control.attach();
  const shot = control.screenshot({});
  await settled();
  control.settle({
    id: sent.at(-1).payload.id,
    ok: true,
    result: { secrets: 0, user_only: ['관리 → 휴지통'], painted: true },
  });
  await assert.rejects(shot, /너무 오래/);
  assert.equal(sent.at(-1).payload.type, 'unmask_capture');
});

test('reloads the screen only while the user has nothing unsaved on it', async () => {
  const { control, sent, log } = setup();
  control.attach();
  const refused = control.perform('app.reload', {});
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(sent.at(-1).payload.type, 'can_reload');
  control.settle({
    id: sent.at(-1).payload.id,
    ok: false,
    error: '사용자가 문서 창에서 글을 쓰고 있어 화면을 조작하지 않았습니다.',
  });
  await assert.rejects(refused, /글을 쓰고/);
  assert.equal(log.includes('reload'), false);
  const allowed = control.perform('app.reload', {});
  await new Promise((resolve) => setImmediate(resolve));
  control.settle({ id: sent.at(-1).payload.id, ok: true, result: { ok: true } });
  assert.deepEqual(await allowed, { ok: true });
  assert.deepEqual(log, ['reload']);
  // A screen that does not answer, or is still loading, is reloaded: that is how it recovers.
  assert.deepEqual(await control.perform('app.reload', {}), { ok: true });
  control.detach();
  assert.deepEqual(await control.perform('app.reload', {}), { ok: true });
  assert.deepEqual(log, ['reload', 'reload', 'reload']);
});

test('asks the screen about unsaved changes before the window closes, holding nothing back when it cannot say', async () => {
  const { control, sent, win } = setup();
  assert.equal(await control.unsaved(), false);
  control.attach();
  const asked = control.unsaved();
  assert.equal(sent.at(-1).payload.type, 'unsaved');
  control.settle({ id: sent.at(-1).payload.id, ok: true, result: { unsaved: true } });
  assert.equal(await asked, true);
  // A screen that does not answer lets the window close.
  assert.equal(await control.unsaved(), false);
  win.isDestroyed = () => true;
  assert.equal(await control.unsaved(), false);
});

test('moves, shows and zooms the window within its limits', () => {
  const { control, state, log } = setup();
  state.minimized = true;
  const shown = control.setWindow({ action: 'show' });
  assert.deepEqual(log, ['restore', 'show', 'moveTop', 'focus']);
  assert.equal(shown.minimized, false);
  assert.equal(control.setWindow({ action: 'maximize' }).maximized, true);
  assert.equal(control.setWindow({ action: 'restore' }).maximized, false);
  assert.deepEqual(control.setWindow({ action: 'set_bounds', width: 1200, x: 10 }).bounds, {
    width: 1200,
    height: 960,
    x: 10,
    y: 0,
  });
  assert.throws(() => control.setWindow({ action: 'set_bounds', width: 300 }), /width/);
  assert.equal(control.setWindow({ action: 'set_zoom', zoom: 1.25 }).zoom, 1.25);
  assert.throws(() => control.setWindow({ action: 'set_zoom', zoom: 5 }), /zoom/);
  assert.throws(() => control.setWindow({ action: 'close' }), /action/);
});

test('captures the window as PNG, scaled down, but not while it is minimized', async () => {
  const { control, state, sent } = setup();
  control.attach();
  const shot = control.screenshot({ max_width: 1280 });
  await settled();
  control.settle({
    id: sent.at(-1).payload.id,
    ok: true,
    result: { secrets: 0, user_only: [], painted: true },
  });
  const taken = await shot;
  assert.deepEqual([taken.mime, taken.width, taken.height], ['image/png', 1280, 800]);
  assert.equal(Buffer.from(taken.data, 'base64').toString(), 'png 1280x800');
  state.minimized = true;
  await assert.rejects(control.screenshot({}), /최소화/);
  assert.equal(sent.length, 2);
});

test('checks navigation and passes the access level to clicks and typing', async () => {
  const { control, sent } = setup();
  control.attach();
  await assert.rejects(control.perform('app.navigate', { view: 'settings' }), /view/);
  await assert.rejects(
    control.perform('app.navigate', { view: 'library', tab: 'history' }),
    /documents, import, knowledge/,
  );
  await assert.rejects(
    control.perform('app.navigate', { view: 'home', section: 'llm' }),
    /section/,
  );
  // Each command answered before the next goes out.
  const answered = (promise) => {
    control.settle({ id: sent.at(-1).payload.id, ok: true, result: {} });
    return promise;
  };
  const navigation = control.perform('app.navigate', { view: 'admin', section: 'api' });
  assert.deepEqual(sent.at(-1).payload.params, { view: 'admin', tab: '', section: 'api' });
  await answered(navigation);
  const click = control.perform('app.click', { name: '저장' }, { access: 'write' });
  assert.equal(sent.at(-1).payload.params.allow_admin, false);
  await answered(click);
  const fill = control.perform('app.fill', { label: 'API Key', value: 'x' }, { access: 'full' });
  assert.equal(sent.at(-1).payload.params.allow_admin, true);
  await answered(fill);
  await assert.rejects(control.perform('app.click', { name: 'x', role: 'image' }), /role/);
  await assert.rejects(control.perform('app.open_document', { id: 'a', terms: 'x' }), /terms/);
  await assert.rejects(control.perform('app.dance', {}), (error) => error.status === 404);
  control.detach();
});

test('notifies through the system when asked, otherwise on the screen', async () => {
  const { control, sent } = setup();
  assert.deepEqual(await control.perform('app.notify', { message: '끝났습니다', system: true }), {
    shown: 'system',
  });
  assert.deepEqual(FakeNotification.shown.at(-1), { title: 'Private Agent', body: '끝났습니다' });
  control.attach();
  const toast = control.perform('app.notify', { message: '확인해 주세요', error: true });
  assert.deepEqual(sent.at(-1).payload, {
    id: sent.at(-1).payload.id,
    type: 'toast',
    params: { message: '확인해 주세요', error: true },
  });
  control.settle({ id: sent.at(-1).payload.id, ok: true, result: { shown: true } });
  assert.deepEqual(await toast, { shown: 'app' });
  await assert.rejects(control.perform('app.notify', { message: 'x'.repeat(501) }), /500/);
  control.stop();
});
