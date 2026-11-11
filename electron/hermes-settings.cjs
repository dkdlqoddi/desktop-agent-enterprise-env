const fs = require('node:fs/promises');
const path = require('node:path');
const { readJsonFile, writeFileSafely } = require('./files.cjs');

// The chat's Hermes settings (hermes.json): its working folder, the messaging gateway, computer use, the MCP servers
// and the service keys and bot tokens Hermes reads from its environment. Like the LLM API Key, every value is kept with
// the OS secure storage (encrypted); without it, values last until the app closes. The screen only ever learns
// whether a value is set. The backend gets them at its start and after each change (hermes.configure), keeps them in
// memory and hands them to the Hermes worker's environment; the LLM connection is never among them.

const VERSION = 1;
// The keys the settings screen lists, by what each is for; any other name the user adds goes the same way (a skill's
// own key, another search provider).
const KNOWN_KEYS = [
  { name: 'EXA_API_KEY', group: 'web', label: 'Exa 웹 검색' },
  { name: 'FIRECRAWL_API_KEY', group: 'web', label: 'Firecrawl 웹 검색·본문 추출' },
  { name: 'TAVILY_API_KEY', group: 'web', label: 'Tavily 웹 검색' },
  { name: 'PARALLEL_API_KEY', group: 'web', label: 'Parallel 웹 검색' },
  { name: 'BRAVE_SEARCH_API_KEY', group: 'web', label: 'Brave 웹 검색' },
  { name: 'FAL_KEY', group: 'media', label: 'FAL 이미지 생성' },
  { name: 'ELEVENLABS_API_KEY', group: 'media', label: 'ElevenLabs 음성' },
  { name: 'TELEGRAM_BOT_TOKEN', group: 'gateway', label: '텔레그램 봇 토큰' },
  { name: 'DISCORD_BOT_TOKEN', group: 'gateway', label: '디스코드 봇 토큰' },
  { name: 'SLACK_BOT_TOKEN', group: 'gateway', label: '슬랙 봇 토큰 (xoxb-)' },
  { name: 'SLACK_APP_TOKEN', group: 'gateway', label: '슬랙 앱 토큰 (xapp-, Socket Mode)' },
];
// The accounts each messaging platform answers; a platform without any stays off even with its token.
const PLATFORMS = ['telegram', 'discord', 'slack'];
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
  'GATEWAY_ALLOWED_USERS',
  'TELEGRAM_ALLOWED_USERS',
  'DISCORD_ALLOWED_USERS',
  'SLACK_ALLOWED_USERS',
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
  constructor({ file, encrypt, decrypt, canEncrypt, stat = fs.stat }) {
    this.file = file;
    this.encrypt = encrypt;
    this.decrypt = decrypt;
    this.canEncrypt = canEncrypt;
    this.stat = stat;
    this.values = { workspace: '', gateway: false, computer_use: true, users: {} };
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

  change(task) {
    const job = this.queue.then(() => {
      if (this.unavailable) throw new Error(this.unavailable);
      return task();
    });
    this.queue = job.catch(() => {});
    return job;
  }

  /** Saves what was sent: {workspace?, gateway?, computer_use?, users?: {platform: ids}, keys?: {NAME: value, or ''
   *  to clear}, mcp?: '<JSON>'}. A value nobody typed again stays as it was. */
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
  PLATFORMS,
  refusedName,
  mcpServers,
  users,
};
