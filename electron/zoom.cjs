const { readJsonFile, writeFileSafely } = require('./files.cjs');

// The zoom of the window's screen, as the browsers have it: Ctrl+= (or Ctrl++) and Ctrl+- step through the factors
// below, also with the numeric keypad, Ctrl+0 goes back to 100%, and Ctrl+wheel steps as the keys do. The packaged app
// has no menu, whose View items would otherwise do this, so the main process reads the keys before the screen does;
// the screen itself needs no change. The factor is kept in the app's settings folder, so the screen opens at it again.
const FACTORS = [0.5, 0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2];
const MIN = FACTORS[0];
const MAX = /** @type {number} */ (FACTORS.at(-1));
// Two factors this close are the same step: a factor read from the file may be rounded.
const EPSILON = 0.001;

const clamp = (factor) => Math.min(MAX, Math.max(MIN, factor));

// The factor one step in (+1) or out (-1) from `factor`; one off the steps (an edited file) goes to the nearest step
// that way. 0 is 100%.
function stepZoom(factor, direction) {
  if (direction === 0) return 1;
  const next =
    direction > 0
      ? FACTORS.find((step) => step > factor + EPSILON)
      : FACTORS.findLast((step) => step < factor - EPSILON);
  return next ?? clamp(factor);
}

// What a key press asks of the zoom (Electron's `before-input-event` input): 1 in, -1 out, 0 back to 100%, or null for
// any other key. Ctrl on Windows, Cmd on macOS; with Alt it is another shortcut, as AltGr is Ctrl+Alt on Windows.
function zoomKey(input) {
  if (input.type !== 'keyDown' || !(input.control || input.meta) || input.alt) return null;
  if (input.key === '=' || input.key === '+' || input.code === 'NumpadAdd') return 1;
  if (input.key === '-' || input.key === '_' || input.code === 'NumpadSubtract') return -1;
  if ((input.key === '0' && !input.shift) || input.code === 'Numpad0') return 0;
  return null;
}

class ScreenZoom {
  // `file`: where the factor is kept, or null to keep it for this run only (a demo recording opens at 100% and leaves
  // the user's zoom as it is). `failed` hears of a factor that could not be saved.
  /** @param {{ file?: string | null, failed?: (error: Error) => void }} [options] */
  constructor({ file = null, failed = () => {} } = {}) {
    this.file = file;
    this.failed = failed;
    this.factor = 1;
    this.written = 1;
    this.contents = new Set();
    this.queue = Promise.resolve();
    this.loaded = null;
    this.ready = false;
  }

  load() {
    this.loaded ??= (async () => {
      if (!this.file) return;
      try {
        const saved = await readJsonFile(this.file);
        const zoom = Number(saved?.zoom);
        if (Number.isFinite(zoom) && zoom > 0) this.factor = this.written = clamp(zoom);
      } catch (error) {
        // A file that cannot be read is not written over (files.cjs): the screen opens at 100% and zooms for this run.
        this.file = null;
        this.failed(error);
      }
      this.ready = true;
      for (const contents of this.contents) this.apply(contents);
    })();
    return this.loaded;
  }

  // Zooms a window's screen: the keys and Ctrl+wheel change the factor, and each page it loads opens at it.
  attach(contents) {
    this.contents.add(contents);
    contents.on('before-input-event', (event, input) => {
      const direction = zoomKey(input);
      if (direction === null) return;
      event.preventDefault();
      this.set(stepZoom(this.factor, direction));
    });
    // Ctrl+wheel and the touchpad's pinch: Electron asks, and zooms only as told.
    contents.on('zoom-changed', (_event, way) =>
      this.set(stepZoom(this.factor, way === 'in' ? 1 : -1)),
    );
    contents.on('did-navigate', () => this.apply(contents));
    contents.once('destroyed', () => this.contents.delete(contents));
    // At the saved factor once it is read (load), at once if it has been.
    if (this.ready) this.apply(contents);
    else void this.load();
  }

  apply(contents) {
    if (!contents.isDestroyed()) contents.setZoomFactor(this.factor);
  }

  set(factor) {
    this.factor = clamp(factor);
    for (const contents of this.contents) this.apply(contents);
    return this.save();
  }

  // Saves the latest factor once the saves before it are done; a key held down saves as it can, not on every repeat.
  save() {
    if (!this.file) return this.queue;
    this.queue = this.queue
      .then(async () => {
        if (this.factor === this.written) return;
        const factor = this.factor;
        await writeFileSafely(this.file, `${JSON.stringify({ zoom: factor }, null, 2)}\n`);
        this.written = factor;
      })
      .catch((error) => this.failed(error));
    return this.queue;
  }
}

module.exports = { FACTORS, ScreenZoom, stepZoom, zoomKey };
