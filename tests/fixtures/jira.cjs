const { createServer } = require('node:http');
const { ssoPage } = require('./confluence.cjs');

// Synthetic JIRA entry page with a link to the test identity provider.
const loginPage = (sso) =>
  `<!doctype html><html><body><a href="${sso}">SSO Login</a></body></html>`;

const PENDING_PAGE = `<!doctype html><html><head><title>SSO 로그인</title></head><body>
<p>승인 대기 중입니다. 등록된 기기에서 로그인을 승인해 주세요.</p></body></html>`;

// JIRA Server's own login form (login.jsp), for the 'atlassian' login method.
const formPage = (
  action,
  message = '',
) => `<!doctype html><html><head><title>JIRA 로그인</title></head><body>
<form id="login-form" method="post" action="${action}">
  <p class="error">${message}</p>
  <label>사용자 이름 <input id="login-form-username" name="os_username" type="text"></label>
  <label>비밀번호 <input id="login-form-password" name="os_password" type="password"></label>
  <input id="login-form-submit" type="submit" value="로그인">
</form></body></html>`;

// A login screen none of the login methods fits, for the LLM's help: the account first, then the
// password, sent by a span that only a script made clickable. A refused login shows the password step
// again.
const stepsPage = (
  action,
  message = '',
) => `<!doctype html><html><head><title>통합 로그인</title></head><body>
<h1>통합 로그인</h1><p>${message}</p>
<form id="steps" method="post" action="${action}">
  <div><label>계정 <input id="account" name="login_id" autocomplete="off"></label><button type="button" id="next">다음</button></div>
  <div id="second"${message ? '' : ' hidden'}><label>암호 <input id="passcode" name="passcode" type="password"></label>
    <span id="go" style="cursor:pointer">확인</span></div>
</form>
<script>
  document.getElementById('next').onclick = () => { document.getElementById('second').hidden = false; };
  document.getElementById('go').onclick = () => document.getElementById('steps').submit();
</script>
</body></html>`;

const USERS = { tester: '테스터', kim: '김개발' };
const ISSUES = {
  'TEAM-1': {
    summary: '로그인 화면 오류',
    assignee: 'kim',
    reporter: 'tester',
    text: '로그인 버튼 오류',
  },
  'TEAM-2': {
    summary: '결제 모듈 개선',
    assignee: 'tester',
    reporter: 'kim',
    text: '결제 응답 시간',
  },
  'TEAM-3': { summary: '로그인 세션 만료', assignee: 'kim', reporter: 'tester', text: '세션 만료' },
};
const user = (name) => ({ name, displayName: USERS[name] });

function issueJson(key, origin) {
  const data = ISSUES[key];
  return {
    key,
    fields: {
      summary: data.summary,
      project: { key: 'TEAM', name: '팀 프로젝트' },
      issuetype: { name: '버그' },
      status: { name: data.status ?? 'Open' },
      assignee: user(data.assignee),
      reporter: user(data.reporter),
      created: '2026-09-01T10:23:45.000+0900',
      description: data.text,
      customfield_10001: '로그인 후 목록이 보여야 한다',
      attachment: [
        { filename: '설계.txt', content: `${origin}/jira/secure/attachment/1/design.txt` },
      ],
      comment: {
        total: 1,
        comments: [
          { id: 'c1', author: user('kim'), body: '마지막 댓글 검색어', created: '2026-09-01' },
        ],
      },
    },
    renderedFields: {
      description: `<h1>재현 절차</h1><p>${data.text}</p><div class="code panel"><pre class="code-java">int answer = 1;</pre></div><script>secret()</script>`,
      customfield_10001: '<p>로그인 후 목록이 보여야 한다</p>',
      comment: { comments: [{ id: 'c1', body: '<p>마지막 댓글 검색어</p>' }] },
    },
    names: { customfield_10001: '인수 조건' },
  };
}

async function jiraServer() {
  const requests = [];
  const logins = [];
  const jql = [];
  // two-step screen no login method fits; pending: the SSO accepts logins but never returns to JIRA.
  const options = {
    slow: false,
    form: false,
    steps: false,
    pending: false,
    user: { id: 'tester', password: 'correct horse' },
  };
  let origin, ssoOrigin;
  const server = createServer((request, response) => {
    const url = new URL(request.url, 'http://localhost');
    requests.push(url.pathname + url.search);
    const send = (status, body, type = 'text/html; charset=utf-8', headers = {}) => {
      response.writeHead(status, { 'Content-Type': type, ...headers });
      response.end(body);
    };
    const json = (value, status = 200) => send(status, JSON.stringify(value), 'application/json');
    const redirect = (location, cookie) =>
      send(302, '', 'text/plain', {
        Location: location,
        ...(cookie ? { 'Set-Cookie': cookie } : {}),
      });
    if (request.headers.host === new URL(ssoOrigin).host) {
      if (url.pathname !== '/sso/login') return send(404, 'Not found');
      const target = url.searchParams.get('return') || '';
      const action = `/sso/login?return=${encodeURIComponent(target)}`;
      if (request.method !== 'POST') return send(200, ssoPage(action));
      let body = '';
      request.on('data', (chunk) => (body += chunk));
      request.on('end', () => {
        const form = new URLSearchParams(body);
        logins.push({ id: form.get('id'), password: form.get('pw') });
        if (form.get('id') !== options.user.id || form.get('pw') !== options.user.password)
          return send(200, ssoPage(action, 'ID 또는 비밀번호가 올바르지 않습니다.'));
        if (options.pending) return send(200, PENDING_PAGE);
        redirect(`${origin}/jira/sso/callback?ticket=ok&return=${encodeURIComponent(target)}`);
      });
      return;
    }
    const signedIn = request.headers.cookie?.includes('session=valid');
    if (url.pathname === '/jira/login.jsp') {
      const destination = url.searchParams.get('os_destination') || '/';
      const target = `${origin}/jira${destination.startsWith('/') ? destination : '/'}`;
      if (!options.form && !options.steps)
        return send(200, loginPage(`${ssoOrigin}/sso/login?return=${encodeURIComponent(target)}`));
      const action = `/jira/login.jsp?os_destination=${encodeURIComponent(destination)}`;
      const screen = options.steps ? stepsPage : formPage;
      if (request.method !== 'POST') return send(200, screen(action));
      let body = '';
      request.on('data', (chunk) => (body += chunk));
      request.on('end', () => {
        const form = new URLSearchParams(body);
        const [id, password] = options.steps
          ? [form.get('login_id'), form.get('passcode')]
          : [form.get('os_username'), form.get('os_password')];
        logins.push({ id, password });
        if (id !== options.user.id || password !== options.user.password)
          return send(200, screen(action, '사용자 이름 또는 비밀번호가 올바르지 않습니다.'));
        redirect(target, 'session=valid; Path=/; Max-Age=3600; HttpOnly; SameSite=Lax');
      });
      return;
    }
    if (url.pathname === '/jira/sso/callback' && url.searchParams.get('ticket') === 'ok')
      return redirect(
        url.searchParams.get('return') || origin,
        'session=valid; Path=/; Max-Age=3600; HttpOnly; SameSite=Lax',
      );
    // The dashboard names the signed-in user, as JIRA pages do.
    if (url.pathname === '/jira/' || url.pathname === '/jira') {
      if (!signedIn) return redirect(`/jira/login.jsp?os_destination=${encodeURIComponent('/')}`);
      return send(
        200,
        '<!doctype html><html><head><meta name="ajs-context-path" content="/jira"><meta name="ajs-remote-user" content="tester"><title>대시보드</title></head><body><h1>JIRA 대시보드</h1></body></html>',
      );
    }
    const page = url.pathname.match(/^\/jira\/browse\/([A-Z]+-\d+)$/);
    if (page) {
      if (!signedIn)
        return redirect(
          `/jira/login.jsp?os_destination=${encodeURIComponent(`/browse/${page[1]}`)}`,
        );
      return send(
        200,
        `<!doctype html><html><head><meta name="ajs-context-path" content="/jira"><meta name="ajs-remote-user" content="tester"><title>${page[1]}</title></head><body><h1>${page[1]}</h1></body></html>`,
      );
    }
    const api = url.pathname.match(/^\/jira\/rest\/api\/2\/(.+)$/);
    if (!api) return send(404, 'Not found');
    if (!signedIn) return json({ errorMessages: ['로그인이 필요합니다.'] }, 401);
    if (api[1] === 'search') {
      const query = url.searchParams.get('jql');
      jql.push(query);
      let keys = Object.keys(ISSUES).sort();
      const text = query.match(/text ~ "([^"]*)"/)?.[1];
      if (text)
        keys = keys.filter((key) =>
          text
            .split(' ')
            .every((word) => `${ISSUES[key].summary} ${ISSUES[key].text}`.includes(word)),
        );
      const people = [...query.matchAll(/(assignee|reporter) = "([^"]*)"/g)];
      const unknown = people.find(([, , name]) => !USERS[name]);
      if (unknown)
        return json(
          {
            errorMessages: [
              `The value '${unknown[2]}' does not exist for the field '${unknown[1]}'.`,
            ],
          },
          400,
        );
      if (people.length)
        keys = keys.filter((key) => people.some(([, field, name]) => ISSUES[key][field] === name));
      const start = Number(url.searchParams.get('startAt') || 0);
      return json({
        startAt: start,
        maxResults: 1,
        total: keys.length,
        issues: keys.slice(start, start + 1).map((key) => ({ key })),
      });
    }
    const issue = api[1].match(/^issue\/([A-Z]+-\d+)$/);
    if (!issue || !ISSUES[issue[1]]) return json({ errorMessages: ['Issue Does Not Exist'] }, 404);
    const reply = () => json(issueJson(issue[1], origin));
    if (!options.slow) return reply();
    const timer = setTimeout(reply, 3000);
    response.on('close', () => clearTimeout(timer));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  ssoOrigin = `http://localhost:${server.address().port}`;
  return {
    origin,
    url: `${origin}/jira/browse/TEAM-1`,
    // The site's base address, as saved for its account.
    site: `${origin}/jira`,
    // The tickets, which a test may change between imports (and must restore).
    issues: ISSUES,
    options,
    requests,
    logins,
    jql,
    close: () =>
      new Promise((resolve) => {
        server.close(resolve);
        server.closeAllConnections();
      }),
  };
}

module.exports = { jiraServer, formPage };
