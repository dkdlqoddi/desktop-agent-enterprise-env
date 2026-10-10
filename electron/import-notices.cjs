// A background import says once when it is about to do something the user should know about, such
// as reading a presentation in the PowerPoint they have open. The main process watches the job, so
// the notice arrives while the window is minimized or shows another screen.
const ACTIVE = ['scanning', 'login', 'running', 'cancelling'];

class ImportNotices {
  constructor({ rpc, Notification, focused, send, onClick, interval = 1000, retries = 30 }) {
    this.rpc = rpc;
    this.Notification = Notification;
    this.focused = focused;
    this.send = send;
    this.onClick = onClick;
    this.interval = interval;
    this.retries = retries;
    this.active = new Set();
    this.timers = new Set();
    this.stopped = false;
  }

  // Follows the job `jobId` of the `kind` importer until it raises its notice or ends.
  watch(kind, jobId) {
    let failures = 0;
    const poll = async () => {
      let job;
      try {
        job = await this.rpc(`${kind}.status`);
        failures = 0;
      } catch {
        // Status polls may be refused while the backend is busy; give up only when it keeps failing.
        if (++failures < this.retries) this.later(poll);
        return;
      }
      if (this.stopped || !job || job.id !== jobId) return;
      if (job.notice) this.show(job.notice);
      else if (ACTIVE.includes(job.status)) this.later(poll);
    };
    this.later(poll);
  }

  later(task) {
    if (this.stopped) return;
    const timer = setTimeout(() => {
      this.timers.delete(timer);
      void task();
    }, this.interval);
    timer.unref?.();
    this.timers.add(timer);
  }

  show(text) {
    // The window shows its own notice when the user is looking at it, and when the system cannot.
    if (this.focused() || !this.Notification.isSupported()) return this.send(text);
    const notification = new this.Notification({
      title: 'Private Agent · 가져오기 주의사항',
      body: text,
    });
    this.active.add(notification);
    const cleanup = () => {
      clearTimeout(timeout);
      this.active.delete(notification);
    };
    const timeout = setTimeout(cleanup, 300_000);
    timeout.unref?.();
    notification.once('click', () => {
      this.onClick();
      cleanup();
    });
    notification.once('failed', () => {
      this.send(text);
      cleanup();
    });
    notification.once('close', cleanup);
    notification.show();
  }

  stop() {
    this.stopped = true;
    for (const timer of this.timers) clearTimeout(timer);
    this.timers.clear();
    for (const notification of this.active) notification.close();
    this.active.clear();
  }
}

module.exports = { ImportNotices };
