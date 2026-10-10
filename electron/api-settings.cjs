const fs = require('node:fs/promises');
const path = require('node:path');
const { randomBytes } = require('node:crypto');
const { ACCESS_LEVELS, DEFAULT_ACCESS, DEFAULT_PORT } = require('./api-catalog.cjs');
const { readJsonFile, writeFileSafely } = require('./files.cjs');

const TOKEN = /^pa_[A-Za-z0-9_-]{43}$/;
const newToken = () => 'pa_' + randomBytes(32).toString('base64url');
const validPort = (value) => Number.isInteger(value) && value >= 1024 && value <= 65535;
const validAccess = (value) => ACCESS_LEVELS.some((level) => level.id === value);
// Version 1 files hold the access level of their first start even when nobody chose it.
const VERSION = 2;
const UNREADABLE =
  'API 설정 파일(api.json)을 읽지 못했습니다. 저장된 설정과 토큰을 덮어쓰지 않도록 이번 실행에서는 서버를 켜거나 설정을 바꿀 수 없습니다. 앱을 다시 실행해 주세요.';

// Whether AI tools may reach the local API, on which port, with how much access, and the token they present. The
// server starts off: the app opens no port until the user turns it on. Like the LLM API Key, the token is kept with
// the OS secure storage; without it, a token lasts until the app closes.
class ApiSettings {
  constructor({ file, encrypt, decrypt, canEncrypt }) {
    this.file = file;
    this.encrypt = encrypt;
    this.decrypt = decrypt;
    this.canEncrypt = canEncrypt;
    this.values = { enabled: false, port: DEFAULT_PORT, access: DEFAULT_ACCESS };
    this.token = '';
    this.stored = false;
    this.queue = Promise.resolve();
    // Why the settings could not be loaded; while set, nothing is saved over api.json.
    this.unavailable = '';
  }

  async load() {
    let record;
    try {
      record = (await readJsonFile(this.file)) ?? {};
    } catch {
      // Held by another program for good, or not readable: the server stays off this session.
      this.unavailable = UNREADABLE;
      this.token = newToken();
      return this.get();
    }
    const enabled = record.enabled === true;
    this.values = {
      enabled,
      port: validPort(record.port) ? record.port : DEFAULT_PORT,
      // Every earlier install stored the default of its time ("write") at its first start; one that never let AI
      // tools in gets today's default.
      access:
        validAccess(record.access) && (record.version >= VERSION || enabled)
          ? record.access
          : DEFAULT_ACCESS,
    };
    if (typeof record.encrypted_token === 'string' && record.encrypted_token)
      try {
        const token = this.decrypt(Buffer.from(record.encrypted_token, 'base64'));
        if (TOKEN.test(token)) {
          this.token = token;
          this.stored = true;
        }
      } catch {
        /* The OS key changed (e.g. another Windows account): AI tools get a new token. */
      }
    if (!this.token) {
      this.token = newToken();
      await this.write().catch(() => {});
    }
    return this.get();
  }

  get() {
    return {
      ...this.values,
      token: this.token,
      token_stored: this.stored,
      error: this.unavailable,
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

  update(params) {
    return this.change(async () => {
      const next = { ...this.values };
      if (Object.hasOwn(params, 'enabled')) {
        if (typeof params.enabled !== 'boolean') throw new Error('켜기 설정을 확인해 주세요.');
        next.enabled = params.enabled;
      }
      if (Object.hasOwn(params, 'port')) {
        if (!validPort(params.port))
          throw new Error('포트는 1024~65535 사이의 숫자로 입력해 주세요.');
        next.port = params.port;
      }
      if (Object.hasOwn(params, 'access')) {
        if (!validAccess(params.access)) throw new Error('허용 범위를 선택해 주세요.');
        next.access = params.access;
      }
      const previous = this.values;
      this.values = next;
      try {
        await this.write();
      } catch {
        this.values = previous;
        throw new Error('API 설정을 저장하지 못했습니다. 앱 설정 폴더의 권한을 확인해 주세요.');
      }
      return this.get();
    });
  }

  // A new token: every AI tool configured with the old one is refused from now on. Should it not reach the disk, the
  // new token still applies until the app closes, but the old one would be back after a restart: the user is told.
  rotate() {
    return this.change(async () => {
      this.token = newToken();
      try {
        await this.write();
      } catch {
        this.stored = false;
        throw new Error(
          '새 토큰을 저장하지 못했습니다. 지금은 새 토큰만 쓸 수 있지만, 앱을 다시 실행하면 이전 토큰이 다시 유효해집니다. 앱 설정 폴더의 권한을 확인한 뒤 다시 발급해 주세요.',
        );
      }
      return this.get();
    });
  }

  async write() {
    /** @type {Record<string, unknown>} */
    const record = { version: VERSION, ...this.values };
    const encrypted = this.canEncrypt() ? this.encrypt(this.token).toString('base64') : '';
    if (encrypted) record.encrypted_token = encrypted;
    await fs.mkdir(path.dirname(this.file), { recursive: true });
    await writeFileSafely(this.file, JSON.stringify(record, null, 2));
    this.stored = !!encrypted;
  }
}

module.exports = { ApiSettings };
