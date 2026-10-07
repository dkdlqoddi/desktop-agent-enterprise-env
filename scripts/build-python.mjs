import { spawnSync } from 'node:child_process';
import { existsSync, rmSync } from 'node:fs';
import path from 'node:path';
import { projectRoot, runPython, venvPython } from './python.mjs';
// A folder, not one file: a one-file backend unpacks about 130MB into %TEMP% at every start
// (seconds on Windows, more under antivirus) and leaves it there when the app ends the backend.
// The portable .exe already unpacks the whole app, this folder included, once per launch.
// dist/backend holds only this build, so an earlier one-file backend never reaches the package.
rmSync(path.join(projectRoot, 'dist', 'backend'), {
  recursive: true,
  force: true,
  maxRetries: 10, // antivirus may still be scanning the previous build
});
const pyinstaller = [
  '-m',
  'PyInstaller',
  '--noconfirm',
  '--clean',
  '--onedir',
  // UTF-8 mode, as PYTHONUTF8=1 gives the source backend: a frozen backend ignores that variable
  // and would otherwise read and write text in the Windows code page (cp949).
  '--python-option',
  'X utf8',
  '--name',
  'private-agent-backend',
  '--distpath',
  'dist/backend',
  '--workpath',
  'build/python',
  '--specpath',
  'build',
  '--paths',
  path.join(projectRoot, 'backend'),
  ...(process.platform === 'win32'
    ? [
        '--hidden-import',
        'pythoncom',
        '--hidden-import',
        'pywintypes',
        '--hidden-import',
        'win32com.client',
        '--hidden-import',
        'win32timezone',
        '--hidden-import',
        'win32gui',
        '--hidden-import',
        'win32con',
      ]
    : []),
  'backend/main.py',
];
// Conda's Python (Anaconda, Miniconda) keeps the DLLs of standard modules, such as sqlite3.dll of
// _sqlite3.pyd, in Library\bin and adds that folder to its own DLL search as it starts, so
// backend/.venv and the tests work. PyInstaller looks there only through PATH, as an activated
// conda environment has it; otherwise it only warns "Library not found" and builds a backend
// that ends at its first import of sqlite3.
const basePrefix = spawnSync(venvPython, ['-c', 'import sys; print(sys.base_prefix)'], {
  encoding: 'utf8',
  env: { ...process.env, PYTHONPATH: '', PYTHONUTF8: '1' },
}).stdout?.trim();
const condaDlls = basePrefix && path.join(basePrefix, 'Library', 'bin');
runPython(
  pyinstaller,
  condaDlls && existsSync(condaDlls)
    ? { PATH: `${condaDlls}${path.delimiter}${process.env.PATH}` }
    : {},
);
// The backend loads every standard module it bundles, so a DLL PyInstaller did not find stops the
// build here with the module's name, not the app at its start (or E2E at its first screen).
if (process.platform === 'win32') {
  const check = spawnSync(
    path.join(projectRoot, 'dist', 'backend', 'private-agent-backend', 'private-agent-backend.exe'),
    ['--check-build'],
    { stdio: 'inherit' },
  );
  if (check.error) console.error(check.error.message);
  if (check.status !== 0) {
    console.error(
      '빌드한 백엔드가 Python 표준 모듈을 불러오지 못했습니다. PyInstaller가 찾지 못한 DLL은 위의 "Library not found" 경고에 있습니다.',
    );
    process.exit(1);
  }
}
