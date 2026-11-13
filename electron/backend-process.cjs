const childProcess = require('node:child_process');
const { createInterface } = require('node:readline');
const { randomUUID } = require('node:crypto');
const path = require('node:path');

const ENDED = 'Python 백엔드가 종료되었습니다. 앱을 다시 실행해 주세요.';
// Requests without a limit of their own: one LLM call of up to 300 seconds (the settings' limit), plus a margin.
const LIMIT = 330_000;
// How long a start waits for a backend that says it brings the Wiki up to date first (wiki.upgrading) instead of the
// minute a start has. A new version's migration may copy all of the Wiki's imported texts once (migrations.py), and
// SQLite writes them a second time as it moves its log into the file after the commit: 10 GB at 30 MB/s, a slow disk
// under antivirus scanning, take about 6 minutes each, some 12 minutes in all. Thirty minutes leave room for a disk
// twice as slow; a backend that does not answer even then has stopped, and the start fails as any other.
const UPGRADE_LIMIT = 30 * 60_000;

// A time limit as the log says it: in minutes from a minute on, else in seconds.
const span = (ms) => (ms >= 60_000 ? `${Math.round(ms / 60_000)}분` : `${Math.round(ms / 1000)}초`);

// What the user is told when the backend's program could not be started at all, by the error Windows gave. A file that
// antivirus quarantined is gone (ENOENT); one it blocks cannot be run (EACCES, EPERM, or an error Node does not name).
function spawnFailure(error, executable, packaged) {
  const code = error?.code || 'UNKNOWN';
  if (!packaged)
    return `Python 백엔드를 시작하지 못했습니다(${code}: ${executable}). 개발 환경에서는 npm run setup:python을 먼저 실행해 주세요.`;
  if (code === 'ENOENT')
    return `Python 백엔드 실행 파일을 찾지 못했습니다(${executable}). 백신이 검역(격리)했을 수 있으니 백신의 검역 기록을 확인한 뒤 앱을 다시 실행해 주세요.`;
  return `Python 백엔드 실행 파일을 실행하지 못했습니다(${code}: ${executable}). 백신이나 보안 프로그램이 막았을 수 있으니 차단 기록을 확인한 뒤 앱을 다시 실행해 주세요.`;
}

// One process of the Python backend. Requests go to it as JSON lines on its standard input and are answered by the line
// of its standard output with the same id; the end of its error output is kept for the log (Diagnostics.capture), and
// with PRIVATE_AGENT_LOG_CONSOLE=1 (npm run dev) it is also written to this process's error output as it comes, so the
// terminal shows the backend's tracebacks and log. `exited` hears of an end the process came to by itself and gives the
// message its requests still waiting fail with.
// `onEvent(name, params)` hears the events the backend sends of its own accord: lines {event, params} without an id,
// such as the parts of an answer as the LLM writes it.
class BackendProcess {
  /**
   * @param {{
   *   exited?: (ended: { code: number | null, signal: NodeJS.Signals | null }) => string | undefined,
   *   onEvent?: (name: string, params: any) => void,
   *   [option: string]: any,
   * }} options
   */
  constructor({
    executable,
    args = [],
    options = {},
    diagnostics,
    packaged = false,
    limits = {},
    limit = LIMIT,
    exited = () => ENDED,
    onEvent = () => {},
    spawn = childProcess.spawn,
    echo = process.env.PRIVATE_AGENT_LOG_CONSOLE === '1' ? process.stderr : null,
  }) {
    this.executable = executable;
    this.diagnostics = diagnostics;
    this.packaged = packaged;
    this.limits = limits;
    this.limit = limit;
    this.exited = exited;
    this.onEvent = onEvent;
    // While started() waits: what extends its time limit when the backend says it brings the Wiki up to date.
    /** @type {((params: any) => void) | null} */
    this.upgrading = null;
    this.pending = new Map();
    // Why the program could not be started, once that is known.
    this.failure = '';
    // How the process ended ({ code, signal }), once it has.
    this.ended = null;
    let spawned;
    this.spawned = new Promise((resolve, reject) => (spawned = { resolve, reject }));
    this.spawned.catch(() => {}); // Read by started(), if at all.
    this.settleSpawn = spawned;
    try {
      // Spawned with its three standard streams as pipes: none of them is null.
      this.child = /** @type {import('node:child_process').ChildProcessWithoutNullStreams} */ (
        spawn(executable, args, options)
      );
    } catch (error) {
      // Some failures (an error Node does not name, an invalid path) are thrown before a process exists.
      this.child = null;
      this.failed(error);
      return;
    }
    const child = this.child;
    child.once('spawn', () => spawned.resolve());
    child.on('error', (error) => {
      // Not started at all: Node emits no 'exit' then, only this.
      if (child.pid === undefined) this.failed(error);
      else void diagnostics.record(`백엔드 프로세스 오류: ${error.message}`);
    });
    child.on('exit', (code, signal) => {
      this.ended = { code, signal };
      this.reject(this.exited({ code, signal }) || ENDED);
    });
    child.stdin?.on('error', () => {});
    child.stderr?.setEncoding('utf8');
    child.stderr?.on('data', (chunk) => {
      diagnostics.capture(chunk);
      echo?.write(chunk);
    });
    if (child.stdout)
      createInterface({ input: child.stdout }).on('line', (line) => this.answer(line));
  }

  get pid() {
    return this.child?.pid;
  }

  // Started, and not ended or stopped since.
  get running() {
    const child = this.child;
    return (
      !!child &&
      child.pid !== undefined &&
      child.exitCode === null &&
      child.signalCode === null &&
      !child.killed &&
      !this.failure
    );
  }

  // The program could not be started: the log keeps the error and the file, the requests fail with what to do.
  failed(error) {
    if (this.failure) return;
    this.failure = spawnFailure(error, this.executable, this.packaged);
    void this.diagnostics.record(
      `백엔드 프로세스를 시작하지 못했습니다: ${error?.message} (코드 ${error?.code ?? '없음'}, errno ${error?.errno ?? '없음'}, 실행 파일 ${this.executable})`,
    );
    this.settleSpawn.reject(new Error(this.failure));
    this.reject(this.failure);
  }

  // Resolves once the backend answers; fails with why it did not: it could not be started, it ended (its error output
  // is in the log), or it gave no answer within `limit` ms (a cold start under antivirus scanning can take a while). A
  // backend that says it brings the Wiki up to date first (wiki.upgrading, before its migrations run) is waited for
  // `upgrade` ms from then on instead, with a note in the log: a start given up during a migration would roll it back,
  // and every later start would end the same way. The time limit ends with the start either way.
  async started({ limit = 60_000, message = ENDED, upgrade = UPGRADE_LIMIT } = {}) {
    let timer;
    try {
      await Promise.race([
        // The answer's own time limit outlasts the start's, however late the backend's word came: the start decides.
        this.spawned.then(() => this.request('health', {}, limit + upgrade)),
        new Promise((_, reject) => {
          timer = setTimeout(() => {
            void this.diagnostics.record(
              `백엔드가 ${Math.round(limit / 1000)}초 안에 응답하지 않았습니다.`,
            );
            reject(new Error(message));
          }, limit);
          this.upgrading = (params) => {
            this.upgrading = null;
            const version = (value) => (Number.isInteger(value) ? value : '?');
            void this.diagnostics.record(
              `백엔드가 Wiki를 스키마 버전 ${version(params.from)}에서 ${version(params.to)}(으)로 올리고 있어 시작을 최대 ${span(upgrade)} 기다립니다.`,
            );
            clearTimeout(timer);
            timer = setTimeout(() => {
              void this.diagnostics.record(
                `Wiki를 올리는 백엔드가 ${span(upgrade)} 안에 시작을 마치지 못했습니다.`,
              );
              reject(new Error(message));
            }, upgrade);
          };
        }),
      ]);
    } catch (error) {
      if (!this.ended) throw error;
      const { code, signal } = this.ended;
      throw new Error(
        `Python 백엔드가 시작하는 중에 종료되었습니다(종료 코드 ${code ?? '없음'}${signal ? `, 신호 ${signal}` : ''}). 앱을 다시 실행해 주세요.`,
        { cause: error },
      );
    } finally {
      clearTimeout(timer);
      this.upgrading = null;
    }
  }

  // One request: the backend's answer, or the error it gave. A request it cannot take fails at once. The line carries
  // the time this process waits for the answer (limit_ms), so that work the backend bounds by it (an AI analysis) ends
  // before then: the method's own limit, or `limit` when given.
  request(method, params = {}, limit = this.limits[method] ?? this.limit) {
    if (!this.running) return Promise.reject(new Error(this.failure || ENDED));
    // Running: the process exists.
    const child = /** @type {import('node:child_process').ChildProcessWithoutNullStreams} */ (
      this.child
    );
    return new Promise((resolve, reject) => {
      const id = randomUUID();
      const timer = setTimeout(() => {
        this.pending.delete(id);
        void this.diagnostics.record(
          `요청 ${method}이(가) ${limit / 1000}초 안에 끝나지 않았습니다.`,
        );
        reject(new Error('응답 제한 시간을 초과했습니다.'));
      }, limit);
      this.pending.set(id, { resolve, reject, timer, method, started: Date.now() });
      child.stdin.write(JSON.stringify({ id, method, params, limit_ms: limit }) + '\n', (error) => {
        if (error && this.pending.has(id)) {
          clearTimeout(timer);
          this.pending.delete(id);
          reject(new Error('백엔드에 요청을 전달하지 못했습니다.'));
        }
      });
    });
  }

  answer(line) {
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      return;
    }
    // A line without an id is no answer but an event of the backend's own; one its listener fails on stays its own.
    if (message && message.id === undefined && typeof message.event === 'string') {
      // The start waits longer for a backend that brings the Wiki up to date (started).
      if (message.event === 'wiki.upgrading') this.upgrading?.(message.params ?? {});
      try {
        this.onEvent(message.event, message.params ?? {});
      } catch (error) {
        void this.diagnostics.record(
          `백엔드 이벤트 ${message.event}을(를) 전하지 못했습니다: ${error.message}`,
        );
      }
      return;
    }
    const item = this.pending.get(message?.id);
    if (!item) return;
    clearTimeout(item.timer);
    this.pending.delete(message.id);
    // At PRIVATE_AGENT_LOG_LEVEL=debug: each request's method and time, never its parameters or its answer.
    void this.diagnostics.debug?.(
      `요청 ${item.method} ${Date.now() - item.started}ms${message.error ? ' (오류)' : ''}`,
    );
    if (message.error) item.reject(new Error(message.error));
    else item.resolve(message.result);
  }

  // Every request still waiting fails with `message`.
  reject(message) {
    for (const item of this.pending.values()) {
      clearTimeout(item.timer);
      item.reject(new Error(message));
    }
    this.pending.clear();
  }

  // Ends the process with the processes it started (browsers, Office workers), waiting for it at most five seconds;
  // its requests still waiting fail with `message`.
  async stop(message = ENDED) {
    this.reject(message);
    const child = this.child;
    if (!child?.pid || child.exitCode !== null || child.signalCode !== null) return;
    const pid = child.pid;
    await new Promise((resolve) => {
      child.once('exit', resolve);
      if (process.platform === 'win32')
        childProcess
          .spawn(
            path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'taskkill.exe'),
            ['/pid', String(pid), '/T', '/F'],
            { windowsHide: true, stdio: 'ignore' },
          )
          .on('error', () => {}); // The wait below still ends.
      else {
        // The process group the backend leads (detached).
        try {
          process.kill(-pid, 'SIGTERM');
        } catch {
          resolve(undefined);
        }
      }
      setTimeout(resolve, 5000).unref();
    });
  }
}

module.exports = { BackendProcess, spawnFailure, ENDED, LIMIT, UPGRADE_LIMIT };
