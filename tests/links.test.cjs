const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

// src/lib/links.ts as CommonJS: it imports nothing, and its types go with the transpiler.
function load(file) {
  const source = fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const module = { exports: {} };
  new Function('module', 'exports', 'require', outputText)(module, module.exports, require);
  return module.exports;
}
const { hasScheme, httpUrl, importTarget, jiraIssueKey } = load('src/lib/links.ts');

test('an address typed without its scheme reads as an https one', () => {
  assert.equal(httpUrl('example.com').href, 'https://example.com/');
  assert.equal(httpUrl(' //example.com/portal ').href, 'https://example.com/portal');
  assert.equal(httpUrl('127.0.0.1:8080/jira').href, 'https://127.0.0.1:8080/jira');
  assert.equal(httpUrl('http://example.com').href, 'http://example.com/');
  assert.equal(hasScheme(' HTTP://example.com'), true);
  assert.equal(hasScheme('example.com'), false);
  for (const value of [
    'C:\\docs\\a.docx',
    'C:/docs/a.docx',
    '\\\\server\\share',
    'a b',
    'user@example.com',
    'mailto:a@example.com',
    'about:blank',
    'ftp://example.com',
    'https://user:pw@example.com',
  ])
    assert.equal(httpUrl(value), null, value);
});

test('generic document links and local files route to supported importers', () => {
  assert.deepEqual(importTarget('jira.example/browse/abc-1'), {
    kind: 'jira',
    url: 'jira.example/browse/abc-1',
    key: 'ABC-1',
  });
  assert.equal(jiraIssueKey('jira.example:8080/jira/browse/ABC-2'), 'ABC-2');
  assert.deepEqual(importTarget('wiki.example/spaces/A/pages/1'), {
    kind: 'confluence',
    url: 'wiki.example/spaces/A/pages/1',
  });
  assert.deepEqual(importTarget('https://example.com/'), {
    kind: 'other-link',
    value: 'https://example.com/',
  });
  // Without a scheme, anything else is a path: a file's name, a folder, one on another computer.
  assert.equal(importTarget('report.docx').kind, 'office');
  assert.equal(importTarget('report.rtf').kind, 'rtf');
  assert.equal(importTarget('example.com/notes').kind, 'folder');
  assert.equal(importTarget('C:\\work\\browse\\ABC-1').kind, 'folder');
  assert.equal(importTarget('//server/share/browse/ABC-1').kind, 'folder');
  assert.equal(importTarget('jira.example/browse/ABC-1', true).kind, 'folder');
});
