const http = require('node:http');
const { createHash, randomUUID, timingSafeEqual } = require('node:crypto');
const { API_PREFIX, ApiError, matchRoute } = require('./api-catalog.cjs');
const { html, markdown, openapi } = require('./api-manual.cjs');
const { PROTOCOL_VERSIONS } = require('./mcp.cjs');

const MAX_BODY = 1024 * 1024;
const RECENT = 30;
// The longest name of a request the recent-request list keeps: tool names are the client's to choose.
const NAME_LIMIT = 100;
const SECURITY_HEADERS = { 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store' };

// Protocol 2025-06-18 took JSON-RPC batches out of MCP; a client that names no version speaks 2025-03-26 or older.
const allowsBatches = (version) => !version || version <= '2025-03-26';
const invalid = (message = 'Invalid Request') => ({
  jsonrpc: '2.0',
  id: null,
  error: { code: -32600, message },
});

function send(res, status, body, headers = {}) {
  if (res.headersSent) return res.end();
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    ...SECURITY_HEADERS,
    ...headers,
  });
  res.end(JSON.stringify(body));
}

function sendText(res, type, text, headers = {}) {
  res.writeHead(200, { 'Content-Type': `${type}; charset=utf-8`, ...SECURITY_HEADERS, ...headers });
  res.end(text);
}

function fail(res, error, headers = {}) {
  send(
    res,
    error instanceof ApiError ? error.status : 400,
    {
      ok: false,
      error: {
        code: error instanceof ApiError ? error.code : 'rejected',
        message: error.message || '요청을 처리하지 못했습니다.',
      },
    },
    headers,
  );
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    let over = false;
    // Past the limit the rest is read and dropped rather than cut off, so the client still gets the 413.
    req.on('data', (chunk) => {
      size += chunk.length;
      if (over) return;
      if (size <= MAX_BODY) return chunks.push(chunk);
      over = true;
      chunks.length = 0;
      reject(new ApiError(413, 'too_large', '요청 본문은 1MiB 이하로 보내 주세요.'));
    });
    req.on('end', () => !over && resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

async function readJson(req) {
  const text = await readBody(req);
  if (!text.trim()) return {};
  try {
    return JSON.parse(text);
  } catch {
    throw new ApiError(400, 'invalid_request', '본문을 JSON으로 읽지 못했습니다.');
  }
}

const plain = (value) => !!value && typeof value === 'object' && !Array.isArray(value);

// Query strings carry text only: values become the type the route declares.
function coerce(name, raw, spec) {
  const bad = (what) => new ApiError(400, 'invalid_request', `${name}: ${what}로 입력해 주세요.`);
  if (spec?.type === 'integer') {
    if (!/^-?\d+$/.test(raw)) throw bad('정수');
    return Number(raw);
  }
  if (spec?.type === 'number') {
    const value = Number(raw);
    if (!raw.trim() || !Number.isFinite(value)) throw bad('숫자');
    return value;
  }
  if (spec?.type === 'boolean') {
    if (['true', '1'].includes(raw)) return true;
    if (['false', '0'].includes(raw)) return false;
    throw bad('true 또는 false');
  }
  return raw;
}

function queryParams(search, specs) {
  const params = {};
  for (const [name, raw] of search) {
    const spec = specs[name];
    if (spec?.type === 'array')
      params[name] = [
        ...(params[name] ?? []),
        ...raw
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean),
      ];
    else params[name] = coerce(name, raw, spec);
  }
  return params;
}

// The local API and MCP server for AI tools on this PC. It listens on 127.0.0.1 only; every request must come with
// this PC's own address as Host and without another web page's Origin (no DNS rebinding, no browser pages), and all
// but the manual need the token. `call(method, params, access)` runs one API method at the given access level;
// `manual(personal)` is the manual, with the access level and this PC's paths only when `personal` (a request with
// the token).
class ApiServer {
  constructor({ call, mcp, settings, manual, version }) {
    this.call = call;
    this.mcp = mcp;
    this.settings = settings;
    this.manual = manual;
    this.version = version;
    this.server = null;
    this.port = null;
    this.error = '';
    this.sessions = new Map();
    this.log = [];
  }

  get running() {
    return !!this.server?.listening;
  }

  baseUrl(port = this.port) {
    return `http://127.0.0.1:${port}`;
  }

  recent() {
    return [...this.log];
  }

  async start(port) {
    await this.stop();
    this.error = '';
    const server = http.createServer((req, res) => {
      this.route(req, res).catch((error) => fail(res, error));
    });
    server.headersTimeout = 30_000;
    server.requestTimeout = 60_000;
    try {
      await new Promise((resolve, reject) => {
        server.once('error', reject);
        server.listen(port, '127.0.0.1', () => {
          server.off('error', reject);
          resolve(undefined);
        });
      });
    } catch (error) {
      this.error =
        error.code === 'EADDRINUSE'
          ? `포트 ${port}을(를) 다른 프로그램이 쓰고 있습니다. 다른 포트를 지정해 주세요.`
          : error.code === 'EACCES'
            ? `포트 ${port}을(를) 열 수 없습니다(사용 권한 또는 Windows 예약 포트). 다른 포트를 지정해 주세요.`
            : `로컬 API 서버를 시작하지 못했습니다: ${error.message}`;
      throw new Error(this.error, { cause: error });
    }
    server.on('error', () => {});
    this.server = server;
    this.port = /** @type {import('node:net').AddressInfo} */ (server.address()).port;
  }

  async stop() {
    const server = this.server;
    this.server = null;
    for (const session of this.sessions.values()) {
      clearInterval(session.ping);
      session.res.end();
    }
    this.sessions.clear();
    if (!server) return;
    await new Promise((resolve) => {
      server.close(() => resolve(undefined));
      server.closeAllConnections();
    });
  }

  record(entry) {
    const name =
      entry.name.length > NAME_LIMIT ? `${entry.name.slice(0, NAME_LIMIT)}…` : entry.name;
    this.log.unshift({ at: new Date().toISOString(), ...entry, name });
    this.log.length = Math.min(this.log.length, RECENT);
  }

  // The tools AI tools may use changed with the access level. Clients of the HTTP+SSE stream hear it at once; those
  // of Streamable HTTP, answered request by request, see it when they list the tools again.
  toolsChanged() {
    const notice = { jsonrpc: '2.0', method: 'notifications/tools/list_changed' };
    for (const session of this.sessions.values())
      if (!session.res.writableEnded)
        session.res.write(`event: message\ndata: ${JSON.stringify(notice)}\n\n`);
  }

  trusted(req) {
    const own = [`127.0.0.1:${this.port}`, `localhost:${this.port}`];
    const host = String(req.headers.host ?? '').toLowerCase();
    const origin = req.headers.origin;
    return (
      own.includes(host) &&
      (origin === undefined || own.some((item) => origin === `http://${item}`))
    );
  }

  authorized(req) {
    const header = req.headers.authorization;
    const presented =
      typeof header === 'string' && /^Bearer\s+/i.test(header)
        ? header.replace(/^Bearer\s+/i, '').trim()
        : typeof req.headers['x-api-key'] === 'string'
          ? req.headers['x-api-key'].trim()
          : '';
    const { token } = this.settings();
    if (!presented || !token) return false;
    const digest = (value) => createHash('sha256').update(value).digest();
    return timingSafeEqual(digest(presented), digest(token));
  }

  refuse(res) {
    fail(
      res,
      new ApiError(
        401,
        'unauthorized',
        '토큰이 없거나 올바르지 않습니다. Private Agent의 관리 → AI 도구 연동에서 토큰을 복사해 Authorization: Bearer <토큰> 헤더로 보내 주세요.',
      ),
      { 'WWW-Authenticate': 'Bearer realm="private-agent"' },
    );
  }

  client(req) {
    return String(req.headers['user-agent'] ?? '').slice(0, 80);
  }

  async route(req, res) {
    const url = new URL(req.url, 'http://127.0.0.1');
    if (!this.trusted(req))
      return fail(
        res,
        new ApiError(403, 'forbidden_origin', '허용하지 않은 주소에서 온 요청입니다.'),
      );
    const path = url.pathname.replace(/\/+$/, '') || '/';
    const base = this.baseUrl();
    if (req.method === 'GET' && path === '/') {
      if (String(req.headers.accept ?? '').includes('text/html'))
        return res.writeHead(302, { Location: '/docs', ...SECURITY_HEADERS }).end();
      return send(res, 200, {
        ok: true,
        result: {
          name: 'Private Agent Local API',
          version: this.version,
          ...this.manual(false).endpoints,
        },
      });
    }
    // The manual needs no token; without one it leaves out what is the user's own (the access level they chose, the
    // path of the stdio relay in their profile folder).
    if (req.method === 'GET' && path === '/docs')
      return sendText(res, 'text/html', html(this.manual(this.authorized(req))), {
        'Content-Security-Policy':
          "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
      });
    if (req.method === 'GET' && path === '/llms.txt')
      return sendText(res, 'text/markdown', markdown(this.manual(this.authorized(req))));
    if (req.method === 'GET' && path === '/openapi.json')
      return send(res, 200, openapi({ baseUrl: base, version: this.version }));
    if (path === '/mcp') return this.streamable(req, res);
    if (path === '/sse') return this.openStream(req, res);
    if (path === '/messages') return this.post(req, res, url);
    if (path === API_PREFIX || path.startsWith(API_PREFIX + '/'))
      return this.rest(req, res, url, path.slice(API_PREFIX.length) || '/');
    fail(res, new ApiError(404, 'not_found', '없는 주소입니다. 매뉴얼은 /docs에 있습니다.'));
  }

  async rest(req, res, url, sub) {
    const { route, params: found, allowed } = matchRoute(req.method, sub);
    if (!route) {
      if (allowed?.length)
        return fail(
          res,
          new ApiError(405, 'method_not_allowed', `이 주소는 ${allowed.join(', ')}로 부릅니다.`),
          { Allow: allowed.join(', ') },
        );
      return fail(
        res,
        new ApiError(
          404,
          'not_found',
          `없는 주소입니다. GET ${API_PREFIX}/manual에서 주소를 확인해 주세요.`,
        ),
      );
    }
    if (!route.public && !this.authorized(req)) return this.refuse(res);
    const params = ['GET', 'DELETE'].includes(req.method)
      ? queryParams(url.searchParams, route.params)
      : await readJson(req);
    if (!plain(params))
      throw new ApiError(400, 'invalid_request', '본문은 JSON 객체로 보내 주세요.');
    for (const [name, value] of Object.entries(found)) params[name] = decodeURIComponent(value);
    for (const [name, spec] of Object.entries(route.params)) {
      if (params[name] === undefined && spec.default !== undefined) params[name] = spec.default;
      if (spec.required && (params[name] === undefined || params[name] === ''))
        throw new ApiError(400, 'invalid_request', `${name}: 꼭 필요한 값입니다.`);
      if (
        spec.enum &&
        params[name] !== undefined &&
        params[name] !== null &&
        !spec.enum.includes(params[name])
      )
        throw new ApiError(
          400,
          'invalid_request',
          `${name}: ${spec.enum.join(', ')} 중 하나로 입력해 주세요.`,
        );
    }
    if (route.special === 'manual') {
      const doc = this.manual(this.authorized(req));
      return params.format === 'markdown'
        ? sendText(res, 'text/markdown', markdown(doc))
        : send(res, 200, { ok: true, result: doc });
    }
    let method = route.method;
    let values = params;
    if (route.special === 'rpc') {
      method = params.method;
      values = params.params ?? {};
      if (typeof method !== 'string' || !plain(values))
        throw new ApiError(
          400,
          'invalid_request',
          'method는 글, params는 JSON 객체로 보내 주세요.',
        );
    }
    const { access } = this.settings();
    let result;
    try {
      result = await this.call(method, values, access);
    } catch (error) {
      this.record({ transport: 'rest', name: method, ok: false, client: this.client(req) });
      throw error;
    }
    this.record({ transport: 'rest', name: method, ok: true, client: this.client(req) });
    if (route.binary && params.format !== 'json' && result?.data) {
      res.writeHead(200, { 'Content-Type': result.mime, ...SECURITY_HEADERS });
      return res.end(Buffer.from(result.data, 'base64'));
    }
    send(res, 200, { ok: true, result });
  }

  // MCP messages of one POST, as the Streamable HTTP transport sends them; answered as JSON, never as a stream.
  // `transport`: 'http', or 'sse' for the older HTTP+SSE transport.
  async answer(body, req, transport = 'http') {
    const batch = Array.isArray(body);
    const messages = batch ? body : [body];
    const { access } = this.settings();
    const answers = await Promise.all(
      messages.map(async (message) => {
        const answer = await this.mcp.handle(message, { access, transport });
        if (answer && typeof message?.method === 'string') {
          // The tool's name as the client sent it, which may be anything: a name only when it is text.
          const tool = message.params?.name;
          this.record({
            transport: 'mcp',
            name:
              message.method !== 'tools/call'
                ? message.method
                : typeof tool === 'string'
                  ? tool
                  : 'tools/call',
            ok: !answer.error && !answer.result?.isError,
            client: this.client(req),
          });
        }
        return answer;
      }),
    );
    const found = answers.filter(Boolean);
    return batch ? found : (found[0] ?? null);
  }

  async streamable(req, res) {
    if (!this.authorized(req)) return this.refuse(res);
    if (req.method !== 'POST')
      return send(
        res,
        405,
        {
          jsonrpc: '2.0',
          id: null,
          error: { code: -32000, message: 'Method not allowed: POST only' },
        },
        { Allow: 'POST' },
      );
    const version = req.headers['mcp-protocol-version'];
    if (version && !PROTOCOL_VERSIONS.includes(version))
      return send(res, 400, {
        jsonrpc: '2.0',
        id: null,
        error: { code: -32600, message: `Unsupported protocol version: ${version}` },
      });
    let body;
    try {
      body = JSON.parse(await readBody(req));
    } catch (error) {
      if (error instanceof ApiError) throw error;
      return send(res, 400, {
        jsonrpc: '2.0',
        id: null,
        error: { code: -32700, message: 'Parse error' },
      });
    }
    if (Array.isArray(body) && !body.length) return send(res, 400, invalid());
    if (Array.isArray(body) && !allowsBatches(version))
      return send(res, 400, invalid(`Invalid Request: protocol ${version} has no batches`));
    const answer = await this.answer(body, req);
    if (!answer || (Array.isArray(answer) && !answer.length))
      return res.writeHead(202, SECURITY_HEADERS).end();
    send(res, 200, answer);
  }

  // The HTTP+SSE transport of protocol 2024-11-05: a GET stream that carries the answers to POST /messages.
  openStream(req, res) {
    if (!this.authorized(req)) return this.refuse(res);
    if (req.method !== 'GET')
      return fail(res, new ApiError(405, 'method_not_allowed', 'GET으로 엽니다.'), {
        Allow: 'GET',
      });
    const id = randomUUID();
    res.writeHead(200, {
      'Content-Type': 'text/event-stream; charset=utf-8',
      Connection: 'keep-alive',
      ...SECURITY_HEADERS,
    });
    res.write(`event: endpoint\ndata: /messages?session_id=${id}\n\n`);
    const ping = setInterval(() => res.write(': ping\n\n'), 15_000);
    this.sessions.set(id, { res, ping });
    req.on('close', () => {
      clearInterval(ping);
      this.sessions.delete(id);
    });
  }

  async post(req, res, url) {
    if (!this.authorized(req)) return this.refuse(res);
    if (req.method !== 'POST')
      return fail(res, new ApiError(405, 'method_not_allowed', 'POST로 보냅니다.'), {
        Allow: 'POST',
      });
    const session = this.sessions.get(
      url.searchParams.get('session_id') ?? url.searchParams.get('sessionId'),
    );
    if (!session)
      return fail(
        res,
        new ApiError(404, 'not_found', 'SSE 연결을 찾을 수 없습니다. /sse를 다시 여세요.'),
      );
    let body;
    try {
      body = JSON.parse(await readBody(req));
    } catch (error) {
      if (error instanceof ApiError) throw error;
      return fail(res, new ApiError(400, 'invalid_request', '본문을 JSON으로 읽지 못했습니다.'));
    }
    // An empty batch, or one in a protocol without batches, gets its error here: nothing would go down the stream.
    if (Array.isArray(body) && !body.length) return send(res, 400, invalid());
    if (Array.isArray(body) && !allowsBatches(session.version))
      return send(res, 400, invalid(`Invalid Request: protocol ${session.version} has no batches`));
    res
      .writeHead(202, { 'Content-Type': 'text/plain; charset=utf-8', ...SECURITY_HEADERS })
      .end('Accepted');
    const answer = await this.answer(body, req, 'sse');
    // The protocol version this stream's client and the server agreed on.
    for (const item of [answer].flat())
      if (item?.result?.protocolVersion) session.version = item.result.protocolVersion;
    if (answer && !(Array.isArray(answer) && !answer.length) && !session.res.writableEnded)
      session.res.write(`event: message\ndata: ${JSON.stringify(answer)}\n\n`);
  }
}

module.exports = { ApiServer, MAX_BODY };
