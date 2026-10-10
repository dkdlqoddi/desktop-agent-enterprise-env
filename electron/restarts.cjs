// The Python backend ending unexpectedly (a crash, antivirus, a bug) is started again, a little later each time, so the
// app goes on without the user restarting it. A backend that keeps ending is left stopped and the user is told: the
// cause is then not a passing one, and the log says what it is. `gaveUp` hears why the last start failed, if it did
// (a program antivirus quarantined, say). The app's window, whose screen may crash too, is loaded again the same way.
const DELAYS = [1000, 5000, 15000];
const WINDOW = 120_000;

class Restarts {
  /** @param {{ failed?: (error: Error) => void, [option: string]: any }} options */
  constructor({
    start,
    stop,
    restarted,
    gaveUp,
    failed = () => {},
    delays = DELAYS,
    window = WINDOW,
    now = Date.now,
    schedule = setTimeout,
  }) {
    this.start = start;
    this.stop = stop;
    this.restarted = restarted;
    this.gaveUp = gaveUp;
    this.failed = failed;
    this.delays = delays;
    this.window = window;
    this.now = now;
    this.schedule = schedule;
    this.recent = [];
    this.active = false;
    this.ended = false;
    // Why the last start failed, until one succeeds.
    this.lastError = undefined;
  }

  // The backend ended without the app asking. A restart under way decides what comes next itself.
  exited() {
    if (this.ended || this.active) return;
    this.next();
  }

  next() {
    const now = this.now();
    this.recent = this.recent.filter((at) => now - at < this.window);
    if (this.recent.length >= this.delays.length) {
      this.gaveUp(this.lastError);
      return;
    }
    const delay = this.delays[this.recent.length];
    this.recent.push(now);
    this.active = true;
    const timer = this.schedule(() => void this.attempt(), delay);
    timer?.unref?.();
  }

  async attempt() {
    if (this.ended) {
      this.active = false;
      return;
    }
    try {
      await this.start();
      this.lastError = undefined;
      this.active = false;
      if (!this.ended) this.restarted();
    } catch (error) {
      this.lastError = error;
      this.failed(error);
      await Promise.resolve(this.stop()).catch(() => {});
      this.active = false;
      if (!this.ended) this.next();
    }
  }

  // The app is closing: no more restarts.
  end() {
    this.ended = true;
  }
}

module.exports = { Restarts, DELAYS, WINDOW };
