const fs = require('node:fs/promises');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { readJsonFile, writeFileSafely } = require('./files.cjs');

// A site address typed without its scheme is saved over https: the backend never opens a site over http unless http://
// is written out, so a saved password never goes over a plain connection by a fallback (browser_import.py _reach).
// The hosts the account was typed into besides the site's own (its identity provider) are kept once they signed in
// (login_hosts): from then on the backend types the account into no other host, until the user resets them.
// sites.json keeps the login ID and the OS-encrypted password; the password never reaches the renderer,
// and without secure storage it is kept in memory for this session only. An account is only ever used for
// links of its own address: its password is typed into that site and no other, so a site moved to another
// origin or kind keeps its password only when it is typed again with the change. An auto site also keeps the
// fields the LLM found on a login screen no method fitted (learned), and the LLM's analysis of its last
// failed login (last_diagnosis). A change shows only once sites.json holds it.
// A login method is a list of steps (login-methods.json): click an element, type the ID, the password or a text the
// user wrote into a field, and last send the login. The user's own method (custom) is such a list too, in any order.
// Every login tries the methods in one order (plan): the site's chosen or saved method, then the standard SSO
// methods (priority), then the rest; after one that does not fit or does not sign in, the next.

const KINDS = ['jira', 'confluence'];
const DEFAULT_NAMES = {
  jira: 'JIRA',
  confluence: 'Confluence',
};
const LEGACY_NAMES = { jira: '기본 JIRA 계정', confluence: '기본 Confluence 계정' };
const SPECIAL_LABELS = {
  auto: '자동 감지',
  custom: '사용자 지정',
  session: '자동 입력 안 함 · 로그인 창 사용',
};
const MAX_NAME = 80;
const MAX_LOGIN_ID = 256;
const MAX_PASSWORD = 1024;
// Identity-provider hosts kept with a site, as backend/agent/web_login.py takes them (MAX_HOSTS, HOST).
const MAX_HOSTS = 20;
const HOST = /^[a-z0-9.\-[\]:]{1,253}$/i;

function hostsOf(value) {
  if (!Array.isArray(value)) return [];
  const found = value
    .filter((host) => typeof host === 'string' && HOST.test(host))
    .map((host) => host.toLowerCase());
  return [...new Set(found)].slice(0, MAX_HOSTS);
}
const MAX_SELECTOR = 500;
const MAX_BEFORE = 5;
// Steps of a login method, its last one the submit; a text the user wrote for a field (backend/agent/web_login.py).
const MAX_STEPS = 16;
const MAX_TEXT = 200;
const ACTIONS = ['click', 'fill', 'submit'];
const VALUES = ['id', 'password', 'text'];
// The method of the fields the LLM found, as backend/agent/web_login.py names it (LEARNED, LEARNED_LABEL).
const LEARNED = { id: 'learned', label: 'AI가 찾은 로그인 화면' };
// Application routes that follow the site's base path (context path) in JIRA and Confluence links.
const APP_ROUTE =
  /\/(?:browse|projects|issues|secure|display|spaces|pages|x|rest|plugins|login\.jsp|login\.action|dashboard\.action)(?:\/|$)/i;
const CONTROL = /[\x00-\x1f\x7f]/;
// An address that names its scheme, and one without, as backend/agent/web_login.py SCHEME and BARE take them: a host (a
// name, an IPv4 address or an [IPv6] one), its port, then its path, without spaces or backslashes.
const SCHEME = /^[a-z][a-z0-9+.-]*:\/\//i;
const BARE =
  /^(?:\/\/)?(?:[\p{L}\p{N}\p{M}_-]+(?:\.[\p{L}\p{N}\p{M}_-]+)*|\[[0-9a-f:.]+\])(?::\d{1,5})?(?:[/?#][^\s\\]*)?$/iu;
const ADDRESS_ERROR =
  '사이트 주소를 확인해 주세요. 예) portal.example.com, https://jira.example.com/jira (https://는 빼도 됩니다. http만 쓰는 사이트는 http://를 붙여 주세요)';
const UNREADABLE =
  '사이트 계정 파일(sites.json)을 읽지 못했습니다. 저장된 계정을 덮어쓰지 않도록 이번 실행에서는 계정을 바꿀 수 없습니다. 앱을 다시 실행해 주세요.';
const UNMOVED =
  '이전 버전의 로그인 정보를 사이트 계정 파일(sites.json)로 옮기지 못했습니다. 이전 정보는 그대로 두었으며, 이번 실행에서는 계정을 바꿀 수 없습니다. 앱을 다시 실행해 주세요.';

class SiteError extends Error {}

/** An http(s) address or link as typed, with or without its scheme: {url, bare}, the URL read over https for one
 *  without (bare), as the backend first opens it; null for anything else. */
function addressOf(value) {
  if (typeof value !== 'string') return null;
  const text = value.trim();
  const bare = !SCHEME.test(text);
  if (bare && !BARE.test(text)) return null;
  try {
    const url = new URL(bare ? `https://${text.replace(/^\/\//, '')}` : text);
    return ['http:', 'https:'].includes(url.protocol) &&
      url.hostname &&
      !url.username &&
      !url.password
      ? { url, bare }
      : null;
  } catch {
    return null;
  }
}

// The origin a site's password belongs to: scheme://host[:port], https for an address without a scheme; '' without
// an address (an account carried over from an earlier version).
function originOf(value) {
  const found = value ? addressOf(value) : null;
  return found ? found.url.origin : '';
}

/** The site's base address: origin and context path of a JIRA or Confluence link, without
 *  query or trailing slash.
 *  An address typed without its scheme is an https address: the site is never opened over http unless
 *  http:// is written out. Throws on anything but a plain http(s) address. */
function normalizeUrl(value, kind) {
  const found = typeof value === 'string' && value.length <= 2048 ? addressOf(value) : null;
  if (!found) throw new SiteError(ADDRESS_ERROR);
  const { url } = found;
  const prefix = url.pathname.split(APP_ROUTE)[0].replace(/\/+$/, '');
  return url.origin + prefix;
}

// A site address an earlier version saved without its scheme (host/path) is the https address it opened first:
// the password stays with it, as its origin is the same.
function withScheme(value) {
  const found = addressOf(value);
  return found?.bare ? `https://${value.trim().replace(/^\/\//, '')}` : value;
}

function selector(value, name) {
  if (value === undefined || value === null) return '';
  if (typeof value !== 'string' || value.length > MAX_SELECTOR || CONTROL.test(value))
    throw new SiteError(`${name}: 줄바꿈 없이 ${MAX_SELECTOR}자 이내로 입력해 주세요.`);
  const trimmed = value.trim();
  // XPath positions copied from the browser's developer tools.
  return /^\(?\//.test(trimmed) ? `xpath=${trimmed}` : trimmed;
}

/** One step of a user-defined method, checked: {action, target} and for a fill its value ('id', 'password', or
 *  'text' with the text), for a click or a text also optional (only when it shows) and unless (not when that element
 *  shows). A submit's target may be empty: Enter in the field filled last. */
function stepOf(value, number) {
  const where = `${number}번째 단계`;
  if (
    !value ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    !ACTIONS.includes(value.action)
  )
    throw new SiteError(`${where}: 동작(텍스트 입력·버튼 클릭·로그인 버튼)을 골라 주세요.`);
  const step = { action: value.action, target: selector(value.target, `${where}의 위치`) };
  if (step.action !== 'submit' && !step.target)
    throw new SiteError(`${where}: 입력하거나 누를 위치(XPath 또는 CSS 선택자)를 입력해 주세요.`);
  if (step.action === 'fill') {
    if (!VALUES.includes(value.value))
      throw new SiteError(`${where}: 입력할 값(로그인 ID·비밀번호·직접 입력)을 골라 주세요.`);
    step.value = value.value;
    if (step.value === 'text') {
      if (
        typeof value.text !== 'string' ||
        !value.text ||
        value.text.length > MAX_TEXT ||
        CONTROL.test(value.text)
      )
        throw new SiteError(
          `${where}: 입력할 글자를 줄바꿈 없이 ${MAX_TEXT}자 이내로 입력해 주세요.`,
        );
      step.text = value.text;
    }
  }
  if (step.action === 'click' || step.value === 'text') {
    step.optional = value.optional === true;
    const unless = selector(value.unless, `${where}의 건너뛸 조건`);
    if (unless) step.unless = unless;
  }
  return step;
}

// The steps of the shape earlier versions kept a user-defined method (and the LLM's fields) in: the buttons to click
// first when they show, unless the ID field does, the ID, the button after it, the password and the login button.
function legacySteps(value) {
  const before = value.before ?? [];
  if (!Array.isArray(before) || before.length > MAX_BEFORE)
    throw new SiteError(`로그인 전에 누를 버튼은 ${MAX_BEFORE}개까지 입력할 수 있습니다.`);
  const clicks = before.map((item) => selector(item, '로그인 전에 누를 버튼')).filter(Boolean);
  const id = selector(value.id, 'ID 입력란');
  const next = selector(value.next, 'ID 입력 후 누를 버튼');
  const password = selector(value.password, '비밀번호 입력란');
  const submit = selector(value.submit, '로그인 버튼');
  if (!id || !password) return [];
  return [
    ...clicks.map((target) => ({ action: 'click', target, optional: true, unless: id })),
    { action: 'fill', target: id, value: 'id' },
    ...(next ? [{ action: 'click', target: next, optional: false }] : []),
    { action: 'fill', target: password, value: 'password' },
    { action: 'submit', target: submit },
  ];
}

/** A user-defined method as {steps}, from its steps or the shape of earlier versions. The login is sent by the last
 *  step, the only submit, which is added (Enter) to steps without one; no steps: no method yet.
 *  @param {any} [value] */
function customOf(value = {}) {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new SiteError('사용자 지정 로그인 방식의 형식을 확인해 주세요.');
  if (value.steps === undefined)
    return { steps: legacySteps(value).map((step, index) => stepOf(step, index + 1)) };
  if (!Array.isArray(value.steps) || value.steps.length > MAX_STEPS)
    throw new SiteError(`사용자 지정 방식의 단계는 ${MAX_STEPS}개까지 입력할 수 있습니다.`);
  const steps = value.steps.map((step, index) => stepOf(step, index + 1));
  if (steps.length && steps.at(-1).action !== 'submit') {
    if (steps.length === MAX_STEPS)
      throw new SiteError(`사용자 지정 방식의 단계는 ${MAX_STEPS}개까지 입력할 수 있습니다.`);
    steps.push({ action: 'submit', target: '' });
  }
  if (steps.slice(0, -1).some((step) => step.action === 'submit'))
    throw new SiteError('로그인 버튼 단계는 마지막에 하나만 둘 수 있습니다.');
  return { steps };
}

// Whether a user-defined method can sign in: it types the ID and the password.
const complete = (custom) =>
  ['id', 'password'].every((value) =>
    custom.steps.some((step) => step.action === 'fill' && step.value === value),
  );

const emptyCustom = () => ({ steps: [] });

// The fields the LLM found for a site (backend/agent/web_login.py), as the steps of a user-defined method; null when
// they are not one.
function learnedOf(value) {
  try {
    const custom = customOf(value);
    return complete(custom) ? custom : null;
  } catch {
    return null;
  }
}

// The LLM's analysis of a failed login as the screens show it (backend/agent/web_assist.py), or only
// why there is none (error); null without one.
function diagnosisOf(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const text = (item, limit) => (typeof item === 'string' ? item.slice(0, limit) : '');
  const list = (items) =>
    Array.isArray(items)
      ? items
          .map((item) => text(item, 200))
          .filter(Boolean)
          .slice(0, 3)
      : [];
  if (typeof value.error === 'string') return { error: text(value.error, 500) };
  if (!text(value.cause, 300)) return null;
  return {
    category: text(value.category, 40),
    label: text(value.label, 40),
    cause: text(value.cause, 300),
    evidence: list(value.evidence),
    actions: list(value.actions),
    attempt: text(value.attempt, 300),
  };
}

/** The elements that show a user-defined login screen: those its steps click or fill up to the first field it types
 *  into (a notice, the SSO link, the ID field), the four nearest it. */
function detectOf(steps) {
  const first = steps.findIndex((step) => step.action === 'fill');
  const targets = steps.slice(0, first + 1).map((step) => step.target);
  return [...new Set(targets.filter(Boolean))].slice(-4);
}

// A user-defined login screen as a method of the catalog's shape.
function customMethod(custom, { id, label } = { id: 'custom', label: SPECIAL_LABELS.custom }) {
  return { id, label, detect: detectOf(custom.steps), steps: structuredClone(custom.steps) };
}

const learnedMethod = (learned) => customMethod(learned, LEARNED);

class SiteStore {
  constructor({ file, legacyFiles = {}, encrypt, decrypt, canEncrypt, methods }) {
    this.file = file;
    this.legacyFiles = legacyFiles;
    this.encrypt = encrypt;
    this.decrypt = decrypt;
    this.canEncrypt = canEncrypt;
    this.methods = methods;
    this.sites = [];
    this.queue = Promise.resolve();
    // Why the accounts could not be loaded; while set, nothing is saved over sites.json.
    this.unavailable = '';
  }

  // --- Loading ------------------------------------------------------------------------------------

  async load() {
    let data;
    try {
      data = await readJsonFile(this.file);
    } catch {
      // Held by another program for good, or not readable: the accounts stay on disk as they are.
      this.unavailable = UNREADABLE;
      return this.list();
    }
    // None yet, or a damaged file that was set aside: the logins of earlier versions move in, if any. When they
    // cannot be written, they stay where they are for the next start, and nothing is saved this session.
    if (!data) {
      try {
        await this.migrate();
      } catch {
        this.unavailable = UNMOVED;
      }
      return this.list();
    }
    this.sites = (Array.isArray(data.sites) ? data.sites : [])
      .map((record) => this.restore(record))
      .filter(Boolean);
    return this.list();
  }

  // One stored record; anything malformed is dropped rather than failing the app start.
  restore(record) {
    if (!record || typeof record !== 'object' || !KINDS.includes(record.kind)) return null;
    if (typeof record.id !== 'string' || !record.id) return null;
    let custom;
    try {
      custom = customOf(record.custom ?? {});
    } catch {
      custom = emptyCustom();
    }
    const method =
      typeof record.method === 'string' && this.knownMethod(record.method) ? record.method : 'auto';
    const learned = learnedOf(record.learned);
    const site = {
      id: record.id,
      kind: record.kind,
      name:
        typeof record.name === 'string' && record.name ? record.name : DEFAULT_NAMES[record.kind],
      url: typeof record.url === 'string' ? withScheme(record.url) : '',
      login_id: typeof record.login_id === 'string' ? record.login_id : '',
      password: '',
      encrypted_password: '',
      method,
      detected_method:
        typeof record.detected_method === 'string' &&
        (this.preset(record.detected_method) || (record.detected_method === LEARNED.id && learned))
          ? record.detected_method
          : null,
      custom,
      learned,
      login_hosts: hostsOf(record.login_hosts),
      verified_at: typeof record.verified_at === 'string' ? record.verified_at : null,
      last_error: typeof record.last_error === 'string' ? record.last_error : '',
      last_diagnosis: diagnosisOf(record.last_diagnosis),
      updated_at:
        typeof record.updated_at === 'string' ? record.updated_at : new Date().toISOString(),
    };
    if (typeof record.encrypted_password === 'string' && record.encrypted_password)
      try {
        site.password = this.decrypt(Buffer.from(record.encrypted_password, 'base64'));
        site.encrypted_password = record.encrypted_password;
      } catch {
        /* The OS key changed (e.g. another Windows account): the password is entered again. */
      }
    return site;
  }

  // The single-account files of earlier versions become accounts without an address. They keep the
  // login ID, but sign in nowhere until the user enters the address of their site, and with it the
  // password again: it was typed for no address.
  async migrate() {
    const found = [];
    const moved = [];
    for (const kind of KINDS) {
      const file = this.legacyFiles[kind];
      if (!file) continue;
      let record;
      try {
        record = JSON.parse(await fs.readFile(file, 'utf8'));
      } catch {
        continue;
      }
      found.push(file);
      const site = this.restore({
        id: randomUUID(),
        kind,
        name: LEGACY_NAMES[kind],
        url: '',
        login_id: typeof record?.login_id === 'string' ? record.login_id.trim() : '',
        encrypted_password: record?.encrypted_password,
        method: 'auto',
      });
      if (site?.login_id) moved.push(site);
    }
    if (!found.length) return;
    if (moved.length) await this.write(moved);
    this.sites = moved;
    // A file another program holds stays behind: what it held has moved.
    await Promise.allSettled(found.map((file) => fs.rm(file, { force: true })));
  }

  // --- Reading ------------------------------------------------------------------------------------

  preset(id) {
    return this.methods.find((method) => method.id === id) || null;
  }

  knownMethod(id) {
    return Object.hasOwn(SPECIAL_LABELS, id) || !!this.preset(id);
  }

  methodLabel(id) {
    return SPECIAL_LABELS[id] ?? (id === LEARNED.id ? LEARNED.label : this.preset(id)?.label) ?? '';
  }

  get(id) {
    return this.sites.find((site) => site.id === id) || null;
  }

  public(site) {
    if (!site) return null;
    return {
      id: site.id,
      kind: site.kind,
      name: site.name,
      url: site.url,
      login_id: site.login_id,
      has_password: !!site.password,
      password_stored: !!site.encrypted_password,
      method: site.method,
      method_label: this.methodLabel(site.method),
      detected_method: site.detected_method,
      detected_label: site.detected_method ? this.methodLabel(site.detected_method) : '',
      custom: structuredClone(site.custom),
      learned: site.learned ? structuredClone(site.learned) : null,
      login_hosts: [...site.login_hosts],
      verified_at: site.verified_at,
      last_error: site.last_error,
      last_diagnosis: site.last_diagnosis ? structuredClone(site.last_diagnosis) : null,
      updated_at: site.updated_at,
    };
  }

  // The catalog's methods with what each does: the elements that show its screen and its steps, for the screens to show
  // and to copy into a user-defined method.
  list() {
    return {
      sites: this.sites.map((site) => this.public(site)),
      methods: this.methods.map(({ id, label, description = '', priority, detect, steps }) => ({
        id,
        label,
        description,
        priority: priority === true,
        detect: [...detect],
        steps: structuredClone(steps),
      })),
      secure_storage: this.canEncrypt(),
      error: this.unavailable,
    };
  }

  /** The site whose address holds `value`: same kind, longest base path, and of two alike the one that names its
   *  scheme. An account without an address never matches, or a link to any host could have its password typed into
   *  that host. An address saved without its scheme holds links of either; a link without one is an https link. */
  match(value, kind) {
    const link = addressOf(String(value));
    if (!link) return null;
    const { url } = link;
    let best = null;
    for (const site of this.sites) {
      if (site.kind !== kind || !site.url) continue;
      const named = SCHEME.test(site.url);
      const key = (named ? url.origin : url.host) + url.pathname;
      if (key !== site.url && !key.startsWith(site.url + '/')) continue;
      const length = site.url.replace(SCHEME, '').length;
      if (!best || length > best.length || (length === best.length && named))
        best = { site, length };
    }
    return best?.site ?? null;
  }

  /** Try the chosen method, then priority methods (standard SSO), then the others. An auto site's saved method
   *  goes first in its group. The chosen method counts as a priority one. */
  methodsOf(site) {
    const priority = this.methods.filter((method) => method.priority === true);
    const others = this.methods.filter((method) => method.priority !== true);
    const without = (list, method) => list.filter((item) => item !== method);
    if (site.method === 'auto') {
      const saved =
        site.detected_method === LEARNED.id && site.learned
          ? learnedMethod(site.learned)
          : this.preset(site.detected_method);
      if (!saved) return [...priority, ...others];
      return saved.priority === true
        ? [saved, ...without(priority, saved), ...others]
        : [...priority, saved, ...without(others, saved)];
    }
    const chosen = site.method === 'custom' ? customMethod(site.custom) : this.preset(site.method);
    if (!chosen) return [];
    return [
      { ...chosen, priority: true },
      ...without(priority, chosen),
      ...without(others, chosen),
    ];
  }

  /** How to sign in to the site automatically, or null to use the browser profile's session. A login tries
   *  methodsOf(site) in order (backend/agent/web_login.py); auto mode keeps the one that signed in. */
  plan(site) {
    if (!site || site.method === 'session' || !site.login_id || !site.password) return null;
    if (site.method === 'custom' && !complete(site.custom)) return null;
    const methods = this.methodsOf(site);
    if (!methods.length) return null;
    return {
      site_id: site.id,
      site_name: site.name,
      credentials: { id: site.login_id, password: site.password },
      mode: site.method === 'auto' ? 'auto' : 'fixed',
      methods: structuredClone(methods),
      hosts: [...site.login_hosts],
    };
  }

  // --- Writing ------------------------------------------------------------------------------------

  update(change) {
    const job = this.queue.then(() => {
      if (this.unavailable) throw new SiteError(this.unavailable);
      return change();
    });
    this.queue = job.catch(() => {});
    return job;
  }

  // Writes `sites`, by default the ones held now; a change is written before it is held, so a failed write changes
  // nothing.
  async write(sites = this.sites) {
    const record = {
      version: 1,
      sites: sites.map((site) => {
        const { password: _password, encrypted_password: encrypted, ...rest } = site;
        return encrypted ? { ...rest, encrypted_password: encrypted } : rest;
      }),
    };
    await fs.mkdir(path.dirname(this.file), { recursive: true });
    await writeFileSafely(this.file, JSON.stringify(record, null, 2));
  }

  save(params) {
    return this.update(async () => {
      if (!params || typeof params !== 'object')
        throw new SiteError('사이트 정보를 확인해 주세요.');
      const existing = params.id ? this.get(params.id) : null;
      if (params.id && !existing)
        throw new SiteError('사이트를 찾을 수 없습니다. 목록을 새로고침해 주세요.');
      const kind = params.kind ?? existing?.kind;
      if (!KINDS.includes(kind))
        throw new SiteError('사이트 종류(JIRA·Confluence)를 선택해 주세요.');
      const rawName = params.name ?? existing?.name ?? '';
      if (typeof rawName !== 'string' || rawName.trim().length > MAX_NAME || CONTROL.test(rawName))
        throw new SiteError(`사이트 이름은 줄바꿈 없이 ${MAX_NAME}자 이내로 입력해 주세요.`);
      const name = rawName.trim() || DEFAULT_NAMES[kind];
      const rawUrl = params.url ?? existing?.url ?? '';
      // Also for an account moved from an earlier version, which has none until it is edited.
      if (typeof rawUrl === 'string' && !rawUrl.trim())
        throw new SiteError(
          '사이트 주소를 입력해 주세요. 계정은 이 주소의 링크를 가져올 때만 로그인에 씁니다.',
        );
      const url = normalizeUrl(rawUrl, kind);
      const method = params.method ?? existing?.method ?? 'auto';
      if (typeof method !== 'string' || !this.knownMethod(method))
        throw new SiteError('로그인 방식을 선택해 주세요.');
      const loginId = params.login_id ?? existing?.login_id ?? '';
      if (
        typeof loginId !== 'string' ||
        loginId.trim().length > MAX_LOGIN_ID ||
        CONTROL.test(loginId)
      )
        throw new SiteError(`로그인 ID를 줄바꿈 없이 ${MAX_LOGIN_ID}자 이내로 입력해 주세요.`);
      if (method !== 'session' && !loginId.trim())
        throw new SiteError('로그인 ID를 입력해 주세요.');
      const typed = params.password ?? '';
      if (typeof typed !== 'string' || typed.length > MAX_PASSWORD || /[\0\r\n]/.test(typed))
        throw new SiteError(
          `비밀번호는 줄바꿈 없이 ${MAX_PASSWORD.toLocaleString()}자 이내로 입력해 주세요.`,
        );
      // The password belongs to the site it was typed for: an account moved to another origin (scheme, host,
      // port) or kind keeps one only when it is typed again in the same save.
      const moved =
        !!existing && (existing.kind !== kind || originOf(existing.url) !== originOf(url));
      const password =
        params.clear_password === true ? '' : typed || (moved ? '' : existing?.password || '');
      // A saved site may lose its password (cleared, or memory-only storage after a restart); it then
      // uses the browser session until the password is entered again.
      if (!existing && method !== 'session' && !password)
        throw new SiteError('비밀번호를 입력해 주세요.');
      const custom =
        params.custom === undefined ? (existing?.custom ?? emptyCustom()) : customOf(params.custom);
      if (method === 'custom' && !complete(custom))
        throw new SiteError(
          '사용자 지정 방식에는 로그인 ID와 비밀번호를 입력하는 텍스트 입력 단계가 있어야 합니다.',
        );
      if (this.sites.some((site) => site !== existing && site.kind === kind && site.url === url))
        throw new SiteError('같은 종류와 주소의 사이트가 이미 있습니다.');
      const changed =
        existing && (existing.kind !== kind || existing.url !== url || existing.method !== method);
      // The identity-provider hosts belong to the site, its address and its method as they are: a change starts
      // over, as does the user (reset_login_hosts) when the site's login screen moved.
      const reset = changed || params.reset_login_hosts === true;
      const changes = {
        kind,
        name,
        url,
        login_id: loginId.trim(),
        password,
        encrypted_password:
          password && this.canEncrypt()
            ? password === existing?.password && existing?.encrypted_password
              ? existing.encrypted_password
              : this.encrypt(password).toString('base64')
            : '',
        method,
        custom,
        updated_at: new Date().toISOString(),
        ...(changed && {
          detected_method: null,
          learned: null,
          verified_at: null,
          last_error: '',
          last_diagnosis: null,
        }),
        ...(reset && { login_hosts: [] }),
      };
      const site = {
        ...(existing ?? {
          id: randomUUID(),
          detected_method: null,
          learned: null,
          login_hosts: [],
          verified_at: null,
          last_error: '',
          last_diagnosis: null,
        }),
        ...changes,
      };
      await this.write(
        existing
          ? this.sites.map((item) => (item === existing ? site : item))
          : [...this.sites, site],
      );
      if (existing) Object.assign(existing, changes);
      else this.sites.push(site);
      return this.public(existing ?? site);
    });
  }

  remove(id) {
    return this.update(async () => {
      const site = this.get(id);
      if (!site) throw new SiteError('사이트를 찾을 수 없습니다. 목록을 새로고침해 주세요.');
      const rest = this.sites.filter((item) => item !== site);
      await this.write(rest);
      this.sites = rest;
      return { ok: true };
    });
  }

  /** Whether the browser profile an account of `kind` at `url` signed in with serves another account still: the
   *  backend keeps one per origin for JIRA and Confluence (over https for an address without its scheme). */
  profileInUse(kind, url) {
    const profile = (value) => addressOf(value)?.url.origin ?? '';
    return this.sites.some(
      (site) => site.kind === kind && site.url && profile(site.url) === profile(url),
    );
  }

  /** How an automatic login went: a detected method, or the fields the LLM found (learned), is kept for
   *  the next login of an auto site; a failure keeps its message and the LLM's analysis of it. The hosts the account
   *  was typed into besides the site's own (hosts) are kept once, by the first login that signed in: the account
   *  goes to no other host from then on.
   *  @param {string} id
   *  @param {{ ok?: boolean, method?: string, detected?: boolean, learned?: any, hosts?: any, error?: string,
   *    diagnosis?: any }} [login] */
  record(id, { ok, method, detected, learned, hosts, error, diagnosis } = {}) {
    return this.update(async () => {
      const site = this.get(id);
      if (!site) return null;
      const changes = {};
      if (ok) {
        changes.verified_at = new Date().toISOString();
        const typed = hostsOf(hosts);
        if (typed.length && !site.login_hosts.length) changes.login_hosts = typed;
        const found = method === LEARNED.id ? learnedOf(learned) : null;
        if (detected && site.method === 'auto') {
          if (found) Object.assign(changes, { learned: found, detected_method: LEARNED.id });
          else if (this.preset(method))
            Object.assign(changes, { learned: null, detected_method: method });
          // The kept fields signed in again.
          else if (method === LEARNED.id && site.learned) changes.detected_method = LEARNED.id;
        }
      }
      changes.last_error = ok ? '' : String(error || '').slice(0, 1000);
      changes.last_diagnosis = ok ? null : diagnosisOf(diagnosis);
      await this.write(this.sites.map((item) => (item === site ? { ...site, ...changes } : item)));
      Object.assign(site, changes);
      return this.public(site);
    });
  }
}

module.exports = { SiteStore, SiteError, addressOf, normalizeUrl, customMethod, diagnosisOf };
