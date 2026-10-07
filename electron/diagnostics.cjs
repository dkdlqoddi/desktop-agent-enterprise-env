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

class Diagnostics {
  constructor({ directory, now = () => new Date() }) {
    Object.assign(this, { directory, now });
    this.file = path.join(directory, 'app.log');
    this.queue = Promise.resolve();
    this.stderr = '';
  }

  // Appends one entry. A log that cannot be written never stops the app.
  record(text) {
    const entry = `${this.now().toISOString()} ${String(text).trimEnd()}\n`;
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

  // The text the user copies to report a problem: the app and the PC, then the end of both logs.
  async report(facts) {
    await this.queue;
    const [own, backend] = await Promise.all([this.tail('app.log'), this.tail('backend.log')]);
    return [
      `Private Agent 진단 정보 · ${this.now().toISOString()}`,
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
  }
}

module.exports = { Diagnostics, MAX_BYTES, STDERR_BYTES, TAIL_BYTES };
