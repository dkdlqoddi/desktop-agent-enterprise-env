import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const projectRoot = fileURLToPath(new URL('../', import.meta.url));

// npm ci recreates node_modules, which also deletes the Electron binary downloaded on first use.
// Skip it when package-lock.json and the platform are unchanged since the last successful npm ci.
export function npmCiSteps() {
  const stampPath = path.join(projectRoot, 'node_modules', '.npm-ci-stamp');
  const lockHash = createHash('sha256')
    .update(readFileSync(path.join(projectRoot, 'package-lock.json')))
    .digest('hex');
  const stamp = `${process.platform}-${process.arch} ${lockHash}`;
  if (existsSync(stampPath) && readFileSync(stampPath, 'utf8') === stamp) {
    console.log('package-lock.json이 바뀌지 않아 npm ci를 건너뜁니다.');
    return [];
  }
  return [['ci'], () => writeFileSync(stampPath, stamp)];
}

export function runWorkflow(steps, env = {}) {
  const [major, minor] = process.versions.node.split('.').map(Number);
  if (major < 22 || (major === 22 && minor < 13)) {
    console.error('Node.js 22.13 이상이 필요합니다. Node.js 설치와 PATH를 확인하세요.');
    process.exit(1);
  }
  // npm provides its CLI path, including when launched through npm.cmd on Windows.
  const npmCli = process.env.npm_execpath;
  if (!npmCli || !existsSync(npmCli)) {
    console.error('npm run dev:ready 또는 npm run build:exe로 실행하세요.');
    process.exit(1);
  }

  const total = steps.filter(Array.isArray).length;
  let count = 0;
  for (const step of steps) {
    // A function step runs in-process once the npm steps before it have succeeded.
    if (typeof step === 'function') {
      step();
      continue;
    }
    const command = `npm ${step.join(' ')}`;
    console.log(`\n[${++count}/${total}] ${command}`);
    const result = spawnSync(process.execPath, [npmCli, ...step], {
      cwd: projectRoot,
      stdio: 'inherit',
      shell: false,
      env: { ...process.env, ...env },
    });
    if (result.error) console.error(result.error.message);
    if (result.status !== 0) {
      console.error(`실행을 중단했습니다: ${command}`);
      process.exit(result.status ?? 1);
    }
  }
}
