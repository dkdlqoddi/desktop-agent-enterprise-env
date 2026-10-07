import { test, expect, type ElectronApplication, type Page } from '@playwright/test';
import { access, mkdir, mkdtemp, readFile, readdir, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { inspect } from '../electron/mp4.cjs';
import { launch } from './helpers';

// record-demo.cmd as a user's PC runs it: the app gets a list, empties a Wiki of its own, imports each path through
// its screens while its window is recorded, makes the video with captions and closes; `--demo-render` makes the video
// again from edited captions.
const FILES = {
  video: '시연 영상.mp4',
  raw: '원본 녹화.mp4',
  captions: '원본 녹화.srt',
  project: '편집 정보.json',
  report: '진행 기록.txt',
};
const svg = (color: string) =>
  `<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180"><rect width="320" height="180" fill="${color}"/></svg>`;
// Holds shortened, the app's own report of the result kept from opening Explorer.
const env = { PRIVATE_AGENT_DEMO_PACE: '0.25', PRIVATE_AGENT_DEMO_REVEAL: '0' };

// The app closes by itself once the video is made. Under Playwright's inspector a process that ends waits for the
// debugger to leave and says so: that line, or the exit, is the end.
function ended(app: ElectronApplication, ms: number) {
  const child = app.process();
  const end = new Promise<void>((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error(`앱이 ${ms / 1000}초 안에 끝나지 않았습니다.`)),
      ms,
    );
    const done = () => {
      clearTimeout(timer);
      resolve();
    };
    child.stderr?.on('data', (chunk) => {
      if (String(chunk).includes('Waiting for the debugger to disconnect')) done();
    });
    child.once('exit', done);
  });
  return end.then(() => app.close().catch(() => {}));
}

async function panel(app: ElectronApplication) {
  await expect
    .poll(async () => {
      for (const page of app.windows())
        if ((await page.title()) === 'Private Agent 녹화') return true;
      return false;
    })
    .toBe(true);
  for (const page of app.windows())
    if ((await page.title()) === 'Private Agent 녹화') return page as Page;
  throw new Error('no recording panel');
}

test('demo recording: own Wiki, the list imported through the screens, video with captions, made again, stopped, window closed', async () => {
  test.setTimeout(540_000);
  const directory = await mkdtemp(path.join(tmpdir(), 'demo-desktop-'));
  const sources = path.join(directory, '자료');
  await mkdir(path.join(sources, '사진'), { recursive: true });
  await writeFile(
    path.join(sources, '회의록.md'),
    '# 주간 회의록\n\n## 결정 사항\n\n1. 목록 하나로 시연을 녹화한다.\n',
  );
  await writeFile(path.join(sources, '구성도.svg'), svg('#2b59c3'));
  await writeFile(path.join(sources, '사진', '첫번째.svg'), svg('#2d7a55'));
  await writeFile(path.join(sources, '사진', '두번째.svg'), svg('#b63a34'));
  await writeFile(path.join(sources, '사진', '설명.md'), '# 사진 설명\n');
  const list = path.join(directory, 'demo-list.txt');
  await writeFile(
    list,
    [
      '# E2E',
      '제목 = E2E 시연',
      `${path.join(sources, '회의록.md')} | 회의 메모를 Wiki 문서로 가져옵니다`,
      path.join(sources, '구성도.svg'),
      path.join(sources, '사진'),
    ].join('\r\n'),
  );
  const userData = path.join(directory, 'user-data');
  const output = path.join(directory, '시연 영상');
  const wiki = path.join(directory, '시연 Wiki');
  try {
    let app = await launch(userData, env, ['--demo', list]);
    let finished = ended(app, 240_000);
    const page = await app.firstWindow();
    // The demo fills in the storage screen itself, then works the import box.
    await expect(page.getByRole('button', { name: '위젯 편집' })).toBeVisible({ timeout: 90_000 });
    const recording = await panel(app);
    await expect(recording.locator('#step')).toContainText('2단계', { timeout: 60_000 });
    await expect(page.getByLabel('가져올 링크 또는 경로')).toBeVisible();
    await finished;

    const [run] = await readdir(output);
    const folder = path.join(output, run);
    expect((await readdir(folder)).sort()).toEqual(Object.values(FILES).sort());
    // A demo never changes the Wiki the app opens next time.
    await expect(access(path.join(userData, 'storage.json'))).rejects.toThrow();
    expect(await readdir(wiki)).toEqual(expect.arrayContaining(['.private-agent-demo', 'wiki.db']));
    const raw = await inspect(path.join(folder, FILES.raw));
    const video = await inspect(path.join(folder, FILES.video));
    expect(raw.layout).toEqual(['ftyp', 'moov', 'mdat']);
    expect(video.layout).toEqual(['ftyp', 'moov', 'mdat']);
    expect(raw.duration).toBeGreaterThan(5);
    // The title and end cards make the video longer than the recording.
    expect(video.duration).toBeGreaterThan(raw.duration + 1);
    expect((video.width % 2) + (video.height % 2)).toBe(0);
    const captions = await readFile(path.join(folder, FILES.captions), 'utf8');
    for (const text of [
      '[1단계 · 저장소 초기화] 시연에 쓸 Wiki 저장소를 비우고 새로 만듭니다',
      '[2단계 · 자료 가져오기 1/3] 회의 메모를 Wiki 문서로 가져옵니다',
      '[2단계 · 자료 가져오기 2/3] 이미지 · 구성도.svg',
      '[2단계 · 자료 가져오기 3/3] 폴더 · 사진 — 이미지 2개',
      '[3단계 · 결과 확인] Wiki에 문서 4개가 저장되었습니다',
      '[3단계 · 결과 확인] 시연을 마칩니다',
    ])
      expect(captions).toContain(text);
    const report = await readFile(path.join(folder, FILES.report), 'utf8');
    expect(report).toContain('Markdown 문서를 Wiki에 저장했습니다.');
    expect(report).toContain('이미지: 완료 · 문서 2개 저장');
    expect(report).toContain('Markdown 파일 1개는 폴더 가져오기가 읽지 않아 건너뛰었습니다');
    expect(report).toContain('합계: 문서 4개 저장 · 업데이트 0 · 이미 있음 0 · 실패 0');
    const project = JSON.parse(await readFile(path.join(folder, FILES.project), 'utf8'));
    expect(project.title).toBe('E2E 시연');

    // Captions edited by hand, then the video made again.
    const before = (await stat(path.join(folder, FILES.video))).mtimeMs;
    await writeFile(
      path.join(folder, FILES.captions),
      captions.replace('시연에 쓸 Wiki 저장소를 비우고 새로 만듭니다', '고친 자막입니다'),
    );
    app = await launch(path.join(directory, 'render-data'), env, ['--demo-render', folder]);
    await ended(app, 180_000);
    expect((await stat(path.join(folder, FILES.video))).mtimeMs).toBeGreaterThan(before);
    expect((await readdir(folder)).sort()).toEqual(Object.values(FILES).sort());
    expect((await inspect(path.join(folder, FILES.video))).layout).toEqual([
      'ftyp',
      'moov',
      'mdat',
    ]);

    // A second recording starts from an emptied Wiki, and the panel's stop button still makes its video.
    app = await launch(userData, env, ['--demo', list]);
    finished = ended(app, 180_000);
    const second = await panel(app);
    await expect(second.locator('#step')).toContainText('2/3', { timeout: 120_000 });
    await second.getByRole('button', { name: '중지' }).click();
    await finished;
    const runs = (await readdir(output)).sort();
    expect(runs).toHaveLength(2);
    const stopped = path.join(output, runs[1]);
    const secondReport = await readFile(path.join(stopped, FILES.report), 'utf8');
    expect(secondReport).toContain('중지 버튼으로 녹화를 중지했습니다.');
    expect(secondReport).toMatch(
      /회의록\.md — Markdown 파일\r\n {4}Markdown 문서를 Wiki에 저장했습니다\./,
    );
    expect((await inspect(path.join(stopped, FILES.video))).layout).toEqual([
      'ftyp',
      'moov',
      'mdat',
    ]);
    expect(await readFile(path.join(stopped, FILES.captions), 'utf8')).toContain(
      '시연을 중지했습니다',
    );

    // Closing the app's window ends a third recording at once: the app closes, keeping what was recorded with its
    // captions and report.
    app = await launch(userData, env, ['--demo', list]);
    finished = ended(app, 120_000);
    const third = await panel(app);
    await expect(third.locator('#step')).toContainText('2단계', { timeout: 120_000 });
    await app.evaluate(({ BrowserWindow }) =>
      BrowserWindow.getAllWindows()
        .find((win) => win.webContents.getURL().startsWith('agent://'))
        ?.close(),
    );
    await finished;
    const closed = path.join(output, (await readdir(output)).sort()[2]);
    expect(await readdir(closed)).toEqual(
      expect.arrayContaining([FILES.raw, FILES.captions, FILES.project, FILES.report]),
    );
    expect(await readFile(path.join(closed, FILES.report), 'utf8')).toContain(
      '멈춘 이유: 앱 창이 닫혀 시연을 멈췄습니다.',
    );
    expect((await inspect(path.join(closed, FILES.raw))).layout).toEqual(['ftyp', 'moov', 'mdat']);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
