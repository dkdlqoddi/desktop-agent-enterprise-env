const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { ImportNotices } = require('../electron/import-notices.cjs');

const running = { id: 'job', status: 'running', notice: '' };
const text = 'PowerPoint 파일을 읽습니다.';
const noticed = { ...running, notice: text };

// `statuses` are the job states the backend reports poll after poll; the last one repeats.
function setup({ statuses, focused = false, supported = true }) {
  const calls = [];
  const sent = [];
  const notifications = [];
  let clicks = 0;
  class FakeNotification extends EventEmitter {
    static isSupported() {
      return supported;
    }
    constructor(options) {
      super();
      this.options = options;
      notifications.push(this);
    }
    show() {}
    close() {
      this.emit('close');
    }
  }
  const queue = [...statuses];
  const notices = new ImportNotices({
    Notification: FakeNotification,
    focused: () => focused,
    send: (message) => sent.push(message),
    onClick: () => clicks++,
    interval: 1,
    retries: 3,
    rpc: async (method) => {
      calls.push(method);
      const status = queue.length > 1 ? queue.shift() : queue[0];
      if (status instanceof Error) throw status;
      return status;
    },
  });
  return { notices, calls, sent, notifications, clicks: () => clicks };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 60));

test('the notice shows once as a system notification, and a click opens the window', async () => {
  const state = setup({ statuses: [running, running, noticed] });
  state.notices.watch('office', 'job');
  await settle();
  assert.deepEqual(state.calls, ['office.status', 'office.status', 'office.status']);
  assert.equal(state.notifications.length, 1);
  assert.deepEqual(state.notifications[0].options, {
    title: 'Private Agent · 가져오기 주의사항',
    body: text,
  });
  assert.deepEqual(state.sent, []);
  state.notifications[0].emit('click');
  assert.equal(state.clicks(), 1);
  state.notices.stop();
});

test('a focused window, or a system without notifications, shows the notice itself', async () => {
  for (const options of [{ focused: true }, { supported: false }]) {
    const state = setup({ statuses: [noticed], ...options });
    state.notices.watch('office', 'job');
    await settle();
    assert.deepEqual(state.sent, [text]);
    assert.equal(state.notifications.length, 0);
  }
  const state = setup({ statuses: [noticed] });
  state.notices.watch('office', 'job');
  await settle();
  state.notifications[0].emit('failed');
  assert.deepEqual(state.sent, [text]);
});

test('watching ends with the job, for another job, after repeated failures, and at app exit', async () => {
  for (const statuses of [
    [{ ...running, status: 'completed' }],
    [{ ...noticed, id: 'other' }],
    [new Error('busy')],
  ]) {
    const state = setup({ statuses });
    state.notices.watch('office', 'job');
    await settle();
    assert.equal(state.calls.length, statuses[0] instanceof Error ? 3 : 1);
    assert.equal(state.notifications.length + state.sent.length, 0);
  }
  const state = setup({ statuses: [running] });
  state.notices.watch('office', 'job');
  await settle();
  state.notices.stop();
  const polls = state.calls.length;
  await settle();
  assert.equal(state.calls.length, polls);
});
