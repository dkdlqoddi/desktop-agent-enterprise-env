#!/usr/bin/env node
// Private Agent MCP over stdio, for AI tools that only start MCP servers as commands: relays each JSON-RPC message
// between this process's stdin/stdout and the app's local MCP endpoint (Streamable HTTP). The app writes this file
// next to its settings when its API server starts; AI tools run it with Node.js 18 or later:
//   PRIVATE_AGENT_MCP_URL=http://127.0.0.1:47615/mcp PRIVATE_AGENT_TOKEN=pa_… node private-agent-mcp.cjs
// It uses node:http with an agent of its own rather than fetch, so proxy settings meant for remote hosts
// never touch 127.0.0.1: with NODE_USE_ENV_PROXY=1 (Node 22.21+, 24.5+) Node's default agent follows HTTP_PROXY too.
'use strict';
const http = require('node:http');
const readline = require('node:readline');

const agent = new http.Agent({ keepAlive: true });

// The token goes to the app on this PC only: an address naming another host, or https, is refused before anything is
// sent, whoever set it.
const LOOPBACK = ['127.0.0.1', 'localhost', '[::1]'];
function local(address) {
  try {
    const url = new URL(address);
    return url.protocol === 'http:' &&
      LOOPBACK.includes(url.hostname) &&
      !url.username &&
      !url.password
      ? url
      : null;
  } catch {
    return null;
  }
}
const address =
  process.env.PRIVATE_AGENT_MCP_URL || process.argv[2] || 'http://127.0.0.1:47615/mcp';
const target = local(address);
const token = process.env.PRIVATE_AGENT_TOKEN || '';
let protocolVersion = '';

function write(message) {
  process.stdout.write(JSON.stringify(message) + '\n');
}

// Called once the address has been found local (relay).
function post(body) {
  return new Promise((resolve, reject) => {
    const data = Buffer.from(body, 'utf8');
    const request = http.request(
      /** @type {URL} */ (target),
      {
        method: 'POST',
        agent,
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json, text/event-stream',
          'Content-Length': data.length,
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...(protocolVersion ? { 'MCP-Protocol-Version': protocolVersion } : {}),
        },
      },
      (response) => {
        const chunks = [];
        response.on('data', (chunk) => chunks.push(chunk));
        response.on('end', () =>
          resolve({ status: response.statusCode, text: Buffer.concat(chunks).toString('utf8') }),
        );
        response.on('error', reject);
      },
    );
    request.on('error', reject);
    request.end(data);
  });
}

async function relay(line) {
  let message;
  try {
    message = JSON.parse(line);
  } catch {
    return write({ jsonrpc: '2.0', id: null, error: { code: -32700, message: 'Parse error' } });
  }
  // Only requests get an answer when the app cannot be reached; notifications and responses do not.
  const requests = (Array.isArray(message) ? message : [message]).filter(
    (item) => item && typeof item.method === 'string' && item.id !== undefined && item.id !== null,
  );
  const refuse = (reason) => {
    for (const item of requests)
      write({ jsonrpc: '2.0', id: item.id, error: { code: -32000, message: reason } });
  };
  if (!target)
    return refuse(
      'PRIVATE_AGENT_MCP_URL에는 이 PC의 Private Agent 주소(http://127.0.0.1:<포트>/mcp)만 쓸 수 있습니다. 앱의 관리 → AI 도구 연동에서 설정을 다시 복사해 주세요.',
    );
  try {
    const { status, text } = await post(line);
    if (status === 202 || !text.trim()) return;
    const answer = JSON.parse(text);
    if (Array.isArray(answer) || answer?.jsonrpc === '2.0') {
      for (const item of Array.isArray(answer) ? answer : [answer])
        if (item?.result?.protocolVersion) protocolVersion = item.result.protocolVersion;
      return write(answer);
    }
    refuse(answer?.error?.message || `Private Agent가 요청을 거절했습니다 (HTTP ${status}).`);
  } catch (error) {
    refuse(
      error.code === 'ECONNREFUSED'
        ? 'Private Agent에 연결하지 못했습니다. 앱이 켜져 있고, 관리 → AI 도구 연동에서 서버가 켜져 있는지 확인해 주세요.'
        : `Private Agent에 연결하지 못했습니다: ${error.message}`,
    );
  }
}

readline.createInterface({ input: process.stdin }).on('line', (line) => {
  if (line.trim()) void relay(line);
});
