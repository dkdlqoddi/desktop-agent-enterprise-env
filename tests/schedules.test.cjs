const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { PromptScheduler } = require('../electron/schedules.cjs');

const due = [
  { id: 'morning', name: '아침 정리', next_run_at: '2026-01-01T00:00:00+00:00', notify: true },
  { id: 'weekly', name: '주간 보고', next_run_at: '2026-01-01T00:05:00+00:00', notify: false },
];
function setup({ focused = false, results = {} } = {}) {
  const calls = [];
  const events = [];
  const notifications = [];
  const clicks = [];
  class FakeNotification extends EventEmitter {
    static isSupported() {
      return true;
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
  const scheduler = new PromptScheduler({
    Notification: FakeNotification,
    ready: () => true,
    focused: () => focused,
    send: (event) => events.push(event),
    onClick: (runId) => clicks.push(runId),
    rpc: async (method, params) => {
      calls.push({ method, params });
      if (method === 'schedules.due') return due;
      const schedule = due.find((item) => item.id === params.id);
      return (
        results[params.id] || {
          ran: true,
          schedule: { id: schedule.id, name: schedule.name, notify: schedule.notify },
          run: { id: `run-${schedule.id}`, status: 'success', error: '' },
        }
      );
    },
  });
  return { scheduler, calls, events, notifications, clicks };
}

test('runs each due schedule in order, reports it, and notifies only when asked', async () => {
  const state = setup();
  await state.scheduler.tick();
  assert.deepEqual(
    state.calls.map((call) => [call.method, call.params?.next_run_at]),
    [
      ['schedules.due', undefined],
      ['schedules.execute', due[0].next_run_at],
      ['schedules.execute', due[1].next_run_at],
    ],
  );
  assert.deepEqual(
    state.events.map((event) => [event.type, event.run_id, event.status]),
    [
      ['finished', 'run-morning', 'success'],
      ['finished', 'run-weekly', 'success'],
    ],
  );
  assert.equal(state.notifications.length, 1);
  assert.equal(state.notifications[0].options.body, '아침 정리');
  state.notifications[0].emit('click');
  assert.deepEqual(state.clicks, ['run-morning']);
  state.scheduler.stop();
});

test('failures carry the error, and a focused window gets no system notification', async () => {
  const failed = {
    ran: true,
    schedule: { id: 'morning', name: '아침 정리', notify: true },
    run: { id: 'run-failed', status: 'error', error: '연결할 수 없습니다.' },
  };
  const background = setup({ results: { morning: failed } });
  await background.scheduler.tick();
  assert.match(background.notifications[0].options.title, /실패/);
  assert.equal(background.notifications[0].options.body, '아침 정리: 연결할 수 없습니다.');
  background.scheduler.stop();
  const focused = setup({ focused: true, results: { morning: failed } });
  await focused.scheduler.tick();
  assert.equal(focused.notifications.length, 0);
  assert.equal(focused.events[0].error, '연결할 수 없습니다.');
  focused.scheduler.stop();
});

test('skips occurrences claimed elsewhere and never overlaps ticks', async () => {
  const state = setup({ results: { morning: { ran: false } } });
  const first = state.scheduler.tick();
  await state.scheduler.tick(); // Busy: returns without a second lookup.
  await first;
  assert.equal(state.calls.filter((call) => call.method === 'schedules.due').length, 1);
  assert.deepEqual(
    state.events.map((event) => event.schedule_id),
    ['weekly'],
  );
  state.scheduler.stop();
});

test('stopping during a lookup runs nothing', async () => {
  const state = setup();
  let resolve;
  state.scheduler.rpc = (method) => {
    state.calls.push({ method });
    return new Promise((r) => {
      resolve = r;
    });
  };
  const tick = state.scheduler.tick();
  state.scheduler.stop();
  resolve(due);
  await tick;
  assert.deepEqual(
    state.calls.map((call) => call.method),
    ['schedules.due'],
  );
});
