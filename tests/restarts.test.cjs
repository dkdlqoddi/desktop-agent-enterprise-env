const { test } = require('node:test');
const assert = require('node:assert/strict');
const { Restarts } = require('../electron/restarts.cjs');

// A clock and timers the test moves by hand.
function setup({ starts = [] } = {}) {
  let now = 0;
  const timers = [];
  const events = [];
  const restarts = new Restarts({
    start: async () => {
      events.push('start');
      const outcome = starts.shift() ?? 'ok';
      if (outcome !== 'ok') throw new Error(outcome);
    },
    stop: async () => events.push('stop'),
    restarted: () => events.push('restarted'),
    gaveUp: (error) => events.push(error ? `gave up: ${error.message}` : 'gave up'),
    failed: (error) => events.push(`failed: ${error.message}`),
    now: () => now,
    schedule: (task, delay) => {
      timers.push({ at: now + delay, task });
      return { unref() {} };
    },
  });
  const advance = async (ms) => {
    now += ms;
    for (const timer of timers.filter((item) => item.at <= now)) {
      timers.splice(timers.indexOf(timer), 1);
      await timer.task();
      await new Promise((resolve) => setImmediate(resolve));
    }
  };
  return { restarts, events, timers, advance };
}

test('a backend that ended is started again after a moment', async () => {
  const { restarts, events, advance } = setup();
  restarts.exited();
  assert.equal(restarts.active, true);
  await advance(999);
  assert.deepEqual(events, []);
  await advance(1);
  assert.deepEqual(events, ['start', 'restarted']);
  assert.equal(restarts.active, false);
});

test('a failed start is stopped and tried again later, and a backend that keeps ending is left stopped', async () => {
  const { restarts, events, timers, advance } = setup({ starts: ['응답 없음', 'ok'] });
  restarts.exited();
  // The new backend ends while it starts: the start under way decides.
  await advance(1000);
  restarts.exited();
  assert.deepEqual(events, ['start', 'failed: 응답 없음', 'stop']);
  assert.equal(timers.length, 1);
  await advance(5000);
  assert.deepEqual(events.slice(3), ['start', 'restarted']);
  // A third end within two minutes: one more try, then no more.
  restarts.exited();
  await advance(15000);
  assert.deepEqual(events.slice(5), ['start', 'restarted']);
  restarts.exited();
  assert.deepEqual(events.slice(7), ['gave up']);
  assert.equal(timers.length, 0);
  // Ends spread over more than two minutes are each started again.
  await advance(120_000);
  restarts.exited();
  await advance(1000);
  assert.deepEqual(events.slice(8), ['start', 'restarted']);
});

test('giving up tells why the last start failed, and nothing once one succeeded', async () => {
  const quarantined = '실행 파일을 찾지 못했습니다';
  const { restarts, events, advance } = setup({ starts: [quarantined, quarantined, quarantined] });
  restarts.exited();
  await advance(1000);
  await advance(5000);
  await advance(15000);
  assert.deepEqual(events.slice(-1), [`gave up: ${quarantined}`]);
  assert.equal(restarts.active, false);
  // Later ends of a backend that did start again: no failure to tell.
  const second = setup({ starts: ['응답 없음'] });
  second.restarts.exited();
  await second.advance(1000);
  await second.advance(5000);
  second.restarts.exited();
  await second.advance(15000);
  second.restarts.exited();
  assert.deepEqual(second.events.slice(-1), ['gave up']);
});

test('no restart once the app is closing', async () => {
  const { restarts, events, advance } = setup();
  restarts.exited();
  restarts.end();
  await advance(1000);
  assert.deepEqual(events, []);
  assert.equal(restarts.active, false);
  restarts.exited();
  assert.equal(restarts.active, false);
});
