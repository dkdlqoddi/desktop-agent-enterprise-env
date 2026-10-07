import { test, expect, type ElectronApplication } from '@playwright/test';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { chooseStorage, drawer, goHome, launch, searchBox, widget } from './helpers';

test('workspace: focused navigation, quick actions, document filtering and responsive layouts', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'private-agent-workspace-'));
  let app: ElectronApplication | undefined;
  try {
    app = await launch(directory);
    const page = await app.firstWindow();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await chooseStorage(page, path.join(directory, 'wiki'));
    const nav = page.getByRole('navigation', { name: '화면' });
    // The default template: the greeting, the shortcuts, and briefing, reminders, tasks and documents.
    await expect(page.locator('section[data-widget]')).toHaveCount(6);
    await expect(nav.getByRole('button', { name: '홈', exact: true })).toHaveAttribute(
      'aria-current',
      'page',
    );

    // The quick action opens the import tab without adding a home widget.
    await page.getByRole('button', { name: /자료 가져오기 파일과 링크/ }).click();
    await expect(page.getByRole('tab', { name: '자료 가져오기' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    await expect(widget(page, 'import').getByLabel('가져올 링크 또는 경로')).toBeVisible();
    await page.getByRole('tab', { name: '자료 가져오기' }).press('ArrowRight');
    await expect(page.getByRole('tab', { name: '지식 관계도' })).toBeFocused();
    await expect(widget(page, 'knowledge')).toBeVisible();
    await page.getByRole('tab', { name: '지식 관계도' }).press('Home');
    await expect(page.getByRole('tab', { name: '문서', exact: true })).toBeFocused();

    // The glass surfaces blur what is behind them: the build keeps the property Chromium reads.
    expect(
      await page.locator('.topbar').evaluate((bar) => getComputedStyle(bar).backdropFilter),
    ).toContain('blur');

    // Write through the same document editor and find it in the new library.
    await page.getByRole('button', { name: '새 문서 작성', exact: true }).click();
    await drawer(page).getByLabel('문서 제목', { exact: true }).fill('디자인 회의 기록');
    await drawer(page)
      .getByRole('textbox', { name: /^내용/ })
      .fill('화면을 단순하게 정리하고 업무 흐름을 개선합니다.');
    // Unsaved writing is never thrown away unasked: Escape, the backdrop and 닫기 ask in the panel first.
    const ask = drawer(page).getByRole('group', { name: /저장하지 않은 내용이 있습니다/ });
    await page.keyboard.press('Escape');
    await expect(ask).toBeVisible();
    await expect(ask.getByRole('button', { name: '계속 쓰기' })).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(ask).toHaveCount(0);
    await page.mouse.click(5, 300); // The backdrop, left of the panel.
    await expect(ask).toBeVisible();
    await ask.getByRole('button', { name: '계속 쓰기' }).click();
    await drawer(page).getByRole('button', { name: '닫기', exact: true }).click();
    await expect(ask).toBeVisible();
    await ask.getByRole('button', { name: '계속 쓰기' }).click();
    // However often Escape comes, it only asks and goes back to the writing: Chromium closes a dialog by itself after
    // a few close requests the page refused, which would hide the writing. 취소 asks the same.
    const content = drawer(page).getByRole('textbox', { name: /^내용/ });
    for (let round = 0; round < 5; round++) {
      await content.press('End');
      await page.keyboard.type('.');
      await page.keyboard.press('Escape');
      await expect(ask).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(ask).toHaveCount(0);
      await expect(content).toBeFocused();
    }
    await drawer(page).getByRole('button', { name: '취소', exact: true }).click();
    await ask.getByRole('button', { name: '계속 쓰기' }).click();
    await expect(content).toBeFocused();
    // Closed by the browser after all, the window opens again and asks.
    await drawer(page).evaluate((dialog: HTMLDialogElement) => dialog.close());
    await expect(ask).toBeVisible();
    await ask.getByRole('button', { name: '계속 쓰기' }).click();
    expect(await drawer(page).evaluate((dialog: HTMLDialogElement) => dialog.open)).toBe(true);
    await expect(drawer(page).getByLabel('문서 제목', { exact: true })).toHaveValue(
      '디자인 회의 기록',
    );
    await content.fill('화면을 단순하게 정리하고 업무 흐름을 개선합니다.');
    await drawer(page).getByRole('button', { name: '문서 저장' }).click();
    await expect(
      drawer(page).getByRole('heading', { name: '디자인 회의 기록', exact: true }),
    ).toBeVisible();
    // 취소 of a changed document asks too, and 버리고 닫기 goes back to the document as it was.
    await drawer(page).getByRole('button', { name: '수정', exact: true }).click();
    await content.fill('버릴 내용');
    await drawer(page).getByRole('button', { name: '취소', exact: true }).click();
    await ask.getByRole('button', { name: '버리고 닫기' }).click();
    await expect(drawer(page).locator('.document-content')).toContainText('업무 흐름을 개선');
    // An inline question answered gives the keyboard back to the button it came from.
    await drawer(page).getByRole('button', { name: '삭제', exact: true }).click();
    await drawer(page)
      .getByRole('group', { name: '이 문서와 분석 결과를 삭제할까요?' })
      .getByRole('button', { name: '취소' })
      .click();
    await expect(drawer(page).getByRole('button', { name: '삭제', exact: true })).toBeFocused();
    await drawer(page).getByRole('button', { name: '닫기', exact: true }).click();
    await expect(widget(page, 'documents').locator('.document-row')).toHaveCount(1);
    const filter = page.getByLabel('보관함 문서 필터');
    await filter.fill('없는 문서');
    await expect(widget(page, 'documents')).toContainText('일치하는 문서가 없습니다');
    await filter.fill('디자인');
    await widget(page, 'documents').locator('.document-row').click();
    await expect(drawer(page)).toContainText('업무 흐름을 개선');
    await page.keyboard.press('Escape');
    await expect(drawer(page)).toHaveCount(0);
    // Closed, the window gives the keyboard back to what opened it.
    await expect(widget(page, 'documents').locator('.document-row')).toBeFocused();
    // A window with nothing to keep that the browser closed is gone, and opens again as asked.
    await page.getByRole('button', { name: '새 문서 작성', exact: true }).click();
    await drawer(page).evaluate((dialog: HTMLDialogElement) => dialog.close());
    await expect(drawer(page)).toHaveCount(0);
    await page.getByRole('button', { name: '새 문서 작성', exact: true }).click();
    await expect(drawer(page).getByLabel('문서 제목', { exact: true })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(drawer(page)).toHaveCount(0);
    // A window that fails to draw stays a window, with the error and 다시 시도, and closes as always. (A test breaks the
    // character count the editor writes.)
    await widget(page, 'documents').locator('.document-row').click();
    await page.evaluate(() => {
      const count = Number.prototype.toLocaleString;
      const test = window as unknown as { count: boolean };
      test.count = true;
      Number.prototype.toLocaleString = function (this: number, ...args: []) {
        if (test.count) throw new Error('창 시험 오류');
        return count.apply(this, args);
      };
    });
    await drawer(page).getByRole('button', { name: '수정', exact: true }).click();
    const failed = drawer(page).getByRole('alert');
    await expect(failed).toContainText('이 창을 표시하지 못했습니다.');
    await expect(failed).toContainText('창 시험 오류');
    await page.evaluate(() => {
      (window as unknown as { count: boolean }).count = false;
    });
    await failed.getByRole('button', { name: '다시 시도' }).click();
    await expect(drawer(page)).toContainText('업무 흐름을 개선');
    await page.keyboard.press('Escape');
    await expect(drawer(page)).toHaveCount(0);

    // A task added on the task page is reflected in the home and nav count.
    await nav.getByRole('button', { name: '할 일', exact: true }).click();
    await page.getByLabel('새 할 일').fill('새로운 워크스페이스 살펴보기');
    await page.getByLabel('새 할 일').press('Enter');
    await expect(widget(page, 'tasks').locator('.task-row')).toHaveCount(1);
    await goHome(page);
    await expect(widget(page, 'tasks')).toContainText('새로운 워크스페이스 살펴보기');
    await expect(nav.getByRole('button', { name: '할 일', exact: true })).toContainText('1');

    // Korean IME composition must not accidentally submit the new question form.
    await page.getByLabel('업무 질문').fill('디자인');
    await page
      .getByLabel('업무 질문')
      .dispatchEvent('keydown', { key: 'Enter', isComposing: true });
    await expect(page.getByRole('region', { name: '검색 결과' })).toHaveCount(0);
    await page.getByRole('button', { name: '질문 보내기' }).click();
    await expect(
      page.getByRole('region', { name: '검색 결과' }).locator('.result-card'),
    ).toHaveCount(1);
    await page.getByRole('button', { name: '검색 결과 닫기' }).click();
    await page.keyboard.press('Control+k');
    await expect(searchBox(page)).toBeFocused();

    await nav.getByRole('button', { name: 'AI 작업', exact: true }).click();
    await expect(widget(page, 'prompt').getByRole('combobox').locator('option')).toHaveCount(4);
    await page.getByRole('tab', { name: '예약 실행' }).click();
    await expect(widget(page, 'schedules')).toBeVisible();
    await nav.getByRole('button', { name: '활동 기록', exact: true }).click();
    await expect(widget(page, 'history')).toBeVisible();
    await page.getByRole('tab', { name: '최근 대화' }).click();
    await expect(widget(page, 'conversations')).toBeVisible();
    await nav.getByRole('button', { name: '관리', exact: true }).click();
    await expect(page.getByRole('tab', { name: 'LLM 연결' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    // The sections are a vertical tab list: the up and down keys go through them, Tab leaves it.
    await page.getByRole('tab', { name: 'LLM 연결' }).press('ArrowDown');
    await expect(page.getByRole('tab', { name: '사이트 계정' })).toBeFocused();
    await expect(page.getByRole('tab', { name: '사이트 계정' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    await page.getByRole('tab', { name: '사이트 계정' }).press('End');
    await expect(page.getByRole('tab', { name: '저장 위치·정보' })).toBeFocused();
    await expect(page.getByRole('tab', { name: 'LLM 연결' })).toHaveAttribute('tabindex', '-1');
    await goHome(page);
    await page.screenshot({ path: 'test-results/workspace-home.png', fullPage: true });

    // Icon-only navigation retains accessible names; narrow screens never scroll sideways.
    for (const width of [900, 390]) {
      await page.setViewportSize({ width, height: 844 });
      await expect(nav.getByRole('button', { name: '지식 보관함', exact: true })).toBeVisible();
      await nav.getByRole('button', { name: '지식 보관함', exact: true }).click();
      await expect(widget(page, 'documents')).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
      await goHome(page);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
    }
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await expect(page.locator('.glass-sculpture')).toHaveCSS('animation-name', 'none');
    await page.screenshot({ path: 'test-results/workspace-mobile.png', fullPage: true });
    expect(errors).toEqual([]);
  } finally {
    await app?.close();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});
