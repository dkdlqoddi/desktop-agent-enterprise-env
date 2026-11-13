'use client';
import { useState } from 'react';
import { storedTheme, type Theme } from '@/components/theme';

// The whole screen when drawing it failed outside the parts that recover on their own (each widget, page and the
// document window). It replaces the app's document, so it brings its own styles: the app's stylesheets may be what
// failed to load. The window allows no form and no navigation (electron/main.cjs: form-action 'none', will-navigate),
// so 다시 시도 draws the app again in place.

// The colors of src/styles/theme.css it draws with, by the palette's names, in the light theme and the dark one; the
// button's fill is the same in both. tests/theme.test.cjs keeps them the same as the palette.
const LIGHT = {
  canvas: '#f3f5fa',
  panel: '#ffffff',
  ink: '#252c40',
  'ink-soft': '#434f6b',
  rule: '#e0e5ef',
  danger: '#b63a34',
  focus: '#7289e6',
};
const DARK: typeof LIGHT = {
  canvas: '#0f121a',
  panel: '#181c27',
  ink: '#e5e8f0',
  'ink-soft': '#cfd5e3',
  rule: '#2c3243',
  danger: '#f2857f',
  focus: '#8fa1f0',
};
const FILL = {
  'accent-fill': '#4e64cb',
  'accent-fill-hover': '#4155ba',
  'on-fill': '#ffffff',
};
const palette = (colors: Record<string, string>) =>
  Object.entries(colors)
    .map(([name, value]) => `--${name}: ${value};`)
    .join(' ');

// As theme.css has it: `data-theme` on <html> fixes the theme, and without it Windows' own decides.
const STYLE = `
:root { color-scheme: light; ${palette({ ...LIGHT, ...FILL })} }
:root[data-theme='dark'] { color-scheme: dark; ${palette(DARK)} }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme='light']) { color-scheme: dark; ${palette(DARK)} }
}
body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: var(--canvas);
  color: var(--ink); font: 0.875rem/1.55 'Segoe UI', 'Malgun Gothic', '맑은 고딕', system-ui, sans-serif; }
main { max-width: 520px; margin: 24px; padding: 28px 30px; border: 1px solid var(--rule); border-radius: 8px;
  background: var(--panel); }
h1 { margin: 0 0 8px; font-size: 1.125rem; }
p { margin: 0 0 12px; color: var(--ink-soft); }
code { display: block; margin-bottom: 18px; color: var(--danger);
  font: 0.75rem 'Cascadia Code', Consolas, monospace; overflow-wrap: anywhere; }
button { min-height: 34px; padding: 6px 14px; border: 1px solid transparent; border-radius: 5px;
  background: var(--accent-fill); color: var(--on-fill); font: inherit; font-weight: 600; cursor: pointer; }
button:hover { background: var(--accent-fill-hover); }
button:focus-visible { outline: 3px solid var(--focus); outline-offset: 3px; }
`;

export default function GlobalError({ error, reset }: { error: unknown; reset: () => void }) {
  // The theme the user chose (components/theme.ts). The <html> this page draws replaces the app's, and with it the
  // data-theme the app's script set before the first paint.
  const [theme] = useState<Theme>(storedTheme);
  return (
    <html lang="ko" data-theme={theme === 'system' ? undefined : theme}>
      <body>
        <style>{STYLE}</style>
        <main role="alert">
          <h1>화면을 표시하지 못했습니다</h1>
          <p>
            앱 화면을 그리다 오류가 났습니다. Wiki에 저장한 문서와 설정은 그대로입니다. 다시
            시도해도 같으면 앱을 닫았다가 다시 실행해 주세요.
          </p>
          {error instanceof Error && error.message && <code>{error.message}</code>}
          <button type="button" autoFocus onClick={() => reset()}>
            다시 시도
          </button>
        </main>
      </body>
    </html>
  );
}
