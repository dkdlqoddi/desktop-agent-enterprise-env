const { test } = require('node:test');
const assert = require('node:assert/strict');
const {
  CaptionTrack,
  merge,
  toSrt,
  parseSrt,
  srtTime,
  fastForward,
  playedLength,
  durationText,
} = require('../electron/captions.cjs');

test('a caption lasts until the next one, the same words twice are one, and nothing empty is kept', () => {
  let now = 0;
  const track = new CaptionTrack(() => now);
  track.show('저장소를 만듭니다', { label: '1단계' });
  now = 1500;
  track.show('저장소를 만듭니다', { label: '1단계' });
  now = 2000;
  track.show('가져오는 중', { label: '2단계', wait: true });
  // Shown and replaced at the same moment: never on screen.
  track.show('곧 바뀝니다');
  track.show('가져오는 중 · 2/3', { label: '2단계', wait: true });
  now = 9000;
  track.show('');
  now = 9500;
  track.show('끝', { label: '3단계' });
  now = 10_000;
  assert.deepEqual(track.finish(), [
    { start: 0, end: 2000, text: '저장소를 만듭니다', label: '1단계', wait: false },
    { start: 2000, end: 9000, text: '가져오는 중 · 2/3', label: '2단계', wait: true },
    { start: 9500, end: 10_000, text: '끝', label: '3단계', wait: false },
  ]);
});

test('captions go to an .srt and back, as a person may have edited it', () => {
  const cues = [
    { start: 361, end: 1355, text: '저장소를 비웁니다', label: '1단계 · 저장소 초기화' },
    { start: 1355, end: 3_725_004, text: '두 줄\n자막', label: '' },
  ];
  const srt = toSrt(cues);
  assert.equal(
    srt,
    '1\r\n00:00:00,361 --> 00:00:01,355\r\n[1단계 · 저장소 초기화] 저장소를 비웁니다\r\n\r\n2\r\n00:00:01,355 --> 01:02:05,004\r\n두 줄\r\n자막\r\n',
  );
  assert.deepEqual(parseSrt('﻿' + srt), cues);
  // Without numbers, "." before the milliseconds, LF line breaks and trailing blank lines.
  assert.deepEqual(parseSrt('00:00:01.5 --> 00:00:02.25\n[라벨]  고친 자막\n\n\n'), [
    { start: 1500, end: 2250, text: '고친 자막', label: '라벨' },
  ]);
  assert.throws(() => parseSrt('1\n00:00:05,000 --> 00:00:01,000\n뒤집힌 시각\n'), /2번째 줄/);
  assert.throws(() => parseSrt('자막만 있는 줄\n'), /1번째 줄을 읽지 못했습니다/);
  assert.equal(srtTime(-5), '00:00:00,000');
});

test('the same words that follow one another become one cue', () => {
  assert.deepEqual(
    merge([
      { start: 0, end: 1000, text: 'a', label: 'x', wait: false },
      { start: 1000, end: 4000, text: 'a', label: 'x', wait: true },
      { start: 4000, end: 5000, text: 'b', label: 'x' },
      { start: 6000, end: 7000, text: 'b', label: 'x' },
    ]),
    [
      { start: 0, end: 4000, text: 'a', label: 'x' },
      { start: 4000, end: 5000, text: 'b', label: 'x' },
      { start: 6000, end: 7000, text: 'b', label: 'x' },
    ],
  );
});

test('long waits play faster, keeping their start and end, at the lowest speed that keeps them short', () => {
  const wait = (start, end) => ({ start, end, text: '…', label: '', wait: true });
  const spans = fastForward([
    { start: 0, end: 5000, text: '시작', label: '', wait: false },
    // Two captions of one wait, 300 ms apart: one stretch of 20 s.
    wait(5000, 15_000),
    wait(15_300, 25_000),
    { start: 25_000, end: 27_000, text: '완료', label: '', wait: false },
    // Too short to speed up.
    wait(27_000, 35_000),
    // 2 minutes.
    wait(40_000, 160_000),
    // Half an hour: the highest speed.
    wait(200_000, 2_000_000),
  ]);
  assert.deepEqual(spans, [
    { start: 6500, end: 24_000, speed: 4 },
    { start: 41_500, end: 159_000, speed: 16 },
    { start: 201_500, end: 1_999_000, speed: 16 },
  ]);
  assert.equal(
    playedLength(30_000, [{ start: 6500, end: 24_000, speed: 4 }]),
    30_000 - 17_500 * 0.75,
  );
  assert.equal(playedLength(10_000, [{ start: 20_000, end: 30_000, speed: 4 }]), 10_000);
  assert.deepEqual([999, 61_000, 3_725_000, 600_000].map(durationText), [
    '1초',
    '1분 1초',
    '1시간 2분',
    '10분',
  ]);
});
