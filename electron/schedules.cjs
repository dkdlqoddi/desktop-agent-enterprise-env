// Runs due prompt schedules from the main process, so they fire while the window is minimized.
class PromptScheduler {
  constructor({ rpc, Notification, ready, focused, send, onClick, interval = 30_000 }) {
    Object.assign(this, { rpc, Notification, ready, focused, send, onClick, interval });
    this.active = new Set();
    this.busy = false;
    this.stopped = false;
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
      // One run at a time: each is a full LLM call, and the backend keeps serving the window.
      for (const due of await this.rpc('schedules.due')) {
        if (this.stopped) break;
        const result = await this.rpc('schedules.execute', {
          id: due.id,
          next_run_at: due.next_run_at,
        });
        if (!result.ran) continue; // Edited, paused, or already run since the lookup.
        const { schedule, run } = result;
        this.send({
          type: 'finished',
          schedule_id: schedule.id,
          name: schedule.name,
          notify: schedule.notify,
          run_id: run.id,
          status: run.status,
          error: run.error,
        });
        // The window shows its own notice when the user is looking at it.
        if (schedule.notify && !this.focused()) this.notify(schedule, run);
      }
    } catch {
      // The next tick retries; each schedule's last outcome is shown on the Prompt page.
    } finally {
      this.busy = false;
    }
  }

  notify(schedule, run) {
    if (!this.Notification.isSupported()) return;
    const success = run.status === 'success';
    const notification = new this.Notification({
      title: `Private Agent · 예약 실행 ${success ? '완료' : '실패'}`,
      body: success ? schedule.name : `${schedule.name}: ${run.error}`,
    });
    this.active.add(notification);
    const cleanup = () => {
      clearTimeout(timeout);
      this.active.delete(notification);
    };
    const timeout = setTimeout(cleanup, 300_000);
    timeout.unref?.();
    notification.once('click', () => {
      this.onClick(run.id);
      cleanup();
    });
    notification.once('failed', cleanup);
    notification.once('close', cleanup);
    notification.show();
  }

  stop() {
    this.stopped = true;
    clearInterval(this.timer);
    for (const notification of this.active) notification.close();
    this.active.clear();
  }
}

module.exports = { PromptScheduler };
