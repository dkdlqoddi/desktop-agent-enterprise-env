import { test, expect, type ElectronApplication } from '@playwright/test';
import { spawnSync } from 'node:child_process';
import { mkdtemp, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { confluenceServer } from './fixtures/confluence.cjs';
import {
  addWidget,
  chooseStorage,
  connectLlm,
  drawer,
  launch,
  llmServer,
  openLibrary,
  widget,
} from './helpers';

// 1×1 red and 2×2 blue PNGs.
const RED =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==';
const BLUE =
  'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR42mNkYPjPwMDAxMDAwMDAAAALHwEDmlnfSQAAAABJRU5ErkJggg==';

test('Imported documents keep their source, tell when it changed and update from it in place', async () => {
  // The source is kept as its full path; a Windows %TEMP% may be given in short (8.3) names.
  const directory = await realpath(await mkdtemp(path.join(tmpdir(), 'document-sources-')));
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    const page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await chooseStorage(page, path.join(directory, 'wiki'));
    await addWidget(page, '가져오기');
    const importer = widget(page, 'import');
    const input = importer.getByLabel('가져올 링크 또는 경로');
    const rows = widget(page, 'documents').locator('.document-row');
    const source = drawer(page).locator('.document-source');
    const toast = page.locator('.toast');

    // A Markdown file keeps its path as its source.
    const note = path.join(directory, '회의록.md');
    await writeFile(note, '# 회의록\n\n첫 안건');
    await input.fill(note);
    await importer.getByRole('button', { name: 'Wiki로 가져오기' }).click();
    await expect(toast).toContainText('‘회의록’: Markdown 문서를 Wiki에 저장했습니다.');
    await rows.filter({ hasText: '회의록' }).click();
    await expect(source).toContainText('가져온 Markdown 파일');
    await expect(source).toContainText(note);
    await expect(source.locator('.source-state')).toHaveText('원본 파일은 가져올 때 그대로입니다');
    await drawer(page).getByRole('button', { name: '닫기', exact: true }).click();

    // The document window sees the file changed and takes its new text into the same document.
    await writeFile(note, '# 회의록\n\n바뀐 안건');
    await rows.filter({ hasText: '회의록' }).click();
    await expect(source.locator('.source-state')).toContainText(
      '가져온 뒤 원본 파일이 바뀌었습니다',
    );
    await source.getByRole('button', { name: '원본에서 업데이트' }).click();
    await expect(toast).toContainText('원본이 바뀌어 문서를 새 내용으로 업데이트했습니다.');
    await expect(drawer(page).locator('.document-content')).toContainText('바뀐 안건');
    await expect(source.locator('.source-state')).toHaveText('원본 파일은 가져올 때 그대로입니다');
    await expect(source).toContainText('원본에서 업데이트 ');
    await expect(rows).toHaveCount(1);

    // Text the user changed is replaced only after asking, and the file's text comes back.
    await drawer(page).getByRole('button', { name: '수정', exact: true }).click();
    await drawer(page).locator('textarea.document-editor').fill('# 회의록\n\n내가 고친 안건');
    await drawer(page).getByRole('button', { name: '문서 저장' }).click();
    await expect(drawer(page).locator('.document-content')).toContainText('내가 고친 안건');
    await expect(source).toContainText('가져온 뒤 본문을 고쳤습니다');
    await source.getByRole('button', { name: '원본에서 업데이트' }).click();
    const ask = source.getByRole('group', {
      name: '가져온 뒤 고친 본문이 원본의 내용으로 바뀝니다. 업데이트할까요?',
    });
    await expect(ask).toBeVisible();
    await page.screenshot({ path: 'test-results/document-source-update.png' });
    await ask.getByRole('button', { name: '원본에서 업데이트' }).click();
    await expect(toast).toContainText('고친 본문 대신 원본의 내용으로 되돌렸습니다.');
    await expect(drawer(page).locator('.document-content')).toContainText('바뀐 안건');
    await expect(source).not.toContainText('가져온 뒤 본문을 고쳤습니다');
    await drawer(page).getByRole('button', { name: '닫기', exact: true }).click();

    // Imported again unchanged, nothing new is saved.
    await input.fill(note);
    await importer.getByRole('button', { name: 'Wiki로 가져오기' }).click();
    await expect(toast).toContainText('이미 가져온 동일한 Markdown 파일입니다.');
    await expect(rows).toHaveCount(1);

    // A file read by a background import updates through the same job, which the import box lists.
    const picture = path.join(directory, '조직도.png');
    await writeFile(picture, Buffer.from(RED, 'base64'));
    await input.fill(picture);
    await importer.getByRole('button', { name: 'Wiki로 가져오기' }).click();
    await expect(importer.locator('.job-counts')).toContainText('저장 1');
    await writeFile(picture, Buffer.from(BLUE, 'base64'));
    await rows.filter({ hasText: '조직도' }).click();
    const view = drawer(page).locator('[data-html-document]');
    await expect(view).toContainText('1×1px');
    await expect(source.locator('.source-state')).toContainText(
      '가져온 뒤 원본 파일이 바뀌었습니다',
    );
    await source.getByRole('button', { name: '원본에서 업데이트' }).click();
    await expect(toast).toContainText('원본이 바뀌어 문서를 새 내용으로 업데이트했습니다.');
    await expect(view).toContainText('2×2px');
    expect(
      await view.locator('img').evaluate((image: HTMLImageElement) => image.naturalWidth),
    ).toBe(2);
    await expect(importer.locator('.job-counts')).toContainText('업데이트 1');
    await expect(rows).toHaveCount(2);
    await drawer(page).getByRole('button', { name: '닫기', exact: true }).click();

    // A file that is gone cannot be read again.
    await rm(picture);
    await rows.filter({ hasText: '조직도' }).click();
    await expect(source.locator('.source-state')).toContainText('원본 파일을 찾을 수 없습니다');
    await expect(source.getByRole('button', { name: '원본에서 업데이트' })).toBeDisabled();

    // The library finds documents by where they came from.
    await drawer(page).getByRole('button', { name: '닫기', exact: true }).click();
    await openLibrary(page, '문서');
    const filter = page.getByLabel('보관함 문서 필터');
    await filter.fill(path.basename(directory));
    await expect(rows).toHaveCount(2);
    await filter.fill('회의록.md');
    await expect(rows).toHaveCount(1);
    expect(errors).toEqual([]);
  } finally {
    await app?.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});

test('An update from a page keeps the document window from editing until it ends; a Markdown file updates at once', async () => {
  const directory = await realpath(await mkdtemp(path.join(tmpdir(), 'source-update-wait-')));
  // The analysis after the first waits for the test: an analyzed document is analyzed again after its update, in the
  // background, while the window already shows the new text.
  let analyses = 0;
  let release = () => {};
  const held = new Promise<void>((resolve) => (release = resolve));
  const llm = await llmServer(async ({ system }) => {
    if (!system.includes('extract metadata')) return '연결 성공';
    if (analyses++) await held;
    return { summary: '회의록 요약입니다.', keywords: ['회의'], relations: [] };
  });
  const confluence = await confluenceServer();
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    const page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await chooseStorage(page, path.join(directory, 'wiki'));

    // A page's update is an import job: while it runs, editing, the raw text and deleting wait, as the text it brings
    // would otherwise land under the editor.
    const started = await page.evaluate(
      (url) =>
        window.privateAgent!.request('confluence.start', { url, recursive: false, analyze: false }),
      confluence.url,
    );
    expect(started.ok).toBe(true);
    const status = async () =>
      (
        (await page.evaluate(() => window.privateAgent!.request('confluence.status', {}))) as {
          result: { job: { status: string } | null };
        }
      ).result.job?.status;
    await expect.poll(status, { timeout: 90_000 }).toBe('completed');
    await page.reload();
    await widget(page, 'documents')
      .locator('.document-row')
      .filter({ hasText: '페이지 1' })
      .click();
    const source = drawer(page).locator('.document-source');
    let letGo = () => {};
    confluence.options.hold = new Promise<void>((resolve) => (letGo = resolve));
    confluence.options.note = '새로 쓴 문단';
    await source.getByRole('button', { name: '원본에서 업데이트' }).click();
    await expect(source.locator('.source-busy')).toContainText('원본에서 가져오는 중');
    for (const name of ['수정', '삭제'])
      await expect(drawer(page).getByRole('button', { name, exact: true })).toBeDisabled();
    letGo();
    await expect(page.locator('.toast')).toContainText(
      '원본이 바뀌어 문서를 새 내용으로 업데이트했습니다.',
      { timeout: 60_000 },
    );
    await expect(drawer(page).locator('.document-content')).toContainText('새로 쓴 문단');
    await expect(drawer(page).getByRole('button', { name: '수정', exact: true })).toBeEnabled();
    await drawer(page).locator('.drawer-head').getByRole('button', { name: '닫기' }).click();

    // A Markdown file's update saves its text at once; its analysis runs on in the background, during which the
    // document can be edited, and the analysis ending does not change what is being written.
    await connectLlm(page, llm.endpoint);
    const note = path.join(directory, '회의록.md');
    await writeFile(note, '# 회의록\n\n첫 안건');
    const imported = await page.evaluate(
      (file) => window.privateAgent!.request('files.import_text', { path: file, analyze: true }),
      note,
    );
    expect(imported.ok).toBe(true);
    await page.reload();
    await widget(page, 'documents').locator('.document-row').filter({ hasText: '회의록' }).click();
    await expect(drawer(page).getByRole('complementary', { name: 'AI 분석' })).toContainText(
      '회의록 요약입니다.',
    );
    await writeFile(note, '# 회의록\n\n바뀐 안건');
    await source.getByRole('button', { name: '원본에서 업데이트' }).click();
    await expect(page.locator('.toast')).toContainText(
      '원본이 바뀌어 문서를 새 내용으로 업데이트했습니다.',
    );
    await expect(drawer(page).locator('.document-content')).toContainText('바뀐 안건');
    await expect.poll(() => analyses).toBe(2);

    // Editing starts from the updated text while the analysis still runs, and saving it is no conflict.
    await drawer(page).getByRole('button', { name: '수정', exact: true }).click();
    const editor = drawer(page).locator('textarea.document-editor');
    await expect(editor).toHaveValue(/바뀐 안건/);
    await editor.fill('# 회의록\n\n바뀐 안건과 메모');
    release();
    await expect(editor).toHaveValue('# 회의록\n\n바뀐 안건과 메모');
    await drawer(page).getByRole('button', { name: '문서 저장' }).click();
    await expect(drawer(page).locator('.document-content')).toContainText('바뀐 안건과 메모');
    await expect(drawer(page).getByRole('alert')).toHaveCount(0);
    expect(errors).toEqual([]);
  } finally {
    await app?.close();
    await llm.close();
    await confluence.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});

const WRITE_SEED = `
import hashlib, sqlite3, sys
sys.path.insert(0, "backend")
from agent.html_document import index_markdown, page
db = sqlite3.connect(sys.argv[1])
for ident, title, table, values in [
        ("word", "보고서", "office_sources", {"source_path": sys.argv[2], "application": "Word"})]:
    html = page(title, "<h1>일정</h1><p>3월 15일까지 마칩니다.</p>", "word")
    content = index_markdown(html)
    db.execute("INSERT INTO documents(id,title,content,format,created_at,updated_at) VALUES (?,?,?,'html',"
               "'2026-10-01T00:00:00+00:00','2026-10-01T00:00:00+00:00')", (ident, title, content))
    db.execute("INSERT INTO document_html VALUES (?, ?)", (ident, html))
    row = {"doc_id": ident, **values, "source_hash": hashlib.sha256(ident.encode()).hexdigest(), "markdown": content,
           "imported_at": "2026-10-01T00:00:00+00:00"}
    db.execute(f"INSERT INTO {table}({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))
db.commit()
`;

test('Text changed in an imported Office document can be written back into its original', async () => {
  const directory = await realpath(await mkdtemp(path.join(tmpdir(), 'write-back-')));
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    const page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const wiki = path.join(directory, 'wiki');
    await chooseStorage(page, wiki);
    const file = path.join(directory, '보고서.docx');
    await writeFile(file, 'office bytes');
    const python = path.resolve(
      'backend',
      '.venv',
      process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
    );
    const seeded = spawnSync(python, ['-c', WRITE_SEED, path.join(wiki, 'wiki.db'), file], {
      encoding: 'utf8',
      env: { ...process.env, PYTHONPATH: '' },
    });
    expect(seeded.stderr).toBe('');
    await page.reload();
    const rows = widget(page, 'documents').locator('.document-row');
    const source = drawer(page).locator('.document-source');
    const write = source.getByRole('button', { name: '원본에 반영' });

    // Only text changed since the import goes back.
    await rows.filter({ hasText: '보고서' }).click();
    await expect(source).toContainText('가져온 Word 문서');
    await expect(source.getByRole('button', { name: '원본에서 업데이트' })).toBeVisible();
    await expect(write).toHaveCount(0);
    await drawer(page).getByRole('button', { name: '수정', exact: true }).click();
    await drawer(page).locator('textarea.document-editor').fill('# 일정\n\n4월 1일까지 마칩니다.');
    await drawer(page).getByRole('button', { name: '문서 저장' }).click();
    await expect(source).toContainText('가져온 뒤 본문을 고쳤습니다');
    // It asks first: the file changes, and its copy as it was is kept.
    await write.click();
    const ask = source.getByRole('group', {
      name: '고친 내용을 원본 파일에 쓰고 저장합니다. 바뀌기 전 파일은 Wiki 폴더의 ‘원본 백업’에 남깁니다. 반영할까요?',
    });
    await expect(ask).toBeVisible();
    await page.screenshot({ path: 'test-results/document-write-back.png' });
    // The file is gone by the answer, so the write is refused before Word starts, on every PC.
    // Real Office writes are covered by the backend Windows integration tests.
    await rm(file, { maxRetries: 10, retryDelay: 200 });
    await ask.getByRole('button', { name: '원본에 반영' }).click();
    await expect(source.locator('.inline-error')).toHaveText('원본 파일을 찾을 수 없습니다.');
    await drawer(page).getByRole('button', { name: '닫기', exact: true }).click();

    // Nothing was written.
    const status = await page.evaluate(() => window.privateAgent!.request('writeback.status', {}));
    expect(status).toEqual({ ok: true, result: null });
    expect(errors).toEqual([]);
  } finally {
    await app?.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});
