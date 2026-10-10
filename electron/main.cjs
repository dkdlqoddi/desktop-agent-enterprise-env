const {
  app,
  BrowserWindow,
  protocol,
  ipcMain,
  dialog,
  safeStorage,
  session,
  clipboard,
  Menu,
  Notification,
  shell,
} = require('electron');
const { spawn } = require('node:child_process');
const { createHash } = require('node:crypto');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { readJsonFile, writeFileSafely } = require('./files.cjs');
const { BackendProcess, ENDED, spawnFailure } = require('./backend-process.cjs');
const { editMenu } = require('./context-menu.cjs');
const { relaunchOptions } = require('./relaunch.cjs');
const { StorageManager } = require('./storage.cjs');
const { SiteStore, addressOf } = require('./sites.cjs');
const LOGIN_METHODS = require('./login-methods.json').methods;
const { ReminderScheduler } = require('./reminders.cjs');
const { PromptScheduler } = require('./schedules.cjs');
const { RoutineRunner, readsFiles } = require('./routines.cjs');
const { ImportNotices } = require('./import-notices.cjs');
const { ApiError, ACCESS_LEVELS, allows, authorize, forbidden } = require('./api-catalog.cjs');
const { ApiSettings } = require('./api-settings.cjs');
const { ApiServer } = require('./api-server.cjs');
const { McpServer } = require('./mcp.cjs');
const apiManual = require('./api-manual.cjs');
const { AppControl } = require('./app-control.cjs');
const { createApiMethods, createPathCheck } = require('./api-methods.cjs');
const { Diagnostics } = require('./diagnostics.cjs');
const { Restarts } = require('./restarts.cjs');
const { Recorder, PRIVILEGES: RECORDER_SCHEME } = require('./recorder.cjs');
const { DemoRun, demoLaunch, renderAgain } = require('./demo.cjs');
const demoPlan = require('./demo-plan.cjs');

// The backend, which runs every browser of the app with Python Playwright, uses the Chromium packaged with the app.
if (app.isPackaged)
  process.env.PLAYWRIGHT_BROWSERS_PATH = path.join(process.resourcesPath, 'browsers');

const ROOT = path.join(__dirname, '..');
const DEV_URL = !app.isPackaged ? process.env.PRIVATE_AGENT_DEV_URL : undefined;
// A demo recording (record-demo.cmd): `--demo <list>` records the app working through a list, `--demo-render
// <folder>` makes a recording's video again.
const LAUNCH = demoLaunch(process.argv);
if (process.env.PRIVATE_AGENT_USER_DATA)
  app.setPath('userData', process.env.PRIVATE_AGENT_USER_DATA);
protocol.registerSchemesAsPrivileged([
  {
    scheme: 'agent',
    // codeCache: the screens' scripts are compiled once and kept, as Chromium does for https pages.
    privileges: { standard: true, secure: true, supportFetchAPI: true, codeCache: true },
  },
  RECORDER_SCHEME,
]);
const ALLOWED = new Set([
  'health',
  'stats',
  'prompts.list',
  'prompts.save',
  'prompts.delete',
  'prompts.run',
  'runs.list',
  'runs.get',
  'runs.delete',
  'runs.stats',
  'runs.active',
  'schedules.list',
  'schedules.save',
  'schedules.toggle',
  'schedules.delete',
  'schedules.run',
  // Automatic routines: ready-made jobs on a schedule. Due runs start from the main process (RoutineRunner).
  'routines.catalog',
  'routines.list',
  'routines.get',
  'routines.save',
  'routines.toggle',
  'routines.delete',
  'routines.run',
  'routines.cancel',
  'routines.runs',
  'wiki.list',
  'wiki.get',
  'wiki.save',
  'wiki.delete',
  'wiki.delete_many',
  'wiki.trash',
  'wiki.restore',
  'wiki.purge',
  'wiki.analyze',
  'wiki.clear_analysis',
  'wiki.search',
  'wiki.graph',
  'analysis.start',
  'analysis.status',
  'analysis.cancel',
  'analysis.dismiss',
  'preferences.get',
  'preferences.save',
  'settings.get',
  'settings.update',
  'settings.test',
  'import.choose',
  'files.import_text',
  'clipboard.write',
  'storage.get',
  'storage.choose',
  'storage.initialize',
  'rtf.capabilities',
  'rtf.start',
  'rtf.status',
  'rtf.cancel',
  'rtf.dismiss',
  'office.capabilities',
  'office.start',
  'office.status',
  'office.cancel',
  'office.dismiss',
  'image.capabilities',
  'image.start',
  'image.status',
  'image.cancel',
  'image.dismiss',
  'text.capabilities',
  'text.start',
  'text.status',
  'text.cancel',
  'text.dismiss',
  'wiki.source_markdown',
  'wiki.source_html',
  'wiki.source_status',
  // Writing a document's changes back into the file it came from.
  'writeback.start',
  'writeback.status',
  'writeback.cancel',
  'writeback.dismiss',
  'confluence.start',
  'confluence.status',
  'confluence.cancel',
  'confluence.dismiss',
  'jira.start',
  'jira.status',
  'jira.cancel',
  'jira.dismiss',
  'sites.list',
  'sites.save',
  'sites.delete',
  'sites.match',
  'sites.check',
  'sites.check_cancel',
  'sites.login',
  'sites.login_close',
  'sites.status',
  'tasks.list',
  'tasks.save',
  'tasks.save_many',
  'tasks.delete',
  'tasks.extract',
  'tasks.snooze',
  'reminders.due',
  'reminders.ack',
  'reminders.status',
  'assistant.conversations',
  'assistant.history',
  'assistant.delete',
  'assistant.ask',
  // Stops an answer in progress; with stream, assistant.ask sends its parts on 'assistant:delta' (forwardEvent).
  'assistant.cancel',
  'briefing.get',
  'briefing.generate',
  // The admin page's own settings of the local API for AI tools; never reachable through that API.
  'api.get',
  'api.update',
  'api.rotate_token',
  'api.copy',
  'api.manual',
  'api.open_docs',
  // What to send when reporting a problem; also from the first-run screen, when the backend fails to start.
  'diagnostics.copy',
  'diagnostics.open_folder',
]);
// The LLM settings settings.json keeps as they are (the custom headers by their names alone), and the secrets it keeps
// encrypted beside them: the API Key, and the headers' values (encrypted_headers).
const LLM_FIELDS = [
  'provider',
  'api_format',
  'base_url',
  'model_name',
  'headers',
  'temperature',
  'timeout',
  'ca_bundle',
  'max_tokens',
  'context_window',
  'concurrency',
];
const LLM_SECRETS = ['api_key'];
// The Python backend's process (a BackendProcess), and the start under way: until the backend that starts has its LLM
// settings, other requests wait for it.
let backend, opening;
let window,
  stopping = false;
// A start of the app asked for while this one was closing: it runs once this one has closed.
let relaunching = false;
let savedSettings = {};
// Why settings.json could not be read; while set, the LLM settings are not saved over it.
let settingsUnavailable = '';
// settings.json has been read in this run: a restart of the backend hands it the settings held here.
let settingsRead = false;
let settingsQueue = Promise.resolve();
let storage;
let sites;
let reminders;
let schedules;
let routines;
let notices;
let apiSettings;
let apiServer;
let control;
let apiMethods;
let checkPaths;
let diagnostics;
let restarts;
let changeTimer;
// The demo being recorded, and its run until the video is made.
let demo;
let demoRunning;

// Tests shorten the schedule check; bounded so it can never poll the backend in a tight loop.
const SCHEDULE_INTERVAL = Math.min(
  60_000,
  Math.max(1000, Number(process.env.PRIVATE_AGENT_SCHEDULE_INTERVAL_MS) || 30_000),
);

function canEncrypt() {
  return (
    safeStorage.isEncryptionAvailable() &&
    (process.platform !== 'linux' || safeStorage.getSelectedStorageBackend() !== 'basic_text')
  );
}

// The OS login name, which the sidebar shows as the workspace's owner.
function osUser() {
  try {
    return os.userInfo().username;
  } catch {
    return ''; // No account entry: the form falls back to its example placeholder.
  }
}

// Requests making several LLM calls of up to 300 seconds each (the settings' limit, which the retries of a call share),
// plus a margin: prompt search = analysis + 2 AI search steps, a question = those + the answer, and a long document's
// PageIndex analysis = up to 5 rounds of section summaries + the metadata.
const RPC_TIMEOUTS = {
  // Auto detection may wait for several login screens and submissions in a row, and the LLM's help.
  'confluence.check': 900_000,
  'jira.check': 900_000,
  'wiki.search': 960_000,
  'assistant.ask': 1_260_000,
  'wiki.analyze': 1_860_000,
};

const RESTARTING = 'Python 백엔드를 다시 시작하고 있습니다. 잠시 후 다시 시도해 주세요.';

// The events the backend sends of its own accord (lines without an id) that reach the window, by the type each has
// there on 'assistant:delta': the parts of an answer as the LLM writes it, and its end, for a question asked with
// stream (assistant.ask). Any other event is dropped.
const BACKEND_EVENTS = { 'assistant.delta': 'delta', 'assistant.done': 'done' };

function forwardEvent(name, params) {
  const type = Object.hasOwn(BACKEND_EVENTS, name) ? BACKEND_EVENTS[name] : null;
  if (!type || !params || typeof params !== 'object' || Array.isArray(params)) return;
  if (window && !window.isDestroyed())
    window.webContents.send('assistant:delta', { ...params, type });
}

// A request to the backend. While it starts (again) a request waits until it has its LLM settings, so that nothing
// reaches a backend that would answer without them: the screens', an AI tool's, or a check of the schedules, routines
// and reminders.
function rpc(method, params = {}) {
  if (opening) return opening.then(() => rpc(method, params));
  if (!backend?.running) return Promise.reject(new Error(restarts?.active ? RESTARTING : ENDED));
  return backend.request(method, params);
}

function backendLaunch() {
  const frozen = app.isPackaged || process.env.PRIVATE_AGENT_USE_FROZEN === '1';
  const base = app.isPackaged ? process.resourcesPath : path.join(ROOT, 'dist');
  // PyInstaller's folder build: the executable beside its _internal folder.
  const executable = frozen
    ? path.join(
        base,
        'backend',
        'private-agent-backend',
        `private-agent-backend${process.platform === 'win32' ? '.exe' : ''}`,
      )
    : process.env.PRIVATE_AGENT_PYTHON ||
      path.join(
        ROOT,
        'backend',
        '.venv',
        process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
      );
  const args = frozen ? [] : [path.join(ROOT, 'backend', 'main.py')];
  return { executable, args };
}

async function stopBackend() {
  const child = backend;
  backend = undefined;
  await child?.stop(restarts?.active ? RESTARTING : ENDED);
}

// How long a copy of an earlier version's Wiki may go without saying how far it got before it is stopped.
const MIGRATE_QUIET = 60_000;

// Copies an earlier version's Wiki (userData/wiki.db) into the chosen folder with the backend (--migrate-storage), which
// says how far it got as it goes: it is stopped only after a minute without progress, never midway through a large Wiki
// on a slow drive. A copy stopped or failed leaves the earlier Wiki as it is and no wiki.db in the chosen folder
// (backend/agent/storage.py), so it can be tried again.
async function migrateStorage(source, destination) {
  const { executable, args } = backendLaunch();
  await new Promise((resolve, reject) => {
    let errors = '';
    let timer;
    let child;
    let settled = false;
    const fail = (message, detail) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      void diagnostics.record(
        `기존 Wiki를 복사하지 못했습니다: ${detail}${errors.trim() ? `\n${errors.trim()}` : ''}`,
      );
      reject(new Error(message));
    };
    const progressed = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        child.kill();
        fail(
          '기존 Wiki 복사가 1분 동안 진행되지 않아 멈췄습니다. 이전 데이터는 그대로 있으니 다시 시도해 주세요.',
          `${MIGRATE_QUIET / 1000}초 동안 진행되지 않아 멈춤`,
        );
      }, MIGRATE_QUIET);
    };
    try {
      child = spawn(executable, [...args, '--migrate-storage', source, destination], {
        env: { ...process.env, PYTHONUTF8: '1', PYTHONUNBUFFERED: '1', PYTHONPATH: '' },
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'pipe'],
      });
    } catch (error) {
      fail(spawnFailure(error, executable, app.isPackaged), `${error.message} (${executable})`);
      return;
    }
    child.stdout.on('data', progressed);
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', (chunk) => (errors = (errors + chunk).slice(-4000)));
    child.once('error', (error) =>
      fail(spawnFailure(error, executable, app.isPackaged), `${error.message} (${executable})`),
    );
    child.once('exit', (code) => {
      if (code === 0 && !settled) {
        settled = true;
        clearTimeout(timer);
        resolve();
      } else
        fail(
          '기존 Wiki를 복사하지 못했습니다. 폴더 권한과 디스크 공간을 확인해 주세요. 이전 데이터는 그대로 있습니다.',
          `종료 코드 ${code}`,
        );
    });
    progressed();
  });
}

// Starts the backend on the Wiki in `dataDirectory` and hands it the LLM settings. Requests wait for this (rpc).
async function startBackend(dataDirectory) {
  let open;
  const start = new Promise((resolve) => (open = resolve));
  opening = start;
  try {
    await launchBackend(dataDirectory);
  } catch (error) {
    // A backend that did not start is asked nothing else; the caller tells why.
    await stopBackend();
    throw error;
  } finally {
    if (opening === start) opening = undefined;
    open();
  }
}

async function launchBackend(dataDirectory) {
  const { executable, args } = backendLaunch();
  const child = new BackendProcess({
    executable,
    args,
    options: {
      cwd: app.isPackaged ? app.getPath('userData') : ROOT,
      env: {
        ...process.env,
        PRIVATE_AGENT_DATA_DIR: dataDirectory,
        // Every browser of the app runs in the backend (Python Playwright): one profile per Confluence and JIRA site.
        PRIVATE_AGENT_CONFLUENCE_PROFILES: path.join(
          app.getPath('userData'),
          'confluence-profiles',
        ),
        PRIVATE_AGENT_JIRA_PROFILES: path.join(app.getPath('userData'), 'jira-profiles'),
        // The backend's log of unexpected failures, beside the app's own.
        PRIVATE_AGENT_LOG_DIR: diagnostics.directory,
        PYTHONUNBUFFERED: '1',
        PYTHONUTF8: '1',
        PYTHONPATH: '',
      },
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
      detached: process.platform !== 'win32',
    },
    diagnostics,
    packaged: app.isPackaged,
    limits: RPC_TIMEOUTS,
    // Only the backend in use speaks to the window.
    onEvent: (name, params) => {
      if (backend === child) forwardEvent(name, params);
    },
    // Not stopped by the app: what the backend last wrote to its error output says why. Once the Wiki is open it is
    // started again; a first start that fails is reported by the storage screen instead.
    exited: ({ code, signal }) => {
      if (backend !== child) return ENDED;
      const again = !stopping && !!storage?.initialized;
      void diagnostics.backendExited({ code, signal });
      if (again) restarts.exited();
      return again ? RESTARTING : ENDED;
    },
  });
  backend = child;
  // A program that cannot be started (quarantined, blocked) or a backend that ends fails at once.
  await child.started({
    limit: 60_000,
    message: app.isPackaged
      ? '백엔드를 시작하지 못했습니다. 앱을 다시 실행해 주세요.'
      : '백엔드를 시작하지 못했습니다. 개발 환경에서는 npm run setup:python을 먼저 실행해 주세요.',
  });
  // settings.json is read until it has been read once in this run: a restart hands the new backend the settings held
  // here, so a file another program holds at that moment can neither leave it without them nor refuse saves.
  if (!settingsRead) {
    const settingsFile = path.join(app.getPath('userData'), 'settings.json');
    try {
      // A damaged file is set aside, and the LLM is set up again on the admin page.
      savedSettings = (await readJsonFile(settingsFile)) ?? {};
      settingsUnavailable = '';
      settingsRead = true;
    } catch {
      // Held by another program for good, or not readable: the Wiki still opens, and the saved settings stay on disk
      // for the next start (of the app, or of the backend).
      savedSettings = {};
      settingsUnavailable =
        '저장된 LLM 설정(settings.json)을 읽지 못했습니다. 저장된 설정을 덮어쓰지 않도록 이번 실행에서는 설정을 바꿀 수 없습니다. 앱을 다시 실행해 주세요.';
    }
  }
  // Header values an earlier version saved in plain text go into the encrypted form now; a file that cannot be written
  // stays as it is, and the next save moves them.
  if (canEncrypt() && (savedSettings.headers ?? []).some((header) => header?.value)) {
    const headers = headerValues(savedSettings);
    const stripped = {
      ...savedSettings,
      headers: headers.map(({ name }) => ({ name })),
      encrypted_headers: safeStorage.encryptString(JSON.stringify(headers)).toString('base64'),
    };
    try {
      await writeFileSafely(
        path.join(app.getPath('userData'), 'settings.json'),
        JSON.stringify(stripped, null, 2),
      );
      savedSettings = stripped;
    } catch {
      /* Kept as it was. */
    }
  }
  const values = { ...savedSettings };
  for (const name of LLM_SECRETS) {
    delete values[`encrypted_${name}`];
    delete values[`${name}_cleared`];
    if (savedSettings[`${name}_cleared`]) values[name] = '';
    if (savedSettings[`encrypted_${name}`]) {
      try {
        values[name] = safeStorage.decryptString(
          Buffer.from(savedSettings[`encrypted_${name}`], 'base64'),
        );
      } catch {
        values[name] = '';
      } // Settings UI lets the user re-enter a key after OS keychain changes.
    }
  }
  delete values.encrypted_headers;
  values.headers = headerValues(savedSettings);
  // One value at a time: a value that is no longer valid keeps its default instead of failing the start. Sent to this
  // backend itself: other requests wait for it.
  await child.request('settings.load', values);
}

// The custom headers with their values, as the backend takes them: the names settings.json lists, each with the value
// kept encrypted beside them (encrypted_headers) or, without secure storage, written with its name as every earlier
// version kept it. A value that cannot be decrypted (the OS key changed) is empty: the admin page takes it again.
function headerValues(saved) {
  const names = Array.isArray(saved.headers) ? saved.headers : [];
  let stored = [];
  if (typeof saved.encrypted_headers === 'string')
    try {
      stored = JSON.parse(
        safeStorage.decryptString(Buffer.from(saved.encrypted_headers, 'base64')),
      );
    } catch {
      stored = [];
    }
  const valueOf = (name) =>
    (Array.isArray(stored) ? stored : []).find(
      (item) => typeof item?.name === 'string' && item.name.toLowerCase() === name.toLowerCase(),
    )?.value ?? '';
  return names
    .filter((header) => header && typeof header.name === 'string')
    .map((header) => ({
      name: header.name,
      value: typeof header.value === 'string' && header.value ? header.value : valueOf(header.name),
    }));
}

async function updateSettings(params) {
  if (settingsUnavailable) throw new Error(settingsUnavailable);
  const values = {};
  for (const key of LLM_FIELDS) if (Object.hasOwn(params, key)) values[key] = params[key];
  for (const name of LLM_SECRETS) {
    if (params[`clear_${name}`] === true) values[name] = '';
    else if (typeof params[name] === 'string' && params[name].trim())
      values[name] = params[name].trim();
  }
  // A key the backend drops by itself, when the endpoint moves to another server, goes here too.
  const before = await rpc('settings.get');
  const result = await rpc('settings.update', values);
  // The headers' values as the backend keeps them now (those sent, those kept under their names): the file lists
  // the names and keeps the values encrypted beside them, as the API Key; without secure storage they stay with
  // their names as every earlier version kept them, so a restart never loses them.
  const secrets = await rpc('settings.secrets');
  const next = { ...savedSettings };
  for (const key of LLM_FIELDS) next[key] = result[key];
  next.headers = secrets.headers.map(({ name }) => ({ name }));
  for (const name of LLM_SECRETS) {
    const dropped = before[`has_${name}`] && !result[`has_${name}`];
    if (!Object.hasOwn(values, name) && !dropped) continue;
    delete next[`encrypted_${name}`];
    next[`${name}_cleared`] = !values[name];
    if (values[name] && canEncrypt())
      next[`encrypted_${name}`] = safeStorage.encryptString(values[name]).toString('base64');
  }
  delete next.encrypted_headers;
  if (secrets.headers.some((header) => header.value)) {
    if (canEncrypt())
      next.encrypted_headers = safeStorage
        .encryptString(JSON.stringify(secrets.headers))
        .toString('base64');
    else next.headers = secrets.headers.map(({ name, value }) => ({ name, value }));
  }
  const file = path.join(app.getPath('userData'), 'settings.json');
  await writeFileSafely(file, JSON.stringify(next, null, 2));
  savedSettings = next;
  return { ...result, ...llmFacts() };
}

// What the main process knows about the LLM settings: whether they could be read, whether keys are kept encrypted,
// and the OS login name.
function llmFacts() {
  return {
    load_error: settingsUnavailable,
    secure_storage: canEncrypt(),
    key_stored: !!savedSettings.encrypted_api_key,
    os_user: osUser(),
  };
}

// AI tools learn which custom headers the LLM requests carry, never their values.
const llmSettingsFor = (settings, access) =>
  access ? { ...settings, headers: settings.headers.map(({ name }) => ({ name })) } : settings;

// --- Site accounts --------------------------------------------------------------------------------

const JOB_ACTIVE = ['scanning', 'login', 'running', 'cancelling'];
const BUSY_MESSAGE = '진행 중인 가져오기 또는 로그인 창을 먼저 닫아 주세요.';
// Same ticket link shapes as backend/agent/jira.py, also below a context path such as /jira.
const JIRA_TICKET = [
  /\/browse\/[A-Z][A-Z0-9_]*-\d+\/?$/i,
  /\/projects\/[^/]+\/issues\/[A-Z][A-Z0-9_]*-\d+\/?$/i,
];
// Same Confluence page link shapes as backend/agent/confluence_import.py, also below /wiki or /confluence.
const CONFLUENCE_PAGE = [
  /\/display\/[^/]+\/[^/]+/, // /display/SPACEKEY/Page+Title
  /\/spaces\/[^/]+\/pages\/\d+/, // /spaces/SPACEKEY/pages/123456/Page+Title
  /\/x\/[A-Za-z0-9_-]+\/?$/, // /x/AbCdEf short links
];
// Imported sites use browsers in the Python backend, so their jobs, login windows and checks share one shape.
const SITE_KINDS = ['confluence', 'jira'];
// The site whose login window is open (or was last opened), per kind.
const loginWindows = { confluence: null, jira: null };
const loginWatches = { confluence: null, jira: null };
// The login check running per kind, as { id, done }: the screens show it again after switching back to them, and
// stopping it waits for its end.
const siteChecks = { confluence: null, jira: null };

function isConfluencePageUrl(url) {
  if (url.pathname.endsWith('/pages/viewpage.action'))
    return (
      !!url.searchParams.get('pageId') ||
      (!!url.searchParams.get('spaceKey') && !!url.searchParams.get('title'))
    );
  return CONFLUENCE_PAGE.some((pattern) => pattern.test(url.pathname));
}

// The kind of an import link, also of one typed without its scheme (an https link).
function linkKind(value) {
  const url = addressOf(String(value))?.url;
  if (!url) return null;
  if (JIRA_TICKET.some((pattern) => pattern.test(url.pathname))) return 'jira';
  return isConfluencePageUrl(url) ? 'confluence' : null;
}

function siteOf(id) {
  const site = typeof id === 'string' ? sites.get(id) : null;
  if (!site) throw new Error('사이트를 찾을 수 없습니다. 목록을 새로고침해 주세요.');
  return site;
}

const later = (task, ms) => setTimeout(() => void task(), ms);
// Status polls may be refused while the backend is busy; give up only when it keeps failing.
const WATCH_RETRIES = 30;

// The Python backend runs the logins of the imports; the result reaches the site once its job has finished, with the
// fields the LLM found on a login screen no method fitted, or its analysis of a failed login. The backend keeps the
// last jobs, so a job that ended while the next one started is still read.
function watchSiteJob(kind, jobId, siteId) {
  let failures = 0;
  const started = { kind, url: sites.get(siteId)?.url };
  const poll = async () => {
    if (stopping) return;
    let job;
    try {
      job = (await rpc(`${kind}.status`, { id: jobId })).job;
      failures = 0;
    } catch {
      if (++failures < WATCH_RETRIES) later(poll, 1000);
      return;
    }
    if (!job || job.id !== jobId) return;
    if (JOB_ACTIVE.includes(job.status)) return later(poll, 1000);
    const login = job.login;
    if ((login?.method || login?.error) && sameSite(siteId, started))
      await sites
        .record(siteId, {
          ok: !!login.method && !login.error,
          method: login.method,
          detected: !!login.detected,
          learned: login.learned,
          hosts: login.hosts,
          error: login.error || '',
          diagnosis: login.error ? job.diagnosis : null,
        })
        .catch(() => {});
  };
  later(poll, 1000);
}

// Whether the site is still at the address and of the kind it had when a login of it began: a result is about that
// address only, not one the account was moved to meanwhile.
function sameSite(id, { kind, url }) {
  const site = sites.get(id);
  return site?.kind === kind && site.url === url;
}

// The visible login window fills in the saved account; keep the method once it signs in.
function watchSiteLogin(kind, siteId) {
  clearTimeout(loginWatches[kind]);
  let recorded = false;
  let failures = 0;
  const opened = { kind, url: sites.get(siteId)?.url };
  const poll = async () => {
    if (stopping || loginWindows[kind] !== siteId) return;
    let status;
    try {
      status = await rpc(`${kind}.status`);
      failures = 0;
    } catch {
      if (++failures < WATCH_RETRIES) loginWatches[kind] = setTimeout(() => void poll(), 1000);
      return;
    }
    const result = status.login_result;
    if (result && !recorded) {
      recorded = true;
      if (sameSite(siteId, opened))
        await sites
          .record(siteId, {
            ok: true,
            method: result.method,
            detected: !!result.detected,
            hosts: result.hosts,
          })
          .catch(() => {});
    }
    if (status.login_open) loginWatches[kind] = setTimeout(() => void poll(), 1000);
  };
  loginWatches[kind] = setTimeout(() => void poll(), 1000);
}

function routineLogins(needs) {
  return (Array.isArray(needs) ? needs : []).map((need) => {
    return { kind: need.kind, url: need.url, login: sites.plan(sites.match(need.url, need.kind)) };
  });
}

// A routine that reads this PC's files imports them as the import box does: an AI tool may make, change, start or turn
// on one only with the "full" access level, as those imports do. The backend decides again on the routine as it saves
// it, told whether the caller may (mayReadFiles). The same level is needed for a routine that opens a site the user
// did not register (checkLinkAccess).
const mayReadFiles = (access) => !access || allows(access, 'files');
async function checkRoutineAccess(method, params, access) {
  if (mayReadFiles(access)) return;
  const saved = typeof params.id === 'string' ? await rpc('routines.get', { id: params.id }) : null;
  if (readsFiles(method, params, saved)) throw forbidden(access, 'files');
  for (const need of await routineNeedsOf(method, params, saved))
    checkLinkAccess(sites.match(need.url, need.kind), access);
}

// The sites a routine opens, as the backend's recipes name them (routines.needs): for one being saved, from the links
// of its form (the url fields, by the site kind of their group) over the saved routine's values the form leaves out.
async function routineNeedsOf(method, params, saved) {
  if (method !== 'routines.save') return saved ? await rpc('routines.needs', { id: saved.id }) : [];
  const kind = saved?.kind ?? params.kind;
  const recipe = (await rpc('routines.catalog')).find((item) => item.kind === kind);
  if (!recipe) return [];
  const given = params.config && typeof params.config === 'object' ? params.config : {};
  const config = { ...(saved?.config ?? {}) };
  for (const [key, value] of Object.entries(given)) if (value !== null) config[key] = value;
  const needs = [];
  for (const field of recipe.fields ?? []) {
    if (!['url', 'urls'].includes(field.type)) continue;
    const siteKind = SITE_KINDS.includes(field.group) ? field.group : recipe.sites?.[0];
    if (!siteKind) continue;
    const value = config[field.key];
    const lines = Array.isArray(value)
      ? value
      : typeof value === 'string'
        ? value.replace(/\r\n/g, '\n').split('\n')
        : [];
    for (const line of lines) {
      const url = typeof line === 'string' ? line.trim() : '';
      if (url) needs.push({ kind: siteKind, url });
    }
  }
  return needs;
}

// The backend opens a link in the browser profile of its site, with the session kept there: an AI tool may have a
// link opened only of a site the user registered (관리 → 사이트 계정), or at the "full" level, which reads this PC's files
// already. The screens open any link the user typed.
function checkLinkAccess(site, access) {
  if (!access || site || allows(access, 'files')) return;
  throw new ApiError(
    403,
    'forbidden',
    '등록된 사이트(관리 → 사이트 계정)의 JIRA·Confluence 링크만 AI 도구가 가져올 수 있습니다. 다른 사이트의 링크는 허용 범위 ‘전체’에서만 가져올 수 있으며, 가져오기 상자에서는 어떤 링크든 직접 가져올 수 있습니다.',
  );
}

async function checkSite(id) {
  const site = siteOf(id);
  if (!site.url) throw new Error('로그인을 확인하려면 사이트 주소를 입력해 주세요.');
  if (site.method === 'session')
    throw new Error(
      '자동으로 입력하지 않는 방식입니다. ‘로그인 창 열기’에서 직접 로그인해 확인해 주세요.',
    );
  if (!site.login_id || !site.password)
    throw new Error('로그인을 확인하려면 로그인 ID와 비밀번호를 저장해 주세요.');
  const plan = sites.plan(site);
  if (!plan) throw new Error('로그인 방식을 확인해 주세요.');
  // The backend checks one site of a kind at a time.
  if (siteChecks[site.kind]) throw new Error(BUSY_MESSAGE);
  const done = runSiteCheck(site, plan);
  siteChecks[site.kind] = { id: site.id, done };
  try {
    return await done;
  } finally {
    siteChecks[site.kind] = null;
  }
}

async function runSiteCheck(site, plan) {
  const checked = { kind: site.kind, url: site.url };
  // Only an answer of the backend's check tells about the login. A request that failed (another browser of the site
  // running, the backend ending or restarting, no answer in time) is the check's error, and nothing is recorded.
  const result = await rpc(`${site.kind}.check`, { url: site.url, login: plan });
  // The user stopped it: nothing was found out about the login, so nothing is recorded.
  if (result.stopped)
    return {
      ok: false,
      stopped: true,
      message: result.message,
      site: sites.public(sites.get(site.id)),
    };
  // The site was moved to another address or kind meanwhile: the result is about the one it had.
  if (!sameSite(site.id, checked))
    return {
      ok: false,
      message:
        '확인하는 동안 사이트의 주소나 종류가 바뀌어 결과를 남기지 않았습니다. 다시 확인해 주세요.',
      site: sites.public(sites.get(site.id)),
    };
  // The LLM's analysis of why the login failed, or of a page it opened again before signing in.
  const diagnosis = result.diagnosis ?? null;
  if (!result.ok) {
    const message = result.message || '로그인을 확인하지 못했습니다.';
    return {
      ok: false,
      message,
      diagnosis,
      site: await sites.record(site.id, { ok: false, error: message, diagnosis }),
    };
  }
  const saved = await sites.record(site.id, {
    ok: true,
    method: result.method,
    detected: !!result.detected,
    learned: result.learned,
    hosts: result.hosts,
  });
  return {
    ok: true,
    message:
      result.message ||
      (result.label
        ? `로그인했습니다 · ${result.label}`
        : '로그인이 필요 없거나 이미 로그인되어 있습니다.'),
    method: result.method ?? null,
    label: result.label || '',
    detected: !!result.detected,
    diagnosis,
    site: saved,
  };
}

async function siteStatus() {
  const windows = {};
  for (const kind of SITE_KINDS) {
    let status = { login_open: false, login_error: '' };
    try {
      status = await rpc(`${kind}.status`);
    } catch {
      /* The backend is restarting; report its window as closed. */
    }
    windows[kind] = {
      site_id: loginWindows[kind],
      open: !!status.login_open,
      error: status.login_error || '',
      // The site whose login check is running.
      checking: siteChecks[kind]?.id ?? null,
    };
  }
  return windows;
}

// Stop the site's running login check and wait for its end. `stopped` is false when none was running, or it ended
// before it could be stopped.
async function stopSiteCheck(id) {
  const kind = SITE_KINDS.find((key) => siteChecks[key]?.id === id);
  if (!kind) return { stopped: false };
  const { done } = siteChecks[kind];
  await rpc(`${kind}.check_cancel`);
  const result = await done.catch(() => null);
  return { stopped: !!result?.stopped };
}

async function openSiteLogin(id) {
  const site = siteOf(id);
  if (!site.url) throw new Error('로그인 창을 열려면 사이트 주소를 입력해 주세요.');
  const plan = sites.plan(site);
  await rpc(`${site.kind}.login`, { url: site.url, login: plan });
  loginWindows[site.kind] = site.id;
  if (plan) watchSiteLogin(site.kind, site.id);
  return siteStatus();
}

async function closeSiteLogin(id) {
  const kind = Object.keys(loginWindows).find((key) => loginWindows[key] === id);
  if (kind) await rpc(`${kind}.login_close`);
  return siteStatus();
}

// A site account is deleted while nothing signs in with it: not during its login check or with its login window
// open. The browser profile its logins used goes with the last account that uses it, sessions and all.
async function removeSite(id) {
  const site = siteOf(id);
  if (siteChecks[site.kind]?.id === id)
    throw new Error('이 계정의 로그인 확인이 끝난 뒤 삭제해 주세요. 확인을 중지해도 됩니다.');
  if (loginWindows[site.kind] === id) {
    const status = await rpc(`${site.kind}.status`).catch(() => null);
    if (status?.login_open) throw new Error('이 계정의 로그인 창을 닫은 뒤 삭제해 주세요.');
  }
  const result = await sites.remove(id);
  if (site.url && !sites.profileInUse(site.kind, site.url))
    void rpc(`${site.kind}.forget`, { url: site.url }).catch(() => {});
  return result;
}

function trustedSender(event) {
  const url = event.senderFrame?.url;
  return (
    event.sender === window?.webContents &&
    event.senderFrame === event.sender.mainFrame &&
    (DEV_URL ? url?.startsWith(DEV_URL + '/') : url?.startsWith('agent://bundle/'))
  );
}

// Markdown files are imported through files.import_text; .txt files are read through a hidden Notepad by the backend
// (text.start).
const MARKDOWN_EXTENSIONS = ['.md', '.markdown'];
// Word, Excel, PowerPoint and Visio files, as FORMATS in backend/agent/office_converter.py.
const OFFICE_EXTENSIONS = [
  'ppt',
  'pptx',
  'pptm',
  'xls',
  'xlsx',
  'xlsm',
  'xlsb',
  'doc',
  'docx',
  'docm',
  'vsd',
  'vsdx',
  'vsdm',
];
// Image files, as EXTENSIONS in backend/agent/images.py.
const IMAGE_EXTENSIONS = [
  'png',
  'jpg',
  'jpeg',
  'jfif',
  'gif',
  'bmp',
  'webp',
  'avif',
  'svg',
  'ico',
  'tif',
  'tiff',
  'emf',
  'wmf',
];
// Every file kind the import widget recognizes; the renderer routes each extension to its importer.
const IMPORT_EXTENSIONS = [
  'md',
  'markdown',
  'txt',
  ...OFFICE_EXTENSIONS,
  'rtf',
  ...IMAGE_EXTENSIONS,
];
// The same, by the importer that reads each one: for the local API's one import entry point and a demo's list.
const FILE_KINDS = {
  markdown: MARKDOWN_EXTENSIONS.map((extension) => extension.slice(1)),
  text: ['txt'],
  office: OFFICE_EXTENSIONS,
  rtf: ['rtf'],
  image: IMAGE_EXTENSIONS,
};

async function chooseImport(mode) {
  const directory = mode === 'directory';
  const picked = await dialog.showOpenDialog(window, {
    title: directory ? '가져올 폴더 선택' : '가져올 파일 선택',
    properties: directory ? ['openDirectory'] : ['openFile'],
    ...(directory
      ? {}
      : {
          filters: [
            { name: '가져올 수 있는 문서', extensions: IMPORT_EXTENSIONS },
            { name: 'Markdown (UTF-8)', extensions: ['md', 'markdown'] },
            { name: '텍스트 (메모장으로 읽기)', extensions: ['txt'] },
            { name: 'Office 문서 (Word·Excel·PowerPoint·Visio)', extensions: OFFICE_EXTENSIONS },
            { name: 'RTF 문서', extensions: ['rtf'] },
            { name: '이미지', extensions: IMAGE_EXTENSIONS },
          ],
        }),
  });
  return picked.canceled ? null : picked.filePaths[0] || null;
}

// A UTF-8 Markdown file becomes a Wiki document as it is, like the other imports. The backend reads it and keeps its
// path as the document's source: importing the same path again updates the document (doc_id and replace as for the
// other imports), and a long text is saved in parts. The answer comes once the file is saved: the AI analysis of its
// documents follows in the backend's background analysis (analysis.start), which the document list shows as for the
// documents chosen there, after a run already under way.
async function importText(params) {
  if (typeof params.path !== 'string' || !params.path.trim() || params.path.length > 4096)
    throw new Error('가져올 Markdown 파일 경로를 입력해 주세요.');
  if (typeof params.analyze !== 'boolean') throw new Error('AI 분석 옵션을 확인해 주세요.');
  const result = await rpc('markdown.import', {
    path: path.resolve(params.path.trim()),
    doc_id: params.doc_id,
    replace: params.replace,
  });
  if (params.analyze && ['imported', 'updated'].includes(result.status))
    try {
      await rpc('analysis.start', { ids: result.doc_ids, append: true });
      result.message = `${result.message} AI 분석은 이어서 진행합니다.`;
      // The screens follow the analysis as they follow one an AI tool started.
      changed();
    } catch (error) {
      result.analysis_error = `문서는 저장되었지만 AI 분석을 시작하지 못했습니다. ${error.message}`;
    }
  return result;
}

// --- Demo recording ------------------------------------------------------------------------------------------------

// A text file the user wrote whose bytes are no text, as Notepad shows it: the backend opens it in a hidden Notepad
// (backend/agent/notepad.py), so a file that opens only in Notepad still reads.
function readWithNotepad(file) {
  if (process.platform !== 'win32')
    return Promise.reject(
      new Error('암호화된 텍스트 파일은 메모장이 있는 Windows에서 읽을 수 있습니다.'),
    );
  const { executable, args } = backendLaunch();
  return new Promise((resolve, reject) => {
    const child = spawn(executable, [...args, '--read-text', file], {
      env: { ...process.env, PYTHONUTF8: '1', PYTHONPATH: '' },
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    const output = [];
    let errors = '';
    child.stdout.on('data', (chunk) => output.push(chunk));
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', (chunk) => (errors = (errors + chunk).slice(-4000)));
    const timer = setTimeout(() => child.kill(), 90_000);
    child.once('error', () => {
      clearTimeout(timer);
      reject(new Error('파일을 메모장으로 읽을 백엔드를 시작하지 못했습니다.'));
    });
    child.once('exit', (code) => {
      clearTimeout(timer);
      try {
        if (code !== 0) throw new Error(errors.trim().split('\n').pop() || '');
        resolve(JSON.parse(Buffer.concat(output).toString('utf8')).text);
      } catch (error) {
        reject(new Error(error.message || '메모장으로 파일을 읽지 못했습니다.'));
      }
    });
  });
}

const readText = (file, what, options = {}) =>
  demoPlan.readUserText(file, { readProtected: readWithNotepad, what, ...options });

const demoPace = () =>
  Math.min(1, Math.max(0.05, Number(process.env.PRIVATE_AGENT_DEMO_PACE) || 1));

// Shows the video in Explorer when the demo is done; tests turn that off.
function revealFile(file) {
  if (process.env.PRIVATE_AGENT_DEMO_REVEAL !== '0') shell.showItemInFolder(file);
}

// The demo's plan, with its Wiki folder emptied: before the window opens, so a mistake in the list shows alone. A list
// that does not exist yet is written to fill in, and opened. Its path must be whole: the exe starts the app in its
// build folder, which the next build deletes (record-demo.cmd always passes a whole path).
async function loadDemo(file) {
  if (!file || !path.isAbsolute(file))
    throw new Error(
      `녹화할 목록 파일을 전체 경로로 지정해 주세요. 예: record-demo.cmd D:\\시연\\demo-list.txt${file ? `\n\n지정한 경로: ${file}` : ''}`,
    );
  try {
    await fs.access(file);
  } catch {
    await demoPlan.writeTemplate(file);
    await shell.openPath(file);
    await dialog.showMessageBox({
      type: 'info',
      title: 'Private Agent 시연 녹화',
      message: '시연 녹화 목록 파일을 만들었습니다.',
      detail: `${file}\n\n가져올 파일·폴더 경로나 JIRA·Confluence 링크를 한 줄에 하나씩 적어 저장한 뒤 record-demo.cmd를 다시 실행해 주세요.`,
    });
    return null;
  }
  const plan = await demoPlan.readPlan(file, {
    linkKind,
    extensions: FILE_KINDS,
    readProtected: readWithNotepad,
  });
  // The Wiki the app normally opens is never emptied. Read as it is: a damaged file stays for the app to deal with.
  let usual = '';
  try {
    usual = JSON.parse(
      await fs.readFile(path.join(app.getPath('userData'), 'storage.json'), 'utf8'),
    ).directory;
  } catch {
    /* No Wiki chosen yet. */
  }
  await demoPlan.prepareWiki(plan.wiki, [usual]);
  return plan;
}

// After the demo: the video in Explorer, or why there is none; then the app ends, unless the list keeps it open on
// the demo's Wiki.
async function finishDemo(outcome) {
  if (stopping) return;
  const report = outcome.folder ? path.join(outcome.folder, demoPlan.FILES.report) : '';
  if (outcome.video) revealFile(outcome.video);
  else if (report) revealFile(report);
  const problem = outcome.failure || outcome.renderError;
  if (problem)
    await dialog.showMessageBox({
      type: outcome.video ? 'warning' : 'error',
      title: 'Private Agent 시연 녹화',
      message: outcome.video
        ? '시연이 중간에 멈췄습니다. 멈추기 전까지 녹화한 영상을 만들었습니다.'
        : '시연 영상을 만들지 못했습니다.',
      detail: `${problem.message}${report ? `\n\n진행 기록: ${report}` : ''}`,
    });
  if (demo.plan.quit || !window || window.isDestroyed()) app.quit();
  else {
    window.setFullScreen(false);
    window.show();
  }
}

// `record-demo.cmd render`: the video of a recording made again from its captions as they are now. No Wiki, backend
// or main window: only the window that shows the video being made.
async function renderOnly(target) {
  try {
    if (!target || !path.isAbsolute(target))
      throw new Error(
        `다시 만들 녹화 폴더나 목록 파일을 전체 경로로 지정해 주세요.${target ? `\n\n지정한 경로: ${target}` : ''}`,
      );
    const folder = await demoPlan.findRecording(target, {
      readList: async (file) =>
        demoPlan.parseList(await readText(file, '목록 파일'), file).settings,
    });
    revealFile(await renderAgain(folder, { recorder: new Recorder(), readText }));
  } catch (error) {
    if (error.name !== 'Stopped')
      dialog.showErrorBox('Private Agent 시연 영상 만들기', error.message);
  }
  app.quit();
}

// --- Local API for AI tools ----------------------------------------------------------------------------------------

// The stdio relay that AI tools run with Node.js. It is kept beside the settings, because the portable exe runs the
// app itself from a new temporary folder each time.
const bridgePath = () => path.join(app.getPath('userData'), 'mcp', 'private-agent-mcp.cjs');

async function writeBridge() {
  const source = await fs.readFile(path.join(__dirname, 'mcp-stdio.cjs'), 'utf8');
  const file = bridgePath();
  try {
    if ((await fs.readFile(file, 'utf8')) === source) return;
  } catch {
    /* Not written yet. */
  }
  await fs.mkdir(path.dirname(file), { recursive: true });
  await fs.writeFile(file, source);
}

// Requests that need the local time zone; the screens send their own, an AI tool gets this PC's.
const LOCAL_TIME = new Set(['briefing.get', 'briefing.generate', 'runs.stats', 'tasks.extract']);

// Scopes whose methods change what the screens show.
const CHANGING_SCOPES = new Set(['write', 'files', 'admin', 'destructive']);

// One method called by an AI tool: refused when the access level the user chose does not allow it, otherwise
// handled exactly like the screens' request of the same name.
async function callApi(method, params, access) {
  const entry = authorize(method, access, params);
  checkParams(params);
  await checkPaths(method, params, access);
  const values =
    LOCAL_TIME.has(method) && params.timezone_offset === undefined
      ? { ...params, timezone_offset: new Date().getTimezoneOffset() }
      : params;
  const result = await handle(method, values, { access });
  if (CHANGING_SCOPES.has(entry.scope)) changed();
  return result;
}

// The screens reload what an AI tool changed, and follow the jobs it started.
function changed() {
  clearTimeout(changeTimer);
  changeTimer = setTimeout(() => {
    if (window && !window.isDestroyed()) window.webContents.send('api:changed');
  }, 300);
}

const apiAddress = () =>
  apiServer.baseUrl(apiServer.running ? apiServer.port : apiSettings.get().port);

function apiState() {
  const settings = apiSettings.get();
  const baseUrl = apiAddress();
  return {
    enabled: settings.enabled,
    running: apiServer.running,
    port: settings.port,
    access: settings.access,
    error: settings.enabled ? apiServer.error : '',
    url: baseUrl,
    mcp_url: `${baseUrl}/mcp`,
    docs_url: `${baseUrl}/docs`,
    token_hint: `${settings.token.slice(0, 5)}…${settings.token.slice(-4)}`,
    load_error: settings.error,
    token_stored: settings.token_stored,
    secure_storage: canEncrypt(),
    levels: ACCESS_LEVELS,
    clients: apiManual.clients({ baseUrl, bridge: bridgePath() }),
    recent: apiServer.recent(),
  };
}

// The manual; `personal` (the admin page, MCP, a request with the token) adds the access level and the relay's path in
// the user's profile folder, which the manual served without the token leaves out.
const apiDocument = (personal = true) =>
  apiManual.manual({
    baseUrl: apiAddress(),
    version: app.getVersion(),
    ...(personal ? { access: apiSettings.get().access, bridge: bridgePath() } : {}),
  });

// Starts, moves or stops the server as the settings say. A failure to start stays in apiServer.error, which the
// admin page shows.
async function applyApi() {
  const { enabled, port } = apiSettings.get();
  if (!enabled || stopping) return apiServer.stop();
  if (apiServer.running && apiServer.port === port) return;
  try {
    await apiServer.start(port);
    await writeBridge().catch(() => {});
  } catch {
    /* Shown on the admin page. */
  }
}

// The admin page's requests about the local API. The token never reaches the screens: copying puts it straight on
// the clipboard.
async function apiRequest(method, params) {
  if (method === 'api.update') {
    const before = apiSettings.get().access;
    await apiSettings.update(params);
    await applyApi();
    if (apiSettings.get().access !== before) apiServer.toolsChanged();
  } else if (method === 'api.rotate_token') await apiSettings.rotate();
  else if (method === 'api.copy') {
    const { token } = apiSettings.get();
    const target = typeof params.target === 'string' ? params.target : '';
    const [client, snippet] = target.split('/');
    const text =
      target === 'token'
        ? token
        : apiManual
            .clients({ baseUrl: apiAddress(), token, bridge: bridgePath() })
            .find((item) => item.id === client)
            ?.snippets.find((item) => item.id === snippet)?.text;
    if (!text) throw new Error('복사할 항목을 찾을 수 없습니다.');
    clipboard.writeText(text);
    return { ok: true };
  } else if (method === 'api.manual') return apiDocument();
  else if (method === 'api.open_docs') {
    if (!apiServer.running) throw new Error('먼저 로컬 API 서버를 켜 주세요.');
    await shell.openExternal(`${apiAddress()}/docs`);
    return { ok: true };
  }
  return apiState();
}

async function appStatus() {
  const llm = await rpc('settings.get').catch(() => null);
  return {
    app: {
      name: 'Private Agent',
      version: app.getVersion(),
      packaged: app.isPackaged,
      platform: process.platform,
    },
    window: control.windowState(),
    screen: storage.initialized ? await control.screen() : null,
    storage: storage.info(),
    backend: { running: !!backend?.running },
    llm: llm
      ? { configured: llm.configured, provider: llm.provider, model_name: llm.active_model }
      : null,
  };
}

// The app and this PC, at the top of the diagnostics the user copies.
function diagnosticFacts() {
  const running = !!backend?.running;
  return [
    `앱 ${app.getVersion()}${app.isPackaged ? '' : ' (소스 실행)'} · ${process.platform} ${os.release()} ${process.arch} · Electron ${process.versions.electron}`,
    `백엔드 ${running ? '실행 중' : '실행 중 아님'} · Wiki 저장 위치 ${storage?.initialized ? '설정됨' : '설정 전'}${storage?.error ? ` · 저장소 안내: ${storage.error}` : ''}`,
  ];
}

function checkParams(params) {
  if (
    !params ||
    typeof params !== 'object' ||
    Array.isArray(params) ||
    JSON.stringify(params).length > 900_000
  )
    throw new Error('요청 크기나 형식을 확인해 주세요.');
}

// One request of the screens or of an AI tool through the local API, once each has been checked: the screens by
// their sender and ALLOWED, AI tools by their token and access level.
async function handle(method, params, { access } = {}) {
  let result;
  if (method === 'storage.get') result = storage.info();
  else if (method === 'diagnostics.copy') {
    clipboard.writeText(await diagnostics.report(diagnosticFacts()));
    result = { ok: true };
  } else if (method === 'diagnostics.open_folder') {
    await fs.mkdir(diagnostics.directory, { recursive: true });
    if (await shell.openPath(diagnostics.directory))
      throw new Error(
        '로그 폴더를 열지 못했습니다. 탐색기에서 앱 설정 폴더의 logs 폴더를 열어 주세요.',
      );
    result = { ok: true };
  } else if (method === 'storage.choose') {
    const picked = await dialog.showOpenDialog(window, {
      title: 'Wiki 저장 폴더 선택',
      properties: ['openDirectory', 'createDirectory'],
    });
    result = picked.canceled ? null : picked.filePaths[0] || null;
  } else if (method === 'storage.initialize') result = await storage.initialize(params.directory);
  // The window works before a Wiki location is chosen; the screen and the data need one.
  else if (method === 'app.status') result = await appStatus();
  else if (['app.window', 'app.screenshot', 'app.reload'].includes(method))
    result = await control.perform(method, params, { access });
  else if (!storage.initialized)
    throw new ApiError(503, 'unavailable', '먼저 Wiki 저장 위치를 선택해 주세요.');
  else if (method.startsWith('app.')) {
    // An unknown document or run fails here, rather than as an empty panel on the screen.
    if (method === 'app.open_document') await rpc('wiki.get', { id: params.id });
    if (method === 'app.open_run') await rpc('runs.get', { id: params.id });
    result = await control.perform(method, params, { access });
  } else if (Object.hasOwn(apiMethods, method))
    result = await apiMethods[method](params, { access });
  else if (method === 'reminders.status') result = reminders.status();
  else if (method === 'sites.list') result = sites.list();
  else if (method === 'sites.save') result = await sites.save(params);
  else if (method === 'sites.delete') result = await removeSite(params.id);
  else if (method === 'sites.match') {
    // A link's own kind, or the kind asked for: a JIRA site's or project's address is no ticket link.
    const kind = SITE_KINDS.includes(params.kind) ? params.kind : linkKind(params.url);
    const site = kind && sites.match(params.url, kind);
    result = { kind, site: kind ? sites.public(site) : null };
  } else if (method === 'sites.check') result = await checkSite(params.id);
  else if (method === 'sites.check_cancel') result = await stopSiteCheck(params.id);
  else if (method === 'sites.login') result = await openSiteLogin(params.id);
  else if (method === 'sites.login_close') result = await closeSiteLogin(params.id);
  else if (method === 'sites.status') result = await siteStatus();
  // An import of a site's page or ticket goes with the login plan of the site account that matches its link.
  else if (method === 'confluence.start') {
    const site = sites.match(params.url, 'confluence');
    checkLinkAccess(site, access);
    const plan = sites.plan(site);
    result = await rpc(method, {
      url: params.url,
      recursive: params.recursive,
      analyze: params.analyze,
      doc_id: params.doc_id,
      replace: params.replace,
      login: plan,
    });
    if (plan && result?.job?.id) watchSiteJob('confluence', result.job.id, plan.site_id);
  } else if (method === 'jira.start') {
    const site = sites.match(params.url, 'jira');
    checkLinkAccess(site, access);
    const plan = sites.plan(site);
    result = await rpc(method, {
      url: params.url,
      scope: params.scope,
      keyword: params.keyword ?? '',
      user: params.user ?? '',
      role: params.role ?? 'any',
      analyze: params.analyze,
      doc_id: params.doc_id,
      replace: params.replace,
      login: plan,
    });
    if (plan && result?.job?.id) watchSiteJob('jira', result.job.id, plan.site_id);
  } else if (method === 'writeback.start') result = await rpc(method, { id: params.id });
  else if (['routines.save', 'routines.toggle', 'routines.run'].includes(method)) {
    await checkRoutineAccess(method, params, access);
    // Whether the caller may have files read is never a value of its request: the screens may, an AI tool by its level.
    const allowed = mayReadFiles(access);
    if (method === 'routines.run') {
      // A run signs in to its sites with their accounts, which only the main process has.
      const needs = await rpc('routines.needs', { id: params.id });
      result = await rpc(method, {
        id: params.id,
        logins: routineLogins(needs),
        allow_files: allowed,
      });
      routines?.poke();
    } else result = await rpc(method, { ...params, allow_files: allowed });
  } else if (method === 'office.start') {
    result = await rpc(method, params);
    // Reading a presentation may use the user's own PowerPoint; the job says so once.
    notices.watch('office', result.id);
  } else if (method === 'settings.update') {
    const job = settingsQueue.then(() => updateSettings(params));
    settingsQueue = job.catch(() => {});
    result = llmSettingsFor(await job, access);
  } else if (method === 'settings.get')
    result = llmSettingsFor({ ...(await rpc(method)), ...llmFacts() }, access);
  else if (method === 'import.choose') result = await chooseImport(params.mode);
  else if (method === 'files.import_text') result = await importText(params);
  else if (method === 'clipboard.write') {
    if (typeof params.text !== 'string' || params.text.length > 800_000)
      throw new Error('복사할 텍스트의 크기나 형식을 확인해 주세요.');
    clipboard.writeText(params.text);
    result = { ok: true };
  } else result = await rpc(method, params);
  return result;
}

async function registerProtocol() {
  const output = path.join(ROOT, 'out');
  const types = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript',
    '.css': 'text/css',
    '.json': 'application/json',
    '.svg': 'image/svg+xml',
    '.png': 'image/png',
    '.ico': 'image/x-icon',
    '.woff2': 'font/woff2',
    '.txt': 'text/plain',
  };
  protocol.handle('agent', async (request) => {
    try {
      const url = new URL(request.url);
      if (url.hostname !== 'bundle' || request.method !== 'GET')
        return new Response('', { status: 403 });
      let pathname = decodeURIComponent(url.pathname);
      if (pathname.endsWith('/')) pathname += 'index.html';
      const file = path.resolve(output, '.' + pathname);
      if (!file.startsWith(output + path.sep)) return new Response('', { status: 403 });
      const data = await fs.readFile(file);
      const hashes =
        path.extname(file) === '.html'
          ? [...data.toString().matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)]
              .filter((m) => m[1])
              .map((m) => `'sha256-${createHash('sha256').update(m[1]).digest('base64')}'`)
              .join(' ')
          : '';
      return new Response(data, {
        headers: {
          'Content-Type': types[path.extname(file)] || 'application/octet-stream',
          'Content-Security-Policy': `default-src 'self'; script-src 'self' ${hashes}; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-src 'none'; form-action 'none'`,
          'X-Content-Type-Options': 'nosniff',
        },
      });
    } catch {
      return new Response('Not found', { status: 404 });
    }
  });
}

// How soon closing the window again closes it with changes the user has not saved.
const CLOSE_AGAIN = 10_000;

function createWindow() {
  const current = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 1060,
    minHeight: 720,
    title: 'Private Agent',
    backgroundColor: '#f8f9fc',
    show: false,
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      spellcheck: false,
    },
  });
  window = current;
  const screen = DEV_URL || 'agent://bundle/';
  // The packaged app has no menu: Electron's default one would reload the screen (Ctrl+R), open the developer tools
  // (Ctrl+Shift+I) and close the only window (Ctrl+W), quitting the app. Chromium itself still copies, pastes and undoes
  // in text fields on Windows; source runs keep the menu for development.
  if (app.isPackaged) current.removeMenu();
  current.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  current.webContents.on('will-navigate', (event) => event.preventDefault());
  current.webContents.on('will-attach-webview', (event) => event.preventDefault());
  // Closing the window while the user has changed something and not saved it (a document being written, a form)
  // keeps it once, with a word to them; closing it again soon after closes it, as the word says. The screen is asked
  // first, so the close waits for its answer; a quitting app closes at once.
  let leaving = false;
  let asking = false;
  let warned = 0;
  current.on('close', (event) => {
    if (stopping || leaving) return;
    event.preventDefault();
    if (asking) return;
    asking = true;
    void Promise.resolve(control?.unsaved()).then((unsaved) => {
      asking = false;
      if (current.isDestroyed()) return;
      if (unsaved && Date.now() - warned > CLOSE_AGAIN) {
        warned = Date.now();
        current.webContents.send(
          'import:notice',
          `저장하지 않은 내용이 있어 창을 닫지 않았습니다. 저장하거나 취소한 뒤 닫아 주세요. 저장하지 않고 닫으려면 ${CLOSE_AGAIN / 1000}초 안에 한 번 더 닫아 주세요.`,
        );
        return;
      }
      leaving = true;
      current.close();
    });
  });
  // A reload starts a new screen, which registers for the local API's commands again.
  current.webContents.on('did-start-loading', () => control?.detach());
  // Electron shows no context menu of its own: cutting, copying and pasting in a field (a link, a password, a token),
  // and copying selected text, with the mouse.
  current.webContents.on('context-menu', (_event, params) => {
    const template = editMenu(params);
    if (template) Menu.buildFromTemplate(template).popup({ window: current });
  });
  // A screen that crashed or was ended (out of memory, antivirus) leaves a blank window, or a hidden one before its first
  // page: the window shows and the screen loads again, at most three times in two minutes, and the log says why. A
  // screen that stops answering is only logged: it may answer again, with what the user was writing.
  const reloads = new Restarts({
    start: () => (current.isDestroyed() ? undefined : current.webContents.loadURL(screen)),
    stop: () => {},
    restarted: () => void diagnostics.record('앱 화면을 다시 불러왔습니다.'),
    gaveUp: () => {
      void diagnostics.record('앱 화면이 계속 종료되어 더 다시 불러오지 않았습니다.');
      dialog.showErrorBox(
        'Private Agent 화면 오류',
        '앱 화면이 계속 종료되어 다시 불러오지 않았습니다. 앱을 다시 실행해 주세요. 계속되면 앱 설정 폴더의 logs\\app.log를 알려 주세요.',
      );
    },
    failed: (error) =>
      void diagnostics.record(`앱 화면을 다시 불러오지 못했습니다: ${error.message}`),
  });
  current.webContents.on('render-process-gone', (_event, details) => {
    if (stopping || details.reason === 'clean-exit' || current.isDestroyed()) return;
    void diagnostics.record(
      `앱 화면 프로세스가 종료되었습니다 (${details.reason}, 종료 코드 ${details.exitCode}).`,
    );
    // Hidden only when its first page never showed; a window the user minimized stays so.
    if (!current.isVisible() && !current.isMinimized()) current.show();
    reloads.exited();
  });
  current.on('unresponsive', () => void diagnostics.record('앱 화면이 응답하지 않습니다.'));
  current.on('responsive', () => void diagnostics.record('앱 화면이 다시 응답합니다.'));
  current.once('ready-to-show', () => current.show());
  current.on('closed', () => {
    reloads.end();
    if (window === current) window = null;
  });
  current.loadURL(screen);
}

// A start of the app asked for while this one closes, which would otherwise end unseen: it runs once this one has
// closed (relaunchOptions), once however often it was asked.
function relaunchAfterQuit(started) {
  if (relaunching) return;
  relaunching = true;
  app.relaunch(relaunchOptions(started, { execPath: process.execPath, packaged: app.isPackaged }));
  void diagnostics?.record('앱이 닫히는 동안 다시 실행되어, 닫힌 뒤 다시 시작합니다.');
}

// Making a recording's video again needs neither the Wiki nor the backend, and may run beside the open app: it keeps
// Chromium's own files apart from the app's. Any other start that finds the app open hands it the program it was started
// as (relaunchAfterQuit).
const hasLock =
  LAUNCH?.kind === 'render'
    ? (app.setPath('userData', path.join(app.getPath('temp'), 'private-agent-render')), false)
    : app.requestSingleInstanceLock({ execPath: process.execPath, args: process.argv.slice(1) });
if (LAUNCH?.kind === 'render') app.whenReady().then(() => renderOnly(LAUNCH.path));
else if (!hasLock) app.quit();
else {
  app.on('second-instance', (_event, argv, _directory, started) => {
    // A demo runs in an app of its own, on its own Wiki.
    if (demoLaunch(argv)?.kind === 'record')
      void dialog.showMessageBox({
        type: 'info',
        title: 'Private Agent 시연 녹화',
        message: demo
          ? '시연을 녹화하고 있습니다. 끝난 뒤 다시 실행해 주세요.'
          : '시연 녹화는 Private Agent를 닫은 뒤 시작할 수 있습니다.',
        detail: demo ? '' : '열려 있는 Private Agent를 닫고 record-demo.cmd를 다시 실행해 주세요.',
      });
    // This app is closing, which may take up to 25 seconds while its backend stops.
    else if (stopping) relaunchAfterQuit(started);
    else if (window) {
      if (window.isMinimized()) window.restore();
      // Also a window not shown yet, as when its screen ended before the first page.
      window.show();
      window.focus();
    }
  });
  app
    .whenReady()
    .then(async () => {
      if (process.platform === 'win32') app.setAppUserModelId('com.privateagent.desktop');
      await fs.mkdir(app.getPath('userData'), { recursive: true });
      let plan = null;
      if (LAUNCH?.kind === 'record') {
        try {
          plan = await loadDemo(LAUNCH.path);
        } catch (error) {
          dialog.showErrorBox('Private Agent 시연 녹화', error.message);
        }
        if (!plan) {
          app.quit();
          return;
        }
      }
      diagnostics = new Diagnostics({ directory: path.join(app.getPath('userData'), 'logs') });
      restarts = new Restarts({
        start: () => startBackend(storage.directory),
        stop: stopBackend,
        restarted: () => {
          void diagnostics.record('백엔드를 다시 시작했습니다.');
          // The notice toast of the screens.
          if (window && !window.isDestroyed())
            window.webContents.send(
              'import:notice',
              'Python 백엔드가 예기치 않게 종료되어 다시 시작했습니다. 진행 중이던 가져오기·AI 분석·질문은 다시 실행해 주세요.',
            );
        },
        // With why its last start failed, if it did: a program antivirus quarantined, say.
        gaveUp: (error) => {
          void diagnostics.record('백엔드가 계속 종료되어 더 다시 시작하지 않았습니다.');
          dialog.showErrorBox(
            '백엔드 종료',
            `Python 백엔드가 계속 종료되어 다시 시작하지 않았습니다. ${error ? `마지막 시작 오류: ${error.message}` : '앱을 다시 실행해 주세요.'} 관리 → 저장 위치·정보의 ‘진단 정보 복사’로 원인을 알려 주실 수 있습니다.`,
          );
        },
        failed: (error) =>
          void diagnostics.record(`백엔드를 다시 시작하지 못했습니다: ${error.message}`),
      });
      session.defaultSession.setPermissionRequestHandler((_wc, _permission, callback) =>
        callback(false),
      );
      session.defaultSession.setPermissionCheckHandler(() => false);
      await registerProtocol();
      storage = new StorageManager({
        configDirectory: app.getPath('userData'),
        suggestedPath: plan ? plan.wiki : path.join(app.getPath('documents'), 'Private Agent'),
        start: startBackend,
        stop: stopBackend,
        migrate: migrateStorage,
        // A demo opens its own Wiki and leaves the one the app opens next time as it is.
        remember: !plan,
      });
      // Accounts of the sites for background sign-in, each with its own login method;
      // passwords are OS-encrypted. Replaces the single confluence-/jira-login.json accounts.
      const userData = app.getPath('userData');
      sites = new SiteStore({
        file: path.join(userData, 'sites.json'),
        legacyFiles: {
          confluence: path.join(userData, 'confluence-login.json'),
          jira: path.join(userData, 'jira-login.json'),
        },
        encrypt: (value) => safeStorage.encryptString(value),
        decrypt: (value) => safeStorage.decryptString(value),
        canEncrypt,
        methods: LOGIN_METHODS,
      });
      // The local API and MCP server for AI tools on this PC, off until the user turns it on.
      apiSettings = new ApiSettings({
        file: path.join(userData, 'api.json'),
        encrypt: (value) => safeStorage.encryptString(value),
        decrypt: (value) => safeStorage.decryptString(value),
        canEncrypt,
      });
      control = new AppControl({ window: () => window, Notification });
      apiMethods = createApiMethods({ handle, linkKind, extensions: FILE_KINDS });
      checkPaths = createPathCheck({ handle, wikiDirectory: () => storage.directory });
      const mcp = new McpServer({
        call: callApi,
        manual: (group) => apiManual.markdown(apiDocument(), group),
        version: app.getVersion(),
      });
      apiServer = new ApiServer({
        call: callApi,
        mcp,
        settings: () => apiSettings.get(),
        manual: apiDocument,
        version: app.getVersion(),
      });
      // The saved settings are read and the backend starts while the window opens on the screens' "preparing" page:
      // the backend's first start can take seconds, more while antivirus scans a new build. Requests wait for both.
      const started = Promise.all([storage.load(), sites.load(), apiSettings.load()]);
      ipcMain.handle('agent:request', async (event, method, params = {}) => {
        try {
          if (!trustedSender(event) || !ALLOWED.has(method))
            throw new Error('허용되지 않은 요청입니다.');
          checkParams(params);
          await started;
          const result = method.startsWith('api.')
            ? await apiRequest(method, params)
            : await handle(method, params);
          demo?.observe(method, result, null);
          return { ok: true, result };
        } catch (error) {
          demo?.observe(method, null, error);
          return { ok: false, error: error.message || '요청 처리에 실패했습니다.' };
        }
      });
      // The screen answers the local API's commands once it has registered for them after each load.
      ipcMain.on('control:ready', (event) => {
        if (trustedSender(event)) control.attach();
      });
      ipcMain.on('control:result', (event, reply) => {
        if (trustedSender(event)) control.settle(reply);
      });
      createWindow();
      await started;
      if (stopping) return; // The window was closed while the app started.
      // The checks of reminders, schedules and routines ask the backend only while it runs with its settings:
      // not while it starts again, nor once it has ended.
      const ready = () => storage.initialized && !!backend?.running && !opening && !stopping;
      const reveal = (channel, payload) => {
        if (!window) return;
        if (window.isMinimized()) window.restore();
        window.show();
        window.focus();
        if (channel) window.webContents.send(channel, payload);
      };
      // A demo's video shows only what the demo does: no reminder, scheduled run, automatic routine or AI tool.
      reminders = new ReminderScheduler({
        rpc,
        Notification,
        ready,
        onClick: () => reveal('assistant:open'),
      });
      if (!plan) reminders.start();
      schedules = new PromptScheduler({
        rpc,
        Notification,
        ready,
        focused: () => !!window?.isFocused(),
        send: (event) => {
          if (window && !window.isDestroyed()) window.webContents.send('schedule:event', event);
        },
        onClick: (runId) => reveal('schedule:event', { type: 'open', run_id: runId }),
        interval: SCHEDULE_INTERVAL,
      });
      if (!plan) schedules.start();
      routines = new RoutineRunner({
        rpc,
        logins: routineLogins,
        record: (siteId, login) => sites.record(siteId, login),
        ready,
        focused: () => !!window?.isFocused(),
        send: (event) => {
          if (window && !window.isDestroyed()) window.webContents.send('routine:event', event);
        },
        changed,
        Notification,
        onClick: (routineId) => reveal('routine:event', { type: 'open', routine_id: routineId }),
        interval: SCHEDULE_INTERVAL,
      });
      if (!plan) routines.start();
      notices = new ImportNotices({
        rpc,
        Notification,
        focused: () => !!window?.isFocused(),
        send: (text) => {
          if (window && !window.isDestroyed()) window.webContents.send('import:notice', text);
        },
        onClick: () => reveal(),
      });
      if (plan) {
        demo = new DemoRun({
          plan,
          window: () => window,
          control,
          rpc,
          storage,
          recorder: new Recorder(),
          pace: demoPace(),
          log: (text) => void diagnostics.record(text),
        });
        demoRunning = demo
          .run()
          .catch((error) => ({ failure: error }))
          .then(finishDemo);
      } else await applyApi();
      app.on('activate', () => {
        if (!window) createWindow();
      });
    })
    .catch(async (error) => {
      await diagnostics?.record(`앱을 시작하지 못했습니다: ${error.stack || error.message}`);
      dialog.showErrorBox('Private Agent 시작 오류', error.message);
      app.quit();
    });
}
// Making a video again ends by itself once the video is written; its window closes before that.
app.on('window-all-closed', () => {
  if (LAUNCH?.kind !== 'render') app.quit();
});
app.on('before-quit', (event) => {
  if (stopping) return;
  event.preventDefault();
  stopping = true;
  restarts?.end();
  reminders?.stop();
  schedules?.stop();
  routines?.stop();
  notices?.stop();
  control?.stop();
  clearTimeout(changeTimer);
  // A demo still recording keeps what it recorded, with its captions, without making the video.
  demo?.abort({ quitting: true });
  void (async () => {
    try {
      await Promise.race([demoRunning, new Promise((resolve) => setTimeout(resolve, 30_000))]);
      await Promise.allSettled([
        apiServer?.stop(),
        // The backend stops its imports and browsers before its process is ended: a stopped Office import closes
        // its file and quits the Office app it started, which Windows would otherwise leave running unseen. The
        // backend waits up to 15 seconds for them.
        backend &&
          Promise.race([
            rpc('backend.shutdown').catch(() => {}),
            new Promise((resolve) => setTimeout(resolve, 20_000)),
          ]),
      ]);
    } finally {
      await stopBackend();
      app.quit();
    }
  })();
});
