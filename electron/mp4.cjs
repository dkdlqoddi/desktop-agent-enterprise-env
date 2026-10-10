const fs = require('node:fs/promises');
const { replaceFile } = require('./files.cjs');

// Chromium's MediaRecorder writes an MP4 as fragments (a moof and an mdat every couple of seconds), so a recording is
// on disk while it goes on, but the file states no duration and players can hardly jump in it. finalize() rewrites
// such a recording as a plain MP4, which every player and PowerPoint take: one moov with the table of every sample,
// placed before one mdat, so the video can start playing before it is all read. Only the video track is kept; the
// app records no sound.

const COPY_BLOCK = 4 * 1024 * 1024;
// Kept from the recording's trak and stbl as they are; the sample tables are written anew, an edit list of the
// fragments would no longer fit.
const SAMPLE_TABLES = new Set([
  'stts',
  'ctts',
  'stss',
  'stsz',
  'stz2',
  'stsc',
  'stco',
  'co64',
  'sdtp',
]);
const NON_SYNC = 0x00010000; // sample_is_non_sync_sample in a sample's flags (ISO/IEC 14496-12 8.8.3.1).
const MAX_32 = 0xffffffff;

function invalid(message = '녹화 파일(MP4)의 형식을 읽지 못했습니다.') {
  return new Error(message);
}

// The boxes between `start` and `end` of a buffer that holds them whole.
function children(buffer, start = 0, end = buffer.length) {
  const boxes = [];
  for (let offset = start; offset + 8 <= end;) {
    let size = buffer.readUInt32BE(offset);
    const type = buffer.toString('latin1', offset + 4, offset + 8);
    let header = 8;
    if (size === 1) {
      if (offset + 16 > end) break;
      size = Number(buffer.readBigUInt64BE(offset + 8));
      header = 16;
    } else if (size === 0) size = end - offset;
    if (size < header || offset + size > end) throw invalid();
    boxes.push({ type, start: offset, header, size, body: offset + header, end: offset + size });
    offset += size;
  }
  return boxes;
}

const child = (buffer, box, type) =>
  children(buffer, box.body, box.end).find((item) => item.type === type);

// The top-level boxes of a file, read by their headers only. A last box cut short (the app ended while it wrote) is
// left out, with the samples it would have held.
async function topBoxes(handle) {
  const { size: length } = await handle.stat();
  const header = Buffer.alloc(16);
  const boxes = [];
  for (let offset = 0; offset + 8 <= length;) {
    await handle.read(header, 0, 16, offset);
    let size = header.readUInt32BE(0);
    const type = header.toString('latin1', 4, 8);
    let head = 8;
    if (size === 1) {
      size = Number(header.readBigUInt64BE(8));
      head = 16;
    } else if (size === 0) size = length - offset;
    if (size < head || offset + size > length) break;
    boxes.push({
      type,
      start: offset,
      header: head,
      size,
      body: offset + head,
      end: offset + size,
    });
    offset += size;
  }
  return boxes;
}

async function readBox(handle, box) {
  const buffer = Buffer.alloc(box.size);
  await handle.read(buffer, 0, box.size, box.start);
  return buffer;
}

function box(type, ...parts) {
  const payload = Buffer.concat(parts);
  const head = Buffer.alloc(8);
  head.writeUInt32BE(8 + payload.length, 0);
  head.write(type, 4, 'latin1');
  return Buffer.concat([head, payload]);
}

function fullBox(type, version, flags, ...parts) {
  const head = Buffer.alloc(4);
  head.writeUInt32BE(((version & 0xff) << 24) | (flags & 0xffffff), 0);
  return box(type, head, ...parts);
}

function uint32s(values) {
  const buffer = Buffer.alloc(values.length * 4);
  values.forEach((value, index) => buffer.writeUInt32BE(value >>> 0, index * 4));
  return buffer;
}

// mvhd, tkhd and mdhd: creation and modification times, then the timescale (or a track's ID and a reserved word),
// then the duration; 32-bit fields in version 0, 64-bit times and duration in version 1. Written again with
// `duration`, in version 1 when it no longer fits 32 bits.
function withDuration(buffer, item, duration, middle) {
  const body = buffer.subarray(item.body, item.end);
  const version = body[0];
  const wide = version === 1;
  const times = wide ? 16 : 8;
  const middleAt = 4 + times;
  const durationAt = middleAt + middle;
  const rest = body.subarray(durationAt + (wide ? 8 : 4));
  const flags = body.readUInt32BE(0) & 0xffffff;
  const big = wide || duration > MAX_32;
  const head = Buffer.alloc(big ? 16 : 8);
  if (wide) body.copy(head, 0, 4, 20);
  else if (big) {
    head.writeBigUInt64BE(BigInt(body.readUInt32BE(4)), 0);
    head.writeBigUInt64BE(BigInt(body.readUInt32BE(8)), 8);
  } else body.copy(head, 0, 4, 12);
  const value = Buffer.alloc(big ? 8 : 4);
  if (big) value.writeBigUInt64BE(BigInt(Math.round(duration)), 0);
  else value.writeUInt32BE(Math.round(duration), 0);
  return fullBox(
    item.type,
    big ? 1 : 0,
    flags,
    head,
    body.subarray(middleAt, durationAt),
    value,
    rest,
  );
}

function timescaleOf(buffer, item) {
  const body = buffer.subarray(item.body, item.end);
  return body.readUInt32BE(body[0] === 1 ? 20 : 12);
}

// The samples of one track in the fragments, in decode order: where each one is in the file, its size, duration,
// composition offset and whether a player can start decoding there. Fragments are grouped in runs whose samples
// lie one after another: each run becomes a chunk of the plain file. A run notes when its fragment starts (tfdt):
// the screen may not change for a while, and MediaRecorder then leaves a gap between fragments that the durations of
// their samples do not tell.
function fragmentSamples(moof, moofStart, trackId, defaults, runs) {
  const top = children(moof)[0];
  for (const traf of children(moof, top.body, top.end).filter((item) => item.type === 'traf')) {
    const parts = children(moof, traf.body, traf.end);
    const tfhd = parts.find((item) => item.type === 'tfhd');
    if (!tfhd) throw invalid();
    const tfdt = parts.find((item) => item.type === 'tfdt');
    let time = null;
    if (tfdt)
      time =
        moof[tfdt.body] === 1
          ? Number(moof.readBigUInt64BE(tfdt.body + 4))
          : moof.readUInt32BE(tfdt.body + 4);
    let at = tfhd.body;
    const flags = moof.readUInt32BE(at) & 0xffffff;
    at += 4;
    if (moof.readUInt32BE(at) !== trackId) continue;
    at += 4;
    let base = moofStart;
    if (flags & 0x1) {
      base = Number(moof.readBigUInt64BE(at));
      at += 8;
    }
    if (flags & 0x2) at += 4;
    let duration = defaults.duration;
    let size = defaults.size;
    let sampleFlags = defaults.flags;
    if (flags & 0x8) {
      duration = moof.readUInt32BE(at);
      at += 4;
    }
    if (flags & 0x10) {
      size = moof.readUInt32BE(at);
      at += 4;
    }
    if (flags & 0x20) sampleFlags = moof.readUInt32BE(at);
    let cursor = base;
    for (const trun of parts.filter((item) => item.type === 'trun')) {
      let offset = trun.body;
      const version = moof[offset];
      const runFlags = moof.readUInt32BE(offset) & 0xffffff;
      const count = moof.readUInt32BE(offset + 4);
      offset += 8;
      if (runFlags & 0x1) {
        cursor = base + moof.readInt32BE(offset);
        offset += 4;
      }
      let first = null;
      if (runFlags & 0x4) {
        first = moof.readUInt32BE(offset);
        offset += 4;
      }
      const run = { start: cursor, time, samples: /** @type {any[]} */ ([]) };
      for (let index = 0; index < count; index++) {
        const sample = {
          duration,
          size,
          flags: index === 0 && first !== null ? first : sampleFlags,
        };
        if (runFlags & 0x100) {
          sample.duration = moof.readUInt32BE(offset);
          offset += 4;
        }
        if (runFlags & 0x200) {
          sample.size = moof.readUInt32BE(offset);
          offset += 4;
        }
        if (runFlags & 0x400) {
          const own = moof.readUInt32BE(offset);
          if (!(index === 0 && first !== null)) sample.flags = own;
          offset += 4;
        }
        sample.offset = 0;
        if (runFlags & 0x800) {
          sample.offset = version === 0 ? moof.readUInt32BE(offset) : moof.readInt32BE(offset);
          offset += 4;
        }
        if (offset > trun.end) throw invalid();
        run.samples.push(sample);
        cursor += sample.size;
      }
      if (time !== null) time += run.samples.reduce((sum, sample) => sum + sample.duration, 0);
      if (run.samples.length) runs.push(run);
    }
  }
}

// Each sample lasts until the next one starts: a fragment's start time (tfdt) is when its first sample is decoded,
// so the last sample before a gap lasts over the gap. Times start at 0 and never go back. Returns when the first
// sample was decoded, in the track's units.
function timeline(runs) {
  const start = runs[0].time ?? 0;
  /** @type {any} */
  let previous = null;
  for (const run of runs) {
    /** @type {any} */
    let time = run.time === null ? null : run.time - start;
    for (const sample of run.samples) {
      if (time !== null && previous) previous.duration = Math.max(1, time - previous.time);
      if (time === null) time = previous ? previous.time + previous.duration : 0;
      if (previous && time <= previous.time) time = previous.time + 1;
      sample.time = time;
      time += sample.duration;
      previous = sample;
    }
  }
  if (previous.duration < 1) previous.duration = 1;
  return start;
}

function runLength(values) {
  const entries = [];
  for (const value of values) {
    const last = entries.at(-1);
    if (last && last[1] === value) last[0]++;
    else entries.push([1, value]);
  }
  return entries;
}

function sampleTables(runs, chunkOffsets, wide) {
  const samples = runs.flatMap((run) => run.samples);
  const stts = runLength(samples.map((sample) => sample.duration));
  const tables = [fullBox('stts', 0, 0, uint32s([stts.length, ...stts.flat()]))];
  if (samples.some((sample) => sample.offset !== 0)) {
    const ctts = runLength(samples.map((sample) => sample.offset));
    const signed = samples.some((sample) => sample.offset < 0);
    const entries = Buffer.alloc(4 + ctts.length * 8);
    entries.writeUInt32BE(ctts.length, 0);
    ctts.forEach(([count, value], index) => {
      entries.writeUInt32BE(count, 4 + index * 8);
      if (signed) entries.writeInt32BE(value, 8 + index * 8);
      else entries.writeUInt32BE(value, 8 + index * 8);
    });
    tables.push(fullBox('ctts', signed ? 1 : 0, 0, entries));
  }
  const sync = [];
  samples.forEach((sample, index) => {
    if (!(sample.flags & NON_SYNC)) sync.push(index + 1);
  });
  // Without an stss every sample is a sync sample.
  if (sync.length < samples.length)
    tables.push(fullBox('stss', 0, 0, uint32s([sync.length, ...sync])));
  tables.push(
    fullBox('stsz', 0, 0, uint32s([0, samples.length, ...samples.map((sample) => sample.size)])),
  );
  const stsc = [];
  runs.forEach((run, index) => {
    if (stsc.at(-1)?.[1] !== run.samples.length) stsc.push([index + 1, run.samples.length, 1]);
  });
  tables.push(fullBox('stsc', 0, 0, uint32s([stsc.length, ...stsc.flat()])));
  if (wide) {
    const offsets = Buffer.alloc(4 + chunkOffsets.length * 8);
    offsets.writeUInt32BE(chunkOffsets.length, 0);
    chunkOffsets.forEach((value, index) => offsets.writeBigUInt64BE(BigInt(value), 4 + index * 8));
    tables.push(fullBox('co64', 0, 0, offsets));
  } else tables.push(fullBox('stco', 0, 0, uint32s([chunkOffsets.length, ...chunkOffsets])));
  return tables;
}

// The plain moov of the recording's one video track, its sample tables pointing into an mdat that starts at
// `dataStart`.
function plainMoov(moov, trak, runs, dataStart, wide) {
  const top = children(moov)[0];
  const mvhd = child(moov, top, 'mvhd');
  const mdia = child(moov, trak, 'mdia');
  const mdhd = child(moov, mdia, 'mdhd');
  const minf = child(moov, mdia, 'minf');
  const stbl = child(moov, minf, 'stbl');
  if (!mvhd || !mdhd || !stbl) throw invalid();
  const mediaDuration = runs.reduce(
    (total, run) => total + run.samples.reduce((sum, sample) => sum + sample.duration, 0),
    0,
  );
  const movieDuration = (mediaDuration * timescaleOf(moov, mvhd)) / timescaleOf(moov, mdhd);
  const chunkOffsets = [];
  let position = dataStart;
  for (const run of runs) {
    chunkOffsets.push(position);
    position += run.samples.reduce((sum, sample) => sum + sample.size, 0);
  }
  const keep = (parent, rebuilt) =>
    children(moov, parent.body, parent.end).map(
      (item) => rebuilt(item) ?? moov.subarray(item.start, item.end),
    );
  const newStbl = box(
    'stbl',
    ...children(moov, stbl.body, stbl.end)
      .filter((item) => !SAMPLE_TABLES.has(item.type))
      .map((item) => moov.subarray(item.start, item.end)),
    ...sampleTables(runs, chunkOffsets, wide),
  );
  const newMinf = box('minf', ...keep(minf, (item) => (item.type === 'stbl' ? newStbl : null)));
  const newMdia = box(
    'mdia',
    ...keep(mdia, (item) =>
      item.type === 'mdhd'
        ? withDuration(moov, item, mediaDuration, 4)
        : item.type === 'minf'
          ? newMinf
          : null,
    ),
  );
  const newTrak = box(
    'trak',
    ...children(moov, trak.body, trak.end)
      .filter((item) => item.type !== 'edts')
      .map((item) =>
        item.type === 'tkhd'
          ? withDuration(moov, item, movieDuration, 8)
          : item.type === 'mdia'
            ? newMdia
            : moov.subarray(item.start, item.end),
      ),
  );
  return {
    moov: box(
      'moov',
      ...children(moov, top.body, top.end)
        .filter(
          (item) => item.type !== 'mvex' && (item.type !== 'trak' || item.start === trak.start),
        )
        .map((item) =>
          item.type === 'mvhd'
            ? withDuration(moov, item, movieDuration, 4)
            : item.type === 'trak'
              ? newTrak
              : moov.subarray(item.start, item.end),
        ),
    ),
    seconds: mediaDuration / timescaleOf(moov, mdhd),
    timescale: timescaleOf(moov, mdhd),
  };
}

function videoTrak(moov) {
  const top = children(moov)[0];
  for (const trak of children(moov, top.body, top.end).filter((item) => item.type === 'trak')) {
    const mdia = child(moov, trak, 'mdia');
    const hdlr = mdia && child(moov, mdia, 'hdlr');
    if (hdlr && moov.toString('latin1', hdlr.body + 8, hdlr.body + 12) === 'vide') {
      const tkhd = child(moov, trak, 'tkhd');
      if (!tkhd) throw invalid();
      const version = moov[tkhd.body];
      const id = moov.readUInt32BE(tkhd.body + (version === 1 ? 20 : 12));
      const width = moov.readUInt32BE(tkhd.end - 8) / 65536;
      const height = moov.readUInt32BE(tkhd.end - 4) / 65536;
      return { trak, id, width, height };
    }
  }
  throw invalid('녹화 파일(MP4)에 영상이 없습니다.');
}

// The defaults of the track's samples that the fragments may leave out (trex in mvex).
function trackDefaults(moov, trackId) {
  const top = children(moov)[0];
  const mvex = child(moov, top, 'mvex');
  for (const trex of mvex ? children(moov, mvex.body, mvex.end) : [])
    if (trex.type === 'trex' && moov.readUInt32BE(trex.body + 4) === trackId)
      return {
        duration: moov.readUInt32BE(trex.body + 12),
        size: moov.readUInt32BE(trex.body + 16),
        flags: moov.readUInt32BE(trex.body + 20),
      };
  return { duration: 0, size: 0, flags: 0 };
}

async function copyRange(input, output, start, length, buffer) {
  for (let done = 0; done < length;) {
    const want = Math.min(buffer.length, length - done);
    const { bytesRead } = await input.read(buffer, 0, want, start + done);
    if (bytesRead !== want) throw invalid('녹화 파일을 끝까지 읽지 못했습니다.');
    await output.write(buffer, 0, want);
    done += want;
  }
}

// Writes `source`, a fragmented MP4 of MediaRecorder, as a plain MP4 at `target` (through a temporary file, so a
// failure leaves no half-written video). Returns the duration in seconds and the picture's size.
async function finalize(source, target) {
  const input = await fs.open(source, 'r');
  const temporary = `${target}.tmp`;
  try {
    const top = await topBoxes(input);
    const ftypBox = top.find((item) => item.type === 'ftyp');
    const moovBox = top.find((item) => item.type === 'moov');
    if (!ftypBox || !moovBox) throw invalid();
    const moov = await readBox(input, moovBox);
    const { trak, id, width, height } = videoTrak(moov);
    const defaults = trackDefaults(moov, id);
    const runs = [];
    for (const item of top.filter((entry) => entry.type === 'moof'))
      fragmentSamples(await readBox(input, item), item.start, id, defaults, runs);
    // A fragment whose data the file no longer holds whole is left out with what follows it.
    const { size: length } = await input.stat();
    const whole = [];
    for (const run of runs) {
      const bytes = run.samples.reduce((sum, sample) => sum + sample.size, 0);
      if (run.start + bytes > length) break;
      whole.push(run);
    }
    if (!whole.length) throw invalid('녹화된 화면이 없습니다.');
    const first = timeline(whole);
    const data = whole.reduce(
      (total, run) => total + run.samples.reduce((sum, sample) => sum + sample.size, 0),
      0,
    );
    const ftyp = await readBox(input, ftypBox);
    // The moov's size depends on whether chunk offsets need 64 bits, which depends on the size of what precedes the
    // data: decided on a first draft, then the real one is made with its own size.
    const draft = plainMoov(moov, trak, whole, 0, false).moov.length;
    const wide = ftyp.length + draft * 2 + 16 + data > MAX_32;
    const mdatHead = 8 + data > MAX_32 ? 16 : 8;
    const sized = plainMoov(moov, trak, whole, 0, wide).moov.length;
    const dataStart = ftyp.length + sized + mdatHead;
    const { moov: plain, seconds, timescale } = plainMoov(moov, trak, whole, dataStart, wide);
    const output = await fs.open(temporary, 'w');
    try {
      const head = Buffer.alloc(mdatHead);
      if (mdatHead === 16) {
        head.writeUInt32BE(1, 0);
        head.write('mdat', 4, 'latin1');
        head.writeBigUInt64BE(BigInt(16 + data), 8);
      } else {
        head.writeUInt32BE(8 + data, 0);
        head.write('mdat', 4, 'latin1');
      }
      await output.write(Buffer.concat([ftyp, plain, head]));
      const buffer = Buffer.alloc(COPY_BLOCK);
      for (const run of whole)
        await copyRange(
          input,
          output,
          run.start,
          run.samples.reduce((sum, sample) => sum + sample.size, 0),
          buffer,
        );
      await output.sync();
    } finally {
      await output.close();
    }
    await replaceFile(temporary, target);
    // `start`: how long after the recording began its first picture came, which the plain file starts with.
    return {
      duration: seconds,
      start: first / timescale,
      width,
      height,
      samples: whole.reduce((total, run) => total + run.samples.length, 0),
    };
  } catch (error) {
    await fs.rm(temporary, { force: true });
    throw error;
  } finally {
    await input.close();
  }
}

// What a plain or fragmented MP4 holds: its layout, the video's duration in seconds, size and number of samples.
async function inspect(file) {
  const input = await fs.open(file, 'r');
  try {
    const top = await topBoxes(input);
    const moovBox = top.find((item) => item.type === 'moov');
    if (!moovBox) throw invalid();
    const moov = await readBox(input, moovBox);
    const { trak, width, height } = videoTrak(moov);
    const mdia = child(moov, trak, 'mdia');
    const mdhd = child(moov, mdia, 'mdhd');
    const stbl = child(moov, child(moov, mdia, 'minf'), 'stbl');
    const stts = child(moov, stbl, 'stts');
    let samples = 0;
    let duration = 0;
    const entries = stts ? moov.readUInt32BE(stts.body + 4) : 0;
    for (let index = 0; stts && index < entries; index++) {
      const count = moov.readUInt32BE(stts.body + 8 + index * 8);
      samples += count;
      duration += count * moov.readUInt32BE(stts.body + 12 + index * 8);
    }
    return {
      layout: top.map((item) => item.type),
      fragmented: top.some((item) => item.type === 'moof'),
      duration: duration / timescaleOf(moov, mdhd),
      samples,
      width,
      height,
    };
  } finally {
    await input.close();
  }
}

module.exports = { finalize, inspect, children };
