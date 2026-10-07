const { createServer } = require('node:http');

const ssoPage = (
  action,
  message = '',
) => `<!doctype html><html><body><main><h1>Example Identity Provider</h1><p>${message}</p>
<form method="post" action="${action}"><label>Account <input name="id" autocomplete="username"></label>
<label>Password <input name="pw" type="password" autocomplete="current-password"></label>
<button id="sign-in" type="submit">Sign in</button></form></main></body></html>`;

// Confluence Server's own login screen (login.action), for the 'atlassian' login method.
const formPage = (
  action,
  message = '',
) => `<!doctype html><html><head><title>Confluence 로그인</title></head><body>
<div id="main"><form id="loginform" method="post" action="${action}">
  <p class="error">${message}</p>
  <label>사용자 이름 <input id="os_username" name="os_username" type="text"></label>
  <label>비밀번호 <input id="os_password" name="os_password" type="password"></label>
  <input id="loginButton" type="submit" value="로그인">
</form></div></body></html>`;

async function confluenceServer() {
  const requests = [];
  const logins = [];
  const options = {
    forbidden: false,
    discoveryFailure: false,
    slow: false,
    auth: false,
    sso: false,
    form: false,
    rendered: false,
    // A promise the pages wait for, to keep an import running until a test lets it go on.
    hold: /** @type {Promise<void> | null} */ (null),
    // A paragraph added to every page, to change what an import finds.
    note: '',
    user: { id: 'tester', password: 'correct horse' },
  };
  let origin, ssoOrigin;
  const server = createServer((request, response) => {
    const url = new URL(request.url, 'http://localhost');
    requests.push(url.pathname + url.search);
    const redirect = (location, cookie) => {
      response.writeHead(302, { Location: location, ...(cookie ? { 'Set-Cookie': cookie } : {}) });
      response.end();
    };
    // The SSO server is a different origin (and cookie host) than Confluence.
    if (request.headers.host === new URL(ssoOrigin).host) {
      if (url.pathname !== '/sso/login') {
        response.writeHead(404);
        return response.end();
      }
      const action = `/sso/login?return=${encodeURIComponent(url.searchParams.get('return') || '')}`;
      if (request.method !== 'POST') {
        response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
        return response.end(ssoPage(action));
      }
      let body = '';
      request.on('data', (chunk) => (body += chunk));
      request.on('end', () => {
        const form = new URLSearchParams(body);
        logins.push({ id: form.get('id'), password: form.get('pw') });
        if (form.get('id') !== options.user.id || form.get('pw') !== options.user.password) {
          response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
          return response.end(ssoPage(action, 'ID 또는 비밀번호가 올바르지 않습니다.'));
        }
        const target = url.searchParams.get('return') || '';
        redirect(
          `${origin}/confluence/sso/callback?ticket=ok&return=${encodeURIComponent(target)}`,
        );
      });
      return;
    }
    if (url.pathname === '/login') {
      response.writeHead(200, {
        'Content-Type': 'text/html',
        'Set-Cookie': 'session=valid; Path=/; Max-Age=3600; HttpOnly; SameSite=Lax',
      });
      return response.end('<h1>로그인 완료</h1>');
    }
    if (url.pathname === '/confluence/sso/callback' && url.searchParams.get('ticket') === 'ok') {
      const target = new URL(url.searchParams.get('return') || '/', origin);
      return redirect(
        target.origin === origin ? target.href : origin,
        'session=valid; Path=/; Max-Age=3600; HttpOnly; SameSite=Lax',
      );
    }
    if (url.pathname === '/confluence/login.action') {
      const destination = url.searchParams.get('os_destination') || '/confluence/';
      const action = `/confluence/login.action?os_destination=${encodeURIComponent(destination)}`;
      if (request.method !== 'POST') {
        response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
        return response.end(formPage(action));
      }
      let body = '';
      request.on('data', (chunk) => (body += chunk));
      request.on('end', () => {
        const form = new URLSearchParams(body);
        logins.push({ id: form.get('os_username'), password: form.get('os_password') });
        if (
          form.get('os_username') !== options.user.id ||
          form.get('os_password') !== options.user.password
        ) {
          response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
          return response.end(formPage(action, '사용자 이름 또는 비밀번호가 올바르지 않습니다.'));
        }
        redirect(
          destination.startsWith('/') && !destination.startsWith('//')
            ? origin + destination
            : `${origin}/confluence/`,
          'session=valid; Path=/; Max-Age=3600; HttpOnly; SameSite=Lax',
        );
      });
      return;
    }
    const signedIn = request.headers.cookie?.includes('session=valid');
    if (options.sso && !signedIn)
      return redirect(`${ssoOrigin}/sso/login?return=${encodeURIComponent(origin + request.url)}`);
    if (options.form && !signedIn)
      return redirect(`/confluence/login.action?os_destination=${encodeURIComponent(request.url)}`);
    if (options.auth && !signedIn) {
      response.writeHead(401);
      return response.end('Login required');
    }
    // The site's start page names the signed-in user, as Confluence pages do.
    if (url.pathname === '/confluence/' || url.pathname === '/confluence') {
      response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      return response.end(
        `<!doctype html><html><head><meta name="ajs-context-path" content="/confluence"><meta name="ajs-remote-user" content="${signedIn ? options.user.id : ''}"><title>대시보드</title></head><body><h1>Confluence 대시보드</h1></body></html>`,
      );
    }
    const api = url.pathname.match(/\/rest\/api\/content\/(\d+)\/child\/page/);
    if (api) {
      if (options.discoveryFailure && api[1] === '2') {
        response.writeHead(403);
        return response.end('Forbidden');
      }
      const start = url.searchParams.get('start') || '0';
      const ids =
        api[1] === '1' ? (start === '0' ? ['2'] : ['4']) : api[1] === '2' ? ['3', '1'] : [];
      response.writeHead(200, { 'Content-Type': 'application/json' });
      return response.end(
        JSON.stringify({
          results: ids.map((id) => ({ id })),
          _links:
            api[1] === '1' && start === '0'
              ? { next: '/rest/api/content/1/child/page?start=1&limit=1' }
              : {},
        }),
      );
    }
    const id =
      url.searchParams.get('pageId') ||
      (url.pathname.startsWith('/confluence/display/') ? '1' : null);
    if (!id) {
      response.writeHead(404);
      return response.end('Not found');
    }
    if (options.forbidden && id === '2') {
      response.writeHead(403);
      return response.end('Forbidden');
    }
    const body = `<p>본문 ${id} 한국어 &amp; Confluence 지식</p><table><tr><th>기준</th><td>내용</td></tr></table><pre>if (x &lt; 3) {\n  return x;\n}</pre>
      <div class="code panel pdl"><div class="codeContent panelContent pdl"><pre class="syntaxhighlighter-pre" data-syntaxhighlighter-params="brush: java; gutter: false; theme: Confluence" data-theme="Confluence">int answer = ${id};</pre></div></div>
      <a href="/attachment.txt">첨부 링크</a> <a href="#section-${id}">목차</a><img src="/image.png" alt="그림 설명">
      <div class="expand-container"><div class="expand-control" role="button" tabindex="0">펼치기</div><div class="expand-content" style="display:none">접힌 세부 내용</div></div>
      <a href="javascript:alert(1)" onclick="alert(1)">위험 링크</a><form><input value="secret"></form><iframe src="about:blank"></iframe>${options.note ? `<p>${options.note}</p>` : ''}`;
    // Server/DC ships #main-content in the HTML; Cloud-like pages only render it with scripts.
    const content = options.rendered
      ? `<div id="root"></div><script>document.getElementById('root').innerHTML = ${JSON.stringify(
          `<div class="ak-renderer-document">${body.replace('본문', '렌더링된 본문')}</div>`,
        )};</script>`
      : `<div id="main-content">${body}</div>`;
    const send = () => {
      response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      response.end(`<!doctype html><html><head><meta name="ajs-page-id" content="${id}"><meta name="ajs-context-path" content="/confluence"><title>페이지 ${id}</title></head><body>
      <nav>탐색 메뉴는 저장하지 않음</nav><h1 id="title-text">페이지 ${id}</h1>
      ${content}
      <script>
        document.querySelector('.expand-control').onclick = () => { document.querySelector('.expand-content').style.display = 'block'; };
        // Like SyntaxHighlighter: the live DOM turns code macros into line-numbered tables.
        document.querySelectorAll('pre.syntaxhighlighter-pre').forEach((pre) => {
          const table = document.createElement('div');
          table.innerHTML = '<table><tr><td class="gutter">1</td><td class="code">' + pre.textContent + '</td></tr></table>';
          pre.replaceWith(table);
        });
      </script></body></html>`);
    };
    if (options.hold) void options.hold.then(() => response.destroyed || send());
    else if (options.slow) {
      const timer = setTimeout(send, 3000);
      response.on('close', () => clearTimeout(timer));
    } else send();
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  ssoOrigin = `http://localhost:${server.address().port}`;
  return {
    origin,
    ssoOrigin,
    url: `${origin}/confluence/pages/viewpage.action?pageId=1`,
    // The site's base address, as saved for its account.
    site: `${origin}/confluence`,
    options,
    requests,
    logins,
    close: () =>
      new Promise((resolve) => {
        server.close(resolve);
        server.closeAllConnections();
      }),
  };
}

module.exports = { confluenceServer, ssoPage, formPage };
