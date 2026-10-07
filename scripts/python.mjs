import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const projectRoot = fileURLToPath(new URL('../', import.meta.url));
export const venvPath = path.join(projectRoot, 'backend', '.venv');
export const venvPython = path.join(
  venvPath,
  process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
);

export function runPython(args, env = {}) {
  if (!existsSync(venvPython)) {
    console.error('Python 개발 환경이 없습니다. npm run setup:python을 먼저 실행하세요.');
    process.exit(1);
  }
  const result = spawnSync(venvPython, args, {
    cwd: projectRoot,
    stdio: 'inherit',
    shell: false,
    env: { ...process.env, PYTHONPATH: '', ...env },
  });
  if (result.error) console.error(result.error.message);
  if (result.status !== 0) process.exit(result.status ?? 1);
}
