const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// The recorder's page (electron/recorder/page.js) keeps every frame of a recording at the size of its first, since an
// MP4 whose frames change size cannot be played past the change. Its code runs here as the page runs it, a classic
// script, with stand-ins for the browser's frame streams: the window's frames go in at several sizes, and what reaches
// the recorder is checked.
const SOURCE = fs.readFileSync(
  path.join(__dirname, '..', 'electron', 'recorder', 'page.js'),
  'utf8',
);

function page() {
  const failures = [];
  const drawn = [];
  const sandbox = {
    TransformStream,
    ReadableStream,
    WritableStream,
    // A job of none: the page loads and does nothing of its own.
    recorder: { job: async () => null, failed: (message) => failures.push(message) },
    MediaStream: class {
      constructor(tracks) {
        this.tracks = tracks;
      }
      getVideoTracks() {
        return this.tracks;
      }
    },
    // The window's frames as they come, then the end of the track or a failure.
    MediaStreamTrackProcessor: class {
      constructor({ track, maxBufferSize }) {
        this.maxBufferSize = maxBufferSize;
        sandbox.processor = this;
        this.readable = new ReadableStream({
          start(controller) {
            for (const frame of track.frames) controller.enqueue(frame);
            if (track.failure) controller.error(track.failure);
            else controller.close();
          },
        });
      }
    },
    // What the recorder gets.
    MediaStreamTrackGenerator: class {
      constructor({ kind }) {
        this.kind = kind;
        this.frames = [];
        this.closed = new Promise((resolve) => {
          this.writable = new WritableStream({
            write: (frame) => void this.frames.push(frame),
            close: resolve,
          });
        });
      }
    },
    OffscreenCanvas: class {
      constructor(width, height) {
        this.width = width;
        this.height = height;
      }
      getContext(type, options) {
        assert.equal(type, '2d');
        assert.deepEqual({ ...options }, { alpha: false });
        const canvas = this;
        return {
          canvas,
          fillStyle: '',
          fillRect(...area) {
            drawn.push({ fill: this.fillStyle, area });
          },
          drawImage(frame, ...area) {
            drawn.push({ frame, area });
          },
        };
      }
    },
    VideoFrame: class {
      constructor(canvas, { timestamp }) {
        this.displayWidth = canvas.width;
        this.displayHeight = canvas.height;
        this.timestamp = timestamp;
      }
    },
  };
  vm.runInNewContext(SOURCE, sandbox, { filename: 'page.js' });
  return { sandbox, failures, drawn };
}

// A frame of the window, which the page must close once it has drawn a copy of it.
const frame = (displayWidth, displayHeight, timestamp) => ({
  displayWidth,
  displayHeight,
  timestamp,
  closed: false,
  close() {
    this.closed = true;
  },
});

test('frames of other sizes reach the recorder at the size of the first, fitted and at their own times', async () => {
  const { sandbox, failures, drawn } = page();
  const frames = [
    frame(1280, 720, 0),
    frame(1280, 720, 33_000),
    // The window made smaller, then taller than wide, then larger than at first, then back.
    frame(1000, 700, 66_000),
    frame(600, 900, 99_000),
    frame(1920, 1080, 133_000),
    frame(1280, 720, 166_000),
  ];
  const track = { frames, readyState: 'live' };
  const stream = sandbox.steady(new sandbox.MediaStream([track]));
  const [generator] = stream.getVideoTracks();
  assert.equal(generator.kind, 'video');
  // Frames wait while one is drawn, rather than being dropped.
  assert.ok(sandbox.processor.maxBufferSize >= 30);
  await generator.closed;
  const made = generator.frames;
  assert.deepEqual(
    made.map((item) => [item.displayWidth, item.displayHeight, item.timestamp]),
    frames.map((item) => [1280, 720, item.timestamp]),
  );
  // Those of the first size pass as they are; the others are drawn anew and let go.
  for (const index of [0, 1, 5]) assert.equal(made[index], frames[index]);
  assert.deepEqual(
    frames.map((item) => item.closed),
    [false, false, true, true, true, false],
  );
  // Each fitted frame: the cards' ink, then the window's picture scaled with its proportions kept, centered.
  assert.deepEqual(drawn, [
    { fill: '#1c2433', area: [0, 0, 1280, 720] },
    { frame: frames[2], area: [125, 0, 1029, 720] },
    { fill: '#1c2433', area: [0, 0, 1280, 720] },
    { frame: frames[3], area: [400, 0, 480, 720] },
    { fill: '#1c2433', area: [0, 0, 1280, 720] },
    { frame: frames[4], area: [0, 0, 1280, 720] },
  ]);
  assert.deepEqual(failures, []);
});

test('frames that stop coming while the window is recorded fail the recording, and not once it has ended', async () => {
  for (const [readyState, expected] of [
    ['live', ['앱 화면을 녹화하지 못했습니다: 프레임을 읽지 못했습니다']],
    ['ended', []],
  ]) {
    const { sandbox, failures } = page();
    const track = {
      frames: [frame(800, 600, 0)],
      failure: new Error('프레임을 읽지 못했습니다'),
      readyState,
    };
    sandbox.steady(new sandbox.MediaStream([track]));
    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.deepEqual(failures, expected, readyState);
  }
});
