const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { API_PREFIX, METHODS, ROUTES } = require('../electron/api-catalog.cjs');
const { TOOLS } = require('../electron/mcp.cjs');

// docs/api.md and docs/openapi.json describe the local API for readers of the repository. They are made from the
// catalog by scripts/export-api-docs.mjs, so a change of the API that is not exported leaves them describing another
// one: these tests check that the committed files are what npm run docs:api writes, and that they cover every method,
// route and MCP tool.
const root = path.join(__dirname, '..');
const exporter = import('../scripts/export-api-docs.mjs');

test('docs/api.md and docs/openapi.json are what npm run docs:api writes', async () => {
  const { render } = await exporter;
  const files = await render();
  assert.equal(Object.keys(files).length, 2);
  for (const [file, text] of Object.entries(files)) {
    const name = path.relative(root, file).replaceAll('\\', '/');
    assert.ok(fs.existsSync(file), `${name}이(가) 없습니다. npm run docs:api를 실행하세요.`);
    assert.equal(
      fs.readFileSync(file, 'utf8').replace(/\r\n/g, '\n'),
      text,
      `${name}이(가) 최신이 아닙니다. npm run docs:api를 실행하세요.`,
    );
  }
});

test('docs/api.md lists every method once and every MCP tool', async () => {
  const { OUTPUTS } = await exporter;
  const markdown = fs.readFileSync(OUTPUTS.markdown, 'utf8');
  const headings = [...markdown.matchAll(/^#### `([^`]+)`$/gm)].map((match) => match[1]);
  for (const name of Object.keys(METHODS))
    assert.equal(headings.filter((heading) => heading === name).length, 1, name);
  for (const tool of TOOLS) assert.ok(headings.includes(tool.name), tool.name);
  for (const route of ROUTES)
    assert.ok(markdown.includes(`\`${route.verb} ${API_PREFIX}${route.path}\``), route.path);
});

test('docs/openapi.json has every route of the catalog', async () => {
  const { OUTPUTS } = await exporter;
  const document = JSON.parse(fs.readFileSync(OUTPUTS.openapi, 'utf8'));
  assert.equal(document.openapi, '3.1.0');
  for (const route of ROUTES)
    assert.ok(document.paths[route.path]?.[route.verb.toLowerCase()], route.path);
});

test('the MCP tools of api.md are read from their handlers', async () => {
  const { toolMethods, methodGroups } = await exporter;
  const tools = toolMethods();
  // Tools of their own call their methods by name; the generic ones take any.
  assert.deepEqual(tools.get('search_documents'), ['wiki.search']);
  assert.deepEqual(tools.get('get_app_status'), ['app.status', 'stats']);
  assert.deepEqual(tools.get('call_api'), []);
  for (const methods of tools.values())
    for (const name of methods) assert.ok(Object.hasOwn(METHODS, name), name);
  // A method without a route goes with the methods of its namespace, or to the methods only POST /rpc reaches.
  const groups = methodGroups();
  assert.equal(groups.get('wiki.search'), 'search');
  assert.equal(groups.get('wiki.list'), 'documents');
  assert.equal(groups.get('tasks.list'), 'tasks');
  assert.equal(groups.get('office.start'), 'rpc');
});
