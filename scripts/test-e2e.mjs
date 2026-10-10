import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);

// The Playwright E2E suite, as npm run test:e2e and scripts/build-exe.mjs run it; any arguments go to Playwright
// (a spec file, --grep …). On Linux the app's windows open on a virtual display (Xvfb) when xvfb-run is installed, so
// a run in the background leaves the desktop alone and needs no display of its own (a CI runner has none);
// PRIVATE_AGENT_E2E_SHOW=1 opens them on the real display instead, to watch a test.
const playwright = [require.resolve('@playwright/test/cli'), 'test', ...process.argv.slice(2)];
let command = process.execPath;
let args = playwright;
if (process.platform === 'linux' && process.env.PRIVATE_AGENT_E2E_SHOW !== '1') {
  const found = spawnSync('xvfb-run', ['--help'], { stdio: 'ignore' });
  if (!found.error) {
    // -a picks a free display number, so two runs at once do not collide; the screen is the size of a usual desktop,
    // which the demo recording (electron/demo.cjs) fills.
    command = 'xvfb-run';
    args = ['-a', '-s', '-screen 0 1920x1080x24', process.execPath, ...playwright];
  } else if (!process.env.DISPLAY && !process.env.WAYLAND_DISPLAY) {
    console.error(
      'Electron needs a display: install xvfb (apt-get install xvfb) for a virtual one, or run on a desktop.',
    );
    process.exit(1);
  }
}
const result = spawnSync(command, args, { stdio: 'inherit' });
if (result.error) console.error(result.error.message);
process.exit(result.status ?? 1);
