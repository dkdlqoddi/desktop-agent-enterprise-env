const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { finalize, inspect, children } = require('../electron/mp4.cjs');
const { fragmentedMp4, recording, sampleBytes } = require('./fixtures/mp4.cjs');

async function folder(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'agent-mp4-test-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  return root;
}

// The boxes along `names` from the start of `buffer`.
function find(buffer, ...names) {
  let box = { body: 0, end: buffer.length };
  for (const name of names) {
    box = children(buffer, box.body, box.end).find((item) => item.type === name);
    if (!box) return null;
  }
  return box;
}

function table(buffer, name, width = 1) {
  const box = find(buffer, 'moov', 'trak', 'mdia', 'minf', 'stbl', name);
  if (!box) return null;
  const count = buffer.readUInt32BE(box.body + (name === 'stsz' ? 8 : 4));
  const start = box.body + (name === 'stsz' ? 12 : 8);
  return Array.from({ length: count }, (_, index) =>
    Array.from({ length: width }, (_, column) =>
      buffer.readUInt32BE(start + (index * width + column) * 4),
    ),
  ).map((row) => (width === 1 ? row[0] : row));
}

test('a fragmented recording becomes one moov before one mdat, with every sample where its table says', async (t) => {
  const root = await folder(t);
  const source = path.join(root, 'raw.part');
  const target = path.join(root, 'raw.mp4');
  await fs.writeFile(source, recording(5, { width: 1280, height: 720 }));
  assert.equal((await inspect(source)).fragmented, true);
  const result = await finalize(source, target);
  assert.deepEqual(result, { duration: 5, start: 0, width: 1280, height: 720, samples: 150 });
  const info = await inspect(target);
  assert.deepEqual(info.layout, ['ftyp', 'moov', 'mdat']);
  assert.equal(info.fragmented, false);
  assert.equal(info.samples, 150);
  assert.equal(info.duration, 5);
  const file = await fs.readFile(target);
  // No mvex left, durations stated: players know the length at once.
  assert.equal(find(file, 'moov', 'mvex'), null);
  const mvhd = find(file, 'moov', 'mvhd');
  assert.equal(file.readUInt32BE(mvhd.body + 16), 5000);
  const sizes = table(file, 'stsz');
  const chunks = table(file, 'stco');
  const perChunk = table(file, 'stsc', 3);
  assert.deepEqual(table(file, 'stts', 2), [[150, 1000]]);
  // Key frames start the fragments: samples 1, 61 and 121.
  assert.deepEqual(table(file, 'stss'), [1, 61, 121]);
  assert.deepEqual(perChunk, [
    [1, 60, 1],
    [3, 30, 1],
  ]);
  // Each chunk's samples are the bytes of their own samples, in order.
  let sample = 0;
  chunks.forEach((offset, chunk) => {
    const count = chunk < 2 ? 60 : 30;
    let at = offset;
    for (let index = 0; index < count; index++, sample++) {
      assert.deepEqual(file.subarray(at, at + sizes[sample]), sampleBytes(sample, sizes[sample]));
      at += sizes[sample];
    }
  });
  assert.equal(sample, 150);
});

test('a pause between fragments stays in the video, and composition offsets are kept', async (t) => {
  const root = await folder(t);
  const source = path.join(root, 'raw.part');
  const target = path.join(root, 'raw.mp4');
  // The screen did not change for two seconds: the second fragment starts at 3 s, not at 1 s.
  await fs.writeFile(
    source,
    fragmentedMp4({
      fragments: [
        {
          time: 0,
          samples: [
            { size: 10, duration: 15000, sync: true, offset: 3000 },
            { size: 12, duration: 15000 },
          ],
        },
        { time: 90000, samples: [{ size: 14, duration: 30000, sync: true }] },
      ],
    }),
  );
  const result = await finalize(source, target);
  assert.equal(result.duration, 4);
  const file = await fs.readFile(target);
  assert.deepEqual(table(file, 'stts', 2), [
    [1, 15000],
    [1, 75000],
    [1, 30000],
  ]);
  assert.deepEqual(table(file, 'ctts', 2), [
    [1, 3000],
    [2, 0],
  ]);
});

test('a recording cut short keeps its whole fragments, and one without pictures is refused', async (t) => {
  const root = await folder(t);
  const source = path.join(root, 'raw.part');
  const target = path.join(root, 'raw.mp4');
  const whole = recording(4);
  await fs.writeFile(source, whole.subarray(0, whole.length - 30));
  const result = await finalize(source, target);
  assert.equal(result.samples, 60);
  assert.equal(result.duration, 2);
  // The moov alone, before any fragment: nothing was recorded.
  await fs.writeFile(source, fragmentedMp4({ fragments: [] }));
  await assert.rejects(finalize(source, target), /녹화된 화면이 없습니다/);
  await fs.writeFile(source, 'not a video');
  await assert.rejects(finalize(source, path.join(root, 'other.mp4')), /형식/);
  await assert.rejects(fs.access(path.join(root, 'other.mp4.tmp')));
});
