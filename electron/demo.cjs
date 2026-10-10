const fs = require('node:fs/promises');
const path = require('node:path');
const {
  CaptionTrack,
  merge,
  toSrt,
  parseSrt,
  fastForward,
  playedLength,
  durationText,
} = require('./captions.cjs');
const mp4 = require('./mp4.cjs');
const { FILES, MAX_CAPTION_BYTES, createRunFolder } = require('./demo-plan.cjs');

// A demo recording (record-demo.cmd): the app works through a list of files, folders and links on this PC as a
// person would, through its own screens, while the window is recorded and captions say what it is doing. Then the
// video is made from the recording: a title card, the captions, the long waits played faster and an end card.
//
// 1. The Wiki: the storage screen is filled in with the demo's own Wiki folder, which was emptied first.
// 2. The imports: each path or link goes into the import box, which starts its import; the captions follow the job.
// 3. The results: the document list, a few of the documents and, after AI analysis, the knowledge graph.

const ACTIVE = ['scanning', 'login', 'running', 'cancelling'];
const INPUT = '가져올 링크 또는 경로';
const START = 'Wiki로 가져오기';
const ANALYZE = '가져온 뒤 LLM으로';
// The import box's buttons for the kind of files a folder import reads.
const FOLDER_OPTIONS = {
  office: 'Office 문서',
  rtf: 'RTF 문서(.rtf)',
  image: '이미지',
  text: '텍스트(.txt)',
};
const KIND_TEXT = {
  office: 'Office 문서',
  rtf: 'RTF 문서(.rtf)',
  image: '이미지',
  text: '텍스트 파일(.txt)',
  markdown: 'Markdown 파일',
  confluence: 'Confluence 페이지',
  jira: 'JIRA 티켓',
};
// How long a run waits for an import before stopping it.
const IMPORT_LIMIT = 60 * 60_000;
// How long the window may take to be drawn at full-screen size before the recording starts all the same, how often it
// is looked at meanwhile, and how long one look at a frame of the screen may take.
const FULL_SCREEN_LIMIT = 15_000;
const FULL_SCREEN_EVERY = 150;
const FRAME_LIMIT = 5000;
const INTRO_MS = 3500;
const OUTRO_MS = 4500;
const MAX_SHOWN = 3;
const CLOSED = '앱 창이 닫혀 시연을 멈췄습니다.';

class Interrupted extends Error {
  constructor() {
    super('시연을 중지했습니다.');
    this.name = 'Interrupted';
  }
}

// `--demo <list>` records a demo; `--demo-render <folder or list>` makes its video again from the recording and its
// captions as they are now. A relative path stays as given, for the app to refuse: the launcher exe starts the app in
// its build folder, so the list, the demo's Wiki and its videos would go there, and the next build deletes that folder.
function demoLaunch(argv) {
  for (let index = 0; index < argv.length; index++) {
    for (const [flag, kind] of [
      ['--demo', 'record'],
      ['--demo-render', 'render'],
    ]) {
      const arg = argv[index];
      const value =
        arg === flag
          ? argv[index + 1]
          : arg.startsWith(`${flag}=`)
            ? arg.slice(flag.length + 1)
            : null;
      if (value && !value.startsWith('--')) {
        const given = value.replace(/^"(.*)"$/, '$1');
        return { kind, path: path.isAbsolute(given) ? path.resolve(given) : given };
      }
      if (arg === flag) return { kind, path: '' };
    }
  }
  return null;
}

const jobOf = (status) =>
  status && typeof status === 'object' && 'job' in status ? status.job : status;
const short = (text, size = 90) => {
  const line = String(text || '')
    .replace(/\s+/g, ' ')
    .trim();
  return line.length > size ? `${line.slice(0, size - 1)}…` : line;
};
const baseName = (value) =>
  value
    ? value
        .replace(/[\\/]+$/, '')
        .split(/[\\/]/)
        .pop()
    : '';
const pageName = (value) => {
  try {
    const url = new URL(value);
    return decodeURIComponent(url.pathname.split('/').filter(Boolean).at(-1) ?? '').replace(
      /\+/g,
      ' ',
    );
  } catch {
    return '';
  }
};
const even = (value) => Math.max(2, Math.round(value / 2) * 2);
// Two sizes in DIPs that are the same but for rounding, as at a display scale of 125%.
const near = (a, b) => Math.abs(a - b) <= 2;

// What `promise` settles with, or `fallback` once `ms` have passed.
function within(promise, ms, fallback) {
  let timer;
  return Promise.race([
    promise,
    new Promise((resolve) => {
      timer = setTimeout(resolve, ms, fallback);
      timer.unref?.();
    }),
  ]).finally(() => clearTimeout(timer));
}

function describe(item, kind) {
  if (item.type === 'folder')
    return `폴더 · ${item.name} — ${KIND_TEXT[kind]} ${item.counts[kind].toLocaleString('ko-KR')}개`;
  if (kind === 'office') return `${item.application ?? 'Office'} 문서 · ${item.name}`;
  return `${KIND_TEXT[kind]} · ${item.name}`;
}

// What a running job is doing, in a few words.
function progressText(kind, job, item) {
  if (job.status === 'login') return '저장한 계정으로 로그인하는 중';
  if (job.status === 'cancelling') return '중지하는 중';
  if (job.status === 'scanning')
    return kind === 'jira' || kind === 'confluence'
      ? '사이트에 연결하는 중'
      : '가져올 파일을 찾는 중';
  const what = {
    office: `${item.type === 'file' && item.application ? `${item.application}로` : 'Office 앱으로'} 열어 HTML 문서로 바꾸는 중`,
    rtf: 'Word로 RTF 문서를 열어 HTML 문서로 바꾸는 중',
    text: '메모장으로 읽어 HTML 문서로 바꾸는 중',
    image: '그림을 HTML 문서로 저장하는 중',
    confluence: '페이지를 Markdown 문서로 저장하는 중',
    jira: '티켓을 Markdown 문서로 저장하는 중',
  }[kind];
  const count = job.total > 1 ? ` ${job.processed}/${job.total}` : '';
  const current = baseName(job.current_file) || pageName(job.current_url);
  return `${item.caption || what}${count}${current ? ` · ${current}` : ''}`;
}

// What an imported document was made from, and into what.
function savedAs(doc) {
  const source = doc.source ?? {};
  return (
    {
      office: `${source.application ?? 'Office'} 문서를 서식과 그림이 담긴 HTML 문서로 저장했습니다`,
      rtf: 'RTF 문서(.rtf)를 HTML 문서로 저장했습니다',
      image: '그림을 HTML 문서로 저장했습니다',
      text: '텍스트 파일을 메모장으로 읽어 HTML 문서로 저장했습니다',
      markdown: 'Markdown 파일을 Wiki 문서로 저장했습니다',
      confluence: 'Confluence 페이지를 Markdown 문서로 저장했습니다',
      jira: 'JIRA 티켓을 Markdown 문서로 저장했습니다',
    }[source.kind] ?? '가져온 문서입니다'
  );
}

function doneText(job) {
  if (job.status === 'cancelled') return '가져오기를 중지했습니다';
  if (job.status !== 'completed') return `가져오지 못했습니다 · ${short(job.error) || '오류'}`;
  const parts = [`완료 · 문서 ${job.imported}개 저장`];
  if (job.updated) parts.push(`업데이트 ${job.updated}`);
  if (job.skipped) parts.push(`이미 있음 ${job.skipped}`);
  if (job.failed) parts.push(`실패 ${job.failed}`);
  if (job.analysis_failed) parts.push(`AI 분석 실패 ${job.analysis_failed}`);
  return parts.join(' · ');
}

const docsOf = (results = []) =>
  results.flatMap((result) => result.doc_ids ?? (result.doc_id ? [result.doc_id] : []));

function counts(runs) {
  const total = { imported: 0, updated: 0, skipped: 0, failed: 0 };
  for (const run of runs) {
    const job = run.job;
    if (job) for (const key of Object.keys(total)) total[key] += job[key] || 0;
    else if (run.result)
      total[
        run.result.status === 'updated'
          ? 'updated'
          : run.result.status === 'skipped'
            ? 'skipped'
            : 'imported'
      ]++;
    else if (run.error) total.failed++;
  }
  return total;
}

const dateText = (date) =>
  new Intl.DateTimeFormat('ko-KR', { dateStyle: 'long', timeStyle: 'short' }).format(date);
const timeText = (date) => new Intl.DateTimeFormat('ko-KR', { timeStyle: 'short' }).format(date);

// The video of a recording: its size (at most 1920 wide), captions, cards and sped-up parts, and how long it lasts.
function renderSpec({ project, cues, video, pace = 1 }) {
  const width = even(Math.min(1920, video.width));
  const height = even((video.height * width) / video.width);
  const speedups = Array.isArray(project.fast_forward)
    ? project.fast_forward
        .filter(
          (part) =>
            Number.isFinite(part?.start) &&
            Number.isFinite(part?.end) &&
            part.end > part.start &&
            [2, 4, 8, 16].includes(part.speed),
        )
        .map(({ start, end, speed }) => ({ start, end, speed }))
    : [];
  const lines = (value) =>
    Array.isArray(value) ? value.filter((line) => typeof line === 'string').slice(0, 4) : [];
  const introMs = Math.round(INTRO_MS * pace);
  const outroMs = Math.round(OUTRO_MS * pace);
  return {
    width,
    height,
    cues: merge(cues),
    speedups,
    intro: { title: String(project.title || 'Private Agent 시연'), lines: lines(project.intro) },
    outro: {
      title: String(project.outro_title || '시연을 마쳤습니다'),
      lines: lines(project.outro),
    },
    introMs,
    outroMs,
    expected: Math.max(1, introMs + playedLength(video.duration * 1000, speedups) + outroMs),
  };
}

class DemoRun {
  /**
   * @param {{
   *   log?: (text: string) => void,
   *   display?: ((win: any) => Electron.Rectangle) | null,
   *   [option: string]: any,
   * }} options
   */
  constructor({
    plan,
    window,
    control,
    rpc,
    storage,
    recorder,
    pace = 1,
    now = Date.now,
    sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    log = () => {},
    // The bounds of the display a window is on (Electron's screen), which a full-screen window covers. Without it the
    // recording starts as soon as the screen is ready.
    display = null,
  }) {
    this.plan = plan;
    this.window = window;
    this.control = control;
    this.rpc = rpc;
    this.storage = storage;
    this.recorder = recorder;
    this.pace = pace;
    this.now = now;
    this.sleep = sleep;
    this.log = log;
    this.display = display;
    this.stopped = false;
    this.quitting = false;
    this.reason = null;
    this.ending = false;
    this.expecting = null;
    this.capture = null;
    this.track = null;
    this.entries = [];
  }

  // The panel's stop button, or the app closing: what was recorded so far still becomes a video, unless the app is
  // quitting. `reason`: what ended the demo when no one asked, such as the app's window closing.
  /** @param {{ quitting?: boolean, reason?: Error | null }} [options] */
  abort({ quitting = false, reason = null } = {}) {
    this.stopped = true;
    if (quitting) this.quitting = true;
    if (reason) this.reason ??= reason;
  }

  // Every request the screens made, so a click learns what it started: the result, or the error the screen shows.
  observe(method, result, error) {
    const waiting = this.expecting;
    if (!waiting || !waiting.methods.includes(method)) return;
    this.expecting = null;
    if (error) waiting.reject(error);
    else waiting.resolve(result);
  }

  check() {
    if (this.stopped) throw new Interrupted();
  }

  async hold(ms) {
    const end = this.now() + ms * this.pace;
    while (this.now() < end) {
      this.check();
      await this.sleep(Math.min(200, end - this.now()));
    }
    this.check();
  }

  async waitFor(test, { timeout, every = 300, message }) {
    const end = this.now() + timeout;
    for (;;) {
      this.check();
      const value = await test();
      if (value) return value;
      if (this.now() >= end) throw new Error(message);
      await this.sleep(every);
    }
  }

  caption(text, { label = '', wait = false } = {}) {
    this.track?.show(text, { label, wait });
    this.capture?.status({ label, text });
  }

  command(type, params = {}, timeout) {
    this.check();
    return this.control.command(type, params, timeout);
  }

  screen() {
    return this.command('read_screen');
  }

  async findControl(test) {
    return (await this.screen()).controls.find(test) ?? null;
  }

  // As a person would: the field or button lights up for a moment so viewers see what was done.
  fill(label, value) {
    return this.command('fill', { label, value, allow_admin: true, show: true });
  }

  click(name, role) {
    return this.command('click', { name, role, exact: true, allow_admin: true, show: true });
  }

  async setCheck(test, wanted) {
    const box = await this.findControl((item) => item.role === 'checkbox' && test(item.name));
    if (box && !box.disabled && box.checked !== wanted) await this.click(box.name, 'checkbox');
    return box;
  }

  // Does `action` (a click) and returns what the screens' request among `methods` answered, or throws its error.
  async expect(methods, action, timeout) {
    let settled = false;
    /** @type {any} */
    let value;
    let failure;
    new Promise((resolve, reject) => {
      this.expecting = { methods, resolve, reject };
    }).then(
      (result) => {
        settled = true;
        value = result;
      },
      (error) => {
        settled = true;
        failure = error;
      },
    );
    try {
      await action();
      await this.waitFor(() => settled, {
        timeout,
        every: 100,
        message: '앱 화면이 가져오기를 시작하지 않았습니다.',
      });
    } finally {
      this.expecting = null;
    }
    if (failure) throw failure;
    return value;
  }

  async prepare() {
    const win = await this.waitFor(
      () => {
        const win = this.window();
        return win && !win.isDestroyed() && win.isVisible() ? win : null;
      },
      { timeout: 90_000, message: '앱 창이 열리지 않았습니다.' },
    );
    win.setFullScreen(true);
    await this.waitFor(() => this.control.ready, {
      timeout: 90_000,
      message: '앱 화면이 준비되지 않았습니다.',
    });
    await this.waitFor(
      async () => !!(await this.findControl((item) => item.name === 'Wiki 저장 폴더')),
      { timeout: 90_000, message: 'Wiki 저장 위치 화면이 열리지 않았습니다.' },
    );
    await this.settleFullScreen(win);
  }

  // The recording starts once the screen is drawn at full-screen size, since a recording whose frames change size
  // partway cannot be made into a video: its player stops at the change with a decoding error, and the demo ends without
  // one. The window calls itself full screen as soon as it is asked, while an X11 window manager resizes it later, and a
  // busy PC draws the first frames at the new size seconds after that (WSL, 2026-10-10). So the size is read off a frame
  // of the screen itself, twice in a row. A screen that does not get there in time is recorded as it is.
  async settleFullScreen(win) {
    if (!this.display) return;
    const end = this.now() + FULL_SCREEN_LIMIT;
    let drawn = 0;
    while (this.now() < end) {
      this.check();
      // A window closed meanwhile is the recording's to report.
      if (win.isDestroyed()) return;
      drawn = (await this.drawnFullScreen(win)) ? drawn + 1 : 0;
      if (drawn === 2) return;
      await this.sleep(FULL_SCREEN_EVERY);
    }
    this.log(
      `시연 창이 ${FULL_SCREEN_LIMIT / 1000}초 안에 전체 화면 크기로 그려지지 않아 그대로 녹화를 시작했습니다.`,
    );
  }

  async drawnFullScreen(win) {
    if (win.isDestroyed() || !win.isFullScreen()) return false;
    const [width, height] = win.getContentSize();
    // Asked only once the display is known (settleFullScreen).
    const display = /** @type {(win: any) => Electron.Rectangle} */ (this.display)(win);
    if (!near(width, display.width) || !near(height, display.height)) return false;
    const frame = await within(
      win.webContents.capturePage().then((image) => image.getSize()),
      FRAME_LIMIT,
      null,
    ).catch(() => null);
    return !!frame && near(frame.width, width) && near(frame.height, height);
  }

  async initializeStorage() {
    const label = '1단계 · 저장소 초기화';
    this.caption('시연에 쓸 Wiki 저장소를 비우고 새로 만듭니다', { label });
    await this.hold(1800);
    await this.fill('Wiki 저장 폴더', this.plan.wiki);
    await this.hold(1500);
    this.caption('빈 폴더에 새 Wiki 저장소를 준비하는 중', { label, wait: true });
    await this.expect(
      ['storage.initialize'],
      () => this.click('이 위치에 저장하고 시작', 'button'),
      150_000,
    );
    await this.waitFor(
      async () => !!(await this.findControl((item) => item.name === '위젯 편집')),
      { timeout: 90_000, message: 'Wiki를 연 뒤 홈 화면이 열리지 않았습니다.' },
    );
    this.caption('빈 Wiki가 준비되었습니다 · 문서 0개', { label });
    await this.hold(2600);
  }

  async importAll() {
    const items = this.plan.items;
    await this.command('navigate', { view: 'library', tab: 'import' });
    this.caption(`목록의 자료 ${items.length}건을 차례로 Wiki로 가져옵니다`, {
      label: '2단계 · 자료 가져오기',
    });
    await this.hold(2600);
    for (const [index, item] of items.entries()) {
      /** @type {{ item: any, runs: any[] }} */
      const entry = { item, runs: [] };
      this.entries.push(entry);
      const label = `2단계 · 자료 가져오기 ${index + 1}/${items.length}`;
      for (const kind of item.type === 'folder' ? item.kinds : [item.kind]) {
        const run = { kind, started: this.now() };
        entry.runs.push(run);
        try {
          await this.importRun(item, kind, label, run);
        } catch (error) {
          if (error instanceof Interrupted) throw error;
          run.error = error.message;
          this.caption(`가져오지 못했습니다 · ${short(error.message)}`, { label });
          await this.hold(3500);
        } finally {
          run.ended = this.now();
        }
      }
    }
  }

  async importRun(item, kind, label, run) {
    this.caption(item.caption || describe(item, kind), { label });
    // A kind this PC cannot read (no Office app, no Notepad) is said without trying.
    if (FOLDER_OPTIONS[kind]) {
      const capability = await this.rpc(`${kind}.capabilities`).catch(() => null);
      if (capability && !capability.available)
        throw new Error(capability.reason || '이 PC에서 가져올 수 없는 종류의 파일입니다.');
    }
    // A folder's path ends with a separator, so the import box takes it as a folder even with a dot in its name.
    const value =
      item.type === 'folder' ? item.source.replace(/[\\/]+$/, '') + path.sep : item.source;
    const field = await this.findControl((control) => control.name === INPUT);
    if (field?.value !== value) {
      await this.fill(INPUT, value);
      await this.hold(1300);
    }
    if (item.type === 'folder') {
      await this.click(FOLDER_OPTIONS[kind], 'option');
      await this.setCheck((name) => name === '하위 폴더 포함', this.plan.subfolders);
    }
    if (kind === 'confluence')
      await this.setCheck((name) => name === '하위 페이지까지 모두 가져오기', this.plan.subpages);
    const analyze = await this.setCheck((name) => name.startsWith(ANALYZE), this.plan.analyze);
    if (this.plan.analyze && analyze?.disabled)
      run.notice = 'LLM이 연결되지 않아 AI 분석 없이 가져왔습니다.';
    await this.hold(900);
    if (kind === 'markdown') {
      this.caption(item.caption || `Markdown 파일을 Wiki 문서로 저장하는 중 · ${item.name}`, {
        label,
        wait: true,
      });
      const result = await this.expect(
        ['files.import_text'],
        () => this.click(START, 'button'),
        180_000,
      );
      run.result = result;
      run.docs = docsOf([result]);
      this.caption(`완료 · ${short(result.message)}`, { label });
      await this.hold(2400);
      return;
    }
    // The button waits for the check of the Office app or Notepad the import needs.
    await this.waitFor(
      async () => {
        const button = await this.findControl(
          (item) => item.role === 'button' && item.name === START,
        );
        return button && !button.disabled;
      },
      { timeout: 20_000, message: '‘Wiki로 가져오기’를 누를 수 없습니다.' },
    );
    const started = jobOf(
      await this.expect([`${kind}.start`], () => this.click(START, 'button'), 60_000),
    );
    if (!started?.id) throw new Error('가져오기를 시작하지 못했습니다.');
    const job = await this.follow(kind, started.id, item, label);
    run.job = job;
    run.docs = docsOf(job.results);
    this.caption(doneText(job), { label });
    await this.hold(job.status === 'completed' && !job.failed ? 2400 : 4000);
  }

  // Follows the job until it ends, the captions saying how far it is. Stopping the demo stops the job too.
  async follow(kind, id, item, label) {
    const end = this.now() + IMPORT_LIMIT;
    let failures = 0;
    let missing = 0;
    for (;;) {
      let job = null;
      try {
        job = jobOf(await this.rpc(`${kind}.status`, { id }));
        failures = 0;
      } catch (error) {
        if (++failures >= 30)
          throw new Error(`가져오기 진행 상황을 읽지 못했습니다: ${error.message}`);
      }
      if (job?.id === id) {
        missing = 0;
        if (!ACTIVE.includes(job.status)) return job;
        this.caption(progressText(kind, job, item), { label, wait: true });
      } else if (job !== null || !failures) {
        if (++missing >= 10)
          throw new Error('가져오기 작업을 찾지 못했습니다. 백엔드가 다시 시작되었을 수 있습니다.');
      }
      const late = this.now() >= end;
      if (this.stopped || late) {
        await this.rpc(`${kind}.cancel`).catch(() => {});
        for (let attempt = 0; attempt < 30; attempt++) {
          const status = jobOf(await this.rpc(`${kind}.status`, { id }).catch(() => null));
          if (!status || status.id !== id || !ACTIVE.includes(status.status)) break;
          await this.sleep(500);
        }
        if (this.stopped) throw new Interrupted();
        throw new Error(`${IMPORT_LIMIT / 60_000}분 안에 끝나지 않아 중지했습니다.`);
      }
      await this.sleep(1000);
    }
  }

  // A few of the imported documents, one per kind of import, in the order of the list.
  showcase(documents) {
    const byId = new Map(documents.map((doc) => [doc.id, doc]));
    const shown = [];
    const kinds = new Set();
    for (const { runs } of this.entries)
      for (const run of runs) {
        const doc = (run.docs ?? []).map((id) => byId.get(id)).find(Boolean);
        if (!doc || kinds.has(run.kind) || shown.length >= MAX_SHOWN) continue;
        kinds.add(run.kind);
        shown.push(doc);
      }
    return shown;
  }

  async showResults() {
    const label = '3단계 · 결과 확인';
    await this.command('navigate', { view: 'library', tab: 'documents' });
    const documents = await this.rpc('wiki.list');
    this.caption(`Wiki에 문서 ${documents.length.toLocaleString('ko-KR')}개가 저장되었습니다`, {
      label,
    });
    await this.hold(3600);
    for (const doc of this.showcase(documents)) {
      await this.command('open_document', { id: doc.id, terms: [] });
      this.caption(`‘${short(doc.title, 50)}’ · ${savedAs(doc)}`, { label });
      await this.hold(2800);
      await this.command('scroll', { to: 0.45 });
      await this.hold(2400);
      await this.command('scroll', { to: 1 });
      await this.hold(2400);
      await this.command('close_reader');
      await this.hold(800);
    }
    if (this.plan.analyze && documents.some((doc) => doc.index_mode)) {
      await this.command('navigate', { view: 'library', tab: 'knowledge' });
      this.caption('AI 분석이 찾은 키워드로 문서 사이의 관계를 그렸습니다', { label });
      await this.hold(5500);
    }
    await this.command('navigate', { view: 'home' });
    this.caption('시연을 마칩니다', { label });
    await this.hold(2600);
  }

  async run() {
    const folder = await createRunFolder(this.plan.output);
    const part = path.join(folder, '원본 녹화.part');
    const startedAt = new Date();
    let failure = null;
    try {
      await this.prepare();
      // Closing the app's window ends the demo: the import it waits for is stopped, not followed to its end.
      this.window()?.once?.('closed', () => this.abort({ reason: new Error(CLOSED) }));
      this.capture = await this.recorder.record(this.window(), part, {
        onAbort: () => this.abort(),
      });
      // The recording ended by itself: its page failed, or the window it records closed. The demo stops there.
      this.capture.done.then(
        () => {
          if (!this.ending) this.abort({ reason: new Error(CLOSED) });
        },
        (error) => this.abort({ reason: error }),
      );
      const zero = this.capture.info.at;
      this.track = new CaptionTrack(() => this.now() - zero);
      await this.hold(1200);
      await this.initializeStorage();
      await this.importAll();
      await this.showResults();
    } catch (error) {
      if (!(error instanceof Interrupted)) failure ??= error;
      failure ??= this.reason;
      this.log(`시연이 멈췄습니다: ${error.stack || error.message}`);
      if (this.track && !this.quitting) {
        this.caption(
          failure ? `시연을 멈췄습니다 · ${short(failure.message)}` : '시연을 중지했습니다',
        );
        await this.sleep(2500 * this.pace);
      }
    }
    failure ??= this.reason;
    const endedAt = new Date();
    const cues = this.track?.finish() ?? [];
    let recording = null;
    if (this.capture) {
      this.ending = true;
      let stopped = null;
      try {
        stopped = await this.capture.stop();
      } catch (error) {
        // The recording did not end well (its page failed or hung, a part could not be written): what it wrote
        // before is kept, and the video is made of it all the same.
        failure ??= error;
      }
      const mime = stopped?.mime || this.capture.info.mime || '';
      try {
        recording = await this.keep({ file: part, mime }, folder);
      } catch (error) {
        failure ??= error;
        // What was recorded stays, as written: players still play it.
        await fs.rename(part, path.join(folder, named(FILES.raw, mime))).catch(() => {});
      }
    }
    await fs.rm(part, { force: true });
    /** @type {{ folder: string, video: string | null, failure: any, stopped: boolean, renderError?: Error }} */
    const outcome = { folder, video: null, failure, stopped: this.stopped && !failure };
    if (!recording) {
      await this.writeReport(folder, { startedAt, endedAt, recording, cues, outcome });
      return outcome;
    }
    const project = this.project({ startedAt, endedAt, cues, recording, outcome });
    await fs.writeFile(path.join(folder, FILES.captions), '﻿' + toSrt(cues));
    await fs.writeFile(path.join(folder, FILES.project), JSON.stringify(project, null, 2) + '\n');
    if (!this.quitting) {
      try {
        const video = path.join(folder, named(FILES.video, recording.mime));
        await makeVideo({
          recorder: this.recorder,
          source: recording.file,
          target: video,
          spec: renderSpec({ project, cues, video: recording, pace: this.pace }),
          mime: recording.mime,
        });
        outcome.video = video;
      } catch (error) {
        outcome.renderError = error;
      }
    }
    await this.writeReport(folder, { startedAt, endedAt, recording, cues, outcome, project });
    return outcome;
  }

  // The recording as it will be kept: an MP4 made plain (duration, seeking), or a WebM as it was written.
  async keep(result, folder) {
    const file = path.join(folder, named(FILES.raw, result.mime));
    if (!result.mime.includes('mp4')) {
      await fs.rename(result.file, file);
      return {
        file,
        mime: result.mime,
        ...this.capture.info,
        duration: (this.now() - this.capture.info.at) / 1000,
      };
    }
    const info = await mp4.finalize(result.file, file);
    return {
      file,
      mime: result.mime,
      width: info.width,
      height: info.height,
      duration: info.duration,
    };
  }

  project({ startedAt, endedAt, cues, recording, outcome }) {
    const items = this.plan.items;
    const kinds = new Map();
    for (const item of items) {
      const name =
        item.type === 'folder'
          ? '폴더'
          : item.kind === 'office'
            ? `${item.application} 문서`
            : KIND_TEXT[item.kind];
      kinds.set(name, (kinds.get(name) ?? 0) + 1);
    }
    const total = counts(this.entries.flatMap((entry) => entry.runs));
    const fast = this.plan.fastForward ? fastForward(cues) : [];
    const elapsed = endedAt - startedAt;
    return {
      version: 1,
      title: this.plan.title,
      intro: [
        `${dateText(startedAt)} · 실제 환경 시연`,
        `가져올 자료 ${items.length}건 · ${[...kinds].map(([name, count]) => `${name} ${count}`).join(' · ')}`,
      ],
      outro_title: outcome.failure || outcome.stopped ? '시연을 중지했습니다' : '시연을 마쳤습니다',
      outro: [
        `Wiki 문서 ${total.imported + total.updated}개를 만들었습니다${total.failed ? ` · 실패 ${total.failed}건` : ''}`,
        `실제로 걸린 시간 ${durationText(elapsed)}${fast.length ? ' · 기다린 구간은 빠르게 보여 드렸습니다' : ''}`,
      ],
      fast_forward: fast,
      video: { width: recording.width, height: recording.height, duration: recording.duration },
      recorded_at: startedAt.toISOString(),
      list: this.plan.list,
      wiki: this.plan.wiki,
    };
  }

  /**
   * @param {string} folder
   * @param {Record<string, any>} report What the run was; `project` only once there is a recording.
   */
  async writeReport(folder, { startedAt, endedAt, recording, cues, outcome, project }) {
    const lines = [
      'Private Agent 시연 녹화',
      `시각: ${dateText(startedAt)} ~ ${startedAt.toDateString() === endedAt.toDateString() ? timeText(endedAt) : dateText(endedAt)} (${durationText(endedAt - startedAt)})`,
      `목록: ${this.plan.list}`,
      `Wiki: ${this.plan.wiki} (녹화를 시작할 때 비움)`,
    ];
    if (outcome.video)
      lines.push(
        `영상: ${path.basename(outcome.video)} (${durationText(project ? renderSpec({ project, cues, video: recording }).expected : 0)}${project?.fast_forward.length ? `, 빨리 감기 ${project.fast_forward.length}곳` : ''})`,
      );
    if (recording)
      lines.push(
        `원본 녹화: ${path.basename(recording.file)} (${durationText(recording.duration * 1000)}), 자막: ${FILES.captions}`,
      );
    if (outcome.failure) lines.push(`멈춘 이유: ${outcome.failure.message}`);
    else if (outcome.stopped) lines.push('중지 버튼으로 녹화를 중지했습니다.');
    if (outcome.renderError) lines.push(`영상을 만들지 못했습니다: ${outcome.renderError.message}`);
    lines.push('');
    for (const [index, { item, runs }] of this.entries.entries()) {
      const what =
        item.type === 'folder'
          ? '폴더'
          : item.kind === 'office'
            ? `${item.application} 문서`
            : KIND_TEXT[item.kind];
      lines.push(`[${index + 1}] ${item.source} — ${what}`);
      if (item.type === 'folder' && item.counts.markdown)
        lines.push(
          `    Markdown 파일 ${item.counts.markdown}개는 폴더 가져오기가 읽지 않아 건너뛰었습니다. 파일 경로를 목록에 따로 적어 주세요.`,
        );
      for (const run of runs) {
        const prefix = item.type === 'folder' ? `${KIND_TEXT[run.kind]}: ` : '';
        const took = run.ended ? ` (${durationText(run.ended - run.started)})` : '';
        if (run.job) lines.push(`    ${prefix}${doneText(run.job)}${took}`);
        else if (run.result) lines.push(`    ${prefix}${run.result.message}${took}`);
        else if (run.error) lines.push(`    ${prefix}가져오지 못했습니다: ${run.error}`);
        if (run.notice) lines.push(`    ${run.notice}`);
        if (run.job?.diagnosis?.cause) lines.push(`    AI 원인 분석: ${run.job.diagnosis.cause}`);
        for (const result of run.job?.results ?? [])
          if (result.status === 'failed')
            lines.push(
              `      실패 ${result.path || result.url || result.key || ''} — ${result.message}`,
            );
      }
    }
    const remaining = this.plan.items.length - this.entries.length;
    if (remaining > 0) lines.push(`… 목록의 나머지 ${remaining}건은 가져오지 않았습니다.`);
    const total = counts(this.entries.flatMap((entry) => entry.runs));
    lines.push(
      '',
      `합계: 문서 ${total.imported}개 저장 · 업데이트 ${total.updated} · 이미 있음 ${total.skipped} · 실패 ${total.failed}`,
    );
    if (recording)
      lines.push(
        '',
        `자막을 고쳐 영상을 다시 만들려면: ${FILES.captions}를 메모장으로 고친 뒤 record-demo.cmd render "${folder}"`,
      );
    await fs.writeFile(path.join(folder, FILES.report), '﻿' + lines.join('\r\n') + '\r\n');
  }
}

// A file name of FILES with the extension of the recording's format.
const named = (file, mime) => (mime.includes('mp4') ? file : file.replace(/\.mp4$/, '.webm'));

// Draws the video from the recording and makes its MP4 plain, through a fragmented draft next to it.
/** @param {{ onProgress?: (value: number) => void, [option: string]: any }} options */
async function makeVideo({ recorder, source, target, spec, mime, onProgress }) {
  if (!mime.includes('mp4')) {
    await recorder.render({ ...spec, sourceFile: source }, target, { onProgress });
    return target;
  }
  const draft = `${target}.part`;
  try {
    const made = await recorder.render({ ...spec, sourceFile: source }, draft, { onProgress });
    if (made.mime.includes('mp4')) await mp4.finalize(draft, target);
    else await fs.rename(draft, target.replace(/\.mp4$/, '.webm'));
  } finally {
    await fs.rm(draft, { force: true });
  }
  return target;
}

// `record-demo.cmd render`: the video of a recording made again with its captions (the .srt) and edit settings (the
// .json) as they are now, possibly edited by hand.
async function renderAgain(folder, { recorder, readText }) {
  const raw = path.join(folder, FILES.raw);
  const exists = async (file) => !!(await fs.stat(file).catch(() => null));
  const source = (await exists(raw)) ? raw : raw.replace(/\.mp4$/, '.webm');
  if (!(await exists(source))) throw new Error(`원본 녹화를 찾지 못했습니다: ${raw}`);
  let project = {};
  try {
    project = JSON.parse(await readText(path.join(folder, FILES.project), '편집 정보 파일'));
  } catch (error) {
    if (!/찾을 수 없습니다/.test(error.message))
      throw new Error(`${FILES.project}를 읽지 못했습니다: ${error.message}`);
  }
  // Captions of a long demo are larger than a list may be (about 700 KB an hour of progress captions).
  const cues = parseSrt(
    await readText(path.join(folder, FILES.captions), '자막 파일', { limit: MAX_CAPTION_BYTES }),
  );
  const mime = source.endsWith('.mp4') ? 'video/mp4' : 'video/webm';
  const info = mime === 'video/mp4' ? await mp4.inspect(source) : project.video;
  if (!info?.width || !info?.height || !info?.duration)
    throw new Error('원본 녹화의 화면 크기와 길이를 알 수 없습니다.');
  const target = path.join(folder, named(FILES.video, mime));
  await makeVideo({
    recorder,
    source,
    target,
    spec: renderSpec({ project, cues, video: info }),
    mime,
  });
  return target;
}

module.exports = {
  DemoRun,
  Interrupted,
  demoLaunch,
  renderSpec,
  renderAgain,
  progressText,
  describe,
};
