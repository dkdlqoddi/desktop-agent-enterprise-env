const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { RoutineRunner, WAKE_DELAY, onWake, readsFiles } = require('../electron/routines.cjs');

const due = [
  {
    id: 'watch',
    next_run_at: '2026-01-01T00:00:00+00:00',
    needs: [{ kind: 'jira', url: 'https://j/browse/A-1' }],
  },
  {
    id: 'digest',
    next_run_at: '2026-01-01T00:05:00+00:00',
    needs: [{ kind: 'jira', url: 'https://jira.test/browse/TEAM-1' }],
  },
];
const run = (values) => ({
  id: 'run-1',
  routine_id: 'watch',
  name: '티켓 감시',
  kind: 'jira_watch',
  notify: true,
  status: 'success',
  changed: true,
  summary: '바뀐 티켓 1개',
  error: '',
  finished_at: '2026-01-01T00:01:00+00:00',
  logins: [],
  ...values,
});

function setup({ focused = false, queued = {}, events = [[]], active = [0] } = {}) {
  const calls = [];
  const sent = [];
  const notifications = [];
  const clicks = [];
  const recorded = [];
  let changes = 0;
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
  let round = 0;
  const runner = new RoutineRunner({
    Notification: FakeNotification,
    ready: () => true,
    focused: () => focused,
    send: (event) => sent.push(event),
    changed: () => changes++,
    onClick: (id) => clicks.push(id),
    record: async (siteId, login) => recorded.push([siteId, login.method]),
    // The plans of the sites a run needs, as main.cjs makes them from the site accounts.
    logins: (needs) => needs.map((need) => ({ ...need, login: { site_id: `site-${need.kind}` } })),
    follow: 10,
    rpc: async (method, params) => {
      calls.push({ method, params });
      if (method === 'routines.due') return due;
      if (method === 'routines.execute') return { queued: queued[params.id] ?? true };
      if (method === 'routines.events') {
        if (params?.after === undefined) return { cursor: 'start', runs: [], active: 0 };
        const runs = events[Math.min(round, events.length - 1)];
        const busy = active[Math.min(round, active.length - 1)];
        round++;
        return { cursor: runs.at(-1)?.finished_at ?? params.after, runs, active: busy };
      }
      throw new Error(method);
    },
  });
  return { runner, calls, sent, notifications, clicks, recorded, changes: () => changes };
}

test('notifies only news and failures, never a focused window, and only when the routine asks', async () => {
  const quiet = setup({
    events: [
      [
        run({ id: 'a', changed: false, summary: '바뀐 곳 없음' }),
        run({ id: 'b', notify: false }),
        run({ id: 'c', status: 'error', changed: false, error: '로그인하지 못했습니다.' }),
        // The same failure as the run before: told once.
        run({
          id: 'd',
          status: 'error',
          changed: false,
          error: '로그인하지 못했습니다.',
          quiet: true,
        }),
      ],
    ],
  });
  await quiet.runner.tick();
  assert.deepEqual(
    quiet.notifications.map((item) => [item.options.title, item.options.body]),
    [['Private Agent · 자동 작업 실패', '티켓 감시: 로그인하지 못했습니다.']],
  );
  assert.equal(quiet.sent.length, 4);
  assert.deepEqual(
    quiet.sent.map((event) => event.notify),
    [false, false, true, false],
  );
  quiet.runner.stop();
  const focused = setup({ focused: true, events: [[run()]] });
  await focused.runner.tick();
  assert.equal(focused.notifications.length, 0);
  assert.equal(focused.sent.length, 1);
  focused.runner.stop();
});

test('follows a queued or running run until it ends, and never overlaps checks', async () => {
  const state = setup({ queued: { digest: false }, events: [[], [], [run()]], active: [1, 1, 0] });
  const first = state.runner.tick();
  await state.runner.tick(); // Busy: returns without a second lookup.
  await first;
  assert.equal(state.calls.filter((call) => call.method === 'routines.due').length, 1);
  // Two quick looks while the run is active, then the run that ended.
  await new Promise((resolve) => setTimeout(resolve, 100));
  assert.equal(state.calls.filter((call) => call.params?.after !== undefined).length, 3);
  assert.equal(state.sent.length, 1);
  state.runner.stop();
});

test('a run the user started is followed, and stopping ends everything', async () => {
  const state = setup({ events: [[], [run()]], active: [1, 0] });
  await state.runner.tick();
  state.runner.poke();
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert.equal(state.sent.length, 1);
  state.runner.stop();
  const calls = state.calls.length;
  state.runner.poke();
  await new Promise((resolve) => setTimeout(resolve, 40));
  assert.equal(state.calls.length, calls);
});

test('back from sleep or with the screen unlocked, it looks at what is due once, without waiting for the interval', async () => {
  const state = setup();
  const power = new EventEmitter();
  const lookups = () => state.calls.filter((call) => call.method === 'routines.due').length;
  const stopWatching = onWake(power, () => state.runner.wake(), 10);
  power.emit('resume');
  // The unlock that often follows a resume makes no second look.
  power.emit('unlock-screen');
  assert.equal(lookups(), 0); // A moment later, for the network to come back.
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert.equal(lookups(), 1);
  assert.deepEqual(
    state.calls.filter((call) => call.method === 'routines.execute').map((call) => call.params.id),
    ['watch', 'digest'],
  );
  power.emit('unlock-screen');
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert.equal(lookups(), 2);
  // Stopped, it no longer listens; a stopped runner does not look either.
  stopWatching();
  assert.equal(power.listenerCount('resume') + power.listenerCount('unlock-screen'), 0);
  power.emit('resume');
  state.runner.stop();
  state.runner.wake();
  await new Promise((resolve) => setTimeout(resolve, 40));
  assert.equal(lookups(), 2);
  assert.ok(WAKE_DELAY >= 1_000 && WAKE_DELAY <= 10_000);
});
