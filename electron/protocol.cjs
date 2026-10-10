const { createHash } = require('node:crypto');
const fs = require('node:fs/promises');
const path = require('node:path');

// The screens' own scheme: the static export of the Next.js screens (out/), served from agent://bundle/ and nowhere
// else. The window loads only this origin (electron/main.cjs), and trusted-sender.cjs trusts IPC messages only from a
// page of it. Nothing here needs Electron, so tests/protocol.test.cjs runs it under plain Node.js.
const SCHEME = 'agent';
const BUNDLE = `${SCHEME}://bundle/`;
// For protocol.registerSchemesAsPrivileged before the app is ready. codeCache: the screens' scripts are compiled once
// and kept, as Chromium does for https pages.
const PRIVILEGES = {
  scheme: SCHEME,
  privileges: { standard: true, secure: true, supportFetchAPI: true, codeCache: true },
};
// The kinds of file a static export holds; any other file is served as bytes, which nosniff keeps from being run.
const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript',
  '.css': 'text/css',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
  '.txt': 'text/plain',
};

// The hashes of a page's inline scripts, for its script-src. Next.js writes the data its scripts start from inline in
// every page; a hash per script allows exactly those, where 'unsafe-inline' would allow whatever script tag a page
// came to carry. Scripts with a src (and so no text) are allowed by 'self' instead.
function scriptHashes(html) {
  return [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)]
    .filter((match) => match[1])
    .map((match) => `'sha256-${createHash('sha256').update(match[1]).digest('base64')}'`)
    .join(' ');
}

// The policy every response carries: scripts from the bundle and the page's own inline scripts by their hashes,
// pictures from the bundle or data: URLs, and no connection, frame, plugin, base or form target anywhere else. Inline
// styles stay allowed: React writes style attributes, and an imported page's own CSS lives in its shadow root.
function contentSecurityPolicy(hashes = '') {
  return `default-src 'self'; script-src 'self' ${hashes}; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-src 'none'; form-action 'none'`;
}

// The handler of protocol.handle(SCHEME) for the export in `output`. A GET of agent://bundle/<path> answers with the
// file at <path> under `output`, a folder (a path ending in /) with its index.html. Another host or method is refused
// (403), and so is a path that leaves `output` once decoded (../ written as %2e%2e or with %2f, %5c on Windows); a file
// that cannot be read (missing, a folder, a name with a NUL byte, a malformed escape) is not found (404).
function bundleHandler(output) {
  const root = path.resolve(output);
  return async (request) => {
    try {
      const url = new URL(request.url);
      if (url.hostname !== 'bundle' || request.method !== 'GET')
        return new Response('', { status: 403 });
      let pathname = decodeURIComponent(url.pathname);
      if (pathname.endsWith('/')) pathname += 'index.html';
      const file = path.resolve(root, '.' + pathname);
      if (!file.startsWith(root + path.sep)) return new Response('', { status: 403 });
      const data = await fs.readFile(file);
      const hashes = path.extname(file) === '.html' ? scriptHashes(data.toString()) : '';
      return new Response(data, {
        headers: {
          'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream',
          'Content-Security-Policy': contentSecurityPolicy(hashes),
          'X-Content-Type-Options': 'nosniff',
        },
      });
    } catch {
      return new Response('Not found', { status: 404 });
    }
  };
}

module.exports = {
  BUNDLE,
  PRIVILEGES,
  SCHEME,
  TYPES,
  bundleHandler,
  contentSecurityPolicy,
  scriptHashes,
};
