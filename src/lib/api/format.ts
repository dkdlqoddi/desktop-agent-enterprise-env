// Each formatter is made once: making one for every row of a long list took most of the time the list took to draw.
const DATE = new Intl.DateTimeFormat('ko-KR', {
  month: 'short',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
});
const DAY_TIME = new Intl.DateTimeFormat('ko-KR', {
  month: 'short',
  day: 'numeric',
  weekday: 'short',
  hour: 'numeric',
  minute: '2-digit',
});
const CLOCK = new Intl.DateTimeFormat('ko-KR', { hour: 'numeric', minute: '2-digit' });
export const date = (value: string) => DATE.format(new Date(value));
export const dayTime = (value: string) => DAY_TIME.format(new Date(value));
export const clock = (value: string) => CLOCK.format(new Date(value));
// A local calendar day as YYYY-MM-DD, and how lists name it: 오늘, 어제, 내일 or the date.
export const localDay = (value: string | Date | number) => {
  const day = new Date(value);
  return `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}-${String(day.getDate()).padStart(2, '0')}`;
};
const DAY = new Intl.DateTimeFormat('ko-KR', { month: 'long', day: 'numeric', weekday: 'short' });
export function dayLabel(key: string) {
  const today = new Date();
  const from = (days: number) =>
    localDay(new Date(today.getFullYear(), today.getMonth(), today.getDate() + days));
  if (key === from(0)) return '오늘';
  if (key === from(-1)) return '어제';
  if (key === from(1)) return '내일';
  return DAY.format(new Date(`${key}T00:00:00`));
}
export const seconds = (ms: number | null) =>
  ms === null
    ? '—'
    : ms < 60_000
      ? `${(ms / 1000).toFixed(1)}초`
      : `${Math.floor(ms / 60_000)}분 ${Math.round((ms % 60_000) / 1000)}초`;
const relativeFormat = new Intl.RelativeTimeFormat('ko', { numeric: 'auto' });
export function relative(value: string, now = Date.now()) {
  const minutes = Math.round((new Date(value).getTime() - now) / 60_000);
  if (Math.abs(minutes) < 60) return relativeFormat.format(minutes, 'minute');
  if (Math.abs(minutes) < 1440) return relativeFormat.format(Math.round(minutes / 60), 'hour');
  return relativeFormat.format(Math.round(minutes / 1440), 'day');
}
// <input type="datetime-local"> works in local wall time; the backend stores UTC.
export const localInputValue = (value: string | null) => {
  if (!value) return '';
  const time = new Date(value);
  return new Date(time.getTime() - time.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
};
export const isoFromLocalInput = (value: string) => (value ? new Date(value).toISOString() : null);
// One-line previews of Markdown, without heading, list, quote and emphasis marks; links and images
// keep their text and escaped characters lose their backslash.
export const plain = (value: string) =>
  value
    .replace(/^\s{0,3}(#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s?)/gm, '')
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\\([\\`*_{}[\]()#+\-.!|])/g, '$1')
    .replace(/[*_`~]+/g, '')
    .replace(/\s+/g, ' ')
    .trim();
// Enter sends and Shift+Enter breaks the line. The Enter that commits Korean IME composition
// arrives while still composing; sending on it would drop the last syllable.
export const submitsOnEnter = (event: React.KeyboardEvent) =>
  event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing;
export const variablesOf = (template: string) => [
  ...new Set([...template.matchAll(/\{\{\s*([^{}]+?)\s*\}\}/g)].map((match) => match[1].trim())),
];
