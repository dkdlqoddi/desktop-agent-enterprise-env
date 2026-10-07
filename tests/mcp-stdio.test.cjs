const { test } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { createInterface } = require('node:readline');
const { ApiServer } = require('../electron/api-server.cjs');
const { McpServer } = require('../electron/mcp.cjs');
const { authorize } = require('../electron/api-catalog.cjs');

const TOKEN = 'pa_' + 'z'.repeat(43);
const BRIDGE = path.join(__dirname, '..', 'electron', 'mcp-stdio.cjs');

// The relay as an AI tool runs it: one JSON-RPC message per line on stdin, the answers on stdout.
function bridge(env) {
  const child = spawn(process.execPath, [BRIDGE], {
    env: {
      ...process.env,
      // A network proxy, which Node 22.21+ and 24.5+ follow with NODE_USE_ENV_PROXY=1.
      HTTP_PROXY: 'http://127.0.0.1:9',
      HTTPS_PROXY: 'http://127.0.0.1:9',
      NODE_USE_ENV_PROXY: '1',
      NO_PROXY: '',
      no_proxy: '',
      ...env,
    },
    stdio: ['pipe', 'pipe', 'inherit'],
  });
  const waiting = new Map();
  createInterface({ input: child.stdout }).on('line', (line) => {
    const message = JSON.parse(line);
    waiting.get(message.id)?.(message);
  });
  return {
    send: (message) =>
      new Promise((resolve) => {
        if (message.id !== undefined) waiting.set(message.id, resolve);
        child.stdin.write(JSON.stringify(message) + '\n');
        if (message.id === undefined) resolve(null);
      }),
    close: () => new Promise((resolve) => (child.once('exit', resolve), child.stdin.end())),
  };
}

test('relays MCP between stdio and the app, ignoring proxy settings for 127.0.0.1', async () => {
  const versions = [];
  const call = async (method, params, access) => {
    authorize(method, access);
    return {
      total: 1,
      documents: [
        {
          id: 'd1',
          title: '회의록',
          status: 'ready',
          index_mode: '',
          keywords: [],
          source: null,
          match: {},
        },
      ],
      plan: {},
    };
  };
  const server = new ApiServer({
    call,
    mcp: new McpServer({ call, manual: () => '', version: '1.0.0' }),
    settings: () => ({ token: TOKEN, access: 'read' }),
    manual: () => ({}),
    version: '1.0.0',
  });
  const original = server.streamable.bind(server);
  server.streamable = (req, res) => {
    versions.push(req.headers['mcp-protocol-version'] ?? null);
    return original(req, res);
  };
  await server.start(0);
  const relay = bridge({
    PRIVATE_AGENT_MCP_URL: `http://127.0.0.1:${server.port}/mcp`,
    PRIVATE_AGENT_TOKEN: TOKEN,
  });
  try {
    const init = await relay.send({
      jsonrpc: '2.0',
      id: 1,
      method: 'initialize',
      params: {
        protocolVersion: '2025-06-18',
        capabilities: {},
        clientInfo: { name: 'stdio-test', version: '1' },
      },
    });
    assert.equal(init.result.serverInfo.name, 'private-agent');
    await relay.send({ jsonrpc: '2.0', method: 'notifications/initialized' });
    const found = await relay.send({
      jsonrpc: '2.0',
      id: 2,
      method: 'tools/call',
      params: { name: 'search_documents', arguments: { query: '회의록' } },
    });
    assert.match(found.result.content[0].text, /회의록 — id: d1/);
    assert.deepEqual(versions.slice(0, 1), [null]);
    assert.equal(versions.at(-1), '2025-06-18');
  } finally {
    await relay.close();
    await server.stop();
  }
});

test("sends the token to this PC's app only, refusing any other address", async () => {
  // A listener at an address the relay must not use, answering as the app would: nothing may arrive there.
  const arrived = [];
  const trap = http.createServer((req, res) => {
    arrived.push(req.headers.authorization);
    res
      .writeHead(200, { 'Content-Type': 'application/json' })
      .end(JSON.stringify({ jsonrpc: '2.0', id: 1, result: {} }));
  });
  await new Promise((resolve) => trap.listen(0, '127.0.0.2', resolve));
  const { port } = trap.address();
  try {
    for (const address of [
      `http://127.0.0.2:${port}/mcp`,
      `https://127.0.0.1:${port}/mcp`,
      `http://user:secret@127.0.0.1:${port}/mcp`,
      'not a url',
    ]) {
      const relay = bridge({ PRIVATE_AGENT_MCP_URL: address, PRIVATE_AGENT_TOKEN: TOKEN });
      try {
        const refused = await relay.send({ jsonrpc: '2.0', id: 1, method: 'ping' });
        assert.equal(refused.error.code, -32000, address);
        assert.match(refused.error.message, /이 PC의 Private Agent 주소/);
        assert.doesNotMatch(refused.error.message, /secret/);
      } finally {
        await relay.close();
      }
    }
    assert.deepEqual(arrived, []);
  } finally {
    await new Promise((resolve) => trap.close(resolve));
  }
});

test('answers each request with the reason when the app refuses or cannot be reached', async () => {
  const server = new ApiServer({
    call: async () => ({}),
    mcp: new McpServer({ call: async () => ({}), manual: () => '', version: '1' }),
    settings: () => ({ token: TOKEN, access: 'read' }),
    manual: () => ({}),
    version: '1',
  });
  await server.start(0);
  const port = server.port;
  const wrong = bridge({
    PRIVATE_AGENT_MCP_URL: `http://127.0.0.1:${port}/mcp`,
    PRIVATE_AGENT_TOKEN: 'pa_wrong',
  });
  try {
    const refused = await wrong.send({ jsonrpc: '2.0', id: 'a', method: 'tools/list' });
    assert.equal(refused.error.code, -32000);
    assert.match(refused.error.message, /토큰/);
  } finally {
    await wrong.close();
    await server.stop();
  }
  const gone = bridge({
    PRIVATE_AGENT_MCP_URL: `http://127.0.0.1:${port}/mcp`,
    PRIVATE_AGENT_TOKEN: TOKEN,
  });
  try {
    const down = await gone.send({ jsonrpc: '2.0', id: 3, method: 'ping' });
    assert.match(down.error.message, /연결하지 못했습니다/);
  } finally {
    await gone.close();
  }
});
