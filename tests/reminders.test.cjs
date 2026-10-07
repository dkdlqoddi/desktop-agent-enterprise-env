const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { ReminderScheduler } = require('../electron/reminders.cjs');

const due = { id: 'task', title: '회의 준비', remind_at: '2026-01-01T00:00:00+00:00' };
function setup(mode = 'show') {
  const calls = [];
  const notifications = [];
  let clicks = 0;
  class FakeNotification extends EventEmitter {
    static isSupported() {
      return mode !== 'unsupported';
    }
    constructor(options) {
      super();
      this.options = options;
      notifications.push(this);
    }
    show() {
      this.emit(mode);
    }
    close() {
      this.emit('close');
    }
  }
  const scheduler = new ReminderScheduler({
    Notification: FakeNotification,
    ready: () => true,
    onClick: () => clicks++,
    rpc: async (method, params) => {
      calls.push({ method, params });
      return method === 'reminders.due' ? [due] : { ok: true };
    },
  });
  return { scheduler, calls, notifications, clicks: () => clicks };
}

test('acknowledges actual display once, click opens assistant, and polling avoids duplicates', async () => {
  const state = setup();
  await state.scheduler.tick();
  await state.scheduler.tick();
  assert.equal(state.notifications.length, 1);
  assert.deepEqual(state.calls.find((c) => c.method === 'reminders.ack').params, {
    id: due.id,
    remind_at: due.remind_at,
  });
  state.notifications[0].emit('click');
  assert.equal(state.clicks(), 1);
  state.scheduler.stop();
});

test('failed or unsupported system notifications remain unacknowledged for the inbox', async () => {
  for (const mode of ['failed', 'unsupported', 'no-show-event']) {
    const state = setup(mode);
    await state.scheduler.tick();
    assert.equal(state.calls.filter((c) => c.method === 'reminders.ack').length, 0);
    if (mode === 'failed') assert.ok(state.scheduler.status().error);
    state.scheduler.stop();
  }
});

test('a failed check is shown until the next one succeeds, and one the backend restart failed is not', async () => {
  const state = setup();
  let ready = true;
  let outcome = 'fail';
  state.scheduler.ready = () => ready;
  state.scheduler.rpc = async (method) => {
    if (method !== 'reminders.due') return { ok: true };
    if (outcome === 'restart') {
      ready = false; // The backend ended: the app is starting it again.
      throw new Error('Python 백엔드를 다시 시작하고 있습니다.');
    }
    if (outcome === 'fail') throw new Error('응답 제한 시간을 초과했습니다.');
    return [];
  };
  await state.scheduler.tick();
  assert.match(state.scheduler.status().error, /알림을 확인하지 못했습니다/);
  // Cleared by the next check that works, though no notification is due.
  outcome = 'none';
  await state.scheduler.tick();
  assert.equal(state.scheduler.status().error, '');
  outcome = 'restart';
  await state.scheduler.tick();
  assert.equal(state.scheduler.status().error, '');
  // Not checked at all until the backend is back.
  outcome = 'fail';
  await state.scheduler.tick();
  assert.equal(state.scheduler.status().error, '');
  assert.equal(state.notifications.length, 0);
  state.scheduler.stop();
});

test('a notification that failed stays reported after later checks, until one is shown', async () => {
  const state = setup('failed');
  await state.scheduler.tick();
  assert.match(state.scheduler.status().error, /시스템 알림을 표시하지 못했습니다/);
  // The same reminder is not shown again for five minutes; the checks in between still work.
  await state.scheduler.tick();
  assert.match(state.scheduler.status().error, /시스템 알림을 표시하지 못했습니다/);
  state.scheduler.stop();
});

test('stopping while due lookup is pending prevents notification delivery', async () => {
  const state = setup();
  let resolve;
  state.scheduler.rpc = () =>
    new Promise((r) => {
      resolve = r;
    });
  const tick = state.scheduler.tick();
  state.scheduler.stop();
  resolve([due]);
  await tick;
  assert.equal(state.notifications.length, 0);
});
