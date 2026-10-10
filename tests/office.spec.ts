import { test, expect, type ElectronApplication } from '@playwright/test';
import { spawnSync } from 'node:child_process';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { addWidget, chooseStorage, drawer, launch, results, searchBox, widget } from './helpers';

test('Office and RTF: one import box for files and folders, capabilities and IPC validation', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'office-desktop-'));
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
    await app.evaluate(({ dialog }) => {
      dialog.showOpenDialog = (async (
        _window: unknown,
        options: { properties: string[]; filters?: { extensions: string[] }[] },
      ) => {
        if (options.properties.includes('openDirectory'))
          return { canceled: false, filePaths: ['C:\\Documents'] };
        // One picker for every kind of file the box imports.
        if (
          options.filters?.[0].extensions.join(',') !==
          'md,markdown,txt,ppt,pptx,pptm,xls,xlsx,xlsm,xlsb,doc,docx,docm,vsd,vsdx,vsdm,rtf,' +
            'png,jpg,jpeg,jfif,gif,bmp,webp,avif,svg,ico,tif,tiff,emf,wmf'
        )
          throw new Error('Import file filters missing');
        return { canceled: false, filePaths: ['C:\\Documents\\업무 보고.pptx'] };
      }) as typeof dialog.showOpenDialog;
    });
    await importer.getByRole('button', { name: '파일 선택' }).click();
    await expect(input).toHaveValue('C:\\Documents\\업무 보고.pptx');
    await expect(importer.locator('.import-kind')).toHaveText('Office 문서 · PowerPoint');
    await expect(importer.getByLabel(/가져온 뒤 LLM으로/)).toBeDisabled();
    const capability = await page.evaluate(() =>
      window.privateAgent!.request('office.capabilities'),
    );
    expect(capability.ok).toBe(true);
    if (process.platform !== 'win32') {
      await expect(
        importer.getByText(/Microsoft Word·Excel·PowerPoint·Visio 데스크톱 앱이 설치된 Windows/),
      ).toBeVisible();
      await expect(importer.getByRole('button', { name: 'Wiki로 가져오기' })).toBeDisabled();
    }
    // A folder asks which files to import and includes subfolders unless told otherwise.
    await importer.getByRole('button', { name: '폴더 선택' }).click();
    await expect(input).toHaveValue('C:\\Documents');
    await expect(importer.locator('.import-kind')).toHaveText('폴더');
    await expect(importer.getByRole('radio', { name: 'Office 문서' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    await expect(importer.getByLabel('하위 폴더 포함')).toBeChecked();
    await importer.getByRole('radio', { name: 'RTF 문서(.rtf)' }).click();
    await expect(importer.getByRole('radio', { name: 'RTF 문서(.rtf)' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    await importer.getByRole('radio', { name: '이미지' }).click();
    // Images need no Office, so a folder of them can be imported on any PC.
    await expect(importer.getByRole('button', { name: 'Wiki로 가져오기' })).toBeEnabled();
    // Text files are read through a hidden Notepad, which only Windows has.
    await importer.getByRole('radio', { name: '텍스트(.txt)' }).click();
    await expect(importer.getByText(/창 없이 띄운 메모장으로/)).toBeVisible();
    if (process.platform !== 'win32') {
      await expect(importer.getByText(/Windows에서 메모장으로 읽어/)).toBeVisible();
      await expect(importer.getByRole('button', { name: 'Wiki로 가져오기' })).toBeDisabled();
    }
    await page.screenshot({ path: 'test-results/import-folder.png', fullPage: true });
    // Typed paths are recognized by their extension, Office files with the app that reads them.
    await input.fill('C:\\Docs\\시스템 구성도.VSDX');
    await expect(importer.locator('.import-kind')).toHaveText('Office 문서 · Visio');
    await input.fill('C:\\Docs\\정산.xlsb');
    await expect(importer.locator('.import-kind')).toHaveText('Office 문서 · Excel');
    await input.fill('C:\\Docs\\회의 안내.rtf');
    await expect(importer.locator('.import-kind')).toHaveText('RTF 문서(.rtf)');
    await input.fill('C:\\Docs\\사진.PNG');
    await expect(importer.locator('.import-kind')).toHaveText('이미지 파일');
    // A .txt file is a job of the backend's Notepad reader; Markdown stays with the main process.
    await input.fill('C:\\Docs\\회의 메모.TXT');
    await expect(importer.locator('.import-kind')).toHaveText('텍스트 파일');
    await input.fill('C:\\Docs\\설명.md');
    await expect(importer.locator('.import-kind')).toHaveText('Markdown 파일');
    await input.fill('C:\\Docs\\압축.zip');
    await expect(importer.locator('.import-kind')).toContainText('지원하지 않는 파일');
    await expect(importer.getByRole('button', { name: 'Wiki로 가져오기' })).toHaveCount(0);
    const invalid = await page.evaluate(() =>
      window.privateAgent!.request('office.start', { path: 'relative.docx', mode: 'file' }),
    );
    expect(invalid.ok).toBe(false);
    expect(invalid.error).toContain('전체 경로');
    const text = await page.evaluate(() =>
      window.privateAgent!.request('files.import_text', { path: '/tmp/x.exe', analyze: false }),
    );
    expect(text.ok).toBe(false);
    expect(text.error).toContain('.md, .markdown 파일만');
    const notepad = await page.evaluate(() =>
      window.privateAgent!.request('text.start', { path: 'relative.txt', mode: 'file' }),
    );
    expect(notepad.ok).toBe(false);
    expect(notepad.error).toContain('전체 경로');
    // Reading a presentation raises the PowerPoint caution from the main process. A focused window
    // shows it itself (a system notification otherwise), and it stays until it is closed.
    await app.evaluate(({ BrowserWindow }, message) => {
      BrowserWindow.getAllWindows()[0].webContents.send('import:notice', message);
    }, 'PowerPoint 파일을 읽습니다. 가져올 파일을 편집 중이면 먼저 닫아 주세요.');
    const notice = page.locator('.toast.notice');
    await expect(notice).toHaveText(/PowerPoint 파일을 읽습니다\./);
    await expect(notice).toHaveAttribute('role', 'alert');
    await page.screenshot({ path: 'test-results/import-notice.png' });
    await notice.getByRole('button', { name: '알림 닫기' }).click();
    await expect(notice).toHaveCount(0);
    expect(errors).toEqual([]);
  } finally {
    await app?.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});

// A 1×1 red PNG.
const PNG =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==';
// A page as the Word reader writes it, with markup a document could carry that must never run or load.
const SEED = `
import hashlib, sqlite3, sys
sys.path.insert(0, "backend")
from agent.html_document import index_markdown, page
png = sys.argv[2]
body = (
    '<h1 style="color:#2F5496">개요</h1>'
    '<p>캐시 정책을 <strong>정리</strong>합니다. <a href="https://wiki.test/x">위키</a> · <a href="#notes">각주로</a></p>'
    '<table><tr><td style="background-color:#FFF2CC">셀</td></tr></table>'
    '<p><math alttext="x^(2)"><msup><mi>x</mi><mn>2</mn></msup></math></p>'
    f'<p><img src="data:image/png;base64,{png}" alt="빨간 점" width="30" height="20"></p>'
    '<p><img src="https://evil.test/track.png" alt="추적"><img src="x" onerror="window.hacked=1" alt="깨짐"></p>'
    '<script>window.hacked = 1</script><p style="background:url(https://evil.test/a)">배경</p>'
    '<div id="notes" onclick="window.hacked=1">각주 영역</div>'
    # Inline styles whose address hides from a check of the text: in a quoted comment, an escape, a custom property.
    '<p id="quoted" style=\\'font-family:"/*"; background-image:url(https://evil.test/b.png); font-family:"*/"\\'>주석</p>'
    r'<p id="escaped" style="color:#C00000; background-image:u\\72l(https://evil.test/c.png)">이스케이프</p>'
    '<p id="set" style="background-image:-webkit-image-set(url(https://evil.test/d.png) 1x)">그림 묶음</p>'
    '<p id="shorthand" style="background:url(https://evil.test/e.png) #FFF2CC; margin:0 0 8pt">줄임 속성</p>'
    r'<p id="variable" style="--x:u\\72l(https://evil.test/f.png); background-image:var(--x)">변수</p>'
    '<p class="MsoNormal">본문 문단</p>'
)
html = page("보고서", body + '<div class="layer">세션이 만료되었습니다</div><p><mark class="search-hit">가짜</mark></p>', "word").replace("</head>", (
    "<style>:host{position:fixed;inset:0}</style>"
    "<style>@scope{:scope{contain:none!important;position:fixed!important}}</style>"
    "<style>:/**/host{position:fixed!important;inset:0}</style>"
    "<style>.layer{position:fixed;top:0;left:0;width:240px;height:40px;z-index:2147483647;background:#fff}</style>"
    # A Word page's stylesheet: its font goes, its paragraph styles stay; addresses behind escapes go, rule by rule.
    "<style>@font-face{font-family:Custom;src:url(https://evil.test/g.woff)} p.MsoNormal{padding-left:6pt;color:#1F3864}</style>"
    r"<style>.MsoNormal{background:u\\72l(https://evil.test/h.png)} #notes{--img:u\\72l(https://evil.test/i.png);background-image:var(--img)}</style>"
    "</head>"))
content = index_markdown(html)
db = sqlite3.connect(sys.argv[1])
db.execute("INSERT INTO documents(id,title,content,format,created_at,updated_at) VALUES ('seeded','보고서',?,'html','2026-10-01T00:00:00+00:00','2026-10-01T00:00:00+00:00')", (content,))
db.execute("INSERT INTO document_html VALUES ('seeded', ?)", (html,))
db.execute("INSERT INTO office_sources(doc_id,source_path,source_hash,application,markdown,imported_at) VALUES ('seeded','C:\\\\Docs\\\\보고서.docx',?,'Word',?,'2026-10-01T00:00:00+00:00')",
           (hashlib.sha256(b"seed").hexdigest(), content))
db.commit()
`;

test('Imported files are HTML pages: shown as pages, cleaned, searchable, with their raw HTML', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'html-pages-'));
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    const page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const wiki = path.join(directory, 'wiki');
    await chooseStorage(page, wiki);
    await addWidget(page, '가져오기');

    // An image file goes through the import box like any file and becomes a page of its own.
    const picture = path.join(directory, '조직도.png');
    await writeFile(picture, Buffer.from(PNG, 'base64'));
    await app.evaluate(({ dialog }, file) => {
      dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [file] });
    }, picture);
    const importer = widget(page, 'import');
    await importer.getByRole('button', { name: '파일 선택' }).click();
    await expect(importer.locator('.import-kind')).toHaveText('이미지 파일');
    await importer.getByRole('button', { name: 'Wiki로 가져오기' }).click();
    await expect(importer.locator('.job')).toContainText('가져오기가 끝났습니다');
    await expect(importer.locator('.job-counts')).toContainText('저장 1');
    await widget(page, 'documents').locator('.document-row').filter({ hasText: '조직도' }).click();
    const view = drawer(page).locator('[data-html-document]');
    await expect(view.locator('img')).toHaveAttribute('alt', '조직도.png');
    expect(
      await view.locator('img').evaluate((image: HTMLImageElement) => image.naturalWidth),
    ).toBe(1);
    await expect(view).toContainText('조직도.png · PNG · 1×1px');
    await expect(drawer(page).locator('.document-source')).toContainText('가져온 이미지');
    await drawer(page).getByRole('button', { name: '닫기', exact: true }).click();

    // A Word page: its own styles inside, nothing that runs or loads from elsewhere.
    const python = path.resolve(
      'backend',
      '.venv',
      process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
    );
    const seeded = spawnSync(python, ['-c', SEED, path.join(wiki, 'wiki.db'), PNG], {
      encoding: 'utf8',
      env: { ...process.env, PYTHONPATH: '' },
    });
    expect(seeded.stderr).toBe('');
    await page.reload();
    await widget(page, 'documents').locator('.document-row').filter({ hasText: '보고서' }).click();
    await expect(view.locator('h1')).toHaveText('개요');
    expect(await view.locator('h1').evaluate((h1) => getComputedStyle(h1).color)).toBe(
      'rgb(47, 84, 150)',
    );
    expect(await view.locator('td').evaluate((td) => getComputedStyle(td).backgroundColor)).toBe(
      'rgb(255, 242, 204)',
    );
    await expect(view.locator('math msup mi')).toHaveText('x');
    expect(
      await view
        .locator('img[alt="빨간 점"]')
        .evaluate((image: HTMLImageElement) => image.naturalWidth),
    ).toBe(1);
    await expect(view.locator('.missing')).toHaveText(['[그림: 추적]', '[그림: 깨짐]']);
    await expect(view.locator('script')).toHaveCount(0);
    expect(await view.locator('#notes').getAttribute('onclick')).toBeNull();
    // CSS is kept declaration by declaration, as the browser reads it: no comment or escape hides an address, and
    // what loads goes without the rest of its style. The page's text and styles name no address any more.
    const style = (selector: string, property: string) =>
      view
        .locator(selector)
        .evaluate((element, name) => getComputedStyle(element).getPropertyValue(name), property);
    for (const selector of [
      'p:has-text("배경")',
      '#quoted',
      '#escaped',
      '#set',
      '#shorthand',
      '#variable',
      'p.MsoNormal',
      '#notes',
    ])
      expect(await style(selector, 'background-image')).toBe('none');
    expect(await view.locator('#quoted').getAttribute('style')).toBe('font-family: "*/";');
    expect(await style('#escaped', 'color')).toBe('rgb(192, 0, 0)');
    expect(await view.locator('#set').getAttribute('style')).toBeNull();
    expect(await style('#shorthand', 'background-color')).toBe('rgb(255, 242, 204)');
    expect(await style('#shorthand', 'margin-bottom')).toBe('10.6667px');
    expect(await style('p.MsoNormal', 'color')).toBe('rgb(31, 56, 100)');
    expect(await style('p.MsoNormal', 'padding-left')).toBe('8px');
    expect(
      await view.evaluate((host) =>
        [
          ...[...host.shadowRoot!.querySelectorAll('style')].map((sheet) => sheet.textContent),
          ...[...host.shadowRoot!.querySelectorAll('[style]')].map((element) =>
            element.getAttribute('style'),
          ),
        ].join('\n'),
      ),
    ).not.toMatch(/evil\.test|@font-face|\\/);
    expect(await view.evaluate((host) => getComputedStyle(host).position)).toBe('static');
    // Its own CSS cannot reach past the page, through @scope or a comment inside :host, or with a fixed layer.
    // Both are measured where the drawer stopped: while it slides in, the second would be taken further left.
    await drawer(page).evaluate((dialog) =>
      Promise.all(dialog.getAnimations().map((animation) => animation.finished)),
    );
    const frame = (await drawer(page).locator('.html-document-frame').boundingBox())!;
    const layer = (await view.locator('.layer').boundingBox())!;
    expect(layer.x).toBeGreaterThanOrEqual(frame.x - 1);
    expect(layer.y).toBeGreaterThanOrEqual(frame.y - 1);
    // A mark of the page's own is no search match.
    await expect(view.locator('mark')).not.toHaveClass(/search-hit/);
    // Links stay in the page; an address shows as its title.
    const before = page.url();
    await view.getByRole('link', { name: '위키' }).click();
    await view.getByRole('link', { name: '각주로' }).click();
    expect(page.url()).toBe(before);
    await expect(view.getByRole('link', { name: '위키' })).toHaveAttribute(
      'title',
      'https://wiki.test/x',
    );
    expect(
      await page.evaluate(() => (window as unknown as { hacked?: number }).hacked),
    ).toBeUndefined();
    await page.screenshot({ path: 'test-results/html-document.png' });

    // The raw HTML is the stored page; the download carries it whole.
    await drawer(page).getByRole('button', { name: '원문 HTML' }).click();
    const raw = drawer(page).getByLabel('HTML 원문');
    await expect(raw).toHaveValue(/^<!doctype html>/);
    await expect(raw).toHaveValue(/<script>window\.hacked = 1<\/script>/);
    await expect(raw).toHaveValue(/data:image\/png;base64,/);
    const download = drawer(page).getByRole('link', { name: '.html 다운로드' });
    await expect(download).toHaveAttribute('download', '보고서.html');
    await expect(download).toHaveAttribute('href', /^blob:/);
    await page.screenshot({ path: 'test-results/html-source.png' });
    await drawer(page).getByRole('button', { name: '문서로 돌아가기' }).click();
    await expect(view.locator('h1')).toHaveText('개요');
    await drawer(page).getByRole('button', { name: '닫기', exact: true }).click();

    // A page that fails to draw shows the error in its place, and the window keeps 수정 and 원문 HTML. (A test breaks
    // the parser the page is read with.)
    await page.evaluate(() => {
      const parse = DOMParser.prototype.parseFromString;
      const test = window as unknown as { parse: boolean };
      test.parse = true;
      DOMParser.prototype.parseFromString = function (
        this: DOMParser,
        ...args: Parameters<DOMParser['parseFromString']>
      ) {
        if (test.parse) throw new Error('페이지 시험 오류');
        return parse.apply(this, args);
      };
    });
    await widget(page, 'documents').locator('.document-row').filter({ hasText: '보고서' }).click();
    const broken = drawer(page).locator('.document-content').getByRole('alert');
    await expect(broken).toContainText('문서 내용을 표시하지 못했습니다.');
    await expect(drawer(page).getByRole('button', { name: '원문 HTML' })).toBeEnabled();
    await expect(drawer(page).getByRole('button', { name: '수정', exact: true })).toBeEnabled();
    await page.evaluate(() => {
      (window as unknown as { parse: boolean }).parse = false;
    });
    await broken.getByRole('button', { name: '다시 시도' }).click();
    await expect(view.locator('h1')).toHaveText('개요');
    await drawer(page).getByRole('button', { name: '닫기', exact: true }).click();

    // Search reads the page's text and marks its words inside the page.
    await searchBox(page).fill('캐시');
    await searchBox(page).press('Enter');
    await results(page)
      .locator('.result-card')
      .filter({ hasText: '보고서' })
      .locator('.document-open')
      .click();
    await expect(page.getByRole('group', { name: '검색어 위치 이동' })).toContainText('1곳');
    await expect(view.locator('mark.search-hit')).toHaveText('캐시');
    await page.getByRole('button', { name: '다음 검색어 위치' }).click();
    await expect(view.locator('mark.search-hit.current')).toHaveCount(1);

    // Edited text makes it a Markdown document; the imported page stays its original.
    await drawer(page).getByRole('button', { name: '수정', exact: true }).click();
    await expect(drawer(page).getByText(/파일에서 가져온 HTML 문서의 글입니다/)).toBeVisible();
    await drawer(page).locator('textarea.document-editor').fill('# 고친 개요\n\n직접 고친 글');
    await drawer(page).getByRole('button', { name: '문서 저장' }).click();
    await expect(drawer(page).getByRole('heading', { name: '고친 개요' })).toBeVisible();
    await expect(view).toHaveCount(0);
    await drawer(page).getByRole('button', { name: '원문 HTML' }).click();
    await expect(drawer(page).getByLabel('HTML 원문')).toHaveValue(/개요<\/h1>/);
    expect(errors).toEqual([]);
  } finally {
    await app?.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});

// A Word file too long for one document, saved as parts by the backend's own code.
const PARTS_SEED = `
import sys
sys.path.insert(0, "backend")
from agent.db import Database
from agent.html_document import page
from agent.parts import insert, page_parts
split = page_parts(page("긴 보고서", "".join(f"<h2>장 {i}</h2><p>{'긴 내용 ' * 300}</p>" for i in range(120)), "word"))
with Database(sys.argv[1]).connect() as db:
    insert(db, split, "긴 보고서", "2026-10-01T00:00:00+00:00", lambda ident, number, text: db.execute(
        "INSERT INTO office_sources(doc_id,source_path,source_hash,application,markdown,imported_at,part,parts) "
        "VALUES (?,?,?,?,?,?,?,?)", (ident, "C:\\\\Docs\\\\긴 보고서.docx", "seed", "Word", text,
                                     "2026-10-01T00:00:00+00:00", number, len(split.parts))))
print(len(split.parts))
`;

test('A file too long for one document opens as parts that lead to each other', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'document-parts-'));
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    const page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const wiki = path.join(directory, 'wiki');
    await chooseStorage(page, wiki);
    const python = path.resolve(
      'backend',
      '.venv',
      process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
    );
    const seeded = spawnSync(python, ['-c', PARTS_SEED, path.join(wiki, 'wiki.db')], {
      encoding: 'utf8',
      env: { ...process.env, PYTHONPATH: '' },
    });
    expect(seeded.stderr).toBe('');
    expect(seeded.stdout.trim()).toBe('3');
    await page.reload();
    // The document list shows the import as one entry that counts its parts; its window leads from part to part.
    const entry = widget(page, 'documents')
      .locator('.document-row')
      .filter({ hasText: /긴 보고서 \([123]\/3\)/ });
    await expect(entry).toHaveCount(1);
    await expect(entry).toContainText('3개로 나눔');
    await entry.click();
    const parts = drawer(page).getByRole('navigation', { name: '나누어 저장한 문서' });
    const view = drawer(page).locator('[data-html-document]');
    await expect(parts).toContainText('긴 글을 나누어 저장한 3개 중');
    // The entry is the part updated last, any of the three here: back to the first one.
    const opened = Number(/3개 중 (\d)번째/.exec((await parts.textContent()) ?? '')![1]);
    for (let part = opened; part > 1; part--) {
      await parts.getByRole('button', { name: '이전 부분' }).click();
      await expect(parts).toContainText(`3개 중 ${part - 1}번째`);
    }
    await expect(parts).toContainText('긴 글을 나누어 저장한 3개 중 1번째');
    await expect(view.locator('h2').first()).toHaveText('장 0');
    await expect(parts.getByRole('button', { name: '이전 부분' })).toBeDisabled();
    await page.screenshot({ path: 'test-results/document-parts.png' });

    // Each part is a page of its own, and the next one goes on where this one ends.
    const last = await view.locator('h2').last().textContent();
    await parts.getByRole('button', { name: '다음 부분' }).click();
    await expect(drawer(page).getByRole('heading', { name: '긴 보고서 (2/3)' })).toBeVisible();
    await expect(parts).toContainText('3개 중 2번째');
    const first = Number((await view.locator('h2').first().textContent())!.replace('장 ', ''));
    expect(first).toBe(Number(last!.replace('장 ', '')) + 1);
    await parts.getByRole('button', { name: '다음 부분' }).click();
    await expect(parts).toContainText('3개 중 3번째');
    await expect(parts.getByRole('button', { name: '다음 부분' })).toBeDisabled();
    await expect(view.locator('h2').last()).toHaveText('장 119');
    await parts.getByRole('button', { name: '이전 부분' }).click();
    await expect(drawer(page).getByRole('heading', { name: '긴 보고서 (2/3)' })).toBeVisible();
    expect(errors).toEqual([]);
  } finally {
    await app?.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});
