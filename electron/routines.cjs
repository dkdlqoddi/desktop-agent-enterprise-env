// Starts the automatic routines (backend/agent/routines.py) when they are due, and tells how their runs ended. It runs
// in the main process, which alone has the site accounts' passwords: each due routine is handed to the backend with the
// login plans of the sites it signs in to. The backend queues the runs and does one at a time; each check asks which
// runs ended since the last one, to show a notification, keep how each site signed in with its account and have the
// screens reload. While a run is queued or running, it looks again every few seconds. Like the other schedules it works
// only while the app is open: a run missed while it was closed starts once it is open again.
class RoutineRunner {
  constructor({
    rpc,
    logins,
    record,
    ready,
    focused,
    send,
    changed,
    Notification,
    onClick,
    interval = 30_000,
    follow = 3_000,
  }) {
    Object.assign(this, {
      rpc,
      logins,
      record,
      ready,
      focused,
      send,
      changed,
      Notification,
      onClick,
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
        const result = await this.rpc('routines.execute', {
          id: due.id,
          next_run_at: due.next_run_at,
          logins: this.logins(due.needs),
        });
        // Taken by an earlier check, edited or turned off since the lookup, or still running.
        if (result.queued) this.changed();
      }
      await this.collect();
    } catch {
      // Asked again on the next check; each routine's last outcome is shown on the AI 작업 page.
    } finally {
      this.busy = false;
    }
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

  notify(run) {
    if (!this.Notification.isSupported()) return;
    const failed = run.status === 'error';
    const notification = new this.Notification({
      title: `Private Agent · 자동 작업${failed ? ' 실패' : ''}`,
      body: `${run.name}: ${failed ? run.error : run.summary}`,
    });
    this.active.add(notification);
    const cleanup = () => {
      clearTimeout(timeout);
      this.active.delete(notification);
    };
    const timeout = setTimeout(cleanup, 300_000);
    timeout.unref?.();
    notification.once('click', () => {
      this.onClick(run.routine_id);
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

module.exports = { RoutineRunner, readsFiles };
