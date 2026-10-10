// Main-process polling continues when the renderer is minimized or on another page.
class ReminderScheduler {
  constructor({ rpc, Notification, ready, onClick, interval = 15_000 }) {
    this.rpc = rpc;
    this.Notification = Notification;
    this.ready = ready;
    this.onClick = onClick;
    this.interval = interval;
    this.attempts = new Map();
    this.active = new Set();
    this.busy = false;
    this.stopped = false;
    // A notification that could not be shown or acknowledged: until one is shown.
    this.error = '';
    // A check of the due reminders that failed: until the next one succeeds.
    this.lookupError = '';
  }

  status() {
    return {
      supported: this.Notification.isSupported(),
      error: this.error || this.lookupError,
    };
  }

  start() {
    this.stopped = false;
    void this.tick();
    this.timer = setInterval(() => void this.tick(), this.interval);
    this.timer.unref?.();
  }

  async tick() {
    if (this.stopped || this.busy || !this.ready() || !this.Notification.isSupported()) return;
    this.busy = true;
    try {
      const due = await this.rpc('reminders.due');
      this.lookupError = '';
      if (this.stopped) return;
      const live = new Set(due.map((task) => `${task.id}:${task.remind_at}`));
      for (const key of this.attempts.keys()) if (!live.has(key)) this.attempts.delete(key);
      let count = 0;
      for (const task of due) {
        const key = `${task.id}:${task.remind_at}`;
        if ((this.attempts.get(key) || 0) > Date.now()) continue;
        if (++count > 3) break;
        this.attempts.set(key, Date.now() + 300_000);
        const notification = new this.Notification({
          title: 'Private Agent · 할 일 알림',
          body: task.title,
        });
        this.active.add(notification);
        const cleanup = () => {
          clearTimeout(timeout);
          this.active.delete(notification);
        };
        const timeout = setTimeout(cleanup, 300_000);
        timeout.unref?.();
        notification.once('show', () => {
          this.error = '';
          // Only acknowledge actual delivery; unsupported/failed notifications remain in the inbox.
          void this.rpc('reminders.ack', { id: task.id, remind_at: task.remind_at }).catch(() => {
            this.error = '알림 전달 상태를 저장하지 못했습니다. 오늘의 업무에서 확인해 주세요.';
          });
        });
        notification.once('failed', () => {
          this.error = '시스템 알림을 표시하지 못했습니다. 오늘의 업무에서 알림을 확인해 주세요.';
          cleanup();
        });
        notification.once('click', () => {
          this.onClick();
          cleanup();
        });
        notification.once('close', cleanup);
        notification.show();
      }
    } catch {
      // A check the backend could not answer because it ended or is starting again is not the reminders' failure: the
      // first check once it is back looks again.
      if (this.ready() && !this.stopped)
        this.lookupError = '알림을 확인하지 못했습니다. 오늘의 업무에서 새로고침해 주세요.';
    } finally {
      this.busy = false;
    }
  }

  stop() {
    this.stopped = true;
    clearInterval(this.timer);
    for (const notification of this.active) notification.close();
    this.active.clear();
  }
}

module.exports = { ReminderScheduler };
