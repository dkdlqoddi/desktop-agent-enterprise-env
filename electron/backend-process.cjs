const childProcess = require('node:child_process');
const { createInterface } = require('node:readline');
const { randomUUID } = require('node:crypto');
const path = require('node:path');

const ENDED = 'Python 백엔드가 종료되었습니다. 앱을 다시 실행해 주세요.';
// Requests without a limit of their own: one LLM call of up to 300 seconds (the settings' limit), plus a margin.
const LIMIT = 330_000;

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
  // is in the log), or it gave no answer within `limit` ms (a cold start under antivirus scanning can take a while). The
  // time limit ends with the start either way.
  async started({ limit = 60_000, message = ENDED } = {}) {
    let timer;
    try {
      await Promise.race([
        this.spawned.then(() => this.request('health')),
        new Promise((_, reject) => {
          timer = setTimeout(() => {
            void this.diagnostics.record(
              `백엔드가 ${Math.round(limit / 1000)}초 안에 응답하지 않았습니다.`,
            );
            reject(new Error(message));
          }, limit);
        }),
      ]);
    } catch (error) {
      if (!this.ended) throw error;
      const { code, signal } = this.ended;
      throw new Error(
        `Python 백엔드가 시작하는 중에 종료되었습니다(종료 코드 ${code ?? '없음'}${signal ? `, 신호 ${signal}` : ''}). 앱을 다시 실행해 주세요.`,
      );
    } finally {
      clearTimeout(timer);
    }
  }

  // One request: the backend's answer, or the error it gave. A request it cannot take fails at once.
  request(method, params = {}) {
    if (!this.running) return Promise.reject(new Error(this.failure || ENDED));
    // Running: the process exists.
    const child = /** @type {import('node:child_process').ChildProcessWithoutNullStreams} */ (
      this.child
    );
    return new Promise((resolve, reject) => {
      const id = randomUUID();
      const limit = this.limits[method] ?? this.limit;
      const timer = setTimeout(() => {
        this.pending.delete(id);
        void this.diagnostics.record(
          `요청 ${method}이(가) ${limit / 1000}초 안에 끝나지 않았습니다.`,
        );
        reject(new Error('응답 제한 시간을 초과했습니다.'));
      }, limit);
      this.pending.set(id, { resolve, reject, timer, method, started: Date.now() });
      child.stdin.write(JSON.stringify({ id, method, params }) + '\n', (error) => {
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

module.exports = { BackendProcess, spawnFailure, ENDED, LIMIT };
