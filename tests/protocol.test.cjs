const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const { existsSync, readFileSync } = require('node:fs');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {
  BUNDLE,
  PRIVILEGES,
  SCHEME,
  bundleHandler,
  contentSecurityPolicy,
  scriptHashes,
} = require('../electron/protocol.cjs');

// The inline scripts of a page of the export, as Next.js writes them: the data its scripts start from.
const BOOT = 'self.__next_f=self.__next_f||[]';
const DATA = 'self.__next_f.push([1,"page data <\\/script> in a string"])';
const PAGE = `<!doctype html><html><head><script src="/_next/static/chunks/main.js" async=""></script></head><body><script>${BOOT}</script><script>${DATA}</script><script></script></body></html>`;
// What a request outside the export would read: the project's files beside out/, and a folder whose name only starts
// with "out".
const SECRET = 'SECRET-OUTSIDE-THE-EXPORT';

const sha256 = (text) => `'sha256-${createHash('sha256').update(text).digest('base64')}'`;

// A static export in a folder of its own, with files beside it that must never be served.
async function exported(t) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'agent-protocol-test-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const out = path.join(directory, 'out');
  const files = {
    'index.html': PAGE,
    'docs/index.html': '<!doctype html><title>docs</title><p>docs</p>',
    '_next/static/chunks/main.js': 'console.log(1)',
    '_next/static/css/app.css': 'body{margin:0}',
    'favicon.ico': 'ico',
    'data.bin': 'bytes',
  };
  for (const [name, text] of Object.entries(files)) {
    await fs.mkdir(path.dirname(path.join(out, name)), { recursive: true });
    await fs.writeFile(path.join(out, name), text);
  }
  await fs.writeFile(path.join(directory, 'package.json'), SECRET);
  await fs.mkdir(path.join(directory, 'out-evil'));
  await fs.writeFile(path.join(directory, 'out-evil', 'x.txt'), SECRET);
  return { out, serve: bundleHandler(out), secret: path.join(directory, 'package.json') };
}

const get = (serve, url, method = 'GET') => serve(new Request(url, { method }));

// The directives of a Content-Security-Policy header, each with its sources.
const directives = (header) =>
  Object.fromEntries(
    header
      .split(';')
      .map((part) => part.trim().split(/\s+/))
      .filter(([name]) => name)
      .map(([name, ...sources]) => [name, sources]),
  );

test('the scheme is standard and secure, and never bypasses the CSP', () => {
  assert.equal(SCHEME, 'agent');
  assert.equal(BUNDLE, 'agent://bundle/');
  assert.equal(PRIVILEGES.scheme, SCHEME);
  assert.deepEqual(PRIVILEGES.privileges, {
    standard: true,
    secure: true,
    supportFetchAPI: true,
    codeCache: true,
  });
  assert.equal(PRIVILEGES.privileges.bypassCSP, undefined);
});

test('the index route and a folder serve their index.html with its script hashes', async (t) => {
  const { serve } = await exported(t);
  const response = await get(serve, 'agent://bundle/');
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('Content-Type'), 'text/html; charset=utf-8');
  assert.equal(response.headers.get('X-Content-Type-Options'), 'nosniff');
  assert.equal(await response.text(), PAGE);
  const policy = directives(response.headers.get('Content-Security-Policy'));
  // The page's two inline scripts by their hashes; the empty one and the one with a src need none.
  assert.deepEqual(policy['script-src'], ["'self'", sha256(BOOT), sha256(DATA)]);

  const docs = await get(serve, 'agent://bundle/docs/');
  assert.equal(docs.status, 200);
  assert.match(await docs.text(), /<p>docs<\/p>/);
  // A page without inline scripts allows the bundle's own scripts alone.
  assert.deepEqual(directives(docs.headers.get('Content-Security-Policy'))['script-src'], [
    "'self'",
  ]);
});

test('no script runs but the bundle and its hashed inline scripts, and nothing loads from elsewhere', async (t) => {
  const { serve } = await exported(t);
  const response = await get(serve, 'agent://bundle/');
  const header = response.headers.get('Content-Security-Policy');
  const policy = directives(header);
  for (const source of policy['script-src'])
    assert.match(source, /^'self'$|^'sha256-[A-Za-z0-9+/]+=*'$/, source);
  assert.doesNotMatch(policy['script-src'].join(' '), /unsafe-inline|unsafe-eval|\*|https?:|data:/);
  assert.deepEqual(policy['default-src'], ["'self'"]);
  assert.deepEqual(policy['img-src'], ["'self'", 'data:']);
  assert.deepEqual(policy['connect-src'], ["'self'"]);
  assert.deepEqual(policy['font-src'], ["'self'"]);
  for (const name of ['object-src', 'base-uri', 'frame-src', 'form-action'])
    assert.deepEqual(policy[name], ["'none'"], name);
  // No host, wildcard or scheme source anywhere: the screens reach nothing outside the app.
  assert.doesNotMatch(header, /https?:|wss?:|\*|blob:|filesystem:/);
  assert.equal(header, contentSecurityPolicy(scriptHashes(PAGE)));
});

test('script hashes cover each inline script with text, whatever its attributes', () => {
  assert.equal(scriptHashes('<p>no scripts</p>'), '');
  assert.equal(
    scriptHashes(
      '<script type="module">a()</script><SCRIPT>b()</SCRIPT><script\n id="x">c()</script>',
    ),
    // The pattern is case-sensitive, as Next.js writes lowercase tags.
    `${sha256('a()')} ${sha256('c()')}`,
  );
  assert.equal(scriptHashes('<script src="/x.js"></script>'), '');
  // A policy without hashes still allows the bundle's own script files.
  assert.match(contentSecurityPolicy(''), /script-src 'self' ;/);
});

test('files are served with their types, and anything else as bytes', async (t) => {
  const { serve } = await exported(t);
  const types = {
    'agent://bundle/_next/static/chunks/main.js': 'text/javascript',
    'agent://bundle/_next/static/css/app.css': 'text/css',
    'agent://bundle/favicon.ico': 'image/x-icon',
    'agent://bundle/data.bin': 'application/octet-stream',
  };
  for (const [url, type] of Object.entries(types)) {
    const response = await get(serve, url);
    assert.equal(response.status, 200, url);
    assert.equal(response.headers.get('Content-Type'), type, url);
    assert.equal(response.headers.get('X-Content-Type-Options'), 'nosniff', url);
    // Not a page: its policy names no hashes.
    assert.match(response.headers.get('Content-Security-Policy'), /script-src 'self' ;/, url);
  }
  assert.equal(await (await get(serve, 'agent://bundle/data.bin')).text(), 'bytes');
});

test('no request reads a file outside the export', async (t) => {
  const { serve, secret } = await exported(t);
  const attempts = [
    // Dot segments, which the URL parser already takes out (as Chromium does for the standard scheme).
    'agent://bundle/../package.json',
    'agent://bundle/%2e%2e/package.json',
    'agent://bundle/.%2E/package.json',
    'agent://bundle/docs/../../package.json',
    // Escaped slashes, which only decoding turns into ../.
    'agent://bundle/..%2fpackage.json',
    'agent://bundle/%2e%2e%2fpackage.json',
    'agent://bundle/docs/..%2f..%2fpackage.json',
    'agent://bundle/..%2f..%2f..%2f..%2f..%2f..%2fetc%2fpasswd',
    // A folder beside out/ whose name starts the same way.
    'agent://bundle/..%2fout-evil%2fx.txt',
    // Backslashes, separators on Windows.
    'agent://bundle/..%5cpackage.json',
    'agent://bundle/..%5c..%5cpackage.json',
    // Absolute paths, which resolve inside the export.
    'agent://bundle//etc/passwd',
    'agent://bundle/%2fetc%2fpasswd',
    `agent://bundle/${encodeURIComponent(secret)}`,
    'agent://bundle/C:%5cWindows%5cwin.ini',
  ];
  for (const url of attempts) {
    const response = await get(serve, url);
    assert.ok([403, 404].includes(response.status), `${url}: ${response.status}`);
    assert.doesNotMatch(await response.text(), new RegExp(SECRET), url);
  }
  // Those that leave the export once decoded are refused outright.
  for (const url of [
    'agent://bundle/..%2fpackage.json',
    'agent://bundle/%2e%2e%2fpackage.json',
    'agent://bundle/..%2fout-evil%2fx.txt',
  ])
    assert.equal((await get(serve, url)).status, 403, url);
});

test('a missing or unreadable file is not found', async (t) => {
  const { serve } = await exported(t);
  for (const url of [
    'agent://bundle/missing.js',
    'agent://bundle/missing/',
    // A folder named without its trailing slash.
    'agent://bundle/docs',
    // A NUL byte, which no file name holds, and an escape that does not decode.
    'agent://bundle/index.html%00.png',
    'agent://bundle/%E0%A4%A',
  ]) {
    const response = await get(serve, url);
    assert.equal(response.status, 404, url);
    assert.equal(await response.text(), 'Not found', url);
  }
});

test('another host or method is refused', async (t) => {
  const { serve } = await exported(t);
  for (const url of [
    'agent://other/',
    'agent://bundle.example.test/',
    'agent://bundlex/index.html',
  ])
    assert.equal((await get(serve, url)).status, 403, url);
  for (const method of ['POST', 'PUT', 'DELETE', 'HEAD'])
    assert.equal((await get(serve, 'agent://bundle/', method)).status, 403, method);
});

// The real export, once npm run build:web has made it: every inline script of its pages is allowed by its hash.
test(
  'the CSP of each exported page names the hash of every inline script it holds',
  { skip: !existsSync(path.join(__dirname, '..', 'out', 'index.html')) && 'out/ is not built' },
  async () => {
    const out = path.join(__dirname, '..', 'out');
    const serve = bundleHandler(out);
    const pages = (await fs.readdir(out, { recursive: true }))
      .filter((name) => name.endsWith('index.html'))
      .map((name) => name.split(path.sep).join('/'));
    assert.ok(pages.length > 0);
    for (const name of pages) {
      const html = readFileSync(path.join(out, name), 'utf8');
      const inline = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)]
        .map((match) => match[1])
        .filter(Boolean);
      const response = await get(serve, BUNDLE + name.replace(/index\.html$/, ''));
      assert.equal(response.status, 200, name);
      const sources = directives(response.headers.get('Content-Security-Policy'))['script-src'];
      for (const script of inline) assert.ok(sources.includes(sha256(script)), name);
      assert.ok(!sources.includes("'unsafe-inline'"), name);
    }
  },
);
