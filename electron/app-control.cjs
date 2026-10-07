const { randomUUID } = require('node:crypto');
const { ADMIN_SECTIONS, ApiError, TABS, VIEWS, WINDOW_ACTIONS } = require('./api-catalog.cjs');

const ROLES = ['button', 'tab', 'link', 'checkbox', 'switch', 'option'];

function text(value, name, limit, required = true) {
  if (value === undefined || value === null || value === '') {
    if (required) throw new Error(`${name}: 꼭 필요한 값입니다.`);
    return '';
  }
  if (typeof value !== 'string' || value.length > limit)
    throw new Error(`${name}: ${limit.toLocaleString('en-US')}자 이내의 글로 입력해 주세요.`);
  return value;
}

function optionalInteger(value, name, min, max) {
  if (value === undefined || value === null) return undefined;
  if (!Number.isInteger(value) || value < min || value > max)
    throw new Error(`${name}: ${min}~${max} 사이의 정수로 입력해 주세요.`);
  return value;
}

// Lets the local API drive the desktop app: the window itself here, and the screen through commands that the renderer
// answers (src/components/app-control.ts) once it has registered for them after each load.
class AppControl {
  constructor({ window, Notification, timeout = 10_000, minWidth = 1060, minHeight = 720 }) {
    Object.assign(this, { window, Notification, timeout, minWidth, minHeight });
    this.ready = false;
    this.pending = new Map();
    this.notifications = new Set();
    // The commands go one at a time: each waits until the one before it is answered (or given up), so a click of the
    // same batch of an AI tool's calls cannot land while a fill waits to submit its form.
    this.waiting = [];
    this.current = null;
  }

  attach() {
    this.ready = true;
  }

  // The screen is loading again: commands sent to the old one are never answered.
  detach() {
    this.ready = false;
    for (const item of this.pending.values()) {
      clearTimeout(item.timer);
      item.reject(
        new ApiError(
          503,
          'unavailable',
          '앱 화면을 다시 불러오고 있습니다. 잠시 후 다시 시도해 주세요.',
        ),
      );
    }
    this.pending.clear();
    this.waiting = [];
    this.current = null;
  }

  settle(reply) {
    const item = reply && this.pending.get(reply.id);
    if (!item) return;
    clearTimeout(item.timer);
    this.pending.delete(reply.id);
    this.done(reply.id);
    if (reply.ok) item.resolve(reply.result ?? null);
    else item.reject(new Error(reply.error || '앱 화면에서 처리하지 못했습니다.'));
  }

  // The next command waiting its turn goes to the screen, once the screen has answered the one before.
  next() {
    while (!this.current && this.waiting.length) {
      const id = this.waiting.shift();
      const item = this.pending.get(id);
      // Given up while it waited: its caller has been told it failed, so it is never sent.
      if (!item) continue;
      this.current = id;
      try {
        item.send();
      } catch {
        clearTimeout(item.timer);
        this.pending.delete(id);
        this.current = null;
        item.reject(new ApiError(503, 'unavailable', '앱 창이 열려 있지 않습니다.'));
      }
    }
  }

  done(id) {
    if (this.current !== id) return;
    this.current = null;
    this.next();
  }

  live() {
    const win = this.window();
    if (!win || win.isDestroyed())
      throw new ApiError(503, 'unavailable', '앱 창이 열려 있지 않습니다.');
    return win;
  }

  command(type, params = {}, timeout = this.timeout) {
    const win = this.live();
    if (!this.ready)
      return Promise.reject(
        new ApiError(
          503,
          'unavailable',
          '앱 화면이 아직 준비되지 않았습니다. 잠시 후 다시 시도해 주세요.',
        ),
      );
    const id = randomUUID();
    const answer = new Promise((resolve, reject) => {
      // The time counts from the call, also while the command waits its turn.
      const timer = setTimeout(() => {
        this.pending.delete(id);
        this.done(id);
        reject(
          new ApiError(
            503,
            'unavailable',
            '앱 화면이 응답하지 않습니다. 잠시 후 다시 시도해 주세요.',
          ),
        );
      }, timeout);
      this.pending.set(id, {
        resolve,
        reject,
        timer,
        send: () => win.webContents.send('control:command', { id, type, params }),
      });
    });
    this.waiting.push(id);
    this.next();
    return answer;
  }

  windowState() {
    const win = this.window();
    if (!win || win.isDestroyed()) return null;
    return {
      visible: win.isVisible(),
      focused: win.isFocused(),
      minimized: win.isMinimized(),
      maximized: win.isMaximized(),
      fullscreen: win.isFullScreen(),
      bounds: win.getBounds(),
      zoom: win.webContents.getZoomFactor(),
    };
  }

  // What the user is looking at, or null while the screen cannot say.
  async screen() {
    try {
      return await this.command('state', {}, 2000);
    } catch {
      return null;
    }
  }

  // Whether the user has changed something on the screen and not saved it, which closing the window would throw away.
  // A screen that cannot say (still loading, not answering) holds nothing back.
  async unsaved() {
    try {
      if (!this.ready) return false;
      const state = await this.command('unsaved', {}, Math.min(this.timeout, 1500));
      return state?.unsaved === true;
    } catch {
      return false;
    }
  }

  setWindow(params) {
    const win = this.live();
    const action = params.action;
    if (!WINDOW_ACTIONS.includes(action))
      throw new Error(`action: ${WINDOW_ACTIONS.join(', ')} 중 하나로 입력해 주세요.`);
    if (action === 'show') {
      if (win.isMinimized()) win.restore();
      win.show();
      win.moveTop();
      win.focus();
    } else if (action === 'minimize') win.minimize();
    else if (action === 'maximize') win.maximize();
    else if (action === 'restore') {
      if (win.isFullScreen()) win.setFullScreen(false);
      if (win.isMinimized()) win.restore();
      else if (win.isMaximized()) win.unmaximize();
    } else if (action === 'fullscreen') win.setFullScreen(true);
    else if (action === 'set_bounds') {
      const current = win.getBounds();
      const bounds = {
        width: optionalInteger(params.width, 'width', this.minWidth, 10000) ?? current.width,
        height: optionalInteger(params.height, 'height', this.minHeight, 10000) ?? current.height,
        x: optionalInteger(params.x, 'x', -10000, 10000) ?? current.x,
        y: optionalInteger(params.y, 'y', -10000, 10000) ?? current.y,
      };
      if (win.isMaximized()) win.unmaximize();
      win.setBounds(bounds);
    } else if (action === 'set_zoom') {
      if (typeof params.zoom !== 'number' || !(params.zoom >= 0.5 && params.zoom <= 2))
        throw new Error('zoom: 0.5~2 사이의 숫자로 입력해 주세요.');
      win.webContents.setZoomFactor(params.zoom);
    }
    return this.windowState();
  }

  async screenshot(params) {
    const win = this.live();
    const width = optionalInteger(params.max_width, 'max_width', 320, 3840) ?? 1280;
    if (win.isMinimized() || !win.isVisible())
      throw new Error(
        '창이 최소화되었거나 숨겨져 있어 캡처할 수 없습니다. app.window의 show로 창을 연 뒤 다시 시도해 주세요.',
      );
    // Passwords, keys and LLM header values never show in a capture, also while their eye button shows them: the
    // screen draws them as dots for it, and as before afterwards. A screen that cannot say it did so (one that does not
    // answer) is not captured; one still loading has none to show.
    const mask = this.ready ? await this.command('mask_secrets') : { secrets: 0 };
    try {
      if (mask?.secrets && !mask.painted)
        throw new Error(
          '비밀번호·키 입력란을 가린 화면을 그리지 못해 캡처하지 않았습니다. app.window의 show로 창을 앞으로 가져온 뒤 다시 시도해 주세요.',
        );
      let image = await win.webContents.capturePage();
      if (image.isEmpty())
        throw new Error('화면을 캡처하지 못했습니다. 창을 연 뒤 다시 시도해 주세요.');
      if (image.getSize().width > width) image = image.resize({ width, quality: 'good' });
      const size = image.getSize();
      return {
        mime: 'image/png',
        width: size.width,
        height: size.height,
        data: image.toPNG().toString('base64'),
      };
    } finally {
      // Also when the window has gone meanwhile (the screen then draws them again by itself).
      if (mask?.secrets)
        Promise.resolve()
          .then(() => this.command('unmask_secrets'))
          .catch(() => {});
    }
  }

  async notify(params) {
    const message = text(params.message, 'message', 500);
    if (params.system === true && this.Notification.isSupported()) {
      const notification = new this.Notification({ title: 'Private Agent', body: message });
      // Kept until it closes, so the notification is not collected while it is shown.
      this.notifications.add(notification);
      const forget = () => this.notifications.delete(notification);
      notification.once('close', forget);
      notification.once('click', () => {
        forget();
        void this.setWindow({ action: 'show' });
      });
      setTimeout(forget, 300_000).unref?.();
      notification.show();
      return { shown: 'system' };
    }
    await this.command('toast', { message, error: params.error === true });
    return { shown: 'app' };
  }

  // One app.* method of the local API. Clicking and typing on the admin page, or into a password, would let a tool
  // change what its access level keeps from it, so the renderer refuses those unless the level is "full".
  async perform(method, params, { access } = {}) {
    switch (method) {
      case 'app.window':
        return this.setWindow(params);
      case 'app.screenshot':
        return this.screenshot(params);
      case 'app.notify':
        return this.notify(params);
      case 'app.reload': {
        const win = this.live();
        // What the user writes is never thrown away: the screen refuses while a document is being written or a form
        // holds unsaved changes. A screen that does not answer is reloaded all the same: that is how it recovers. The
        // new screen detaches the old one as it starts loading.
        if (this.ready)
          await this.command('can_reload', {}, Math.min(this.timeout, 3000)).catch((error) => {
            if (!(error instanceof ApiError)) throw error;
          });
        win.webContents.reload();
        return { ok: true };
      }
      case 'app.navigate': {
        const view = params.view;
        if (!VIEWS.includes(view))
          throw new Error(`view: ${VIEWS.join(', ')} 중 하나로 입력해 주세요.`);
        const tab = text(params.tab, 'tab', 40, false);
        if (tab && !(TABS[view] ?? []).includes(tab))
          throw new Error(
            TABS[view]
              ? `tab: ${view} 화면의 탭은 ${TABS[view].join(', ')}입니다.`
              : `tab: ${view} 화면에는 탭이 없습니다.`,
          );
        const section = text(params.section, 'section', 40, false);
        if (section && (view !== 'admin' || !ADMIN_SECTIONS.includes(section)))
          throw new Error(
            `section: view가 admin일 때 ${ADMIN_SECTIONS.join(', ')} 중 하나로 입력해 주세요.`,
          );
        return this.command('navigate', { view, tab, section });
      }
      case 'app.search':
        return this.command('search', { query: text(params.query, 'query', 2000) });
      case 'app.open_document': {
        const terms = params.terms ?? [];
        if (
          !Array.isArray(terms) ||
          terms.length > 20 ||
          !terms.every((term) => typeof term === 'string')
        )
          throw new Error('terms: 글 목록(최대 20개)으로 입력해 주세요.');
        return this.command('open_document', { id: text(params.id, 'id', 80), terms });
      }
      case 'app.open_run':
        return this.command('open_run', { id: text(params.id, 'id', 80) });
      case 'app.new_document':
        return this.command('new_document', {
          title: text(params.title, 'title', 200, false),
          content: text(params.content, 'content', 80_000, false),
        });
      case 'app.close_reader':
        return this.command('close_reader');
      case 'app.read_screen':
        return this.command('read_screen');
      case 'app.click': {
        if (params.role !== undefined && !ROLES.includes(params.role))
          throw new Error(`role: ${ROLES.join(', ')} 중 하나로 입력해 주세요.`);
        return this.command('click', {
          name: text(params.name, 'name', 200),
          role: params.role ?? '',
          exact: params.exact === true,
          index: optionalInteger(params.index, 'index', 0, 1000) ?? null,
          allow_admin: access === 'full',
        });
      }
      case 'app.fill':
        return this.command('fill', {
          label: text(params.label, 'label', 200),
          value: text(params.value, 'value', 80_000, false),
          submit: params.submit === true,
          allow_admin: access === 'full',
        });
      default:
        throw new ApiError(404, 'not_found', `없는 메서드입니다: ${method}`);
    }
  }

  stop() {
    this.detach();
    for (const notification of this.notifications) notification.close();
    this.notifications.clear();
  }
}

module.exports = { AppControl };
