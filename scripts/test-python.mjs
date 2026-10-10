import { runPython } from './python.mjs';
// Every Python test, or only those named: npm run test:python -- backend/tests/test_web_login.py
// With --coverage (npm run test:python:coverage), under coverage.py, a development extra: the backend's own code is
// measured, branches included, and a report per file follows the tests. Its data goes to .coverage, which git ignores.
const coverage = process.argv.includes('--coverage');
const targets = process.argv.slice(2).filter((arg) => arg !== '--coverage');
const pytest = [
  '-m',
  'pytest',
  '-c',
  'backend/pyproject.toml',
  ...(targets.length ? targets : ['backend/tests']),
  '-q',
];
const env = { PYTEST_DISABLE_PLUGIN_AUTOLOAD: '1' };
if (coverage) {
  const measured = ['--source=backend', '--omit=*/backend/tests/*,*/backend/.venv/*'];
  runPython(['-m', 'coverage', 'run', '--branch', ...measured, ...pytest], env);
  runPython(['-m', 'coverage', 'report', '--skip-empty'], env);
} else {
  runPython(pytest, env);
}
