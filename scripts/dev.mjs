import { spawn, spawnSync } from 'node:child_process';
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
function stop(code = 0) {
  if (closing) return;
  closing = true;
  end(next);
  end(desktop);
  process.exitCode = code;
}
process.on('SIGINT', () => stop());
process.on('SIGTERM', () => stop());
next.on('exit', (code) => stop(code || 0));
for (let attempt = 0; attempt < 120 && !closing; attempt++) {
  if (url)
    try {
      const response = await fetch(url);
      if (response.ok) {
        desktop = spawn(electron, ['.'], {
          stdio: 'inherit',
          env: {
            ...process.env,
            PRIVATE_AGENT_DEV_URL: url,
            ...(userData ? { PRIVATE_AGENT_USER_DATA: userData } : {}),
          },
        });
        desktop.on('exit', (code) => stop(code || 0));
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
