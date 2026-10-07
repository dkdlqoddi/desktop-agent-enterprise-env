import { runPython } from './python.mjs';
// Every browser of the app runs in the Python backend: its Playwright installs the Chromium of its release into
// Playwright's shared folder, where the development app and the tests find it. Run after npm run setup:python.
runPython(['-m', 'playwright', 'install', 'chromium', '--no-shell']);
