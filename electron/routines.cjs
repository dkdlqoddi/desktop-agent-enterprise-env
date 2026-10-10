// The app's one scheduler: it starts the automatic routines and the prompt schedules (backend/agent/routines.py and
// schedules.py) when they are due, and tells how their runs ended. It runs in the main process, which alone has the site
// accounts' passwords: each due routine is handed to the backend with the login plans of the sites it signs in to. The
// backend queues the runs and runs a few at a time; each check asks which runs ended since the last one, to show a
// notification, keep how each site signed in with its account and have the screens reload. While a run is queued or
// running, it looks again every few seconds. It works only while the app is open: a run missed while it was closed starts
// once it is open again, and one that came due while the computer slept as soon as it wakes (onWake).
class RoutineRunner {
  constructor({
    rpc,
    logins,
    record,
    ready,
    focused,
    send,
    schedule,
    changed,
    Notification,
    onClick,
    openRun,
    interval = 30_000,
    follow = 3_000,
  }) {
    // `send` tells the window about a routine's run, `schedule` about a prompt schedule's; a click on a notification
    // opens the routine (onClick) or the prompt run (openRun).
    Object.assign(this, {
      rpc,
      logins,
      record,
      ready,
      focused,
      send,
      schedule,
      changed,
      Notification,
      onClick,
      openRun,
      interval,
      followInterval: follow,
    });
    this.active = new Set();
    this.busy = false;
    this.stopped = false;
    // The finished_at of the last run told; null until the first check, which starts from the runs that ended before.
    this.cursor = null;
  }

  start() {
    this.stopped = false;
    void this.tick();
    this.timer = setInterval(() => void this.tick(), this.interval);
    this.timer.unref?.();
  }

  async tick() {
    if (this.stopped || this.busy || !this.ready()) return;
    this.busy = true;
    try {
      if (this.cursor === null) this.cursor = (await this.rpc('routines.events')).cursor;
      for (const due of await this.rpc('routines.due')) {
        if (this.stopped) break;
        // A prompt schedule's run (`schedule`) signs in nowhere: its needs are empty.
        const result = await this.rpc('routines.execute', {
          id: due.id,
          next_run_at: due.next_run_at,
          logins: this.logins(due.needs),
          schedule: due.schedule === true,
        });
        // Taken by an earlier check, edited or turned off since the lookup, or still running.
        if (result.queued) this.changed();
      }
      await this.collect();
    } catch {
      // Asked again on the next check; each routine's and schedule's last outcome is shown on its screen.
    } finally {
      this.busy = false;
    }
  }

  // The computer woke from sleep or its screen was unlocked (onWake): look at what is due now rather than at the next
  // check, which may be most of an interval away.
  wake() {
    if (!this.stopped) void this.tick();
  }

  // A run the user started: follow it from now on.
  poke() {
    this.later(this.followInterval);
  }

  later(delay) {
    clearTimeout(this.soon);
    if (this.stopped) return;
    this.soon = setTimeout(() => void this.follow(), delay);
    this.soon.unref?.();
  }

  async follow() {
    if (this.stopped || this.busy || !this.ready() || this.cursor === null) return this.later(1000);
    this.busy = true;
    try {
      await this.collect();
    } catch {
      this.later(this.followInterval);
    } finally {
      this.busy = false;
    }
  }

  async collect() {
    const events = await this.rpc('routines.events', { after: this.cursor });
    this.cursor = events.cursor;
    for (const run of events.runs) {
      try {
        if (run.schedule_id) {
          this.scheduled(run);
          continue;
        }
        // How each site signed in, as an import of the user's keeps it with the site's account.
        for (const login of run.logins ?? [])
          await Promise.resolve(this.record(login.site_id, login)).catch(() => {});
        // News only: a run that found something, or a failure that is not the same as the last run's. The window
        // shows its own notice when it has focus.
        const news = run.notify && (run.changed || (run.status === 'error' && !run.quiet));
        this.send({
          type: 'finished',
          routine_id: run.routine_id,
          run_id: run.id,
          name: run.name,
          notify: news,
          status: run.status,
          changed: run.changed,
          summary: run.summary,
          error: run.error,
        });
        if (news && !this.focused()) this.notify(run);
      } catch {
        // One run's notice failing leaves the others.
      }
    }
    if (events.runs.length) this.changed();
    if (events.active) this.later(this.followInterval);
  }

  // A prompt schedule's run ended: the window shows it in its run history, and says how it went whenever its schedule
  // asks to be told, with a notice of its own while it has focus and a notification otherwise.
  scheduled(run) {
    this.schedule({
      type: 'finished',
      schedule_id: run.schedule_id,
      name: run.name,
      notify: run.notify,
      run_id: run.id,
      status: run.status,
      error: run.error,
    });
    if (!run.notify || this.focused()) return;
    const success = run.status === 'success';
    this.show(
      `Private Agent · 예약 실행 ${success ? '완료' : '실패'}`,
      success ? run.name : `${run.name}: ${run.error}`,
      () => this.openRun(run.id),
    );
  }

  notify(run) {
    const failed = run.status === 'error';
    this.show(
      `Private Agent · 자동 작업${failed ? ' 실패' : ''}`,
      `${run.name}: ${failed ? run.error : run.summary}`,
      () => this.onClick(run.routine_id),
    );
  }

  show(title, body, onClick) {
    if (!this.Notification.isSupported()) return;
    const notification = new this.Notification({ title, body });
    this.active.add(notification);
    const cleanup = () => {
      clearTimeout(timeout);
      this.active.delete(notification);
    };
    const timeout = setTimeout(cleanup, 300_000);
    timeout.unref?.();
    notification.once('click', () => {
      onClick();
      cleanup();
    });
    notification.once('failed', cleanup);
    notification.once('close', cleanup);
    notification.show();
  }

  stop() {
    this.stopped = true;
    clearInterval(this.timer);
    clearTimeout(this.soon);
    for (const notification of this.active) notification.close();
    this.active.clear();
  }
}

// Whether a request on a routine (routines.save, routines.toggle or routines.run) concerns one that reads this PC's
// files: AI tools may create, change, start or turn it on only with the "full" access level. `saved` is the routine as
// routines.get gives it, null for a new one. The backend decides again on the routine as it saves it (Routines.guard).
function readsFiles(method, params, saved) {
  if (saved?.files) return true;
  if (method !== 'routines.save') return false;
  const kind = saved?.kind ?? params.kind;
  return kind === 'source_refresh';
}

// How long after the computer resumes from sleep, or its screen is unlocked, it looks at what is due: Windows tells of
// the resume before Wi-Fi and a VPN are back, and the runs it starts reach sites and the LLM.
const WAKE_DELAY = 5_000;

// Calls `check` once WAKE_DELAY after the computer resumes from sleep or its screen is unlocked (Electron's
// powerMonitor), which often come together: what came due while it slept then starts at once instead of at the next
// interval. Returns a function that stops it.
function onWake(powerMonitor, check, delay = WAKE_DELAY) {
  const events = ['resume', 'unlock-screen'];
  let timer;
  const soon = () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      try {
        check();
      } catch {
        // The regular checks go on; one of them looks again.
      }
    }, delay);
    timer.unref?.();
  };
  for (const event of events) powerMonitor.on(event, soon);
  return () => {
    clearTimeout(timer);
    for (const event of events) powerMonitor.removeListener(event, soon);
  };
}

module.exports = { RoutineRunner, readsFiles, onWake, WAKE_DELAY };
