// The backend's background jobs (the imports of each kind and the AI analysis) and the automatic routines whose runs
// start imports, followed from the main process while they run. Each change reaches the window as a 'job:progress'
// event, so the screens show a job's progress as it comes instead of each asking the backend every second; they read
// every job once as they start (jobs.status), and again now and then in case an event was missed while they loaded.
//
// Every read of the backend is numbered as it is asked (seq), the events' and jobs.status's alike: the backend answers
// requests side by side, so a state read earlier may arrive after one read later, and the screens keep the later one.
const IMPORT_KINDS = ['office', 'rtf', 'image', 'text', 'confluence', 'jira'];
const JOB_KINDS = [...IMPORT_KINDS, 'analysis'];
const ACTIVE = ['scanning', 'login', 'running', 'cancelling'];
// The requests whose answer starts, stops or clears a job, or queues or stops a routine's run.
const STARTS = new RegExp(
  `^(?:(?:${JOB_KINDS.join('|')})\\.(?:start|cancel|dismiss)|routines\\.(?:run|cancel))$`,
);

const active = (job) => !!job && ACTIVE.includes(job.status);
// Confluence and JIRA answer { job }; the other importers and the analysis answer the job itself.
const unwrap = (kind, value) =>
  kind === 'confluence' || kind === 'jira' ? (value?.job ?? null) : (value ?? null);
// What the screens show of a job, with its results by their count: a job keeps up to 5,000 results, and the count
// changes with every one added, so the same text means nothing changed and no event is sent.
const signature = (value) =>
  JSON.stringify(value && { ...value, results: value.results?.length ?? 0 });

class JobProgress {
  constructor({
    rpc,
    send,
    ready = () => true,
    interval = 1000,
    routineInterval = 2000,
    retry = 3000,
    retries = 20,
  }) {
    this.rpc = rpc;
    this.send = send;
    this.ready = ready;
    this.interval = interval;
    this.routineInterval = routineInterval;
    this.retry = retry;
    this.retries = retries;
    this.seq = 0;
    // Per kind, and for 'routines': the number and text of the latest state read.
    this.latest = new Map();
    /** @type {NodeJS.Timeout | undefined} */
    this.timer = undefined;
    this.running = false;
    // follow() was called during a check: check again as soon as it ends.
    this.again = false;
    this.following = false;
    this.failures = 0;
    // Whether a routine's run was queued or running at the last look, when that was, and whether one may have
    // started since (a run was asked for).
    this.routinesActive = false;
    this.routinesAt = 0;
    this.routinesDue = false;
    this.stopped = false;
  }

  // Every job as it is now, for the screens (jobs.status), numbered like the events. While a job runs, or a routine
  // may be starting one, the changes follow as events.
  async snapshot() {
    const read = await this.readJobs();
    this.take(read, false);
    this.routinesDue = true;
    this.follow({ soon: false });
    return read;
  }

  // A request was handled: one that starts, stops or clears a job, or a routine's run, is followed from now on.
  observe(method) {
    if (!STARTS.test(method)) return;
    if (method.startsWith('routines.')) this.routinesDue = true;
    this.follow();
  }

  // Looks at the jobs now (soon) or after the usual interval, and keeps looking while one runs. An AI tool's change
  // or a routine's queued run may have started one (main.cjs changed()).
  follow({ soon = true, routines = false } = {}) {
    if (this.stopped) return;
    if (routines) this.routinesDue = true;
    if (this.running) {
      this.following = true;
      this.again ||= soon;
      return;
    }
    if (this.following && !soon) return;
    this.following = true;
    this.schedule(soon ? 0 : this.interval);
  }

  schedule(delay) {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.check(), delay);
    this.timer.unref?.();
  }

  async check() {
    this.timer = undefined;
    if (this.stopped) return;
    this.running = true;
    let next;
    try {
      if (!this.ready()) throw new Error('not ready');
      const read = await this.readJobs();
      this.take(read, true);
      const jobsActive = JOB_KINDS.some((kind) => active(read.jobs[kind]));
      // A routine's run starts its imports one after another: while one is queued or running the jobs are looked at
      // even when none runs, so that each import shows as it starts, and the routines' own state goes along.
      const now = Date.now();
      if (
        !jobsActive ||
        this.routinesDue ||
        (this.routinesActive && now - this.routinesAt >= this.routineInterval)
      ) {
        this.routinesDue = false;
        this.routinesAt = now;
        this.routinesActive = await this.readRoutines();
      }
      this.failures = 0;
      next = jobsActive ? this.interval : this.routinesActive ? this.routineInterval : null;
    } catch {
      // The backend is busy or starting again: look again shortly, and give up when it keeps failing. The screens'
      // own look now and then (jobs.status) starts following again.
      next = ++this.failures < this.retries ? this.retry : null;
      if (next === null) this.failures = 0;
    } finally {
      this.running = false;
    }
    if (this.stopped) return;
    if (this.again) {
      this.again = false;
      next = 0;
    }
    if (next === null) this.following = false;
    else this.schedule(next);
  }

  async readJobs() {
    const seq = ++this.seq;
    const values = await Promise.all(JOB_KINDS.map((kind) => this.rpc(`${kind}.status`)));
    return {
      seq,
      jobs: Object.fromEntries(JOB_KINDS.map((kind, index) => [kind, unwrap(kind, values[index])])),
    };
  }

  // Keeps the jobs read as `seq`, sending each that changed (`announce`); a state read before the latest kept is old
  // news and is dropped. The screens learn of jobs only from here, so a kind never read before is new to them only if
  // it has a job.
  take({ seq, jobs }, announce) {
    for (const kind of JOB_KINDS) {
      const text = signature(jobs[kind]);
      const last = this.latest.get(kind);
      if (last && last.seq > seq) continue;
      this.latest.set(kind, { seq, text });
      if (announce && (last ? last.text !== text : jobs[kind] !== null))
        this.send({ seq, kind, job: jobs[kind] });
    }
  }

  // The routines, sent when they changed; whether a run is queued or running.
  async readRoutines() {
    const seq = ++this.seq;
    const routines = await this.rpc('routines.list');
    const list = Array.isArray(routines) ? routines : [];
    const text = JSON.stringify(list);
    const last = this.latest.get('routines');
    if (!last || last.seq < seq) {
      this.latest.set('routines', { seq, text });
      if (last?.text !== text) this.send({ seq, kind: 'routines', routines: list });
    }
    return list.some((routine) => routine.running || routine.queued);
  }

  stop() {
    this.stopped = true;
    clearTimeout(this.timer);
    this.timer = undefined;
  }
}

module.exports = { JobProgress, JOB_KINDS, IMPORT_KINDS };
