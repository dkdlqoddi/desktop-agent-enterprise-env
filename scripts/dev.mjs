import { spawn, spawnSync } from 'node:child_process';
import { watch } from 'node:fs';
import { createRequire } from 'node:module';
import { createServer } from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const require = createRequire(import.meta.url);
const root = fileURLToPath(new URL('../', import.meta.url));
// The electron package downloads its binary on first require. Resolve it before starting Next so a
// failed download stops here once instead of being retried and hidden by the readiness loop below.
const electron = require('electron');

// The development window trusts the screens it loads with the app's own requests (main.cjs
// trustedSender), so they must come from this Next and no other server: Next gets a port that is free
// now (3000 when it is), binds exactly that one or stops, and the window opens on the address Next
// itself reports once it listens there.
function freePort(port) {
  return new Promise((resolve) => {
    const server = createServer();
    server.once('error', () => resolve(port ? freePort(0) : null));
    server.listen(port, '127.0.0.1', () => {
      const { port: found } = server.address();
      server.close(() => resolve(found));
    });
  });
}
const port = await freePort(3000);
if (!port) {
  console.error('Next.js 개발 서버에 쓸 빈 포트를 찾지 못했습니다.');
  process.exit(1);
}

// The development app keeps its settings, site accounts, login profiles and choice of Wiki apart from
// the installed app's (%APPDATA%\Private Agent): trying a change never reads or rewrites what the user
// works with. PRIVATE_AGENT_USER_DATA names another folder; PRIVATE_AGENT_DEV_SHARED=1 uses the
// installed app's.
const userData =
  process.env.PRIVATE_AGENT_USER_DATA ||
  (process.env.PRIVATE_AGENT_DEV_SHARED === '1'
    ? ''
    : path.join(root, '.local-data', 'dev-user-data'));

const next = spawn(
  process.execPath,
  [require.resolve('next/dist/bin/next'), 'dev', '--hostname', '127.0.0.1', '--port', String(port)],
  { stdio: ['inherit', 'pipe', 'inherit'] },
);
let url = null;
let output = '';
next.stdout.on('data', (chunk) => {
  process.stdout.write(chunk);
  if (url) return;
  output = (output + chunk).slice(-4096);
  // "- Local: http://127.0.0.1:<port>", printed once the server listens.
  const local = /- Local:\s+(http:\/\/127\.0\.0\.1:(\d+))\b/.exec(
    output.replace(/\x1b\[[0-9;]*m/g, ''),
  );
  if (local && Number(local[2]) === port) url = local[1];
});

// On Windows, killing the Next CLI alone leaves the dev server it started running, still holding the
// port, and Electron's own children (the Python backend) outlive it: each process tree is ended whole.
function end(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  if (process.platform === 'win32')
    spawnSync('taskkill', ['/pid', String(child.pid), '/T', '/F'], {
      stdio: 'ignore',
      windowsHide: true,
    });
  else child.kill();
}

let desktop;
let closing = false;
let restarting = false;
// The file watchers (watchSources), closed with the session: on Linux a recursive watcher keeps Node running even
// after unref().
const watchers = [];
function stop(code = 0) {
  if (closing) return;
  closing = true;
  for (const watcher of watchers) watcher.close();
  end(next);
  end(desktop);
  process.exitCode = code;
}
process.on('SIGINT', () => stop());
process.on('SIGTERM', () => stop());
next.on('exit', (code) => stop(code || 0));

// The development app writes the backend's error output (its tracebacks) and every log entry, the app's and the
// backend's, to this terminal as well (PRIVATE_AGENT_LOG_CONSOLE; set it to 0 to keep them to the log files).
// Closing its window ends the session; an Electron this script restarts does not.
function startDesktop() {
  const child = spawn(electron, ['.'], {
    stdio: 'inherit',
    env: {
      ...process.env,
      PRIVATE_AGENT_DEV_URL: url,
      PRIVATE_AGENT_LOG_CONSOLE: process.env.PRIVATE_AGENT_LOG_CONSOLE ?? '1',
      ...(userData ? { PRIVATE_AGENT_USER_DATA: userData } : {}),
    },
  });
  child.on('exit', (code) => {
    if (child === desktop && !restarting) stop(code || 0);
  });
  return child;
}

// Electron starts again, and the backend with it, when a file of the main process or of the backend changes: a reload
// of the window would load neither again. The screens need nothing from here, as Next reloads them itself (Fast
// Refresh). Changes within a moment of each other make one restart; the old Electron has ended, with its single-instance
// lock, before the new one starts.
let again = [];
async function restartDesktop(files) {
  again.push(...files);
  if (restarting || closing || !desktop) return;
  restarting = true;
  while (again.length && !closing) {
    console.log(`\n바뀐 파일: ${[...new Set(again)].join(', ')}. Electron을 다시 시작합니다.`);
    again = [];
    const old = desktop;
    const exited =
      old.exitCode !== null || old.signalCode !== null
        ? Promise.resolve()
        : new Promise((resolve) => old.once('exit', resolve));
    end(old);
    let timer;
    await Promise.race([exited, new Promise((resolve) => (timer = setTimeout(resolve, 10_000)))]);
    clearTimeout(timer);
    if (!closing) desktop = startDesktop();
  }
  restarting = false;
}

// What is watched: everything the main process loads, and the backend's Python files (not its tests or its virtual
// environment). PRIVATE_AGENT_DEV_WATCH=0 turns watching off, for a drive whose change events are unreliable (a network
// drive, a folder shared into a virtual machine).
const WATCHED = [
  {
    folder: 'electron',
    recursive: true,
    matches: (file) => /\.(cjs|js|json|html|css)$/.test(file),
  },
  {
    folder: path.join('backend', 'agent'),
    recursive: true,
    matches: (file) => file.endsWith('.py'),
  },
  { folder: 'backend', recursive: false, matches: (file) => file === 'main.py' },
];
function watchSources() {
  if (process.env.PRIVATE_AGENT_DEV_WATCH === '0') return;
  let timer;
  let changed = [];
  for (const { folder, recursive, matches } of WATCHED) {
    try {
      const watcher = watch(path.join(root, folder), { recursive }, (_event, file) => {
        if (closing || !file || !matches(String(file))) return;
        changed.push(path.join(folder, String(file)));
        clearTimeout(timer);
        timer = setTimeout(() => {
          const files = changed;
          changed = [];
          void restartDesktop(files);
        }, 300);
      });
      watcher.on('error', (error) =>
        console.error(`${folder}의 변경을 더 감시하지 못합니다: ${error.message}`),
      );
      watchers.push(watcher);
    } catch (error) {
      console.error(
        `${folder}의 변경을 감시하지 못했습니다: ${error.message}. 바꾼 뒤에는 앱을 직접 다시 실행하세요(PRIVATE_AGENT_DEV_WATCH=0이면 감시하지 않습니다).`,
      );
    }
  }
}

for (let attempt = 0; attempt < 120 && !closing; attempt++) {
  if (url)
    try {
      const response = await fetch(url);
      if (response.ok) {
        desktop = startDesktop();
        watchSources();
        break;
      }
    } catch {
      /* Next is compiling the first page. */
    }
  await new Promise((resolve) => setTimeout(resolve, 500));
}
if (!desktop && !closing) {
  console.error(`Next.js failed to start on port ${port}.`);
  stop(1);
}
