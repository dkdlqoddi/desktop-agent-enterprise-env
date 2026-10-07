import path from 'node:path';
import { projectRoot, runPython } from './python.mjs';
// The Chromium packaged with the app (resources/browsers), installed by the backend's Python Playwright, so the
// packaged backend finds the revision of its release there: Electron points PLAYWRIGHT_BROWSERS_PATH to it.
runPython(['-m', 'playwright', 'install', 'chromium', '--no-shell'], {
  PLAYWRIGHT_BROWSERS_PATH: path.join(projectRoot, 'dist', 'browsers'),
});
