import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ANDROID_TASKS, androidTools, gradleCommand } from './android-check.mjs';

// A local stand-in for the checks that used to run in .github/workflows. `npm run ci` runs the core checks and then
// the Electron E2E suite; `npm run ci:core` (or the --core flag) stops after the core checks. Everything runs once on
// this machine with the installed Node.js and Python, where GitHub Actions ran a matrix of versions and operating
// systems. The core checks end with the Android app's (scripts/android-check.mjs), which a machine without a JDK or
// the Android SDK skips. The Windows .exe build stays on Windows, as `npm run build:exe`.

const projectRoot = fileURLToPath(new URL('../', import.meta.url));

const flags = new Set(process.argv.slice(2));
if (flags.has('--help') || flags.has('-h')) {
  console.log(
    [
      'Usage: npm run ci [-- <flags>]   (or npm run ci:core for the core checks only)',
      '',
      '  --core             Run the core checks only, skip the E2E suite.',
      '  --source-backend   In the E2E phase, run the backend from backend/.venv instead of building',
      '                     it: skips `npm run build:python` and sets PRIVATE_AGENT_E2E_SOURCE_BACKEND=1.',
      "  --no-android       Skip the Android app's checks (skipped by themselves without a JDK 17+ or the",
      '                     Android SDK).',
      '  --help, -h         Show this help.',
    ].join('\n'),
  );
  process.exit(0);
}
const coreOnly = flags.has('--core');
const sourceBackend = flags.has('--source-backend');
const skipAndroid = flags.has('--no-android');

// Node.js and npm, resolved the way scripts/workflow.mjs does, so a run through `npm run ci` finds the same npm.
const [major, minor] = process.versions.node.split('.').map(Number);
if (major < 22 || (major === 22 && minor < 13)) {
  console.error('Node.js 22.13 이상이 필요합니다. Node.js 설치와 PATH를 확인하세요.');
  process.exit(1);
}
const npmCli = process.env.npm_execpath;
if (!npmCli || !existsSync(npmCli)) {
  console.error('npm run ci 또는 npm run ci:core로 실행하세요.');
  process.exit(1);
}

const baseEnv = { ...process.env, NEXT_TELEMETRY_DISABLED: '1' };

// The core checks match what .github/workflows/check.yml ran per push and pull request; the E2E phase matches
// e2e-linux.yml. On one machine the matrix (Node.js 22/24, Python 3.10/3.13, Ubuntu/Windows) collapses to one run.
const coreChecks = [
  'format:check',
  'lint',
  'typecheck',
  'typecheck:electron',
  'test:main',
  'build:web',
  'test:python',
  'lint:python',
  'format:python:check',
  'notices:check',
];
const e2eSteps = [
  'setup:electron',
  ...(sourceBackend ? [] : ['build:python']),
  'build:hermes',
  'test:e2e',
];

function runStep(name, env) {
  console.log(`\n── npm run ${name}`);
  const started = Date.now();
  const result = spawnSync(process.execPath, [npmCli, 'run', name], {
    cwd: projectRoot,
    stdio: 'inherit',
    shell: false,
    env,
  });
  if (result.error) console.error(result.error.message);
  return {
    name,
    label: `npm run ${name}`,
    ok: result.status === 0,
    skipped: false,
    ms: Date.now() - started,
  };
}

// The Android app's checks, with its Gradle wrapper: skipped, with the reason, when this machine lacks what they need.
function runAndroid() {
  const label = `android: gradlew ${ANDROID_TASKS.join(' ')}`;
  const skipped = { name: 'android', label, ok: false, skipped: true, ms: 0 };
  if (skipAndroid) return skipped;
  console.log(`\n── ${label}`);
  const tools = androidTools({ root: projectRoot });
  if (!tools.ok) {
    console.log('Android 검사를 건너뜁니다.');
    for (const reason of tools.missing) console.log(`  - ${reason}`);
    return skipped;
  }
  const started = Date.now();
  const { command, args } = gradleCommand(tools.androidDir);
  const result = spawnSync(command, args, {
    cwd: tools.androidDir,
    stdio: 'inherit',
    shell: false,
    env: baseEnv,
  });
  if (result.error) console.error(result.error.message);
  return {
    name: 'android',
    label,
    ok: result.status === 0,
    skipped: false,
    ms: Date.now() - started,
  };
}

// Warn about anything a check needs that is missing, so the failure that follows is easy to read.
const warnings = [];
if (!existsSync(path.join(projectRoot, 'node_modules')))
  warnings.push('node_modules가 없습니다. `npm ci`를 먼저 실행하세요.');
if (!existsSync(path.join(projectRoot, 'backend', '.venv')))
  warnings.push('backend/.venv가 없습니다. `npm run setup:python`을 먼저 실행하세요.');
for (const warning of warnings) console.log(`⚠️  ${warning}`);

const results = [];
// The core checks run to the end even after one fails, so a single run reports every problem.
for (const name of coreChecks) results.push(runStep(name, baseEnv));
results.push(runAndroid());
const coreFailed = results.some((r) => !r.ok && !r.skipped);

if (!coreOnly) {
  if (coreFailed) {
    // The E2E suite builds for several minutes; skip it when a core check is already broken.
    for (const name of e2eSteps)
      results.push({ name, label: `npm run ${name}`, ok: false, skipped: true, ms: 0 });
    console.log('\n핵심 검사가 실패해 E2E를 건너뜁니다. 위 오류를 먼저 고치세요.');
  } else {
    const e2eEnv = sourceBackend ? { ...baseEnv, PRIVATE_AGENT_E2E_SOURCE_BACKEND: '1' } : baseEnv;
    // The E2E phase stops at the first failure: a failed setup or build makes the test step pointless.
    let stopped = false;
    for (const name of e2eSteps) {
      if (stopped) {
        results.push({ name, label: `npm run ${name}`, ok: false, skipped: true, ms: 0 });
        continue;
      }
      const result = runStep(name, e2eEnv);
      results.push(result);
      if (!result.ok) stopped = true;
    }
  }
}

const line = '─'.repeat(48);
console.log(`\n${line}\n${coreOnly ? '핵심 검사 결과' : '로컬 CI 결과'}`);
for (const r of results) {
  const mark = r.skipped ? '– 건너뜀' : r.ok ? '✓ 통과 ' : '✗ 실패 ';
  const time = r.skipped ? '' : `  ${(r.ms / 1000).toFixed(1)}s`;
  console.log(`  ${mark}  ${r.label}${time}`);
}
console.log(line);

const failed = results.filter((r) => !r.ok && !r.skipped);
if (failed.length) {
  console.error(
    `\n${failed.length}개 검사가 실패했습니다: ${failed.map((r) => r.name).join(', ')}`,
  );
  process.exit(1);
}
console.log('\n모든 검사를 통과했습니다.');
