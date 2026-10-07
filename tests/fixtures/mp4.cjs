// A fragmented MP4 like those Chromium's MediaRecorder writes: an ftyp, a moov with empty sample tables and an mvex,
// then a moof and an mdat per fragment. Each sample's bytes say which sample it is, so tests can tell where they went.

const ascii = (text) => Buffer.from(text, 'latin1');

function u32(...values) {
  const buffer = Buffer.alloc(values.length * 4);
  values.forEach((value, index) => buffer.writeUInt32BE(value >>> 0, index * 4));
  return buffer;
}

function u64(value) {
  const buffer = Buffer.alloc(8);
  buffer.writeBigUInt64BE(BigInt(value));
  return buffer;
}

function box(type, ...parts) {
  const payload = Buffer.concat(
    parts.map((part) => (typeof part === 'string' ? ascii(part) : part)),
  );
  return Buffer.concat([u32(8 + payload.length), ascii(type), payload]);
}

const fullBox = (type, version, flags, ...parts) =>
  box(type, u32(((version & 0xff) << 24) | (flags & 0xffffff)), ...parts);

const MATRIX = u32(0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000);
const SYNC = 0x02000000;
const NON_SYNC = 0x01010000;

function moov({ width, height, timescale }) {
  const visual = Buffer.alloc(78);
  visual.writeUInt16BE(1, 6); // data_reference_index
  visual.writeUInt16BE(width, 24);
  visual.writeUInt16BE(height, 26);
  visual.writeUInt32BE(0x00480000, 28);
  visual.writeUInt32BE(0x00480000, 32);
  visual.writeUInt16BE(1, 40); // frame_count
  visual.writeUInt16BE(0x18, 74);
  visual.writeInt16BE(-1, 76);
  const avcC = box('avcC', Buffer.from([1, 0x42, 0xc0, 0x1e, 0xff, 0xe0, 0x00]));
  return box(
    'moov',
    fullBox(
      'mvhd',
      0,
      0,
      u32(0, 0, 1000, 0, 0x10000),
      Buffer.from([1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]),
      MATRIX,
      Buffer.alloc(24),
      u32(2),
    ),
    box(
      'trak',
      fullBox(
        'tkhd',
        0,
        3,
        u32(0, 0, 1, 0, 0, 0, 0, 0, 0),
        MATRIX,
        u32(width * 65536, height * 65536),
      ),
      box(
        'mdia',
        fullBox('mdhd', 0, 0, u32(0, 0, timescale, 0), Buffer.from([0x55, 0xc4, 0, 0])),
        fullBox('hdlr', 0, 0, u32(0), ascii('vide'), u32(0, 0, 0), ascii('VideoHandler\0')),
        box(
          'minf',
          fullBox('vmhd', 0, 1, Buffer.alloc(8)),
          box('dinf', fullBox('dref', 0, 0, u32(1), fullBox('url ', 0, 1))),
          box(
            'stbl',
            fullBox('stsd', 0, 0, u32(1), box('avc1', visual, avcC)),
            fullBox('stts', 0, 0, u32(0)),
            fullBox('stsc', 0, 0, u32(0)),
            fullBox('stsz', 0, 0, u32(0, 0)),
            fullBox('stco', 0, 0, u32(0)),
          ),
        ),
      ),
    ),
    box('mvex', fullBox('trex', 0, 0, u32(1, 1, 0, 0, 0))),
  );
}

// The bytes of sample `index` (counted over the whole file).
const sampleBytes = (index, size) => Buffer.alloc(size, index % 251);

function fragment(sequence, time, samples, first) {
  const withOffsets = samples.some((sample) => sample.offset);
  const flags = 0x1 | 0x100 | 0x200 | 0x400 | (withOffsets ? 0x800 : 0);
  const make = (dataOffset) =>
    box(
      'moof',
      fullBox('mfhd', 0, 0, u32(sequence)),
      box(
        'traf',
        fullBox('tfhd', 0, 0x020000, u32(1)),
        fullBox('tfdt', 1, 0, u64(time)),
        fullBox(
          'trun',
          0,
          flags,
          u32(samples.length, dataOffset),
          ...samples.map((sample) =>
            u32(
              sample.duration,
              sample.size,
              sample.sync ? SYNC : NON_SYNC,
              ...(withOffsets ? [sample.offset ?? 0] : []),
            ),
          ),
        ),
      ),
    );
  const moof = make(make(0).length + 8);
  const data = Buffer.concat(
    samples.map((sample, index) => sampleBytes(first + index, sample.size)),
  );
  return Buffer.concat([moof, box('mdat', data)]);
}

// `fragments`: [{ time, samples: [{ size, duration, sync, offset }] }], `time` in the track's units. `cut` leaves
// out the last bytes, as a recording the app could not finish.
function fragmentedMp4({ width = 640, height = 360, timescale = 30000, fragments, cut = 0 }) {
  let first = 0;
  const parts = [
    box('ftyp', 'isom', u32(512), 'isomiso6iso2avc1mp41'),
    moov({ width, height, timescale }),
  ];
  fragments.forEach((item, index) => {
    parts.push(fragment(index + 1, item.time, item.samples, first));
    first += item.samples.length;
  });
  const file = Buffer.concat(parts);
  return cut ? file.subarray(0, file.length - cut) : file;
}

// A short recording: `seconds` of samples at 30 frames per second in 2-second fragments, a key frame starting each.
function recording(seconds = 4, { width = 640, height = 360 } = {}) {
  const fragments = [];
  for (let start = 0; start < seconds * 30; start += 60)
    fragments.push({
      time: start * 1000,
      samples: Array.from({ length: Math.min(60, seconds * 30 - start) }, (_, index) => ({
        size: 40 + ((start + index) % 7),
        duration: 1000,
        sync: index === 0,
      })),
    });
  return fragmentedMp4({ width, height, fragments });
}

module.exports = { fragmentedMp4, recording, sampleBytes, box, fullBox, u32 };
