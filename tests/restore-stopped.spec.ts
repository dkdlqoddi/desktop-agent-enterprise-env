import { test, expect, type ElectronApplication } from '@playwright/test';
import { mkdtemp, readdir, realpath, rename, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { chooseStorage, launch, openAdmin, openDashboard } from './helpers';

// A restore is carried out at the start of the backend by two renames: the Wiki moves into backups/ as
// wiki-before-restore-….db, and the copy of the backup takes the name wiki.db. A start stopped between them leaves a
// folder without wiki.db. The app must not report that the Wiki cannot be found: it starts the backend, which finishes
// the restore.
test('a start stopped between the two renames of a restore is finished by the next start of the app', async () => {
  test.setTimeout(120_000);
  const directory = await realpath(await mkdtemp(path.join(tmpdir(), 'private-agent-stopped-')));
  const wikiDirectory = path.join(directory, 'wiki');
  const backups = path.join(wikiDirectory, 'backups');
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    let page = await app.firstWindow();
    await chooseStorage(page, wikiDirectory);
    const request = async <T>(method: string, params: Record<string, unknown> = {}) => {
      const reply = await page.evaluate(
        ([name, values]) => window.privateAgent!.request(name, values),
        [method, params] as const,
      );
      expect(reply.ok, `${method}: ${JSON.stringify(reply)}`).toBe(true);
      return reply.result as T;
    };
    await request('wiki.save', { title: '백업 전 문서', content: '백업에 들어가는 문서입니다.' });
    const { file } = await request<{ file: string }>('wiki.backup');
    await request('wiki.save', { title: '백업 뒤 문서', content: '복원하면 사라지는 문서입니다.' });
    await request('wiki.restore_backup', { file });
    await app.close();
    app = undefined;

    // What the backend leaves when it stops after moving the Wiki aside, before the copy takes its name.
    const aside = 'wiki-before-restore-20261009-093000.db';
    for (const suffix of ['-wal', '-shm', '']) {
      await rename(
        path.join(wikiDirectory, `wiki.db${suffix}`),
        path.join(backups, `${aside}${suffix}`),
      ).catch((error) => {
        if (suffix === '' || error.code !== 'ENOENT') throw error;
      });
    }
    expect(await readdir(wikiDirectory)).not.toContain('wiki.db');

    app = await launch(directory);
    page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    // Not the first-run screen with "찾을 수 없습니다": the Wiki opens, with the backup's documents.
    await openDashboard(page, 30_000);
    const titles = (await request<{ title: string }[]>('wiki.list')).map((doc) => doc.title);
    expect(titles).toContain('백업 전 문서');
    expect(titles).not.toContain('백업 뒤 문서');
    await openAdmin(page, '저장 위치·정보');
    await expect(page.locator('.restore-result')).toContainText('되돌렸습니다');
    await expect(page.locator('.restore-pending')).toHaveCount(0);
    expect(await readdir(wikiDirectory)).toContain('wiki.db');
    expect(await readdir(backups)).toContain(aside);
    expect(errors).toEqual([]);
  } finally {
    await app?.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});
