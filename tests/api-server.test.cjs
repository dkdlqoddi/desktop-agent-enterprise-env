const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const { ApiServer } = require('../electron/api-server.cjs');
const { McpServer } = require('../electron/mcp.cjs');
const { ApiError, authorize } = require('../electron/api-catalog.cjs');
const apiManual = require('../electron/api-manual.cjs');

const TOKEN = 'pa_' + 'x'.repeat(43);
const settings = { token: TOKEN, access: 'write' };
const calls = [];
const PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47]);

// The main process's callApi, with fake answers in place of the app.
async function call(method, params, access) {
  authorize(method, access, params);
  calls.push({ method, params });
  if (method === 'health') return { ok: true };
  if (method === 'app.screenshot')
    return { mime: 'image/png', width: 4, height: 1, data: PNG.toString('base64') };
  if (method === 'wiki.delete') throw new Error('문서를 찾을 수 없습니다.');
  if (method === 'documents.query') return { total: 0, offset: 0, limit: 50, documents: [] };
  return { method };
}

const BRIDGE = 'C:\\Users\\hong\\AppData\\Roaming\\Private Agent\\mcp\\private-agent-mcp.cjs';

let server;
let port;
before(async () => {
  // As the main process's apiDocument: the access level and the relay's path only for the token's holder.
  const manual = (personal) =>
    apiManual.manual({
      baseUrl: server.baseUrl(),
      version: '0.1.0',
      ...(personal ? { access: settings.access, bridge: BRIDGE } : {}),
    });
  server = new ApiServer({
    call,
    mcp: new McpServer({ call, manual: () => '# manual', version: '0.1.0' }),
    settings: () => settings,
    manual,
    version: '0.1.0',
  });
  await server.start(0);
  port = server.port;
});
after(() => server.stop());

function request({ method = 'GET', path, headers = {}, body, token = TOKEN, host }) {
  return new Promise((resolve, reject) => {
    const data =
      body === undefined
        ? null
        : Buffer.from(typeof body === 'string' ? body : JSON.stringify(body));
    const req = http.request(
      {
        host: '127.0.0.1',
        port,
        method,
        path,
        headers: {
          Host: host ?? `127.0.0.1:${port}`,
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...(data ? { 'Content-Type': 'application/json', 'Content-Length': data.length } : {}),
          ...headers,
        },
      },
      (res) => {
        const chunks = [];
        res.on('data', (chunk) => chunks.push(chunk));
        res.on('end', () => {
          const raw = Buffer.concat(chunks);
          const text = raw.toString('utf8');
          let json = null;
          try {
            json = JSON.parse(text);
          } catch {
            /* Not JSON. */
          }
          resolve({ status: res.statusCode, headers: res.headers, raw, text, json });
        });
      },
    );
    req.on('error', reject);
    req.end(data ?? undefined);
  });
}

test('listens on 127.0.0.1 only, and answers only its own Host without a foreign Origin', async () => {
  assert.equal(server.server.address().address, '127.0.0.1');
  assert.equal((await request({ path: '/api/v1/stats' })).status, 200);
  assert.equal((await request({ path: '/api/v1/stats', host: `localhost:${port}` })).status, 200);
  const rebound = await request({ path: '/api/v1/stats', host: `attacker.example:${port}` });
  assert.equal(rebound.status, 403);
  assert.equal(rebound.json.error.code, 'forbidden_origin');
  const page = await request({
    path: '/api/v1/health',
    headers: { Origin: 'https://attacker.example' },
  });
  assert.equal(page.status, 403);
  const own = await request({
    path: '/api/v1/health',
    headers: { Origin: `http://127.0.0.1:${port}` },
  });
  assert.equal(own.status, 200);
  // Even the public manual is refused to a DNS-rebound page.
  assert.equal(
    (await request({ path: '/docs', host: `attacker.example:${port}`, token: '' })).status,
    403,
  );
});

test('needs the token for everything but the health check and the manuals', async () => {
  const refused = await request({ path: '/api/v1/stats', token: '' });
  assert.equal(refused.status, 401);
  assert.equal(refused.json.error.code, 'unauthorized');
  assert.match(refused.headers['www-authenticate'], /^Bearer/);
  assert.equal(
    (await request({ path: '/api/v1/stats', token: TOKEN.slice(0, -1) + 'y' })).status,
    401,
  );
  assert.equal(
    (await request({ path: '/api/v1/stats', token: '', headers: { 'X-API-Key': TOKEN } })).status,
    200,
  );
  const health = await request({ path: '/api/v1/health', token: '' });
  assert.deepEqual(health.json, { ok: true, result: { ok: true } });
  assert.equal((await request({ path: '/mcp', method: 'POST', body: {}, token: '' })).status, 401);
  assert.equal((await request({ path: '/sse', token: '' })).status, 401);
});

test('routes REST calls by the catalog: query types, path values, defaults and literal segments', async () => {
  calls.length = 0;
  const listed = await request({ path: '/api/v1/documents?limit=5&analysis=analyzed' });
  assert.equal(listed.status, 200);
  assert.deepEqual(calls.at(-1), {
    method: 'documents.query',
    params: { limit: 5, analysis: 'analyzed', offset: 0 },
  });
  await request({ path: '/api/v1/documents/a%20b%2Fc' });
  assert.deepEqual(calls.at(-1), {
    method: 'documents.get',
    params: { id: 'a b/c', include_html: false },
  });
  await request({ path: '/api/v1/runs/stats?days=7' });
  assert.equal(calls.at(-1).method, 'runs.stats');
  assert.equal(calls.at(-1).params.days, 7);
  await request({ path: '/api/v1/runs/r1' });
  assert.deepEqual(calls.at(-1), { method: 'runs.get', params: { id: 'r1' } });
  await request({ path: '/api/v1/search?query=%EC%BA%90%EC%8B%9C' });
  assert.deepEqual(calls.at(-1), {
    method: 'wiki.search',
    params: { query: '캐시', mode: 'auto' },
  });
  await request({ path: '/api/v1/tasks/t1', method: 'PUT', body: { status: 'done' } });
  assert.deepEqual(calls.at(-1), { method: 'tasks.save', params: { status: 'done', id: 't1' } });
  await request({ path: '/api/v1/rpc', method: 'POST', body: { method: 'stats' } });
  assert.deepEqual(calls.at(-1), { method: 'stats', params: {} });
  const ok = await request({
    path: '/api/v1/prompts/p1/run',
    method: 'POST',
    body: { variables: { a: '1' } },
  });
  assert.deepEqual(ok.json, { ok: true, result: { method: 'prompts.run' } });
  assert.deepEqual(calls.at(-1).params, { variables: { a: '1' }, prompt_id: 'p1' });
});

test('refuses bad requests with a code and a reason', async () => {
  const missing = await request({ path: '/api/v1/search' });
  assert.equal(missing.status, 400);
  assert.equal(missing.json.error.code, 'invalid_request');
  assert.equal((await request({ path: '/api/v1/search?query=a&mode=everything' })).status, 400);
  assert.equal((await request({ path: '/api/v1/documents?limit=many' })).status, 400);
  const wrongVerb = await request({ path: '/api/v1/documents', method: 'PATCH' });
  assert.equal(wrongVerb.status, 405);
  assert.equal(wrongVerb.headers.allow, 'GET, POST');
  assert.equal((await request({ path: '/api/v1/nothing' })).status, 404);
  assert.equal((await request({ path: '/nothing' })).status, 404);
  const notJson = await request({ path: '/api/v1/documents', method: 'POST', body: '{"title":' });
  assert.equal(notJson.json.error.code, 'invalid_request');
  const array = await request({ path: '/api/v1/documents', method: 'POST', body: [1] });
  assert.equal(array.status, 400);
  const app = await request({ path: '/api/v1/documents/gone', method: 'DELETE' });
  assert.equal(app.status, 400);
  assert.deepEqual(app.json, {
    ok: false,
    error: { code: 'rejected', message: '문서를 찾을 수 없습니다.' },
  });
  const large = await request({
    path: '/api/v1/documents',
    method: 'POST',
    body: { title: 'x', content: 'x'.repeat(1024 * 1024) },
  });
  assert.equal(large.status, 413);
  const rpc = await request({
    path: '/api/v1/rpc',
    method: 'POST',
    body: { method: 'storage.initialize' },
  });
  assert.equal(rpc.status, 404);
});

test('sends screenshots as PNG, or as base64 JSON when asked', async () => {
  const image = await request({ path: '/api/v1/app/screenshot' });
  assert.equal(image.headers['content-type'], 'image/png');
  assert.deepEqual(image.raw, PNG);
  assert.deepEqual(calls.at(-1).params, { max_width: 1280, format: 'png' });
  const json = await request({ path: '/api/v1/app/screenshot?format=json&max_width=640' });
  assert.equal(json.json.result.data, PNG.toString('base64'));
});

test('serves the manual as JSON, Markdown, HTML and OpenAPI without the token', async () => {
  const doc = await request({ path: '/api/v1/manual', token: '' });
  assert.equal(doc.json.result.base_url, `http://127.0.0.1:${port}`);
  assert.ok(doc.json.result.groups.some((group) => group.id === 'search'));
  assert.ok(doc.json.result.mcp.tools.some((tool) => tool.name === 'search_documents'));
  const md = await request({ path: '/api/v1/manual?format=markdown', token: '' });
  assert.match(md.headers['content-type'], /text\/markdown/);
  assert.match(md.text, /# Private Agent 로컬 API 매뉴얼/);
  assert.match(md.text, /<PRIVATE_AGENT_TOKEN>/);
  assert.doesNotMatch(md.text, new RegExp(TOKEN));
  assert.equal((await request({ path: '/llms.txt', token: '' })).text, md.text);
  const page = await request({ path: '/docs', token: '' });
  assert.match(page.headers['content-type'], /text\/html/);
  assert.match(page.headers['content-security-policy'], /default-src 'none'/);
  assert.match(page.text, /Private Agent API 매뉴얼/);
  assert.doesNotMatch(page.text, /<script/);
  const spec = await request({ path: '/openapi.json', token: '' });
  assert.equal(spec.json.openapi, '3.1.0');
  assert.ok(spec.json.paths['/documents/{id}'].get);
  assert.deepEqual(spec.json.paths['/health'].get.security, []);
  const index = await request({ path: '/', token: '', headers: { Accept: 'text/html' } });
  assert.equal(index.status, 302);
  assert.equal(index.headers.location, '/docs');
});

test("leaves the user's access level and profile path out of the manual served without the token", async () => {
  const anyone = await request({ path: '/api/v1/manual', token: '' });
  assert.equal(anyone.json.result.access, null);
  for (const text of [
    anyone.text,
    (await request({ path: '/api/v1/manual?format=markdown', token: '' })).text,
    (await request({ path: '/llms.txt', token: '' })).text,
    (await request({ path: '/docs', token: '' })).text,
    (await request({ path: '/', token: '' })).text,
  ]) {
    assert.doesNotMatch(text, /hong/);
    assert.doesNotMatch(text, /← 현재/);
  }
  assert.match(anyone.text, /<앱 설정 폴더>/);
  // The token's holder gets them.
  const holder = await request({ path: '/api/v1/manual' });
  assert.equal(holder.json.result.access, 'write');
  assert.match(holder.text, /hong/);
  assert.match((await request({ path: '/llms.txt' })).text, /\*\*write\*\* \([^)]*\) ← 현재/);
});

test('needs the full level to change the AI analysis of login pages, not the dashboard', async () => {
  const refused = await request({
    path: '/api/v1/preferences',
    method: 'PUT',
    body: { ai_answer: false, web_assist: false },
  });
  assert.equal(refused.status, 403);
  assert.match(refused.json.error.message, /전체|설정/);
  const rpc = await request({
    path: '/api/v1/rpc',
    method: 'POST',
    body: { method: 'preferences.save', params: { web_assist: true } },
  });
  assert.equal(rpc.status, 403);
  assert.equal(
    (await request({ path: '/api/v1/preferences', method: 'PUT', body: { ai_answer: false } }))
      .status,
    200,
  );
  settings.access = 'full';
  try {
    assert.equal(
      (await request({ path: '/api/v1/preferences', method: 'PUT', body: { web_assist: false } }))
        .status,
      200,
    );
  } finally {
    settings.access = 'write';
  }
});

test('lets the read level compare an edit a save refused, which changes nothing, but not save it', async () => {
  settings.access = 'read';
  const rpc = (method, params) =>
    request({ path: '/api/v1/rpc', method: 'POST', body: { method, params } });
  try {
    const compared = await rpc('wiki.compare', {
      id: 'd1',
      base_revision: 1,
      base: 'a',
      content: 'b',
    });
    assert.equal(compared.status, 200);
    assert.deepEqual(compared.json.result, { method: 'wiki.compare' });
    assert.equal((await rpc('wiki.save', { id: 'd1', title: 't', content: 'b' })).status, 403);
  } finally {
    settings.access = 'write';
  }
});

test('speaks MCP over Streamable HTTP', async () => {
  const mcp = (body, headers) => request({ path: '/mcp', method: 'POST', body, headers });
  const init = await mcp({
    jsonrpc: '2.0',
    id: 1,
    method: 'initialize',
    params: {
      protocolVersion: '2025-06-18',
      capabilities: {},
      clientInfo: { name: 'test', version: '1' },
    },
  });
  assert.equal(init.status, 200);
  assert.equal(init.json.result.protocolVersion, '2025-06-18');
  assert.equal(init.json.result.serverInfo.name, 'private-agent');
  assert.equal(init.headers['mcp-session-id'], undefined);
  const note = await mcp({ jsonrpc: '2.0', method: 'notifications/initialized' });
  assert.equal(note.status, 202);
  assert.equal(note.text, '');
  const tools = await mcp(
    { jsonrpc: '2.0', id: 2, method: 'tools/list' },
    { 'MCP-Protocol-Version': '2025-06-18' },
  );
  assert.ok(tools.json.result.tools.some((tool) => tool.name === 'app_screenshot'));
  const batch = await mcp([
    { jsonrpc: '2.0', id: 3, method: 'ping' },
    { jsonrpc: '2.0', method: 'notifications/initialized' },
  ]);
  assert.deepEqual(batch.json, [{ jsonrpc: '2.0', id: 3, result: {} }]);
  const version = await mcp(
    { jsonrpc: '2.0', id: 4, method: 'ping' },
    { 'MCP-Protocol-Version': '1999-01-01' },
  );
  assert.equal(version.status, 400);
  const parse = await request({ path: '/mcp', method: 'POST', body: '{nope' });
  assert.equal(parse.json.error.code, -32700);
  const get = await request({ path: '/mcp' });
  assert.equal(get.status, 405);
  assert.equal(get.headers.allow, 'POST');
  assert.ok(
    server.recent().some((entry) => entry.transport === 'mcp' && entry.name === 'initialize'),
  );
});

test('keeps to MCP: batches before 2025-06-18 only, errors for odd tool names, short names on record', async () => {
  const mcp = (body, headers) => request({ path: '/mcp', method: 'POST', body, headers });
  const batch = [{ jsonrpc: '2.0', id: 1, method: 'ping' }];
  for (const version of ['2025-06-18', '2025-11-25']) {
    const refused = await mcp(batch, { 'MCP-Protocol-Version': version });
    assert.equal(refused.status, 400, version);
    assert.equal(refused.json.error.code, -32600);
  }
  assert.equal((await mcp(batch, { 'MCP-Protocol-Version': '2025-03-26' })).status, 200);
  // A tool's name that is no text, nested past any stack: a JSON-RPC error like any other.
  const depth = 50_000;
  const odd = await mcp(
    `{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":${'['.repeat(depth)}${']'.repeat(depth)}}}`,
  );
  assert.equal(odd.status, 200);
  assert.equal(odd.json.error.code, -32602);
  assert.equal(server.recent()[0].name, 'tools/call');
  // The recent requests keep a client's long name short.
  await mcp({
    jsonrpc: '2.0',
    id: 3,
    method: 'tools/call',
    params: { name: 'x'.repeat(5000), arguments: {} },
  });
  assert.equal(server.recent()[0].name, `${'x'.repeat(100)}…`);
});

// An HTTP+SSE client: the address to post to, and the messages that came down the stream.
async function openStream() {
  const events = [];
  let endpoint;
  const req = await new Promise((resolve, reject) => {
    const req = http.request(
      {
        host: '127.0.0.1',
        port,
        path: '/sse',
        headers: {
          Host: `127.0.0.1:${port}`,
          Authorization: `Bearer ${TOKEN}`,
          Accept: 'text/event-stream',
        },
      },
      (res) => {
        assert.match(res.headers['content-type'], /text\/event-stream/);
        let buffer = '';
        res.on('data', (chunk) => {
          buffer += chunk;
          let end;
          while ((end = buffer.indexOf('\n\n')) >= 0) {
            const block = buffer.slice(0, end);
            buffer = buffer.slice(end + 2);
            const event = /^event: (.*)$/m.exec(block)?.[1];
            const data = /^data: (.*)$/m.exec(block)?.[1];
            if (event === 'endpoint') {
              endpoint = data;
              resolve(req);
            } else if (event) events.push(JSON.parse(data));
          }
        });
      },
    );
    req.on('error', reject);
    req.end();
  });
  return {
    endpoint,
    post: (body) => request({ path: endpoint, method: 'POST', body }),
    // The messages once `count` have come.
    received: async (count) => {
      for (let i = 0; i < 50 && events.length < count; i++)
        await new Promise((resolve) => setTimeout(resolve, 20));
      return events;
    },
    close: () => req.destroy(),
  };
}

test('speaks the older HTTP+SSE transport', async () => {
  const stream = await openStream();
  assert.match(stream.endpoint, /^\/messages\?session_id=/);
  const posted = await stream.post({ jsonrpc: '2.0', id: 7, method: 'ping' });
  assert.equal(posted.status, 202);
  assert.deepEqual(await stream.received(1), [{ jsonrpc: '2.0', id: 7, result: {} }]);
  const unknown = await request({ path: '/messages?session_id=nope', method: 'POST', body: {} });
  assert.equal(unknown.status, 404);
  stream.close();
});

test('tells HTTP+SSE clients that their tools changed, and keeps to the version they agreed', async () => {
  const stream = await openStream();
  // An empty batch gets its error.
  const empty = await stream.post([]);
  assert.equal(empty.status, 400);
  assert.equal(empty.json.error.code, -32600);
  // The transport's own protocol (2024-11-05) has batches.
  assert.equal((await stream.post([{ jsonrpc: '2.0', id: 1, method: 'ping' }])).status, 202);
  assert.deepEqual(await stream.received(1), [[{ jsonrpc: '2.0', id: 1, result: {} }]]);
  await stream.post({
    jsonrpc: '2.0',
    id: 2,
    method: 'initialize',
    params: { protocolVersion: '2025-06-18', capabilities: {} },
  });
  const init = (await stream.received(2))[1];
  assert.equal(init.result.protocolVersion, '2025-06-18');
  assert.equal(init.result.capabilities.tools.listChanged, true);
  const batch = await stream.post([{ jsonrpc: '2.0', id: 3, method: 'ping' }]);
  assert.equal(batch.status, 400);
  server.toolsChanged();
  assert.deepEqual((await stream.received(3))[2], {
    jsonrpc: '2.0',
    method: 'notifications/tools/list_changed',
  });
  stream.close();
});

test('reports a port in use, and stops cleanly', async () => {
  const other = new ApiServer({
    call,
    mcp: null,
    settings: () => settings,
    manual: () => ({}),
    version: '0',
  });
  await assert.rejects(other.start(port), /다른 프로그램이 쓰고 있습니다/);
  assert.match(other.error, new RegExp(String(port)));
  assert.equal(other.running, false);
  await other.stop();
  const spare = new ApiServer({
    call,
    mcp: null,
    settings: () => settings,
    manual: () => ({}),
    version: '0',
  });
  await spare.start(0);
  const sparePort = spare.port;
  await spare.stop();
  assert.equal(spare.running, false);
  await new Promise((resolve, reject) => {
    const probe = http.createServer();
    probe.once('error', reject);
    probe.listen(sparePort, '127.0.0.1', () => probe.close(resolve));
  });
});

test('ApiError carries its status and code', () => {
  const error = new ApiError(503, 'unavailable', 'x');
  assert.equal(error.status, 503);
  assert.equal(error.code, 'unavailable');
});
