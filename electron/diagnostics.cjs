const { readFileSync } = require('node:fs');
const fs = require('node:fs/promises');
const path = require('node:path');

// The app's own log beside the backend's, in the settings folder's logs/: what went wrong where the screens could
// only say that something failed (the backend ending, a request running out of time, a start that failed). Like
// backend.log it never holds request parameters, document text or credentials, only what failed and the error.
const MAX_BYTES = 1_000_000;
// The end of the backend's error output, kept in memory and written down only when the backend ends unexpectedly.
const STDERR_BYTES = 32_000;
// How much of each log goes into the diagnostics the user copies.
const TAIL_BYTES = 32_000;
// Secrets as they could appear in what is logged (an error's message quoting a URL, a header or a JSON body): the
// app's own API tokens (pa_…), OpenAI-style keys, bearer tokens and anything named as a password, key, token or
// cookie. Written down as [REDACTED]; backend/agent/diagnostics.py does the same for the backend's log.
const SECRETS = [
  [/\bpa_[A-Za-z0-9_-]{6,}/g, 'pa_[REDACTED]'],
  [/\bsk-[A-Za-z0-9_-]{6,}/g, 'sk-[REDACTED]'],
  // A header whose whole value is secret, to the end of its line: "Authorization: Bearer <token>" keeps nothing of
  // the token, and a cookie header hides every cookie, not the first alone.
  [
    /\b(authorization|proxy-authorization|cookie|set-cookie)\b("?\s*[=:]\s*)("[^"]*"|'[^']*'|[^\r\n]+)/gi,
    '$1$2[REDACTED]',
  ],
  [
    /\b(password|passwd|pwd|api[_-]?key|x-api-key|token|secret)\b("?\s*[=:]\s*)("[^"]*"|'[^']*'|[^\s,;&}\]]+)/gi,
    '$1$2[REDACTED]',
  ],
  [/\b(bearer)\s+[A-Za-z0-9._~+/=-]{6,}/gi, '$1 [REDACTED]'],
];
// The user's name in the paths of their profile folder, which the copied report leaves out.
const USERS = [/([A-Za-z]:\\Users\\)[^\\\s"']+/g, /(\/home\/)[^/\s"']+/g, /(\/Users\/)[^/\s"']+/g];

// The commit of a packaged build and its date, which scripts/package.mjs writes into the package as
// electron/build-info.json: { commit: <SHA, with -dirty when the tree had uncommitted changes>, date: <ISO date> }.
// A run from source has none.
function buildInfo(file = path.join(__dirname, 'build-info.json')) {
  try {
    const { commit, date } = JSON.parse(readFileSync(file, 'utf8'));
    if (typeof commit !== 'string' || !/^[0-9a-f]{7,40}(-dirty)?$/.test(commit)) return null;
    return { commit, date: typeof date === 'string' ? date : '' };
  } catch {
    return null;
  }
}

// The line the copied report gives the build: the commit's first 12 characters, as `git log --abbrev=12` shows it.
function buildLine(build) {
  if (!build) return null;
  const [sha, dirty] = build.commit.split('-');
  return `빌드 ${sha.slice(0, 12)}${dirty ? '-dirty' : ''}${build.date ? ` · ${build.date}` : ''}`;
}

function redact(text) {
  let out = String(text);
  for (const [pattern, replacement] of SECRETS) out = out.replace(pattern, replacement);
  return out;
}

function anonymize(text) {
  let out = String(text);
  for (const pattern of USERS) out = out.replace(pattern, '$1<user>');
  return out;
}

// PRIVATE_AGENT_LOG_LEVEL=debug adds the debug entries (debug()); the failures record() writes are kept at every level,
// as the copied report depends on them. PRIVATE_AGENT_LOG_CONSOLE=1, which npm run dev sets, writes each entry to the
// error output too, where the terminal shows it.
class Diagnostics {
  constructor({
    directory,
    now = () => new Date(),
    build = buildInfo(),
    level = process.env.PRIVATE_AGENT_LOG_LEVEL,
    terminal = process.env.PRIVATE_AGENT_LOG_CONSOLE === '1',
    output = process.stderr,
  }) {
    Object.assign(this, { directory, now, build, terminal, output });
    this.debugging =
      String(level ?? '')
        .trim()
        .toLowerCase() === 'debug';
    this.file = path.join(directory, 'app.log');
    this.queue = Promise.resolve();
    this.stderr = '';
  }

  // Appends one entry, with the secrets it may quote redacted. A log that cannot be written never stops the app.
  record(text) {
    const entry = `${this.now().toISOString()} ${redact(text).trimEnd()}\n`;
    if (this.terminal) this.output.write(entry);
    this.queue = this.queue
      .then(async () => {
        await fs.mkdir(this.directory, { recursive: true });
        try {
          if ((await fs.stat(this.file)).size > MAX_BYTES)
            await fs.rename(this.file, this.file + '.1');
        } catch {
          /* No log yet. */
        }
        await fs.appendFile(this.file, entry, 'utf8');
      })
      .catch(() => {});
    return this.queue;
  }

  // An entry only PRIVATE_AGENT_LOG_LEVEL=debug keeps: what helps while developing, never request parameters.
  debug(text) {
    return this.debugging ? this.record(text) : this.queue;
  }

  // What the backend writes to its error output: tracebacks Python prints, and messages of a failed start.
  capture(chunk) {
    this.stderr = (this.stderr + chunk).slice(-STDERR_BYTES);
  }

  backendExited({ code, signal }) {
    const tail = this.stderr.trim();
    this.stderr = '';
    return this.record(
      `백엔드가 예기치 않게 종료되었습니다 (종료 코드 ${code ?? '없음'}${signal ? `, 신호 ${signal}` : ''}).${tail ? `\n${tail}` : ''}`,
    );
  }

  // The end of one log, from its first whole line.
  async tail(name) {
    let handle;
    try {
      handle = await fs.open(path.join(this.directory, name), 'r');
      const { size } = await handle.stat();
      const length = Math.min(size, TAIL_BYTES);
      const buffer = Buffer.alloc(length);
      await handle.read(buffer, 0, length, size - length);
      const text = buffer.toString('utf8');
      return length < size ? text.slice(text.indexOf('\n') + 1) : text;
    } catch {
      return '';
    } finally {
      await handle?.close();
    }
  }

  // The text the user copies to report a problem: the app and the PC, then the end of both logs. Secrets are redacted
  // once more (a log an earlier version wrote) and the user's name in paths left out: the report is meant to be sent.
  async report(facts) {
    await this.queue;
    const [own, backend] = await Promise.all([this.tail('app.log'), this.tail('backend.log')]);
    const build = buildLine(this.build);
    const text = [
      `Private Agent 진단 정보 · ${this.now().toISOString()}`,
      ...(build ? [build] : []),
      ...facts,
      `로그 폴더: ${this.directory}`,
      '',
      '=== app.log (마지막 부분) ===',
      own.trim() || '(기록 없음)',
      '',
      '=== backend.log (마지막 부분) ===',
      backend.trim() || '(기록 없음)',
      '',
    ].join('\n');
    return anonymize(redact(text));
  }
}

module.exports = {
  Diagnostics,
  MAX_BYTES,
  STDERR_BYTES,
  TAIL_BYTES,
  redact,
  anonymize,
  buildInfo,
  buildLine,
};
