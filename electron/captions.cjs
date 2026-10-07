// The captions of a demo recording: what the app was doing and from when to when, in milliseconds since the recording
// began. They are drawn into the video, and kept as an .srt file beside the recording, which people can edit before
// the video is made again.

// Playback speeds for the stretches spent waiting on an import; a stretch is shown at the lowest speed that keeps it
// within TARGET.
const SPEEDS = [2, 4, 8, 16];
const TARGET = 6000;
// Waits shorter than this play at normal speed; a sped-up wait keeps its first and last moments as they were, so
// viewers see the import start and end.
const MINIMUM = 9000;
const LEAD = 1500;
const TAIL = 1000;
// Wait captions this close together are one wait.
const JOIN = 400;

class CaptionTrack {
  constructor(clock) {
    this.clock = clock;
    this.cues = [];
    this.current = null;
  }

  // Shows `text` from now on, with `label` above it (the step: "2단계 · 자료 가져오기 3/7"). `wait` marks time spent
  // waiting for the app, which the video may play faster.
  show(text, { label = '', wait = false } = {}) {
    const current = this.current;
    if (current && current.text === text && current.label === label && current.wait === wait)
      return;
    const now = this.clock();
    this.close(now);
    if (text) this.current = { start: now, end: now, text, label, wait };
  }

  close(now = this.clock()) {
    const current = this.current;
    this.current = null;
    if (!current) return;
    current.end = Math.max(now, current.start);
    if (current.end > current.start) this.cues.push(current);
  }

  finish() {
    this.close();
    return this.cues;
  }
}

const pad = (value, size = 2) => String(value).padStart(size, '0');

function srtTime(ms) {
  const value = Math.max(0, Math.round(ms));
  const hours = Math.floor(value / 3_600_000);
  const minutes = Math.floor((value % 3_600_000) / 60_000);
  const seconds = Math.floor((value % 60_000) / 1000);
  return `${pad(hours)}:${pad(minutes)}:${pad(seconds)},${pad(value % 1000, 3)}`;
}

// Cues that follow one another with the same words are one: a caption that only began to count as waiting, say.
function merge(cues) {
  const merged = [];
  for (const cue of cues) {
    const last = merged.at(-1);
    if (last && last.text === cue.text && last.label === cue.label && cue.start - last.end <= 50)
      last.end = Math.max(last.end, cue.end);
    else merged.push({ start: cue.start, end: cue.end, text: cue.text, label: cue.label ?? '' });
  }
  return merged;
}

// A cue's text as an .srt shows it: the label in brackets before the text, which the next rendering reads back.
const cueText = (cue) => (cue.label ? `[${cue.label}] ${cue.text}` : cue.text);

function toSrt(cues) {
  return merge(cues)
    .map(
      (cue, index) =>
        `${index + 1}\r\n${srtTime(cue.start)} --> ${srtTime(cue.end)}\r\n${cueText(cue).replace(/\r?\n/g, '\r\n')}\r\n`,
    )
    .join('\r\n');
}

const TIME = /(\d+):(\d{1,2}):(\d{1,2})(?:[,.](\d{1,3}))?/;
const ARROW = new RegExp(`${TIME.source}\\s*-->\\s*${TIME.source}`);

function milliseconds(match, at) {
  const [hours, minutes, seconds, fraction = '0'] = match.slice(at, at + 4);
  return (
    Number(hours) * 3_600_000 +
    Number(minutes) * 60_000 +
    Number(seconds) * 1000 +
    Number(fraction.padEnd(3, '0'))
  );
}

// The cues of an .srt file as a person may have edited it: numbering, blank lines at the end, either kind of line
// break and "." before the milliseconds are all taken. A cue whose times cannot be read is an error, with its line.
function parseSrt(text) {
  const lines = text.replace(/^﻿/, '').split(/\r\n|\r|\n/);
  const cues = [];
  let cue = null;
  lines.forEach((line, index) => {
    const times = ARROW.exec(line);
    if (times) {
      cue = { start: milliseconds(times, 1), end: milliseconds(times, 5), lines: [] };
      if (cue.end < cue.start)
        throw new Error(`자막 ${index + 1}번째 줄: 끝 시각이 시작 시각보다 앞입니다.`);
      cues.push(cue);
    } else if (!line.trim()) cue = null;
    else if (cue) cue.lines.push(line.trim());
    else if (!/^\d+$/.test(line.trim()))
      throw new Error(`자막 ${index + 1}번째 줄을 읽지 못했습니다: ${line.slice(0, 60)}`);
  });
  return cues
    .filter((item) => item.lines.length)
    .map(({ start, end, lines: [first, ...rest] }) => {
      const label = /^\[([^\]]{1,80})\]\s*(.*)$/.exec(first);
      return {
        start,
        end,
        label: label ? label[1].trim() : '',
        text: [label ? label[2] : first, ...rest].filter(Boolean).join('\n'),
      };
    });
}

// The stretches of the recording to play faster: the waits on imports long enough to bore, each at the lowest speed
// that keeps it short.
function fastForward(cues) {
  const waits = [];
  for (const cue of cues.filter((item) => item.wait)) {
    const last = waits.at(-1);
    if (last && cue.start - last.end <= JOIN) last.end = Math.max(last.end, cue.end);
    else waits.push({ start: cue.start, end: cue.end });
  }
  return waits
    .filter((wait) => wait.end - wait.start >= MINIMUM)
    .map((wait) => {
      const start = wait.start + LEAD;
      const end = wait.end - TAIL;
      const length = end - start;
      return {
        start,
        end,
        speed: SPEEDS.find((speed) => length / speed <= TARGET) ?? SPEEDS.at(-1),
      };
    });
}

// How long a recording of `duration` ms lasts once `speedups` play faster.
const playedLength = (duration, speedups) =>
  speedups.reduce(
    (total, part) =>
      total - Math.max(0, Math.min(part.end, duration) - part.start) * (1 - 1 / part.speed),
    duration,
  );

function durationText(ms) {
  const seconds = Math.max(0, Math.round(ms / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  if (hours) return `${hours}시간 ${minutes}분`;
  if (minutes) return rest ? `${minutes}분 ${rest}초` : `${minutes}분`;
  return `${rest}초`;
}

module.exports = {
  CaptionTrack,
  merge,
  toSrt,
  parseSrt,
  srtTime,
  fastForward,
  playedLength,
  durationText,
  SPEEDS,
};
