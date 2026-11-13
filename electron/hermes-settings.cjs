const fs = require('node:fs/promises');
const path = require('node:path');
const { readJsonFile, writeFileSafely } = require('./files.cjs');

// The chat's Hermes settings (hermes.json): its working folder, computer use, the MCP servers, the service keys
// Hermes reads from its environment, and the messenger gateway (its switch, the bot tokens of Telegram, Discord and
// Slack, and the accounts each bot answers). Like the LLM API Key, every value is kept with the OS secure storage
// (encrypted); without it, values last until the app closes. The screen only ever learns whether a value is set. The
// backend gets all of it at its start and after each change (agent(), sent as agent.configure), keeps it in memory
// and hands the keys to the Hermes workers' environment; the LLM connection is never among them.
//
// The bot tokens go to the gateway's process alone (backend/agent/hermes_gateway.py), and only while the gateway is
// on: the chat's own worker has no use for them.
//
// What the agent may run without asking and what it may not touch are kept here too, with the app's settings on this
// PC and not in the Wiki folder (which may be copied, synced or restored from elsewhere): the commands the user chose
// to always allow (allowed: each to the letter, added only by an answer to the agent's own question — allow — and
// taken back on the settings screen), the folders the user protected (folders), whether the places credentials are
// kept are protected (credentials, on unless turned off) and the command patterns the user refuses (blocked). So are
// how long and how far a chat turn may go (turn_limits).

const VERSION = 1;
// The keys the settings screen lists, by what each is for; any other name the user adds goes the same way (a skill's
// own key, another search provider).
const KNOWN_KEYS = [
  { name: 'EXA_API_KEY', group: 'web', label: 'Exa 웹 검색·페이지 읽기' },
  { name: 'FIRECRAWL_API_KEY', group: 'web', label: 'Firecrawl 웹 검색·페이지 읽기' },
  { name: 'TAVILY_API_KEY', group: 'web', label: 'Tavily 웹 검색·페이지 읽기' },
  { name: 'PARALLEL_API_KEY', group: 'web', label: 'Parallel 웹 검색·페이지 읽기' },
  { name: 'BRAVE_SEARCH_API_KEY', group: 'web', label: 'Brave 웹 검색' },
  // The image tool's key. No voice key is listed: the chat turns on no text-to-speech tool that would read one.
  { name: 'FAL_KEY', group: 'media', label: 'FAL 이미지 생성' },
  { name: 'TELEGRAM_BOT_TOKEN', group: 'gateway', label: '텔레그램 봇 토큰' },
  { name: 'DISCORD_BOT_TOKEN', group: 'gateway', label: '디스코드 봇 토큰' },
  { name: 'SLACK_BOT_TOKEN', group: 'gateway', label: '슬랙 봇 토큰 (xoxb-)' },
  { name: 'SLACK_APP_TOKEN', group: 'gateway', label: '슬랙 앱 토큰 (xapp-, Socket Mode)' },
];
// Which service searches the web for the agent, and which reads a page for it: the first whose key the user stored
// (Hermes names its web services so). Hermes takes the service from its settings, not from the keys it finds, so a key
// stored here does nothing until its service is named. Without a key the search is DuckDuckGo's (ddgs), which needs
// none, and no service reads pages: ddgs only searches, and the free tiers Hermes could fall back on are services
// the user never chose.
const SEARCH_SERVICES = [
  ['TAVILY_API_KEY', 'tavily'],
  ['EXA_API_KEY', 'exa'],
  ['PARALLEL_API_KEY', 'parallel'],
  ['FIRECRAWL_API_KEY', 'firecrawl'],
  ['BRAVE_SEARCH_API_KEY', 'brave-free'],
];
const EXTRACT_SERVICES = [
  ['FIRECRAWL_API_KEY', 'firecrawl'],
  ['TAVILY_API_KEY', 'tavily'],
  ['EXA_API_KEY', 'exa'],
  ['PARALLEL_API_KEY', 'parallel'],
];
const serviceOf = (services, env) => services.find(([key]) => env[key])?.[1] ?? 'ddgs';
// The bot tokens of the messenger gateway, which only the gateway's process is given.
const GATEWAY_KEYS = new Set(
  KNOWN_KEYS.filter((key) => key.group === 'gateway').map((key) => key.name),
);
// The accounts each messaging platform answers; a platform without any stays off even with its token.
const PLATFORMS = ['telegram', 'discord', 'slack'];
// Names that would change who a bot answers (everyone, other bots, whole chats or roles) or make the gateway listen
// on a port (an API server, webhooks). The accounts listed per messenger are the only way in, so no key may carry
// such a name; the gateway's worker drops them from its environment as well (runtime/hermes/worker.py,
// GATEWAY_OPENINGS).
const GATEWAY_STEERING =
  /_ALLOW_ALL_USERS$|_ALLOW_BOTS$|_ALLOWED_(USERS|CHATS|CHANNELS|ROLES|GROUPS)$|_HOME_CHANNEL|^GATEWAY_|^API_SERVER_|^WEBHOOK_|^TELEGRAM_WEBHOOK_/;
const NAME = /^[A-Z][A-Z0-9_]{1,63}$/;
// Names that steer the worker's runtime, or would choose a model provider instead of the app's LLM connection.
const RESERVED = new Set([
  'PATH',
  'PATHEXT',
  'HOME',
  'USERPROFILE',
  'APPDATA',
  'LOCALAPPDATA',
  'TEMP',
  'TMP',
  'TMPDIR',
  'SYSTEMROOT',
  'WINDIR',
  'COMSPEC',
  'SYSTEMDRIVE',
  'LANG',
  'SSL_CERT_FILE',
  'SSL_CERT_DIR',
  'REQUESTS_CA_BUNDLE',
  'CURL_CA_BUNDLE',
]);
// Names that steer the Python runtime, the Node/Electron host, or the dynamic loader are refused by prefix (so the
// dangerous full names are never spelled out in the source either).
const RESERVED_PREFIX = /^(HERMES_|PYTHON|PRIVATE_AGENT_|XDG_|NODE_|ELECTRON_|LD_|DYLD_)/;
const PROVIDER_PREFIX =
  /^(OPENAI|ANTHROPIC|OPENROUTER|NOUS|GEMINI|AZURE_OPENAI|AZURE_AI|MISTRAL|DEEPSEEK|GROQ|TOGETHER|FIREWORKS|CEREBRAS|COHERE|MOONSHOT|KIMI|ZAI|GLM|MINIMAX|DASHSCOPE|QWEN|OLLAMA|LMSTUDIO|VLLM|LLAMA|HF|HUGGINGFACE|AWS|BEDROCK|VERTEX|NVIDIA|SAMBANOVA|NEBIUS|COPILOT|GITHUB_COPILOT|CODEX)_/;
const MAX_KEYS = 64;
const MAX_VALUE = 8192;
const MAX_USERS = 2000;
const MAX_MCP = 65536;
const MAX_PATH = 1024;
const MAX_ALLOWED = 200;
const MAX_COMMAND = 1000;
const MAX_FOLDERS = 50;
const MAX_BLOCKED = 50;
const MAX_PATTERN = 200;
// A chat turn's limits, each [default, lowest, highest], and why a value sent is refused. The defaults are the limits
// from before they could be set. A model request's seconds (the LLM connection's own response limit still applies
// when it is shorter), the agent's iterations, the whole turn's minutes, not counting the time the user spends
// answering (0: automatic, four times the connection's response limit, 2 to 15 minutes), and the minutes a command's
// approval or a question waits for the user's answer. The backend takes them the same way (backend/agent/hermes.py,
// TURN_LIMITS); the messenger gateway's conversations go by them too.
const TURN_LIMITS = {
  request_seconds: [120, 30, 300],
  iterations: [24, 4, 200],
  turn_minutes: [0, 2, 60],
  answer_minutes: [5, 1, 30],
};
const TURN_LIMIT_ERRORS = {
  request_seconds: '모델 요청 하나의 제한 시간은 30~300초로 정해 주세요.',
  iterations: '반복 횟수는 4~200번으로 정해 주세요.',
  turn_minutes: '요청 전체의 제한 시간은 2~60분으로 정하거나, 자동(0)으로 두세요.',
  answer_minutes: '확인과 질문을 기다리는 시간은 1~30분으로 정해 주세요.',
};
const DEFAULT_TURN_LIMITS = Object.fromEntries(
  Object.entries(TURN_LIMITS).map(([name, [fallback]]) => [name, fallback]),
);
// Reject control characters in the short single-line fields (names, values, paths, account IDs).
// eslint-disable-next-line no-control-regex
const CONTROL = /[\x00-\x1f\x7f]/;
const UNREADABLE =
  'Hermes 설정 파일(hermes.json)을 읽지 못했습니다. 저장된 설정을 덮어쓰지 않도록 이번 실행에서는 바꿀 수 없습니다. 앱을 다시 실행해 주세요.';

class HermesSettingsError extends Error {}

/** Why an environment variable name cannot hold a key for Hermes, or '' when it can. */
function refusedName(name) {
  if (typeof name !== 'string' || !NAME.test(name))
    return '이름은 영문 대문자, 숫자, 밑줄(_)로 2~64자여야 합니다. 예) EXA_API_KEY';
  if (RESERVED.has(name) || RESERVED_PREFIX.test(name))
    return `${name}은(는) 앱이 정하는 실행 환경 값이라 넣을 수 없습니다.`;
  if (GATEWAY_STEERING.test(name))
    return `${name}은(는) 메신저 봇이 누구에게 답할지, 어떻게 연결할지를 바꾸는 값이라 넣을 수 없습니다. 답할 계정은 메신저 게이트웨이의 허용 계정에 적어 주세요.`;
  if (PROVIDER_PREFIX.test(name))
    return `${name}은(는) 모델 제공자의 키입니다. 모델 호출은 관리 → LLM 연결로만 하므로 넣을 수 없습니다.`;
  return '';
}

/** Comma- or space-separated account IDs, as Hermes' *_ALLOWED_USERS take them: '' or "1234, 5678". */
function users(value) {
  if (typeof value !== 'string' || value.length > MAX_USERS || CONTROL.test(value))
    throw new HermesSettingsError('허용할 계정 ID를 쉼표로 나눠 적어 주세요.');
  const ids = value
    .split(/[\s,]+/)
    .map((id) => id.trim())
    .filter(Boolean);
  if (ids.some((id) => !/^[A-Za-z0-9@._:-]{1,100}$/.test(id)))
    throw new HermesSettingsError('계정 ID에는 영문, 숫자와 @ . _ : - 만 쓸 수 있습니다.');
  return [...new Set(ids)].join(',');
}

/** A command kept to the letter: one line of text. */
function command(value) {
  if (typeof value !== 'string' || CONTROL.test(value)) return '';
  const text = value.trim();
  return text && text.length <= MAX_COMMAND ? text : '';
}

/** The commands the user always allows, as they are kept: [{command, added_at}], each command once. */
function allowedList(value) {
  const kept = [];
  for (const item of Array.isArray(value) ? value : []) {
    const text = command(item?.command);
    if (!text || kept.some((entry) => entry.command === text)) continue;
    kept.push({ command: text, added_at: typeof item.added_at === 'string' ? item.added_at : '' });
  }
  return kept.slice(0, MAX_ALLOWED);
}

/** Whether `inner` is `outer` or lies inside it. */
function within(inner, outer) {
  const relative = path.relative(outer, inner);
  return !relative || (!relative.startsWith('..') && !path.isAbsolute(relative));
}

/** The folders the user protects: absolute paths, each once. A stored list is read leniently (strict: false). */
function folderList(value, strict = true) {
  if (!Array.isArray(value)) {
    if (strict) throw new HermesSettingsError('보호할 폴더 목록을 확인해 주세요.');
    return [];
  }
  const kept = [];
  for (const item of value) {
    const folder = typeof item === 'string' ? item.trim() : '';
    const valid =
      folder &&
      folder.length <= MAX_PATH &&
      !CONTROL.test(folder) &&
      path.isAbsolute(folder) &&
      path.dirname(path.resolve(folder)) !== path.resolve(folder);
    if (!valid) {
      if (strict)
        throw new HermesSettingsError(
          '보호할 폴더는 전체 경로로 적어 주세요. 드라이브의 최상위 폴더는 넣을 수 없습니다.',
        );
      continue;
    }
    if (!kept.includes(folder)) kept.push(folder);
  }
  if (kept.length > MAX_FOLDERS) {
    if (strict)
      throw new HermesSettingsError(`보호할 폴더는 ${MAX_FOLDERS}개까지 넣을 수 있습니다.`);
    return kept.slice(0, MAX_FOLDERS);
  }
  return kept;
}

/** The command patterns the user refuses outright (Hermes' approvals.deny: * and ? stand for any text). */
function blockedList(value, strict = true) {
  if (!Array.isArray(value)) {
    if (strict) throw new HermesSettingsError('실행하지 않을 명령 목록을 확인해 주세요.');
    return [];
  }
  const kept = [];
  for (const item of value) {
    const pattern = typeof item === 'string' ? item.trim() : '';
    if (!pattern) continue;
    // A pattern of wildcards alone would refuse every command.
    const valid =
      pattern.length <= MAX_PATTERN && !CONTROL.test(pattern) && /[^*?\s]{2}/.test(pattern);
    if (!valid) {
      if (strict)
        throw new HermesSettingsError(
          `실행하지 않을 명령은 한 줄에 하나씩, ${MAX_PATTERN}자 이내로 적어 주세요. *만으로 된 줄은 넣을 수 없습니다.`,
        );
      continue;
    }
    if (!kept.includes(pattern)) kept.push(pattern);
  }
  if (kept.length > MAX_BLOCKED) {
    if (strict)
      throw new HermesSettingsError(`실행하지 않을 명령은 ${MAX_BLOCKED}개까지 넣을 수 있습니다.`);
    return kept.slice(0, MAX_BLOCKED);
  }
  return kept;
}

/** A chat turn's limits: `value`'s fields over `base`, each a whole number within its bounds (TURN_LIMITS;
 *  turn_minutes may be 0, automatic). A value sent out of bounds is refused; one stored is read leniently (strict:
 *  false), its default in its place. */
function turnLimits(value, base = DEFAULT_TURN_LIMITS, strict = true) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    if (strict) throw new HermesSettingsError('요청 한도를 확인해 주세요.');
    return { ...base };
  }
  const kept = { ...base };
  for (const [name, [fallback, lowest, highest]] of Object.entries(TURN_LIMITS)) {
    if (!Object.hasOwn(value, name)) continue;
    const number = value[name];
    const valid =
      Number.isInteger(number) &&
      ((number >= lowest && number <= highest) || (name === 'turn_minutes' && number === 0));
    if (valid) kept[name] = number;
    else if (strict) throw new HermesSettingsError(TURN_LIMIT_ERRORS[name]);
    else kept[name] = fallback;
  }
  return kept;
}

/** The MCP servers as Hermes' config.yaml mcp_servers takes them, from the JSON the user wrote: {} when empty. */
function mcpServers(text) {
  if (typeof text !== 'string' || text.length > MAX_MCP)
    throw new HermesSettingsError('MCP 서버 설정은 64KB 이하의 JSON이어야 합니다.');
  if (!text.trim()) return {};
  let value;
  try {
    value = JSON.parse(text);
  } catch {
    throw new HermesSettingsError(
      'MCP 서버 설정을 JSON으로 읽지 못했습니다. 예) {"files": {"command": "npx", "args": ["-y", "@example/mcp-server"]}}',
    );
  }
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new HermesSettingsError('MCP 서버 설정은 이름별 객체여야 합니다.');
  for (const [name, server] of Object.entries(value)) {
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(name))
      throw new HermesSettingsError(
        `MCP 서버 이름 '${name}'은(는) 영문, 숫자, _, - 로 64자까지입니다.`,
      );
    if (!server || typeof server !== 'object' || Array.isArray(server))
      throw new HermesSettingsError(`MCP 서버 '${name}'의 설정은 객체여야 합니다.`);
    const local = typeof server.command === 'string' && server.command.trim();
    const remote = typeof server.url === 'string' && /^https?:\/\//i.test(server.url);
    if (!local && !remote)
      throw new HermesSettingsError(
        `MCP 서버 '${name}'에 실행할 command나 연결할 url(http/https)을 적어 주세요.`,
      );
    if (
      Object.hasOwn(server, 'args') &&
      !(Array.isArray(server.args) && server.args.every((arg) => typeof arg === 'string'))
    )
      throw new HermesSettingsError(`MCP 서버 '${name}'의 args는 문자열 목록이어야 합니다.`);
    for (const field of ['env', 'headers'])
      if (
        Object.hasOwn(server, field) &&
        !(
          server[field] &&
          typeof server[field] === 'object' &&
          !Array.isArray(server[field]) &&
          Object.values(server[field]).every((item) => typeof item === 'string')
        )
      )
        throw new HermesSettingsError(
          `MCP 서버 '${name}'의 ${field}는 문자열 값의 객체여야 합니다.`,
        );
  }
  return value;
}

class HermesSettings {
  // `zones`: the app's own folders the agent may not touch (its settings folder). `reachable`: the folders the agent
  // must be able to use beside its work folder (the Wiki folder, which holds its home), which the user therefore
  // cannot protect.
  constructor({
    file,
    encrypt,
    decrypt,
    canEncrypt,
    stat = fs.stat,
    zones = /** @type {() => string[]} */ (() => []),
    reachable = /** @type {() => string[]} */ (() => []),
    now = () => new Date().toISOString(),
  }) {
    this.file = file;
    this.encrypt = encrypt;
    this.decrypt = decrypt;
    this.canEncrypt = canEncrypt;
    this.stat = stat;
    this.zones = zones;
    this.reachable = reachable;
    this.now = now;
    this.values = {
      workspace: '',
      gateway: false,
      computer_use: true,
      users: {},
      allowed: /** @type {{command: string, added_at: string}[]} */ ([]),
      credentials: true,
      folders: /** @type {string[]} */ ([]),
      blocked: /** @type {string[]} */ ([]),
      turn_limits: { ...DEFAULT_TURN_LIMITS },
    };
    // {env: {NAME: value}, mcp: '<the JSON the user wrote>'}: never written in plain text.
    this.secrets = { env: {}, mcp: '' };
    this.stored = false;
    this.queue = Promise.resolve();
    // Why hermes.json could not be read; while set, nothing is saved over it.
    this.unavailable = '';
  }

  async load() {
    let record;
    try {
      record = (await readJsonFile(this.file)) ?? {};
    } catch {
      this.unavailable = UNREADABLE;
      return this.get();
    }
    const saved = record.users && typeof record.users === 'object' ? record.users : {};
    this.values = {
      workspace: typeof record.workspace === 'string' ? record.workspace.slice(0, MAX_PATH) : '',
      gateway: record.gateway === true,
      computer_use: record.computer_use !== false,
      users: Object.fromEntries(
        PLATFORMS.map((platform) => {
          try {
            return [platform, users(saved[platform] ?? '')];
          } catch {
            return [platform, ''];
          }
        }),
      ),
      allowed: allowedList(record.allowed),
      credentials: record.credentials !== false,
      folders: folderList(record.folders, false),
      blocked: blockedList(record.blocked, false),
      turn_limits: turnLimits(record.turn_limits ?? {}, DEFAULT_TURN_LIMITS, false),
    };
    if (typeof record.encrypted === 'string' && record.encrypted)
      try {
        const secrets = JSON.parse(this.decrypt(Buffer.from(record.encrypted, 'base64')));
        const env = secrets && typeof secrets.env === 'object' && secrets.env ? secrets.env : {};
        this.secrets = {
          env: Object.fromEntries(
            Object.entries(env).filter(
              ([name, value]) => !refusedName(name) && typeof value === 'string' && value,
            ),
          ),
          mcp: typeof secrets.mcp === 'string' ? secrets.mcp : '',
        };
        this.stored = true;
      } catch {
        /* The OS key changed (another account): the keys are entered again on the settings screen. */
      }
    return this.get();
  }

  /** What the screen shows: the settings and which keys are set, never a value. */
  get() {
    const names = new Set([...KNOWN_KEYS.map((key) => key.name), ...Object.keys(this.secrets.env)]);
    let servers;
    try {
      servers = Object.keys(mcpServers(this.secrets.mcp));
    } catch {
      servers = [];
    }
    return {
      workspace: this.values.workspace,
      gateway: this.values.gateway,
      computer_use: this.values.computer_use,
      users: { ...this.values.users },
      keys: [...names].map((name) => {
        const known = KNOWN_KEYS.find((key) => key.name === name);
        return {
          name,
          label: known?.label ?? '',
          group: known?.group ?? 'other',
          has_value: !!this.secrets.env[name],
        };
      }),
      mcp_servers: servers,
      has_mcp: !!this.secrets.mcp.trim(),
      allowed: this.values.allowed.map((entry) => ({ ...entry })),
      protected: { credentials: this.values.credentials, folders: [...this.values.folders] },
      blocked: [...this.values.blocked],
      turn_limits: { ...this.values.turn_limits },
      secure_storage: this.canEncrypt(),
      stored: this.stored,
      error: this.unavailable,
    };
  }

  /** What the backend hands to Hermes: the values themselves. Only the main process ever calls this. */
  backend() {
    let servers;
    try {
      servers = mcpServers(this.secrets.mcp);
    } catch {
      servers = {};
    }
    return {
      workspace: this.values.workspace,
      gateway: this.values.gateway,
      computer_use: this.values.computer_use,
      users: { ...this.values.users },
      env: { ...this.secrets.env },
      mcp_servers: servers,
    };
  }

  /** What the agent is run with, as the backend's agent.configure takes it (backend/agent/agent_chat.py): the folder
   *  it works in, computer use, the MCP servers, the services that search the web and read pages, the keys of the
   *  chat worker's environment (env), and the messenger gateway: whether it is on, the accounts each bot answers and,
   *  only while it is on, the bot tokens, apart from env so that the chat's worker is never given them. With them go
   *  the commands that run without asking (allowed), what the agent may not touch (protected: the app's own folders,
   *  whether the places credentials are kept count, the user's folders), the command patterns refused (blocked) and
   *  how long and how far a turn may go (turn_limits). Only the main process ever calls this. */
  agent() {
    const backend = this.backend();
    const [env, tokens] = [{}, {}];
    for (const [name, value] of Object.entries(backend.env))
      (GATEWAY_KEYS.has(name) ? tokens : env)[name] = value;
    return {
      workspace: backend.workspace,
      computer_use: backend.computer_use,
      mcp_servers: backend.mcp_servers,
      search_backend: serviceOf(SEARCH_SERVICES, env),
      extract_backend: serviceOf(EXTRACT_SERVICES, env),
      allowed: this.values.allowed.map((entry) => entry.command),
      protected: {
        app: this.zones().filter((folder) => typeof folder === 'string' && folder),
        credentials: this.values.credentials,
        folders: [...this.values.folders],
      },
      blocked: [...this.values.blocked],
      turn_limits: { ...this.values.turn_limits },
      env,
      gateway: {
        enabled: backend.gateway,
        users: backend.gateway ? backend.users : {},
        env: backend.gateway ? tokens : {},
      },
    };
  }

  change(task) {
    const job = this.queue.then(() => {
      if (this.unavailable) throw new Error(this.unavailable);
      return task();
    });
    this.queue = job.catch(() => {});
    return job;
  }

  /** Keeps a command the user chose to always allow, as the backend told it from the agent's own question
   *  (agent.approve_command): true when it is on the list. The screens cannot name one: they can only take one back
   *  (update: disallow). */
  allow(text) {
    return this.change(async () => {
      const kept = command(text);
      if (!kept) return false;
      if (this.values.allowed.some((entry) => entry.command === kept)) return true;
      if (this.values.allowed.length >= MAX_ALLOWED) return false;
      const previous = this.values;
      this.values = {
        ...previous,
        allowed: [...previous.allowed, { command: kept, added_at: this.now() }],
      };
      try {
        await this.write();
      } catch {
        this.values = previous;
        return false;
      }
      return true;
    });
  }

  /** Saves what was sent: {workspace?, gateway?, computer_use?, users?: {platform: ids}, keys?: {NAME: value, or ''
   *  to clear}, mcp?: '<JSON>', disallow?: [commands to ask about again], protected?: {credentials?, folders?},
   *  blocked?: [command patterns], turn_limits?: {request_seconds?, iterations?, turn_minutes?, answer_minutes?}}.
   *  A value nobody typed again stays as it was. */
  update(params) {
    return this.change(async () => {
      const values = { ...this.values, users: { ...this.values.users } };
      const secrets = { env: { ...this.secrets.env }, mcp: this.secrets.mcp };
      if (Object.hasOwn(params, 'workspace')) {
        const folder = params.workspace;
        if (typeof folder !== 'string' || folder.length > MAX_PATH || CONTROL.test(folder))
          throw new HermesSettingsError('작업 폴더 경로를 확인해 주세요.');
        if (folder && !path.isAbsolute(folder))
          throw new HermesSettingsError('작업 폴더는 전체 경로로 정해 주세요.');
        if (folder) {
          const found = await this.stat(folder).catch(() => null);
          if (!found?.isDirectory()) throw new HermesSettingsError('작업 폴더를 찾을 수 없습니다.');
        }
        values.workspace = folder;
      }
      for (const field of ['gateway', 'computer_use'])
        if (Object.hasOwn(params, field)) {
          if (typeof params[field] !== 'boolean')
            throw new HermesSettingsError('켜기 설정을 확인해 주세요.');
          values[field] = params[field];
        }
      if (Object.hasOwn(params, 'disallow')) {
        if (
          !Array.isArray(params.disallow) ||
          params.disallow.some((item) => typeof item !== 'string')
        )
          throw new HermesSettingsError('허용을 취소할 명령을 확인해 주세요.');
        const taken = new Set(params.disallow.map((item) => item.trim()));
        values.allowed = values.allowed.filter((entry) => !taken.has(entry.command));
      }
      if (Object.hasOwn(params, 'protected')) {
        const given = params.protected;
        if (!given || typeof given !== 'object' || Array.isArray(given))
          throw new HermesSettingsError('보호 구역 설정을 확인해 주세요.');
        if (Object.hasOwn(given, 'credentials')) {
          if (typeof given.credentials !== 'boolean')
            throw new HermesSettingsError('켜기 설정을 확인해 주세요.');
          values.credentials = given.credentials;
        }
        if (Object.hasOwn(given, 'folders')) values.folders = folderList(given.folders);
      }
      if (Object.hasOwn(params, 'blocked')) values.blocked = blockedList(params.blocked);
      if (Object.hasOwn(params, 'turn_limits'))
        values.turn_limits = turnLimits(params.turn_limits, values.turn_limits);
      // A folder the agent itself works in cannot be closed to it: its work folder, and the Wiki folder with its home.
      const open = [values.workspace, ...this.reachable()].filter(
        (folder) => typeof folder === 'string' && folder,
      );
      const closing = values.folders.find((folder) => open.some((used) => within(used, folder)));
      if (closing && (Object.hasOwn(params, 'protected') || Object.hasOwn(params, 'workspace')))
        throw new HermesSettingsError(
          `${closing}에는 에이전트의 작업 폴더나 Wiki 폴더가 들어 있어 함께 쓸 수 없습니다. 보호할 폴더나 작업 폴더를 바꿔 주세요.`,
        );
      if (Object.hasOwn(params, 'users')) {
        if (!params.users || typeof params.users !== 'object')
          throw new HermesSettingsError('허용할 계정을 확인해 주세요.');
        for (const platform of PLATFORMS)
          if (Object.hasOwn(params.users, platform))
            values.users[platform] = users(params.users[platform]);
      }
      if (Object.hasOwn(params, 'keys')) {
        if (!params.keys || typeof params.keys !== 'object' || Array.isArray(params.keys))
          throw new HermesSettingsError('키 목록을 확인해 주세요.');
        for (const [name, value] of Object.entries(params.keys)) {
          const refused = refusedName(name);
          if (refused) throw new HermesSettingsError(refused);
          if (typeof value !== 'string' || value.length > MAX_VALUE || CONTROL.test(value))
            throw new HermesSettingsError(`${name}의 값을 확인해 주세요.`);
          if (value.trim()) secrets.env[name] = value.trim();
          else delete secrets.env[name];
        }
        if (Object.keys(secrets.env).length > MAX_KEYS)
          throw new HermesSettingsError(`키는 ${MAX_KEYS}개까지 넣을 수 있습니다.`);
      }
      if (Object.hasOwn(params, 'mcp')) {
        mcpServers(params.mcp);
        secrets.mcp = params.mcp.trim();
      }
      const previous = { values: this.values, secrets: this.secrets };
      this.values = values;
      this.secrets = secrets;
      try {
        await this.write();
      } catch {
        this.values = previous.values;
        this.secrets = previous.secrets;
        throw new Error('Hermes 설정을 저장하지 못했습니다. 앱 설정 폴더의 권한을 확인해 주세요.');
      }
      return this.get();
    });
  }

  async write() {
    /** @type {Record<string, unknown>} */
    const record = { version: VERSION, ...this.values };
    const any = Object.keys(this.secrets.env).length > 0 || !!this.secrets.mcp;
    const encrypted = any && this.canEncrypt();
    if (encrypted) record.encrypted = this.encrypt(JSON.stringify(this.secrets)).toString('base64');
    await fs.mkdir(path.dirname(this.file), { recursive: true });
    await writeFileSafely(this.file, JSON.stringify(record, null, 2));
    this.stored = encrypted;
  }
}

module.exports = {
  HermesSettings,
  HermesSettingsError,
  KNOWN_KEYS,
  GATEWAY_KEYS,
  PLATFORMS,
  TURN_LIMITS,
  refusedName,
  mcpServers,
  users,
};
