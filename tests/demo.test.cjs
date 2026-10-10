const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {
  DemoRun,
  demoLaunch,
  renderSpec,
  renderAgain,
  progressText,
} = require('../electron/demo.cjs');
const { FILES, MAX_CAPTION_BYTES } = require('../electron/demo-plan.cjs');
const { finalize, inspect } = require('../electron/mp4.cjs');
const { parseSrt } = require('../electron/captions.cjs');
const { recording } = require('./fixtures/mp4.cjs');

const ANALYZE = '가져온 뒤 LLM으로 요약·키워드 분석';
const LOCAL = ['office', 'rtf', 'image', 'text'];

test('the demo is started by its own arguments, anywhere among those Chromium adds', () => {
  const list = path.resolve('/시연/list.txt');
  assert.deepEqual(demoLaunch(['app.exe', '--inspect=0', '--demo', list]), {
    kind: 'record',
    path: list,
  });
  assert.deepEqual(demoLaunch(['app.exe', `--demo-render=${list}`]), {
    kind: 'render',
    path: list,
  });
  assert.deepEqual(demoLaunch(['app.exe', '--demo', '"' + list + '"']), {
    kind: 'record',
    path: list,
  });
  // A second instance's arguments may come with the flag alone.
  assert.deepEqual(demoLaunch(['app.exe', '--demo', '--allow-file-access']), {
    kind: 'record',
    path: '',
  });
  assert.equal(demoLaunch(['app.exe', '.']), null);
  // A relative path stays as given, for the app to refuse: the launcher starts the app in its build folder.
  assert.deepEqual(demoLaunch(['app.exe', '--demo', 'demo-list.txt']), {
    kind: 'record',
    path: 'demo-list.txt',
  });
  assert.deepEqual(demoLaunch(['app.exe', '--demo-render=시연 영상']), {
    kind: 'render',
    path: '시연 영상',
  });
});

test('the video is at most 1920 wide with even sides, and keeps only the speed-ups it can play', () => {
  const spec = renderSpec({
    project: {
      title: '시연',
      intro: ['날짜', 3],
      fast_forward: [
        { start: 1000, end: 9000, speed: 4 },
        { start: 9000, end: 8000, speed: 4 },
        { start: 10_000, end: 20_000, speed: 3 },
      ],
    },
    cues: [
      { start: 0, end: 500, text: 'a', label: '', wait: false },
      { start: 500, end: 900, text: 'a', label: '', wait: true },
    ],
    video: { width: 2561, height: 1441, duration: 30 },
    pace: 0.5,
  });
  assert.equal(spec.width, 1920);
  assert.equal(spec.height, 1080);
  assert.deepEqual(spec.speedups, [{ start: 1000, end: 9000, speed: 4 }]);
  assert.deepEqual(spec.cues, [{ start: 0, end: 900, text: 'a', label: '' }]);
  assert.deepEqual(spec.intro, { title: '시연', lines: ['날짜'] });
  assert.equal(spec.outro.title, '시연을 마쳤습니다');
  assert.equal(spec.expected, 1750 + (30_000 - 6000) + 2250);
});

test('captions name what a job is doing', () => {
  const item = { type: 'file', kind: 'office', application: 'Excel', name: '표.xlsx', caption: '' };
  const job = { status: 'running', total: 3, processed: 1, current_file: 'D:\\자료\\표.xlsx' };
  assert.equal(
    progressText('office', job, item),
    'Excel로 열어 HTML 문서로 바꾸는 중 1/3 · 표.xlsx',
  );
  assert.equal(
    progressText('office', { ...job, total: 1, current_file: '' }, { ...item, type: 'folder' }),
    'Office 앱으로 열어 HTML 문서로 바꾸는 중',
  );
  assert.equal(progressText('jira', { status: 'login' }, item), '저장한 계정으로 로그인하는 중');
  assert.equal(
    progressText(
      'confluence',
      {
        status: 'running',
        total: 4,
        processed: 2,
        current_url: 'https://w/display/T/%ED%8C%80+%EC%9C%84%ED%82%A4',
      },
      { caption: '팀 위키' },
    ),
    '팀 위키 2/4 · 팀 위키',
  );
});

// The app as the demo sees it: the screens it reads and works, the backend it asks, a clock that runs only when the
// demo waits, and a recorder that writes small recordings. `stop(file, seconds)` ends the recording another way, and
// `endRecording()` ends it by itself, as when the app's window closes. `frames(look)` is the size of the screen's frame
// at each look the demo takes (1, 2, …), or nothing for the full-screen size.
async function harness(
  t,
  {
    items,
    plan = {},
    llm = true,
    capabilities = {},
    startErrors = {},
    polls = {},
    onPoll,
    stop,
    frames = () => null,
  },
) {
  const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'agent-demo-run-')));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  let clock = 1_000_000;
  const screen = {
    view: 'setup',
    input: '',
    option: 'office',
    checks: { '하위 폴더 포함': true, '하위 페이지까지 모두 가져오기': false, [ANALYZE]: false },
  };
  const storage = { initialized: false };
  const actions = [];
  const jobs = {};
  const calls = [];
  const statuses = [];
  const renders = [];
  let serial = 0;
  let demo;
  const kindOf = (value) => {
    if (/^https?:/.test(value)) return value.includes('/browse/') ? 'jira' : 'confluence';
    if (/[\\/]$/.test(value)) return screen.option;
    const extension = path.extname(value).slice(1);
    return { md: 'markdown', docx: 'office', txt: 'text', svg: 'image', rtf: 'rtf' }[extension];
  };
  const answer = (method, result, error) =>
    queueMicrotask(() => demo.observe(method, result, error));
  const control = {
    ready: true,
    async command(type, params = {}) {
      if (type !== 'read_screen')
        actions.push([
          type,
          params.label ?? params.name ?? params.view ?? params.id ?? params.to ?? '',
          params.value ?? params.tab ?? '',
        ]);
      if (type === 'read_screen') {
        if (screen.view === 'setup')
          return {
            controls: [
              { role: 'input', name: 'Wiki 저장 폴더', value: '' },
              { role: 'button', name: '이 위치에 저장하고 시작' },
            ],
          };
        const controls = [
          { role: 'button', name: '위젯 편집' },
          { role: 'input', name: '가져올 링크 또는 경로', value: screen.input },
        ];
        if (screen.input) {
          const kind = kindOf(screen.input);
          if (/[\\/]$/.test(screen.input))
            controls.push({
              role: 'checkbox',
              name: '하위 폴더 포함',
              checked: screen.checks['하위 폴더 포함'],
            });
          if (kind === 'confluence')
            controls.push({
              role: 'checkbox',
              name: '하위 페이지까지 모두 가져오기',
              checked: screen.checks['하위 페이지까지 모두 가져오기'],
            });
          controls.push(
            {
              role: 'checkbox',
              name: llm ? ANALYZE : `${ANALYZE} (LLM 연결 필요)`,
              checked: screen.checks[ANALYZE],
              disabled: !llm,
            },
            { role: 'button', name: 'Wiki로 가져오기', disabled: false },
          );
        }
        return { controls };
      }
      if (type === 'fill') {
        if (params.label === '가져올 링크 또는 경로') screen.input = params.value;
        return {};
      }
      if (type === 'navigate') {
        screen.view = params.view;
        return {};
      }
      if (type !== 'click') return {};
      if (params.name === '이 위치에 저장하고 시작') {
        storage.initialized = true;
        screen.view = 'home';
        answer('storage.initialize', { initialized: true });
      } else if (params.role === 'checkbox') {
        const name = params.name.startsWith(ANALYZE) ? ANALYZE : params.name;
        screen.checks[name] = !screen.checks[name];
      } else if (params.role === 'option')
        screen.option = {
          'Office 문서': 'office',
          'RTF 문서(.rtf)': 'rtf',
          이미지: 'image',
          '텍스트(.txt)': 'text',
        }[params.name];
      else if (params.name === 'Wiki로 가져오기') {
        const kind = kindOf(screen.input);
        const method = kind === 'markdown' ? 'files.import_text' : `${kind}.start`;
        if (startErrors[kind]) return answer(method, null, new Error(startErrors[kind]));
        const id = `doc-${++serial}`;
        if (kind === 'markdown')
          return answer(method, {
            status: 'imported',
            doc_id: id,
            title: '회의록',
            message: 'Markdown 문서를 Wiki에 저장했습니다.',
          });
        const job = {
          id: `job-${serial}`,
          status: 'running',
          total: 2,
          processed: 0,
          imported: 0,
          updated: 0,
          skipped: 0,
          failed: 0,
          analysis_failed: 0,
          current_file: kind in { office: 1, text: 1, image: 1 } ? screen.input : '',
          current_url: '',
          results: [],
          left: polls[kind] ?? 2,
          doc: id,
          analyze: screen.checks[ANALYZE],
        };
        jobs[kind] = job;
        answer(method, LOCAL.includes(kind) ? { ...job } : { job: { ...job } });
      }
      return {};
    },
  };
  const rpc = async (method) => {
    calls.push(method);
    const [kind, action] = method.split('.');
    if (method === 'wiki.list')
      return Object.values(jobs)
        .filter((job) => job.status === 'completed')
        .map((job) => ({
          id: job.doc,
          title: `${job.id} 문서`,
          format: 'html',
          source: { kind: 'office', application: 'Word' },
          index_mode: job.analyze ? 'wiki' : '',
        }));
    if (action === 'capabilities') return capabilities[kind] ?? { available: true, reason: '' };
    const job = jobs[kind];
    if (action === 'cancel') {
      if (job) job.status = 'cancelled';
      return {};
    }
    if (action === 'status') {
      onPoll?.(kind, job, demo);
      if (job?.status === 'running') {
        job.processed = Math.min(job.total, job.processed + 1);
        if (--job.left <= 0) {
          job.status = 'completed';
          job.imported = 2;
          job.results = [
            { path: 'a', status: 'imported', doc_ids: [job.doc] },
            { path: 'b', status: 'failed', message: '깨진 파일입니다.' },
          ];
          job.failed = 1;
          job.imported = 1;
        }
      }
      const copy = job && { ...job };
      return LOCAL.includes(kind) ? copy : { job: copy };
    }
    throw new Error(`unexpected ${method}`);
  };
  let ended;
  // When the recording started, by the frames looked at before it; and what the demo logged.
  const starts = [];
  const logs = [];
  const recorder = {
    async record(_target, file, { onAbort }) {
      starts.push({ at: clock, looks: looks.length });
      recorder.onAbort = onAbort;
      const mime = 'video/mp4;codecs=avc1.42E028';
      return {
        info: { at: clock, mime, width: 640, height: 360 },
        status: (value) => statuses.push(value),
        stop: async () => {
          const seconds = Math.max(2, Math.round((clock - 1_000_000) / 1000));
          if (stop) return stop(file, seconds);
          await fs.writeFile(file, recording(seconds));
          return { file, mime };
        },
        done: new Promise((resolve) => (ended = () => resolve({ file, mime }))),
      };
    },
    async render(spec, file) {
      renders.push(spec);
      await fs.writeFile(file, recording(3));
      return { file, mime: 'video/mp4;codecs=avc1.42E028' };
    },
  };
  // A 1440×960 window on a 1920×1080 display, full screen as soon as it is asked; its screen's frames are as `frames`
  // says. `looks` holds when each frame was looked at.
  const display = { x: 0, y: 0, width: 1920, height: 1080 };
  let fullScreen = false;
  const looks = [];
  const window = {
    isDestroyed: () => false,
    isVisible: () => true,
    isFullScreen: () => fullScreen,
    setFullScreen: (on) => {
      fullScreen = on;
      actions.push(['fullscreen', on, '']);
    },
    getBounds: () => (fullScreen ? display : { x: 240, y: 60, width: 1440, height: 960 }),
    getContentSize: () => (fullScreen ? [display.width, display.height] : [1440, 960]),
    webContents: {
      capturePage: async () => {
        looks.push(clock);
        const size = frames(looks.length) ?? { width: display.width, height: display.height };
        return { getSize: () => size };
      },
    },
  };
  demo = new DemoRun({
    plan: {
      list: path.join(root, 'list.txt'),
      title: '시연',
      analyze: false,
      subfolders: true,
      subpages: false,
      fastForward: true,
      quit: true,
      wiki: path.join(root, 'wiki'),
      output: path.join(root, 'out'),
      items,
      ...plan,
    },
    window: () => window,
    control,
    rpc,
    storage,
    recorder,
    now: () => clock,
    sleep: async (ms) => {
      clock += Math.max(1, ms);
      await new Promise((resolve) => setImmediate(resolve));
    },
    log: (text) => logs.push(text),
    display: () => display,
  });
  return {
    demo,
    actions,
    calls,
    statuses,
    renders,
    root,
    screen,
    looks,
    starts,
    logs,
    endRecording: () => ended(),
  };
}

const read = (folder, name) => fs.readFile(path.join(folder, name), 'utf8');

test('a demo initializes its Wiki, imports each item through the import box and makes the video with captions', async (t) => {
  const items = [
    {
      line: 1,
      source: '/자료/회의록.md',
      caption: '',
      type: 'file',
      kind: 'markdown',
      name: '회의록.md',
    },
    {
      line: 2,
      source: '/자료/설계서.docx',
      caption: '설계 문서를 가져옵니다',
      type: 'file',
      kind: 'office',
      application: 'Word',
      name: '설계서.docx',
    },
    {
      line: 3,
      source: '/자료/회의',
      caption: '',
      type: 'folder',
      kind: 'folder',
      name: '회의',
      kinds: ['office', 'text'],
      counts: { office: 4, rtf: 0, image: 0, text: 2, markdown: 1 },
    },
    {
      line: 4,
      source: 'https://w.example.com/display/T/Page',
      caption: '',
      type: 'link',
      kind: 'confluence',
      name: 'Page',
    },
  ];
  // The Confluence import takes 40 seconds: a wait to speed up.
  const { demo, actions, statuses, renders } = await harness(t, {
    items,
    plan: { analyze: true },
    polls: { confluence: 40 },
  });
  const outcome = await demo.run();
  assert.equal(outcome.failure, null);
  assert.equal(outcome.stopped, false);
  const folder = outcome.folder;
  assert.equal(outcome.video, path.join(folder, FILES.video));
  // What the screens were made to do, in order.
  const done = actions.map(([type, name, value]) =>
    [type, name, value].filter((part) => part !== '').join(' '),
  );
  assert.deepEqual(done.slice(0, 4), [
    'fullscreen true',
    `fill Wiki 저장 폴더 ${demo.plan.wiki}`,
    'click 이 위치에 저장하고 시작',
    'navigate library import',
  ]);
  const importBox = done.filter((line) =>
    /가져올 링크|Office 문서|텍스트\(\.txt\)|Wiki로 가져오기|하위|LLM/.test(line),
  );
  assert.deepEqual(importBox, [
    'fill 가져올 링크 또는 경로 /자료/회의록.md',
    `click ${ANALYZE}`,
    'click Wiki로 가져오기',
    'fill 가져올 링크 또는 경로 /자료/설계서.docx',
    'click Wiki로 가져오기',
    `fill 가져올 링크 또는 경로 /자료/회의${path.sep}`,
    'click Office 문서',
    'click Wiki로 가져오기',
    // The folder's path is in the box already: only its kind changes.
    'click 텍스트(.txt)',
    'click Wiki로 가져오기',
    'fill 가져올 링크 또는 경로 https://w.example.com/display/T/Page',
    'click Wiki로 가져오기',
  ]);
  assert.ok(done.includes('navigate library documents'));
  assert.ok(done.includes('navigate library knowledge'), 'the knowledge graph after AI analysis');
  assert.equal(done.filter((line) => line.startsWith('open_document')).length, 3);
  assert.equal(done.at(-1), 'navigate home');
  // The panel showed the steps.
  assert.ok(
    statuses.some(
      (status) =>
        status.label === '2단계 · 자료 가져오기 4/4' && /Confluence|페이지/.test(status.text),
    ),
  );

  const cues = parseSrt(await read(folder, FILES.captions));
  const labels = [...new Set(cues.map((cue) => cue.label))];
  assert.deepEqual(labels.slice(0, 3), [
    '1단계 · 저장소 초기화',
    '2단계 · 자료 가져오기',
    '2단계 · 자료 가져오기 1/4',
  ]);
  assert.ok(labels.includes('3단계 · 결과 확인'));
  assert.ok(cues.some((cue) => cue.text === '설계 문서를 가져옵니다'));
  assert.ok(cues.some((cue) => cue.text === '폴더 · 회의 — 텍스트 파일(.txt) 2개'));
  assert.ok(cues.some((cue) => cue.text === '완료 · 문서 1개 저장 · 실패 1'));
  const project = JSON.parse(await read(folder, FILES.project));
  assert.equal(project.title, '시연');
  assert.equal(project.fast_forward.length, 1);
  assert.equal(project.fast_forward[0].speed, 8);
  assert.match(
    project.intro[1],
    /^가져올 자료 4건 · Markdown 파일 1 · Word 문서 1 · 폴더 1 · Confluence 페이지 1$/,
  );
  assert.match(project.outro[0], /Wiki 문서 \d+개를 만들었습니다 · 실패 4건/);
  assert.deepEqual(renders[0].speedups, project.fast_forward);
  assert.deepEqual((await inspect(path.join(folder, FILES.raw))).layout, ['ftyp', 'moov', 'mdat']);
  assert.deepEqual((await inspect(outcome.video)).layout, ['ftyp', 'moov', 'mdat']);
  assert.deepEqual((await fs.readdir(folder)).sort(), Object.values(FILES).sort());
  const report = await read(folder, FILES.report);
  assert.match(
    report,
    /\[3\] \/자료\/회의 — 폴더\r\n {4}Markdown 파일 1개는 폴더 가져오기가 읽지 않아 건너뛰었습니다/,
  );
  assert.match(report, / {4}Office 문서: 완료 · 문서 1개 저장 · 실패 1/);
  assert.match(report, / {6}실패 b — 깨진 파일입니다\./);
  assert.match(report, /합계: 문서 5개 저장 · 업데이트 0 · 이미 있음 0 · 실패 4/);
});

test('an import that cannot start or run is reported and the demo goes on with the next one', async (t) => {
  const items = [
    {
      line: 1,
      source: '/자료/메모.txt',
      caption: '',
      type: 'file',
      kind: 'text',
      name: '메모.txt',
    },
    {
      line: 2,
      source: '/자료/설계서.docx',
      caption: '',
      type: 'file',
      kind: 'office',
      application: 'Word',
      name: '설계서.docx',
    },
    {
      line: 3,
      source: 'https://jira.example.com/browse/ABC-1',
      caption: '',
      type: 'link',
      kind: 'jira',
      name: 'ABC-1',
    },
  ];
  const { demo, calls } = await harness(t, {
    items,
    llm: false,
    plan: { analyze: true },
    capabilities: { text: { available: false, reason: '메모장(notepad.exe)을 찾지 못했습니다.' } },
    startErrors: { office: '이미 Office 문서 가져오기가 진행 중입니다.' },
  });
  const outcome = await demo.run();
  assert.equal(outcome.failure, null);
  assert.ok(outcome.video);
  assert.ok(!calls.includes('text.start'));
  const report = await read(outcome.folder, FILES.report);
  assert.match(
    report,
    /\[1\] \/자료\/메모\.txt — 텍스트 파일\(\.txt\)\r\n {4}가져오지 못했습니다: 메모장\(notepad\.exe\)을 찾지 못했습니다\./,
  );
  assert.match(
    report,
    /\[2\] .*\r\n {4}가져오지 못했습니다: 이미 Office 문서 가져오기가 진행 중입니다\./,
  );
  // Without an LLM the box cannot analyze: said once the import ran without it.
  assert.match(
    report,
    /\[3\] .*JIRA 티켓\r\n {4}완료 · 문서 1개 저장 · 실패 1 \(.*\)\r\n {4}LLM이 연결되지 않아 AI 분석 없이 가져왔습니다\./,
  );
});

test('the stop button cancels the running import and still makes the video of what was recorded', async (t) => {
  const items = [
    {
      line: 1,
      source: '/자료/회의',
      caption: '',
      type: 'folder',
      kind: 'folder',
      name: '회의',
      kinds: ['office'],
      counts: { office: 9, rtf: 0, image: 0, text: 0, markdown: 0 },
    },
    {
      line: 2,
      source: '/자료/b.docx',
      caption: '',
      type: 'file',
      kind: 'office',
      application: 'Word',
      name: 'b.docx',
    },
  ];
  let polls = 0;
  const { demo, calls } = await harness(t, {
    items,
    polls: { office: 100 },
    onPoll: (_kind, job, run) => {
      if (job?.status === 'running' && ++polls === 3) run.abort();
    },
  });
  const outcome = await demo.run();
  assert.equal(outcome.failure, null);
  assert.equal(outcome.stopped, true);
  assert.ok(calls.includes('office.cancel'));
  assert.ok(outcome.video);
  const report = await read(outcome.folder, FILES.report);
  assert.match(report, /중지 버튼으로 녹화를 중지했습니다\./);
  assert.match(report, /… 목록의 나머지 1건은 가져오지 않았습니다\./);
  const project = JSON.parse(await read(outcome.folder, FILES.project));
  assert.equal(project.outro_title, '시연을 중지했습니다');
  const cues = parseSrt(await read(outcome.folder, FILES.captions));
  assert.equal(cues.at(-1).text, '시연을 중지했습니다');
});

const markdown = {
  line: 1,
  source: '/자료/회의록.md',
  caption: '',
  type: 'file',
  kind: 'markdown',
  name: '회의록.md',
};

test('a recording that does not end well keeps what it wrote: its video, captions and edit settings', async (t) => {
  // The capture page hung, a part could not be written: stopping fails after part of the last fragment was written.
  const { demo } = await harness(t, {
    items: [markdown],
    stop: async (file, seconds) => {
      const whole = recording(seconds);
      await fs.writeFile(file, whole.subarray(0, whole.length - 30));
      throw new Error('녹화를 마무리하지 못했습니다.');
    },
  });
  const outcome = await demo.run();
  assert.match(outcome.failure.message, /녹화를 마무리하지 못했습니다/);
  assert.equal(outcome.video, path.join(outcome.folder, FILES.video));
  assert.deepEqual((await fs.readdir(outcome.folder)).sort(), Object.values(FILES).sort());
  const raw = await inspect(path.join(outcome.folder, FILES.raw));
  assert.deepEqual(raw.layout, ['ftyp', 'moov', 'mdat']);
  assert.ok(raw.duration >= 2, `${raw.duration}`);
  assert.ok(parseSrt(await read(outcome.folder, FILES.captions)).length > 3);
  assert.equal(
    JSON.parse(await read(outcome.folder, FILES.project)).outro_title,
    '시연을 중지했습니다',
  );
  assert.match(await read(outcome.folder, FILES.report), /멈춘 이유: 녹화를 마무리하지 못했습니다/);
});

test('a recording with nothing to keep is left as written, beside the report', async (t) => {
  const { demo } = await harness(t, {
    items: [markdown],
    stop: async (file) => {
      await fs.writeFile(file, 'not a video');
      throw new Error('영상을 기록하지 못했습니다.');
    },
  });
  const outcome = await demo.run();
  assert.match(outcome.failure.message, /영상을 기록하지 못했습니다/);
  assert.equal(outcome.video, null);
  assert.deepEqual((await fs.readdir(outcome.folder)).sort(), [FILES.raw, FILES.report].sort());
  assert.equal(await read(outcome.folder, FILES.raw), 'not a video');
});

test('the app window closing ends the demo: the running import is stopped, what was recorded kept', async (t) => {
  let polls = 0;
  const { demo, calls, endRecording } = await harness(t, {
    items: [
      { ...markdown, source: '/자료/a.docx', kind: 'office', application: 'Word', name: 'a.docx' },
      { ...markdown, line: 2 },
    ],
    polls: { office: 100 },
    // The window closed: the capture of its page ended, and with it the recording.
    onPoll: (_kind, job) => {
      if (job?.status === 'running' && ++polls === 3) endRecording();
    },
  });
  const outcome = await demo.run();
  assert.match(outcome.failure.message, /앱 창이 닫혀 시연을 멈췄습니다/);
  assert.equal(outcome.stopped, false);
  assert.ok(calls.includes('office.cancel'));
  assert.ok(polls < 20, `${polls}`);
  assert.ok(outcome.video);
  const report = await read(outcome.folder, FILES.report);
  assert.match(report, /멈춘 이유: 앱 창이 닫혀 시연을 멈췄습니다/);
  assert.match(report, /… 목록의 나머지 1건은 가져오지 않았습니다\./);
});

test('a long demo is made again: its captions are read beyond the 1 MB of a list', async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'agent-demo-render-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const draft = path.join(root, 'draft.part');
  await fs.writeFile(draft, recording(4));
  await finalize(draft, path.join(root, FILES.raw));
  const read = [];
  const renders = [];
  const target = await renderAgain(root, {
    recorder: {
      async render(spec, file) {
        renders.push(spec);
        await fs.writeFile(file, recording(3));
        return { file, mime: 'video/mp4;codecs=avc1.42E028' };
      },
    },
    readText: async (file, what, options = {}) => {
      read.push([path.basename(file), options.limit]);
      if (file.endsWith('.json')) throw new Error(`${what}을 찾을 수 없습니다: ${file}`);
      return '1\r\n00:00:00,500 --> 00:00:02,000\r\n[1단계] 고친 자막\r\n';
    },
  });
  assert.equal(target, path.join(root, FILES.video));
  assert.deepEqual(read, [
    [FILES.project, undefined],
    [FILES.captions, MAX_CAPTION_BYTES],
  ]);
  assert.ok(MAX_CAPTION_BYTES >= 32 * 1024 * 1024);
  assert.deepEqual(renders[0].cues, [{ start: 500, end: 2000, text: '고친 자막', label: '1단계' }]);
});

test('a demo that cannot reach its window records nothing and says why', async (t) => {
  const { demo } = await harness(t, { items: [] });
  demo.control.ready = false;
  const outcome = await demo.run();
  assert.match(outcome.failure.message, /앱 화면이 준비되지 않았습니다/);
  assert.equal(outcome.video, null);
  const report = await read(outcome.folder, FILES.report);
  assert.match(report, /멈춘 이유: 앱 화면이 준비되지 않았습니다/);
});

test('the recording starts once the screen is drawn at full-screen size, twice in a row', async (t) => {
  // The window manager resizes the window, and the screen draws its first full-size frames, a while after it is asked;
  // a frame of the old size between two full-size ones starts the count again.
  const small = { width: 1440, height: 960 };
  const { demo, starts, logs } = await harness(t, {
    items: [markdown],
    frames: (look) => (look <= 3 || look === 5 ? small : null),
  });
  const outcome = await demo.run();
  assert.equal(outcome.failure, null);
  assert.ok(outcome.video);
  assert.equal(starts.length, 1);
  assert.equal(starts[0].looks, 7);
  assert.deepEqual(
    logs.filter((text) => text.includes('전체 화면')),
    [],
  );
});

test('a screen that is not drawn at full-screen size in time is recorded all the same, and the log says so', async (t) => {
  const { demo, starts, looks, logs } = await harness(t, {
    items: [markdown],
    frames: () => ({ width: 1440, height: 960 }),
  });
  const outcome = await demo.run();
  assert.equal(outcome.failure, null);
  assert.ok(outcome.video);
  assert.ok(starts[0].at - looks[0] >= 15_000, `${starts[0].at - looks[0]}`);
  assert.match(
    logs.join('\n'),
    /15초 안에 전체 화면 크기로 그려지지 않아 그대로 녹화를 시작했습니다/,
  );
});
