// Prompt schedules run through the routines' one scheduler (electron/routines.cjs): their occurrences come due in the
// same list as the routines', are taken with routines.execute and queued by the backend, and their runs are told on
// 'schedule:event' with the notification the schedule asks for.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { RoutineRunner } = require('../electron/routines.cjs');

const due = [
  { id: 'morning', next_run_at: '2026-01-01T00:00:00+00:00', needs: [], schedule: true },
  {
    id: 'watch',
    next_run_at: '2026-01-01T00:05:00+00:00',
    needs: [{ kind: 'jira', url: 'https://jira.test/browse/TEAM-1' }],
  },
];
const ended = (values) => ({
  id: 'run-morning',
  schedule_id: 'morning',
  name: '아침 정리',
  notify: true,
  status: 'success',
  error: '',
  finished_at: '2026-01-01T00:01:00+00:00',
  ...values,
});

function setup({ focused = false, runs = [] } = {}) {
  const calls = [];
  const routineEvents = [];
  const scheduleEvents = [];
  const notifications = [];
  const opened = [];
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
  const runner = new RoutineRunner({
    Notification: FakeNotification,
    ready: () => true,
    focused: () => focused,
    send: (event) => routineEvents.push(event),
    schedule: (event) => scheduleEvents.push(event),
    changed: () => {},
    onClick: (id) => opened.push(['routine', id]),
    openRun: (id) => opened.push(['run', id]),
    record: async () => {},
    logins: (needs) => needs.map((need) => ({ ...need, login: { site_id: `site-${need.kind}` } })),
    follow: 10,
    rpc: async (method, params) => {
      calls.push({ method, params });
      if (method === 'routines.due') return due;
      if (method === 'routines.execute') return { queued: true };
      if (method === 'routines.events') {
        if (params?.after === undefined) return { cursor: 'start', runs: [], active: 0 };
        return { cursor: runs.at(-1)?.finished_at ?? params.after, runs, active: 0 };
      }
      throw new Error(method);
    },
  });
  return { runner, calls, routineEvents, scheduleEvents, notifications, opened };
}

test('a due schedule is taken in the same list as the routines, with no logins', async () => {
  const state = setup();
  await state.runner.tick();
  assert.deepEqual(
    state.calls
      .filter((call) => call.method === 'routines.execute')
      .map((call) => [call.params.id, call.params.schedule, call.params.logins.length]),
    [
      ['morning', true, 0],
      ['watch', false, 1],
    ],
  );
  state.runner.stop();
});

test('a finished scheduled run is told to the window and notified only when its schedule asks', async () => {
  const state = setup({
    runs: [ended(), ended({ id: 'run-quiet', name: '조용한 예약', notify: false })],
  });
  await state.runner.tick();
  assert.deepEqual(state.scheduleEvents, [
    {
      type: 'finished',
      schedule_id: 'morning',
      name: '아침 정리',
      notify: true,
      run_id: 'run-morning',
      status: 'success',
      error: '',
    },
    {
      type: 'finished',
      schedule_id: 'morning',
      name: '조용한 예약',
      notify: false,
      run_id: 'run-quiet',
      status: 'success',
      error: '',
    },
  ]);
  // Not a routine's run: nothing on 'routine:event'.
  assert.equal(state.routineEvents.length, 0);
  assert.deepEqual(
    state.notifications.map((item) => [item.options.title, item.options.body]),
    [['Private Agent · 예약 실행 완료', '아침 정리']],
  );
  // A click opens the run.
  state.notifications[0].emit('click');
  assert.deepEqual(state.opened, [['run', 'run-morning']]);
  state.runner.stop();
});

test('a failure carries its error, and a focused window gets no system notification', async () => {
  const failed = ended({ id: 'run-failed', status: 'error', error: '연결할 수 없습니다.' });
  const background = setup({ runs: [failed] });
  await background.runner.tick();
  assert.deepEqual(
    background.notifications.map((item) => [item.options.title, item.options.body]),
    [['Private Agent · 예약 실행 실패', '아침 정리: 연결할 수 없습니다.']],
  );
  background.runner.stop();
  const focused = setup({ focused: true, runs: [failed] });
  await focused.runner.tick();
  assert.equal(focused.notifications.length, 0);
  assert.equal(focused.scheduleEvents[0].error, '연결할 수 없습니다.');
  focused.runner.stop();
});
