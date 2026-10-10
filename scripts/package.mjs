import { spawn, spawnSync } from 'node:child_process';
import { copyFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { removeBuildInfo, writeBuildInfo } from './build-info.mjs';
const require = createRequire(import.meta.url);
const target = process.argv[2] || 'win';
const targets = {
  // The one .exe users run: electron-builder's nsis target with scripts/launcher.nsi, which unpacks the app once
  // per build into %LOCALAPPDATA% and starts it from there; nothing is installed.
  win: ['--win', 'nsis'],
  dir: ['--win', '--dir'],
};
if (!Object.hasOwn(targets, target)) {
  console.error('지원하는 패키징 대상은 win(실행 파일), dir(실행 폴더)입니다.');
  process.exit(1);
}
if (process.platform !== 'win32') {
  console.error(
    '이 앱은 Windows 전용입니다. 내장 Python 런타임을 포함한 패키지는 Windows에서 빌드하세요. .github/workflows/build.yml의 Windows 빌드도 사용할 수 있습니다.',
  );
  process.exit(1);
}
const env = { ...process.env, NEXT_TELEMETRY_DISABLED: '1' };
// The commit the package is built from goes into it as electron/build-info.json, for the diagnostics the user copies.
// Written before the build steps, so that a file they regenerate never counts as an uncommitted change, and removed
// when this script ends, however it ends, so that a run from source never reports the last build.
const build = writeBuildInfo();
process.on('exit', removeBuildInfo);
console.log(
  build ? `빌드 커밋: ${build.commit}` : 'git 정보가 없어 빌드 커밋을 기록하지 않습니다.',
);
for (const args of [
  [require.resolve('next/dist/bin/next'), 'build'],
  ['scripts/build-python.mjs'],
  ['scripts/build-browser.mjs'],
]) {
  const result = spawnSync(process.execPath, args, { stdio: 'inherit', env });
  if (result.error) console.error(result.error.message);
  if (result.status !== 0) process.exit(result.status ?? 1);
}

// electron-builder unpacks its tools (NSIS) into its cache folder, then renames the folder. While
// antivirus still scans the new files, Windows refuses the rename (EPERM, EBUSY), and electron-builder
// stops. The next run goes on from the tools already in the cache, so such a failure is retried.
const ATTEMPTS = 3;
for (let attempt = 1; ; attempt++) {
  const { status, output } = await new Promise((resolve) => {
    const child = spawn(
      process.execPath,
      [require.resolve('electron-builder/cli.js'), ...targets[target]],
      { stdio: ['inherit', 'pipe', 'pipe'], env },
    );
    let output = '';
    const echo = (stream) => (chunk) => {
      stream.write(chunk);
      output = (output + chunk).slice(-65536);
    };
    child.stdout.on('data', echo(process.stdout));
    child.stderr.on('data', echo(process.stderr));
    child.on('error', (error) => {
      console.error(error.message);
      resolve({ status: 1, output: '' });
    });
    child.on('close', (status) => resolve({ status, output }));
  });
  if (status === 0) break;
  if (attempt === ATTEMPTS || !/\b(EPERM|EBUSY)\b/.test(output)) process.exit(status ?? 1);
  console.log(
    `\n백신 검사 중인 파일 때문에 electron-builder가 중단되었습니다. 다시 시도합니다 (${attempt + 1}/${ATTEMPTS}).`,
  );
  await new Promise((resolve) => setTimeout(resolve, 5000));
}

// The demo recording script (record-demo.cmd) goes out beside the exe: it finds the app next to itself.
copyFileSync(
  new URL('../record-demo.cmd', import.meta.url),
  new URL('../release/record-demo.cmd', import.meta.url),
);
