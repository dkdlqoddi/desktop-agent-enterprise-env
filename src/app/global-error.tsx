'use client';

// The whole screen when drawing it failed outside the parts that recover on their own (each widget, page and the
// document window). It replaces the app's document, so it brings its own styles. The window allows no form and no
// navigation (electron/main.cjs: form-action 'none', will-navigate), so 다시 시도 draws the app again in place.
const STYLE = `
body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #edf0f3; color: #1c2433;
  font: 14px/1.55 'Segoe UI', 'Malgun Gothic', '맑은 고딕', system-ui, sans-serif; }
main { max-width: 520px; margin: 24px; padding: 28px 30px; border: 1px solid #d9dfe6; border-radius: 8px;
  background: #fff; }
h1 { margin: 0 0 8px; font-size: 18px; }
p { margin: 0 0 12px; color: #445063; }
code { display: block; margin-bottom: 18px; color: #b63a34; font: 12px 'Cascadia Code', Consolas, monospace;
  overflow-wrap: anywhere; }
button { min-height: 34px; padding: 6px 14px; border: 1px solid #1c2433; border-radius: 5px; background: #1c2433;
  color: #fff; font: inherit; font-weight: 600; cursor: pointer; }
button:focus-visible { outline: 2px solid #1c2433; outline-offset: 2px; }
`;

export default function GlobalError({ error, reset }: { error: unknown; reset: () => void }) {
  return (
    <html lang="ko">
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
