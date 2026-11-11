import { runPython } from './python.mjs';

runPython(['scripts/build-hermes.py', ...process.argv.slice(2)]);
