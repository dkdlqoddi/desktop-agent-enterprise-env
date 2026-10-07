'use strict';
// The recorder's page (electron/recorder.cjs): in a capture job it records the app's window and shows the recording
// panel; in a render job it plays a recording into a canvas with the captions, title and end cards and sped-up waits,
// and records that canvas as the video.

const FONT =
  '"Malgun Gothic", "맑은 고딕", "Segoe UI", "Apple SD Gothic Neo", "Noto Sans CJK KR", "Noto Sans KR", sans-serif';
const INK = '#1c2433';
const INK_DEEP = '#121826';
const MARK = '#f6e94b';
const KEYFRAMES = 2000; // A fragment of the MP4 is written at each key frame.
const SLICE = 1000;

// Chunks reach the main process in the order the recorder made them: a file put together out of order cannot be
// decoded.
function recordInto(stream, job) {
  const mimeType = job.types.find((type) => MediaRecorder.isTypeSupported(type));
  if (!mimeType) throw new Error('이 PC에서 영상을 기록할 형식(MP4·WebM)을 찾지 못했습니다.');
  const media = new MediaRecorder(stream, {
    mimeType,
    videoBitsPerSecond: job.bitrate,
    videoKeyFrameIntervalDuration: KEYFRAMES,
  });
  let queue = Promise.resolve();
  media.ondataavailable = (event) => {
    const data = event.data;
    if (data.size)
      queue = queue.then(() => data.arrayBuffer()).then((buffer) => recorder.chunk(buffer));
  };
  media.onstop = () => queue.then(() => recorder.finished({ mime: media.mimeType }));
  media.onerror = (event) =>
    recorder.failed(`영상을 기록하지 못했습니다: ${event.error?.message || '알 수 없는 오류'}`);
  return media;
}

const clock = (ms) => {
  const seconds = Math.floor(ms / 1000);
  const hours = Math.floor(seconds / 3600);
  const rest = `${String(Math.floor((seconds % 3600) / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
  return hours ? `${hours}:${rest}` : rest;
};

async function capture(job) {
  document.getElementById('capture').hidden = false;
  const stop = document.getElementById('stop');
  let stream;
  try {
    stream = await navigator.mediaDevices.getDisplayMedia({
      audio: false,
      video: {
        width: { max: job.maxWidth },
        height: { max: job.maxHeight },
        frameRate: { max: job.frameRate },
      },
    });
  } catch (error) {
    recorder.failed(`앱 화면을 녹화하지 못했습니다: ${error.message}`);
    return;
  }
  const [track] = stream.getVideoTracks();
  let media;
  try {
    media = recordInto(stream, job);
  } catch (error) {
    recorder.failed(error.message);
    return;
  }
  const end = () => {
    if (media.state !== 'inactive') media.stop();
    track.stop();
  };
  let began = 0;
  media.onstart = () => {
    began = performance.now();
    const { width, height } = track.getSettings();
    recorder.started({ mime: media.mimeType, width, height });
  };
  // The app's window closed: what was recorded is kept.
  track.onended = end;
  recorder.onStop(end);
  stop.addEventListener('click', () => {
    stop.disabled = true;
    stop.textContent = '중지하는 중…';
    recorder.abort();
  });
  recorder.onStatus(({ label, text }) => {
    document.getElementById('step').textContent = label || '';
    document.getElementById('caption').textContent = text || '';
  });
  setInterval(() => {
    if (began) document.getElementById('elapsed').textContent = clock(performance.now() - began);
  }, 500);
  media.start(SLICE);
}

// Lines of `text` that fit `width`: words wrap at spaces, and a word too long for a line (a path, a link) breaks
// where it must.
function wrap(context, text, width) {
  const fits = (value) => context.measureText(value).width <= width;
  const lines = [];
  for (const paragraph of String(text).split('\n')) {
    let line = '';
    for (const word of paragraph.split(/\s+/).filter(Boolean)) {
      const next = line ? `${line} ${word}` : word;
      if (fits(next)) {
        line = next;
        continue;
      }
      if (line) lines.push(line);
      line = '';
      if (fits(word)) {
        line = word;
        continue;
      }
      for (const character of word) {
        if (line && !fits(line + character)) {
          lines.push(line);
          line = '';
        }
        line += character;
      }
    }
    lines.push(line);
  }
  return lines;
}

function limit(context, lines, count, width) {
  if (lines.length <= count) return lines;
  const kept = lines.slice(0, count);
  let last = kept[count - 1];
  while (last && context.measureText(`${last}…`).width > width) last = last.slice(0, -1);
  kept[count - 1] = `${last}…`;
  return kept;
}

function drawCard(context, width, height, card, alpha) {
  const background = context.createLinearGradient(0, 0, width, height);
  background.addColorStop(0, INK);
  background.addColorStop(1, INK_DEEP);
  context.globalAlpha = 1;
  context.fillStyle = background;
  context.fillRect(0, 0, width, height);
  context.globalAlpha = Math.max(0, Math.min(1, alpha));
  const left = width * 0.1;
  const textWidth = width * 0.8;
  let y = height * 0.3;
  context.textBaseline = 'alphabetic';
  context.fillStyle = '#9aa4b2';
  context.font = `700 ${Math.round(height * 0.024)}px ${FONT}`;
  context.letterSpacing = `${Math.round(height * 0.004)}px`;
  context.fillText('PRIVATE AGENT', left, y);
  context.letterSpacing = '0px';
  y += height * 0.03;
  context.fillStyle = MARK;
  context.fillRect(left, y, width * 0.05, Math.max(3, height * 0.007));
  const titleSize = Math.round(height * 0.064);
  context.font = `700 ${titleSize}px ${FONT}`;
  context.fillStyle = '#ffffff';
  y += height * 0.02;
  for (const line of limit(context, wrap(context, card.title, textWidth), 2, textWidth)) {
    y += titleSize * 1.3;
    context.fillText(line, left, y);
  }
  const lineSize = Math.round(height * 0.031);
  context.font = `500 ${lineSize}px ${FONT}`;
  context.fillStyle = '#c9d1dc';
  y += lineSize * 0.8;
  for (const text of card.lines ?? []) {
    for (const line of limit(context, wrap(context, text, textWidth), 2, textWidth)) {
      y += lineSize * 1.55;
      context.fillText(line, left, y);
    }
  }
  context.font = `500 ${Math.round(height * 0.02)}px ${FONT}`;
  context.fillStyle = '#7d8899';
  context.fillText('Private Agent · 자료는 내 PC에, AI는 내가 정한 연결로', left, height * 0.9);
  context.globalAlpha = 1;
}

function drawCaption(context, width, height, cue) {
  const size = Math.round(height * 0.031);
  const labelSize = Math.round(height * 0.02);
  const padX = Math.round(height * 0.024);
  const padY = Math.round(height * 0.016);
  const maxWidth = width * 0.8 - padX * 2;
  context.font = `600 ${size}px ${FONT}`;
  const lines = limit(context, wrap(context, cue.text, maxWidth), 2, maxWidth);
  let boxWidth = Math.max(...lines.map((line) => context.measureText(line).width));
  if (cue.label) {
    context.font = `700 ${labelSize}px ${FONT}`;
    boxWidth = Math.max(boxWidth, context.measureText(cue.label).width);
  }
  boxWidth += padX * 2;
  const labelHeight = cue.label ? labelSize * 1.55 : 0;
  const boxHeight = padY * 2 + labelHeight + lines.length * size * 1.38;
  const x = (width - boxWidth) / 2;
  const y = height - height * 0.055 - boxHeight;
  context.fillStyle = 'rgba(17, 22, 33, 0.86)';
  context.beginPath();
  context.roundRect(x, y, boxWidth, boxHeight, Math.round(height * 0.012));
  context.fill();
  context.textAlign = 'center';
  context.textBaseline = 'top';
  let top = y + padY;
  if (cue.label) {
    context.font = `700 ${labelSize}px ${FONT}`;
    context.fillStyle = MARK;
    context.fillText(cue.label, width / 2, top);
    top += labelHeight;
  }
  context.font = `600 ${size}px ${FONT}`;
  context.fillStyle = '#ffffff';
  for (const line of lines) {
    context.fillText(line, width / 2, top + size * 0.12);
    top += size * 1.38;
  }
  context.textAlign = 'start';
  context.textBaseline = 'alphabetic';
}

function drawSpeed(context, width, height, speed) {
  const size = Math.round(height * 0.024);
  context.font = `700 ${size}px ${FONT}`;
  const text = `▶▶ ${speed}배속`;
  const padX = size * 0.8;
  const boxWidth = context.measureText(text).width + padX * 2;
  const boxHeight = size * 1.9;
  const x = width - boxWidth - height * 0.03;
  const y = height * 0.03;
  context.fillStyle = 'rgba(17, 22, 33, 0.82)';
  context.beginPath();
  context.roundRect(x, y, boxWidth, boxHeight, boxHeight / 2);
  context.fill();
  context.fillStyle = MARK;
  context.textBaseline = 'middle';
  context.fillText(text, x + padX, y + boxHeight / 2);
  context.textBaseline = 'alphabetic';
}

// Cards fade in over their first moments and out over their last.
const fade = (elapsed, length) => Math.min(1, elapsed / 400, (length - elapsed) / 350);

async function render(job) {
  document.getElementById('render').hidden = false;
  const canvas = document.getElementById('canvas');
  const progressBar = document.getElementById('progress');
  const percent = document.getElementById('percent');
  canvas.width = job.width;
  canvas.height = job.height;
  // Drawn by the CPU: Chromium's software GPU, which PCs without a graphics card (virtual desktops) use, filled
  // rounded boxes as crossed triangles (2026-10-05, WSL).
  const context = canvas.getContext('2d', { alpha: false, willReadFrequently: true });
  const { width, height } = canvas;
  const video = document.createElement('video');
  video.muted = true;
  video.preload = 'auto';
  video.src = job.source;
  await new Promise((resolve, reject) => {
    video.onloadeddata = resolve;
    video.onerror = () =>
      reject(new Error(`원본 녹화를 열지 못했습니다: ${video.error?.message || '파일 형식'}`));
  });
  video.onerror = () =>
    recorder.failed(`원본 녹화를 재생하지 못했습니다: ${video.error?.message || '파일 형식'}`);
  const cues = job.cues;
  const speedups = job.speedups;
  const media = recordInto(canvas.captureStream(job.frameRate), job);
  let phase = 'intro';
  let phaseStart = 0;
  let lastTime = -1;
  let stalledSince = 0;
  let lastReport = 0;
  let begun = 0;
  const finish = () => {
    clearInterval(timer);
    if (media.state !== 'inactive') media.stop();
  };
  const tick = () => {
    const now = performance.now();
    const elapsed = now - phaseStart;
    if (phase === 'intro') {
      drawCard(context, width, height, job.intro, fade(elapsed, job.introMs));
      if (elapsed >= job.introMs) {
        phase = 'video';
        phaseStart = now;
        void video
          .play()
          .catch((error) => recorder.failed(`원본 녹화를 재생하지 못했습니다: ${error.message}`));
      }
    } else if (phase === 'video') {
      const time = video.currentTime * 1000;
      const part = speedups.find((item) => time >= item.start && time < item.end);
      const speed = part ? part.speed : 1;
      if (video.playbackRate !== speed) video.playbackRate = speed;
      if (video.readyState >= 2) context.drawImage(video, 0, 0, width, height);
      const cue = cues.find((item) => time >= item.start && time < item.end);
      if (cue) drawCaption(context, width, height, cue);
      if (speed > 1) drawSpeed(context, width, height, speed);
      if (time !== lastTime) {
        lastTime = time;
        stalledSince = now;
      } else if (now - stalledSince > 30_000 && !video.ended) {
        finish();
        recorder.failed('원본 녹화가 30초 동안 재생되지 않아 영상을 만들지 못했습니다.');
        return;
      }
      if (video.ended) {
        phase = 'outro';
        phaseStart = now;
      }
    } else {
      drawCard(context, width, height, job.outro, fade(elapsed, job.outroMs));
      if (elapsed >= job.outroMs) {
        finish();
        return;
      }
    }
    if (now - lastReport >= 500) {
      lastReport = now;
      const done = Math.min(job.expected, now - begun);
      progressBar.value = done / job.expected;
      percent.textContent = `${Math.floor((100 * done) / job.expected)}%`;
      recorder.progress({ done, total: job.expected });
    }
  };
  drawCard(context, width, height, job.intro, 0);
  begun = phaseStart = performance.now();
  stalledSince = begun;
  const timer = setInterval(tick, 1000 / job.frameRate);
  media.start(SLICE);
}

(async () => {
  const job = await recorder.job();
  if (!job) return;
  try {
    if (job.mode === 'capture') await capture(job);
    else await render(job);
  } catch (error) {
    recorder.failed(error.message);
  }
})();
