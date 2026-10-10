const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// The palette of src/styles/theme.css, measured: every text color against the surfaces it is drawn on, in both themes,
// by the WCAG 2 contrast ratio; and the stylesheets use the palette's names only.
const STYLES = path.join(__dirname, '..', 'src', 'styles');
const theme = fs
  .readFileSync(path.join(STYLES, 'theme.css'), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '');

function declarations(selector) {
  const start = theme.indexOf(`${selector} {`);
  assert.ok(start >= 0, `${selector} is missing`);
  const body = theme.slice(theme.indexOf('{', start) + 1, theme.indexOf('}', start));
  return Object.fromEntries(
    [...body.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map(([, name, value]) => [name, value.trim()]),
  );
}
const light = declarations(':root');
const dark = declarations(":root[data-theme='dark']");
const system = declarations(":root:not([data-theme='light'])");
// The colors the dark theme keeps as they are: fills under white text, the brand, the paper of imported pages.
const SHARED = [
  '--accent-fill',
  '--accent-fill-hover',
  '--on-fill',
  '--online',
  '--offline',
  '--toast-ink',
  '--toast-ink-muted',
  '--knob',
  '--brand-1',
  '--brand-2',
  '--brand-3',
  '--ask-light',
  '--sculpture-spark',
  '--demo-pulse',
  '--paper',
  '--paper-ink',
];

function rgba(value) {
  const hex = value.replace('#', '');
  const digits = hex.length <= 4 ? [...hex].map((c) => c + c).join('') : hex;
  const [r, g, b, a = 255] = digits.match(/../g).map((pair) => parseInt(pair, 16));
  return { r, g, b, a: a / 255 };
}
const over = (top, bottom) => ({
  r: top.r * top.a + bottom.r * (1 - top.a),
  g: top.g * top.a + bottom.g * (1 - top.a),
  b: top.b * top.a + bottom.b * (1 - top.a),
  a: 1,
});
const channel = (c) => {
  const s = c / 255;
  return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
};
const luminance = ({ r, g, b }) => 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
const ratio = (a, b) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

const SURFACES = ['--panel', '--canvas', '--canvas-base', '--inset', '--inset-2'];
// Text colors and where the screens draw them; translucent surfaces lie between --panel and the canvas beneath.
const TEXT = {
  '--ink': [
    ...SURFACES,
    '--accent-soft',
    '--mark',
    '--mark-soft',
    '--highlight',
    '--ok-soft',
    '--tint-blue',
  ],
  '--ink-soft': SURFACES,
  '--ink-2': [...SURFACES, '--accent-soft', '--tint-blue', '--tint-peach', '--tint-mint'],
  '--muted': [
    ...SURFACES,
    '--accent-soft',
    '--accent-tint',
    '--mark-soft',
    '--tint-blue',
    '--tint-peach',
    '--tint-mint',
  ],
  '--accent': [...SURFACES, '--accent-soft', '--accent-tint', '--mark-soft'],
  '--accent-muted': [...SURFACES, '--accent-soft', '--accent-tint'],
  '--ok': ['--panel', '--inset', '--ok-soft'],
  '--ok-strong': ['--ok-soft'],
  '--warn': ['--panel', '--inset', '--warn-soft'],
  '--danger': ['--panel', '--inset', '--danger-soft', '--tint-peach'],
  '--danger-strong': ['--danger-soft'],
  '--graph-keyword': ['--panel', '--inset'],
  '--on-fill': [
    '--accent-fill',
    '--accent-fill-hover',
    '--ok-fill',
    '--warn-fill',
    '--danger-fill',
  ],
  '--on-inverse': ['--inverse', '--danger'],
  '--on-inverse-muted': ['--inverse'],
  '--paper-ink': ['--paper'],
};
// Large text (24px, or 18.7px bold): the dashboard's figures and the greeting's dot.
const LARGE = {
  '--tint-blue-ink': ['--tint-blue'],
  '--tint-peach-ink': ['--tint-peach'],
  '--tint-mint-ink': ['--tint-mint'],
  '--chart': ['--canvas'],
};

function measure(palette) {
  const color = (name) => {
    assert.ok(palette[name], `${name} has no value`);
    return rgba(palette[name]);
  };
  const rows = [];
  const add = (text, surface, minimum, background = color(surface)) =>
    rows.push({ text, surface, minimum, ratio: ratio(over(color(text), background), background) });
  for (const [text, surfaces] of Object.entries(TEXT))
    for (const surface of surfaces) add(text, surface, 4.5);
  for (const [text, surfaces] of Object.entries(LARGE))
    for (const surface of surfaces) add(text, surface, 3);
  // The toast is translucent over the screen.
  add('--toast-ink', '--toast', 4.5, over(color('--toast'), color('--canvas')));
  return rows;
}

for (const [name, palette] of [
  ['light', light],
  ['dark', { ...light, ...dark }],
]) {
  test(`${name} theme: every text color reaches WCAG AA on its surfaces`, () => {
    const rows = measure(palette);
    if (process.env.THEME_CONTRAST)
      console.log(
        rows.map((r) => `${name} ${r.text} on ${r.surface}: ${r.ratio.toFixed(2)}`).join('\n'),
      );
    const failing = rows
      .filter((r) => r.ratio < r.minimum)
      .map((r) => `${r.text} on ${r.surface}: ${r.ratio.toFixed(2)} < ${r.minimum}`);
    assert.deepEqual(failing, []);
  });
}

test('the dark palette is the same for the setting and for the system, and changes every other color', () => {
  assert.deepEqual(system, dark);
  const unchanged = Object.keys(light).filter((name) => !(name in dark));
  assert.deepEqual(unchanged.sort(), [...SHARED].sort());
  assert.deepEqual(
    Object.keys(dark).filter((name) => !(name in light)),
    [],
  );
});

test('the stylesheets and components use the palette by name, and every name they use exists', () => {
  const sheets = fs.readdirSync(STYLES).filter((file) => file.endsWith('.css'));
  const defined = new Set();
  const used = new Map();
  const read = (file) => fs.readFileSync(file, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
  for (const sheet of sheets) {
    const text = read(path.join(STYLES, sheet));
    for (const [, name] of text.matchAll(/(--[\w-]+)\s*:/g)) defined.add(name);
    if (sheet !== 'theme.css')
      assert.deepEqual(text.match(/#[0-9a-f]{3,8}\b/gi), null, `${sheet} has colors of its own`);
    for (const [, name] of text.matchAll(/var\((--[\w-]+)/g)) used.set(name, sheet);
  }
  const components = path.join(__dirname, '..', 'src', 'components');
  for (const entry of fs.readdirSync(components, { recursive: true }))
    if (/\.tsx?$/.test(entry))
      for (const [, name] of read(path.join(components, entry)).matchAll(
        /var\((--[\w-]+)|'(--[\w-]+)'/g,
      ))
        if (name) used.set(name, entry);
  const missing = [...used]
    .filter(([name]) => !defined.has(name))
    .map(([n, where]) => `${n} (${where})`);
  assert.deepEqual(missing, []);
});
