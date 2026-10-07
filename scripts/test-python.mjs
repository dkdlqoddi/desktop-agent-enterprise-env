import { runPython } from './python.mjs';
// Every Python test, or only those named: npm run test:python -- backend/tests/test_web_login.py
const targets = process.argv.slice(2);
runPython(
  [
    '-m',
    'pytest',
    '-c',
    'backend/pyproject.toml',
    ...(targets.length ? targets : ['backend/tests']),
    '-q',
  ],
  {
    PYTEST_DISABLE_PLUGIN_AUTOLOAD: '1',
  },
);
