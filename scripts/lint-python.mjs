import { runPython } from './python.mjs';
// Ruff, the backend's linter and formatter, from the virtual environment (a development extra that npm run setup:python
// installs at the version backend/constraints.txt pins), with its rules from backend/pyproject.toml: npm run lint:python
// checks the code (ruff check), npm run format:python formats it (ruff format) and npm run format:python:check only
// tells whether it would (ruff format --check), as CI does. Arguments after -- go to ruff: npm run lint:python -- --fix.
runPython(['-m', 'ruff', ...process.argv.slice(2)]);
