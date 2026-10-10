import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

// docs/api.md and docs/openapi.json: the local API that AI tools use, for readers of the repository. Both are made from
// the catalog (electron/api-catalog.cjs) with the functions that make the manual and the OpenAPI document the app serves
// itself (electron/api-manual.cjs, served by electron/api-server.cjs at /api/v1/manual, /docs and /openapi.json), for
// the default address and without what is the user's own (the access level they chose, the token, this PC's paths).
// api.md lists every method in the group of its REST routes, with the access it needs, its routes with their parameters
// and the MCP tools that call it. `--check` writes nothing and fails when a committed file differs from what it would
// write; tests/api-docs.test.cjs checks the same.

const require = createRequire(import.meta.url);
export const projectRoot = fileURLToPath(new URL('../', import.meta.url));
export const OUTPUTS = {
  markdown: path.join(projectRoot, 'docs', 'api.md'),
  openapi: path.join(projectRoot, 'docs', 'openapi.json'),
};

const {
  API_PREFIX,
  ACCESS_LEVELS,
  DEFAULT_PORT,
  FIELD_SCOPES,
  GROUPS,
  METHODS,
  ROUTES,
  SCOPES,
} = require('../electron/api-catalog.cjs');
const { manual, openapi } = require('../electron/api-manual.cjs');
const { TOOLS } = require('../electron/mcp.cjs');

const BASE_URL = `http://127.0.0.1:${DEFAULT_PORT}`;
// The MCP tools that take any method by name: query_api only the read-only ones (scope read), call_api every one.
const GENERIC_TOOLS = { read: 'query_api', other: 'call_api' };
// The group of the methods that neither have a REST route nor share a namespace with a method that has one.
const RPC_ONLY = {
  id: 'rpc',
  title: 'RPC로만 부르는 메서드',
  description: `REST 경로가 없는 메서드입니다. \`POST ${API_PREFIX}/rpc\`나 MCP 도구로 부릅니다.`,
};

const version = () =>
  JSON.parse(readFileSync(path.join(projectRoot, 'package.json'), 'utf8')).version;

// The methods each MCP tool calls, read from its handler: a tool of its own calls them by name
// (ctx.call('wiki.search', …)), while query_api and call_api take the method from their arguments.
export function toolMethods() {
  const names = new Set(TOOLS.map((tool) => tool.name));
  for (const name of Object.values(GENERIC_TOOLS))
    if (!names.has(name)) throw new Error(`electron/mcp.cjs에 ${name} 도구가 없습니다.`);
  return new Map(
    TOOLS.map((tool) => [
      tool.name,
      [...new Set([...tool.run.toString().matchAll(/ctx\.call\(\s*'([^']+)'/g)].map((m) => m[1]))],
    ]),
  );
}

// The group each method is listed in: that of its first REST route; without one, the group most methods of its
// namespace (the part before the dot) are in, the earlier group on a tie; failing that, RPC_ONLY.
export function methodGroups() {
  const order = GROUPS.map((group) => group.id);
  const first = new Map();
  for (const route of ROUTES)
    if (route.method && !first.has(route.method)) first.set(route.method, route.group);
  const namespace = (name) => name.split('.')[0];
  const votes = new Map();
  for (const [name, group] of first) {
    const counts = votes.get(namespace(name)) ?? new Map();
    counts.set(group, (counts.get(group) ?? 0) + 1);
    votes.set(namespace(name), counts);
  }
  const groups = new Map();
  for (const name of Object.keys(METHODS)) {
    if (first.has(name)) groups.set(name, first.get(name));
    else {
      const counts = [...(votes.get(namespace(name)) ?? new Map())];
      counts.sort((a, b) => b[1] - a[1] || order.indexOf(a[0]) - order.indexOf(b[0]));
      groups.set(name, counts[0]?.[0] ?? RPC_ONLY.id);
    }
  }
  return groups;
}

// --- Markdown --------------------------------------------------------------------------------------------------------

// Text from the catalog as Markdown text: a "<" before a letter would start an HTML tag (<<<untrusted ID>>>), which
// GitHub drops with its text.
const text = (value) => String(value ?? '').replace(/<(?=[A-Za-z/!?])/g, '\\<');
const cell = (value) => text(value).replace(/\|/g, '\\|').replace(/\n/g, ' ');
const code = (value) => `\`${value}\``;
const levelLabel = (id) => ACCESS_LEVELS.find((level) => level.id === id)?.label ?? id;

// The parameters of a route or a tool, as the in-app manual lists them (electron/api-manual.cjs).
function paramLines(params, indent = '') {
  return params.map((param) => {
    const extra = [
      param.enum ? param.enum.join(' · ') : '',
      param.default !== undefined ? `기본 ${JSON.stringify(param.default)}` : '',
    ]
      .filter(Boolean)
      .join(', ');
    const where = param.in === 'argument' ? '' : `${param.in}, `;
    return `${indent}- ${code(param.name)} (${where}${param.type}${param.required ? ', 필수' : ''}) ${text(param.description)}${extra ? ` [${text(extra)}]` : ''}`;
  });
}

// Who may call a method of `scope`: the lowest access level that allows it, each level including the ones before it.
function access(scope) {
  const levels = SCOPES[scope].levels;
  // Every scope allows at least the last level, the widest.
  const lowest = /** @type {{ id: string, label: string }} */ (
    ACCESS_LEVELS.find((level) => levels.includes(level.id))
  );
  return lowest.id === ACCESS_LEVELS[ACCESS_LEVELS.length - 1].id
    ? `허용 범위 ‘${lowest.label}’에서만`
    : `허용 범위 ‘${lowest.label}’ 이상`;
}

function routeLines(route) {
  return [
    `- ${code(`${route.verb} ${route.path}`)} — ${text(route.summary)}`,
    ...paramLines(route.params, '  '),
  ];
}

function methodLines(method, routes, tools) {
  const fields = Object.entries(FIELD_SCOPES[method.name] ?? {}).map(
    ([field, scope]) =>
      `- 값 ${code(field)}는 범위 ${code(scope)}(${SCOPES[scope].label}): ${access(scope)}`,
  );
  const own = [...tools]
    .filter(([, methods]) => methods.includes(method.name))
    .map(([name]) => name);
  const generic = method.scope === 'read' ? GENERIC_TOOLS.read : GENERIC_TOOLS.other;
  return [
    `#### ${code(method.name)}`,
    '',
    text(method.summary),
    '',
    `- 범위 ${code(method.scope)}(${SCOPES[method.scope].label}) · ${access(method.scope)}${method.slow ? ' · slow' : ''}`,
    ...fields,
    `- MCP: ${[...own, generic].map(code).join(', ')}`,
    ...(routes.length
      ? routes.flatMap(routeLines)
      : [`- REST 경로 없음: ${code(`POST ${API_PREFIX}/rpc`)}로 부릅니다`]),
    '',
  ];
}

export function renderMarkdown(doc = manual({ baseUrl: BASE_URL, version: version() })) {
  const tools = toolMethods();
  const groups = methodGroups();
  const lines = [
    '# 로컬 API',
    '',
    '<!-- 이 파일은 scripts/export-api-docs.mjs가 electron/api-catalog.cjs와 electron/mcp.cjs에서 만듭니다. 직접 고치지 말고, API를 바꾼 뒤 npm run docs:api를 실행하세요. tests/api-docs.test.cjs가 최신인지 확인합니다. -->',
    '',
    `Private Agent ${doc.version}이 AI 도구(Claude Code, Codex, OpenCode, Antigravity 등)에 여는 로컬 REST API와 MCP 서버입니다. 서버는 기본으로 꺼져 있고, 앱의 **관리 → AI 도구 연동**에서 켜면 이 PC의 \`127.0.0.1\`에서만 받습니다. 기본 포트는 ${DEFAULT_PORT}이며 같은 화면에서 바꿀 수 있습니다.`,
    '',
    `앱은 같은 내용을 \`GET ${API_PREFIX}/manual\`(JSON, \`?format=markdown\`이면 Markdown), \`/docs\`(HTML), \`/llms.txt\`와 \`/openapi.json\`으로도 제공합니다. 이 저장소의 OpenAPI 3.1 문서는 [openapi.json](openapi.json)입니다.`,
    '',
    '## 연결',
    '',
    ...Object.entries(doc.endpoints).map(([name, url]) => `- ${name}: ${code(url)}`),
    '',
    '## 인증',
    '',
    `- 헤더: ${code(doc.authentication.header)} (또는 ${code(doc.authentication.alternative)})`,
    `- 토큰: ${text(doc.authentication.token)}`,
    `- 토큰 없이 부를 수 있는 주소: ${doc.authentication.public.map(code).join(', ')}`,
    `- ${text(doc.authentication.network)}`,
    '',
    '## 응답',
    '',
    `- 성공: ${code(doc.responses.success)}`,
    `- 실패: ${code(doc.responses.failure)}`,
    ...doc.responses.notes.map((note) => `- ${text(note)}`),
    '',
    '## 허용 범위',
    '',
    '사용자가 고른 허용 범위가 AI 도구가 부를 수 있는 메서드를 정합니다. 각 수준은 앞 수준을 포함합니다.',
    '',
    ...doc.access_levels.map(
      (level) => `- ${code(level.id)}(${text(level.label)}): ${text(level.description)}`,
    ),
    '',
    '메서드마다 범위가 있고, 범위마다 쓸 수 있는 허용 범위가 정해져 있습니다.',
    '',
    '| 범위 | 이름 | 쓸 수 있는 허용 범위 |',
    '| --- | --- | --- |',
    ...doc.scopes.map(
      (scope) =>
        `| ${code(scope.id)} | ${cell(scope.label)} | ${scope.levels.map(levelLabel).join(', ')} |`,
    ),
    '',
    '## 전체 메서드',
    '',
    `모든 메서드는 \`POST ${API_PREFIX}/rpc\`에 \`{"method": "<이름>", "params": {…}}\`를 보내 부를 수 있습니다. REST 경로와 그 매개변수, 메서드를 부르는 MCP 도구를 함께 적었습니다. MCP 도구 ${code(GENERIC_TOOLS.read)}는 범위 \`read\`인 메서드를, ${code(GENERIC_TOOLS.other)}는 모든 메서드를 이름으로 부릅니다. slow는 LLM이나 브라우저를 기다려 몇 분까지 걸릴 수 있는 메서드입니다. 매개변수의 위치는 path(주소의 일부), query(GET·DELETE의 쿼리), body(JSON 본문)입니다.`,
    '',
  ];
  for (const group of [...doc.groups, RPC_ONLY]) {
    const methods = doc.methods.filter((method) => groups.get(method.name) === group.id);
    const shared = (group.routes ?? []).filter((route) => !route.method);
    if (!methods.length && !shared.length) continue;
    // A group of the catalog goes with its id, which the manual's `group` takes (api_manual, ?group=).
    const id = group === RPC_ONLY ? '' : ` (${code(group.id)})`;
    lines.push(`### ${text(group.title)}${id}`, '', text(group.description), '');
    if (shared.length) lines.push(...shared.flatMap(routeLines), '');
    for (const method of methods) {
      const routes = doc.groups.flatMap((item) =>
        item.routes.filter((route) => route.method === method.name),
      );
      lines.push(...methodLines(method, routes, tools));
    }
  }
  lines.push(
    '## MCP',
    '',
    ...doc.mcp.transports.map(
      (item) => `- ${item.name}: ${code(item.endpoint)} — ${text(item.note)}`,
    ),
    `- 프로토콜 버전: ${doc.mcp.protocol_versions.join(', ')}`,
    '- 느린 도구는 wait_seconds(기본 50초) 안에 끝나지 않으면 job_id를 돌려줍니다. get_result로 결과를 받습니다.',
    ...doc.mcp.notes.map((note) => `- ${text(note)}`),
    '',
    '### 도구',
    '',
  );
  for (const tool of doc.mcp.tools) {
    const methods = tools.get(tool.name) ?? [];
    lines.push(
      `#### ${code(tool.name)}`,
      '',
      `${text(tool.title)} · 범위 ${code(tool.scope)}${tool.slow ? ' · slow' : ''}${methods.length ? ` · 메서드 ${methods.map(code).join(', ')}` : ''}`,
      '',
      text(tool.description),
      '',
      ...(tool.params.length ? [...paramLines(tool.params), ''] : []),
    );
  }
  lines.push(
    '### 리소스',
    '',
    ...doc.mcp.resources.map((item) => `- ${code(item.uri)}: ${text(item.description)}`),
    '',
    '### 프롬프트',
    '',
    ...doc.mcp.prompts.map(
      (item) =>
        `- ${code(item.name)} (${text(item.title)}): ${text(item.description)}${item.arguments.length ? ` 인자: ${item.arguments.map((arg) => code(arg.name)).join(', ')}` : ''}`,
    ),
    '',
    '## AI 도구별 설정',
    '',
    '앱의 ‘복사’ 버튼은 `<PRIVATE_AGENT_TOKEN>` 자리에 토큰을, `<앱 설정 폴더>` 자리에 이 PC의 경로를 채워 복사합니다.',
    '',
  );
  for (const client of doc.clients) {
    lines.push(`### ${text(client.name)}`, '');
    for (const snippet of client.snippets)
      lines.push(`${text(snippet.label)}:`, '', '```' + snippet.language, snippet.text, '```', '');
    lines.push(...client.notes.map((note) => `- ${text(note)}`), '');
  }
  lines.push(
    '## 오류',
    '',
    '| HTTP | code | 뜻 |',
    '| --- | --- | --- |',
    ...doc.errors.map(
      (item) => `| ${item.status} | ${code(item.code)} | ${cell(item.description)} |`,
    ),
    '',
  );
  return lines.join('\n');
}

export function renderOpenApi() {
  return JSON.stringify(openapi({ baseUrl: BASE_URL, version: version() }), null, 2);
}

// Both files as they are committed: formatted by Prettier with the repository's configuration.
export async function render() {
  const prettier = require('prettier');
  const format = async (source, file, parser) =>
    prettier.format(source, { ...(await prettier.resolveConfig(file)), parser });
  return {
    [OUTPUTS.markdown]: await format(renderMarkdown(), OUTPUTS.markdown, 'markdown'),
    [OUTPUTS.openapi]: await format(renderOpenApi(), OUTPUTS.openapi, 'json'),
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const files = await render();
  const name = (file) => path.relative(projectRoot, file).replaceAll('\\', '/');
  const names = Object.keys(files).map(name).join(', ');
  if (process.argv.includes('--check')) {
    const stale = Object.entries(files)
      .filter(
        ([file, text]) =>
          (existsSync(file) ? readFileSync(file, 'utf8').replace(/\r\n/g, '\n') : '') !== text,
      )
      .map(([file]) => name(file));
    if (stale.length) {
      console.error(
        `${stale.join(', ')}이(가) 최신이 아닙니다. npm run docs:api를 실행한 뒤 커밋하세요.`,
      );
      process.exit(1);
    }
    console.log(`${names}이(가) 최신입니다.`);
  } else {
    for (const [file, text] of Object.entries(files)) {
      mkdirSync(path.dirname(file), { recursive: true });
      writeFileSync(file, text);
    }
    console.log(`${names}을(를) 갱신했습니다.`);
  }
}
