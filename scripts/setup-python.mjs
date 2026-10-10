import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { projectRoot, runPython, venvPath, venvPython } from './python.mjs';

const versionCheck = ['-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'];
const options = { cwd: projectRoot, shell: false, env: { ...process.env, PYTHONPATH: '' } };

if (!existsSync(venvPython)) {
  const candidates = process.env.PYTHON
    ? [[process.env.PYTHON]]
    : process.platform === 'win32'
      ? [['python'], ['py', '-3']]
      : [['python3'], ['python']];
  const python = candidates.find(
    ([command, ...args]) =>
      spawnSync(command, [...args, ...versionCheck], { ...options, stdio: 'ignore' }).status === 0,
  );
  if (!python) {
    console.error(
      'Python 3.10 이상을 설치하고 PATH를 확인하세요. PYTHON 환경변수로 Python 실행 파일 경로를 지정할 수도 있습니다.',
    );
    process.exit(1);
  }
  const [command, ...args] = python;
  const result = spawnSync(command, [...args, '-m', 'venv', venvPath], {
    ...options,
    stdio: 'inherit',
  });
  if (result.error) console.error(result.error.message);
  if (result.status !== 0) process.exit(result.status ?? 1);
}

runPython(versionCheck);
// Existing virtual environments may not have pip installed yet.
if (
  spawnSync(venvPython, ['-m', 'pip', '--version'], { ...options, stdio: 'ignore' }).status !== 0
) {
  runPython(['-m', 'ensurepip', '--upgrade']);
}
// pip builds the backend's package in an isolated environment, with the setuptools its pyproject asks for; constraints
// given with -c do not reach that environment, so a pip that knows --build-constraint gets the same file for it and the
// build uses the pinned setuptools. An older pip, as an older Python's venv brings, builds with the newest one.
const help = spawnSync(venvPython, ['-m', 'pip', 'install', '--help'], {
  ...options,
  encoding: 'utf8',
});
const buildConstraint = /--build-constraint\b/.test(help.stdout ?? '')
  ? ['--build-constraint', 'backend/constraints.txt']
  : [];
runPython([
  '-m',
  'pip',
  'install',
  '--disable-pip-version-check',
  '-c',
  'backend/constraints.txt',
  ...buildConstraint,
  '-e',
  './backend[dev]',
]);
