const { test } = require('node:test');
const assert = require('node:assert/strict');
const { relaunchOptions } = require('../electron/relaunch.cjs');

const own =
  'C:\\Users\\me\\AppData\\Local\\Private Agent\\app-0.1.0-20261001000000\\Private Agent.exe';
const newer =
  'C:\\Users\\me\\AppData\\Local\\Private Agent\\app-0.1.1-20261007000000\\Private Agent.exe';

test('the app starts again as the program that was started, of another build too, without arguments', () => {
  assert.deepEqual(
    relaunchOptions({ execPath: newer, args: [] }, { execPath: own, packaged: true }),
    { execPath: newer, args: [] },
  );
  // Whatever the start was given, a packaged app takes no arguments: a demo's never come here.
  assert.deepEqual(
    relaunchOptions(
      { execPath: newer.toUpperCase(), args: ['--remote-debugging-port=9222'] },
      { execPath: own, packaged: true },
    ),
    { execPath: newer.toUpperCase(), args: [] },
  );
});

test('another program, a relative path or nothing known starts this app as it is', () => {
  for (const started of [
    { execPath: 'C:\\Windows\\System32\\cmd.exe', args: [] },
    { execPath: 'Private Agent.exe' },
    { execPath: 42 },
    null,
    undefined,
  ])
    assert.deepEqual(relaunchOptions(started, { execPath: own, packaged: true }), {
      execPath: own,
      args: [],
    });
});

test('a source run starts again with its own arguments, the app folder among them', () => {
  const electron = '/repo/node_modules/electron/dist/electron';
  assert.deepEqual(
    relaunchOptions({ execPath: electron, args: ['.'] }, { execPath: electron, packaged: false }),
    { execPath: electron, args: ['.'] },
  );
  assert.deepEqual(
    relaunchOptions(
      { execPath: electron, args: ['.', 3] },
      { execPath: electron, packaged: false },
    ),
    { execPath: electron, args: [] },
  );
});
