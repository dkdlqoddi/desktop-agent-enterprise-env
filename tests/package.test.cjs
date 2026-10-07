const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync, readdirSync } = require('node:fs');
const path = require('node:path');
const pkg = require('../package.json');

// The exe users run is electron-builder's nsis target with a script of our own, scripts/launcher.nsi: the first
// launch of a build unpacks the app into %LOCALAPPDATA%\Private Agent\app-<version>-<build time>, later launches start
// it from there. The portable target before it unpacked all of the app (about 950 MB) into %TEMP% at every launch: the
// dashboard took 73-89 seconds on a fast PC on 2026-10-02, against 2 seconds for the app already unpacked.
test('the exe unpacks the app once per build and starts it from there', () => {
  assert.deepEqual(pkg.build.win.target, ['nsis']);
  assert.equal(pkg.build.nsis.script, 'scripts/launcher.nsi');
  // The launcher never asks for administrator rights, so no elevate.exe; and a plain 7z package, without the blockmap
  // of auto-updates.
  assert.equal(pkg.build.nsis.packElevateHelper, false);
  assert.equal(pkg.build.nsis.differentialPackage, false);
  // Nothing is published anywhere: a build on a tag with GH_TOKEN set would otherwise upload a GitHub release.
  assert.equal(pkg.build.publish, null);
  const script = readFileSync(path.join(__dirname, '..', 'scripts', 'launcher.nsi'), 'utf8');
  // What electron-builder hands the script.
  for (const name of [
    'APP_64',
    'APP_64_UNPACKED_SIZE',
    'APP_EXECUTABLE_FILENAME',
    'PRODUCT_FILENAME',
    'APP_GUID',
    'VERSION',
  ])
    assert.match(script, new RegExp(`\\$\\{${name}\\}`), name);
  assert.equal(
    require('electron-builder/package.json').version,
    '26.15.3',
    'Before moving to another electron-builder, build the exe on Windows and check that its first launch unpacks ' +
      'into %LOCALAPPDATA%\\Private Agent, that the next launch starts the app at once, and that a launch of a new ' +
      'build removes the old folder only after the app has ended; then update this version.',
  );
});

// Every browser of the app runs in the Python backend (backend/agent/browser_import.py): the app ships no Node
// Playwright, and nothing in the main process loads one.
test('no Node Playwright ships with the app or runs in it', () => {
  assert.equal(pkg.dependencies['playwright-core'], undefined);
  assert.ok(!pkg.build.extraResources.some((item) => /playwright/.test(item.from)));
  const electron = path.join(__dirname, '..', 'electron');
  for (const file of readdirSync(electron).filter((name) => name.endsWith('.cjs')))
    assert.doesNotMatch(
      readFileSync(path.join(electron, file), 'utf8'),
      /require\(['"]playwright/,
      file,
    );
});
