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

const root = path.join(__dirname, '..');

// Whether Playwright's _electron.launch starts the app with --inspect=0, read from the installed playwright-core.
function playwrightInspects() {
  const lib = path.join(path.dirname(require.resolve('playwright-core/package.json')), 'lib');
  return readdirSync(lib, { recursive: true })
    .filter((name) => name.endsWith('.js'))
    .some((name) => readFileSync(path.join(lib, name), 'utf8').includes('"--inspect=0"'));
}

// Electron fuses: switches in the exe that electron-builder flips right before signing (build.electronFuses). They close
// ways of running code in the signed exe other than the app's own, except one the release build's E2E needs. Only a
// Windows build applies them: after changing one, build the exe there and check that it starts and that E2E passes.
test('the packaged exe runs only its own app code, from its validated archive', () => {
  const fuses = pkg.build.electronFuses;
  const electron = path.join(root, 'electron');
  const sources = readdirSync(electron, { recursive: true })
    .filter((name) => /\.(cjs|js)$/.test(name))
    .map((name) => [name, readFileSync(path.join(electron, name), 'utf8')]);
  // ELECTRON_RUN_AS_NODE would make the exe a plain Node.js. No part of the app runs it so: the backend is a program of
  // its own, and the MCP relay that AI tools start runs on their own Node.js (api-manual.cjs: command 'node').
  // child_process.fork needs the variable, so the main process must not use it.
  assert.equal(fuses.runAsNode, false);
  // NODE_OPTIONS, and NODE_EXTRA_CA_CERTS, which the same fuse governs: nothing sets them, and the main process makes no
  // HTTPS request of its own (the backend's LLM calls have their own CA setting).
  assert.equal(fuses.enableNodeOptionsEnvironmentVariable, false);
  for (const [name, source] of sources)
    assert.doesNotMatch(
      source,
      /ELECTRON_RUN_AS_NODE|NODE_OPTIONS|NODE_EXTRA_CA_CERTS|\.fork\(/,
      name,
    );
  // Left on: Playwright's _electron.launch, which the release build's E2E runs against the packaged exe
  // (PRIVATE_AGENT_E2E_EXECUTABLE in scripts/build-exe.mjs), starts it with --inspect=0 and drives the main process
  // through the Node inspector the exe announces ("Debugger listening on ws://…"); --remote-debugging-port only reaches
  // the windows. With the fuse off, the exe ignores the flag and every launch waits until it times out.
  assert.equal(fuses.enableNodeCliInspectArguments, true);
  assert.ok(
    playwrightInspects(),
    'Playwright no longer starts Electron with --inspect=0: set build.electronFuses.enableNodeCliInspectArguments to false.',
  );
  // The app is resources/app.asar and nothing else (no app folder beside it, no default_app.asar), checked against the
  // hash of its header that electron-builder embeds in the exe (on Windows since Electron 30): an edited archive does
  // not load. Nothing is unpacked beside it.
  assert.equal(pkg.build.asar, true);
  assert.equal(pkg.build.asarUnpack, undefined);
  assert.equal(fuses.enableEmbeddedAsarIntegrityValidation, true);
  assert.equal(fuses.onlyLoadAppFromAsar, true);
  // The screens come from the agent:// protocol (protocol.cjs) and the recorder's own scheme, never from file://, so
  // file:// pages need none of the privileges early Electron gave them.
  assert.equal(fuses.grantFileProtocolExtraPrivileges, false);
  for (const [name, source] of sources)
    assert.doesNotMatch(source, /loadFile\(|['"`]file:\/\//, name);
});

// npm 11 reads package.json's allowScripts (npm RFC 868): which dependencies' install scripts may run. The only
// dependency with one is electron-winstaller, which npm installs as a peer of electron-builder for the Squirrel.Windows
// target. This project builds the nsis target alone and the script only picks a 7-Zip binary for Squirrel, so it is
// denied, which also keeps npm from listing it as unreviewed. A new dependency with an install script stops here until
// someone has read its script and listed it.
test('install scripts run only where package.json allows them', () => {
  const lock = require('../package-lock.json');
  for (const [key, entry] of Object.entries(lock.packages)) {
    if (!entry.hasInstallScript) continue;
    const name = key.slice(key.lastIndexOf('node_modules/') + 'node_modules/'.length);
    assert.ok(
      Object.hasOwn(pkg.allowScripts, name),
      `${name} has an install script: read it, then allow (true) or deny (false) it in package.json's allowScripts.`,
    );
  }
  assert.deepEqual(pkg.allowScripts, { 'electron-winstaller': false });
});

// scripts/package.mjs writes electron/build-info.json before it packages, so the copied diagnostics name the commit of
// the build (electron/diagnostics.cjs); electron/**/* takes the file into the asar, and git never sees it.
test('the package carries the commit it was built from', async () => {
  const { buildInfoPath } = await import('../scripts/build-info.mjs');
  assert.equal(buildInfoPath, path.join(root, 'electron', 'build-info.json'));
  assert.ok(pkg.build.files.includes('electron/**/*'));
  assert.match(
    readFileSync(path.join(root, '.gitignore'), 'utf8'),
    /^electron\/build-info\.json$/m,
  );
  assert.match(
    readFileSync(path.join(root, 'electron', 'diagnostics.cjs'), 'utf8'),
    /'build-info\.json'/,
  );
});

// The Node.js the project needs, named alike in package.json's engines, .nvmrc (nvm, fnm, setup-node) and the check of
// scripts/workflow.mjs; CI runs this major version and the next LTS (.github/workflows/check.yml).
test('one Node.js version everywhere', () => {
  assert.equal(pkg.engines.node, '>=22.12');
  assert.equal(readFileSync(path.join(root, '.nvmrc'), 'utf8').trim(), '22');
  assert.match(
    readFileSync(path.join(root, 'scripts', 'workflow.mjs'), 'utf8'),
    /major < 22 \|\| \(major === 22 && minor < 12\)/,
  );
});
