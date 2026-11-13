// A long Markdown text of the document window, read from the text itself rather than from what is drawn of it: the
// pieces it is drawn in, how often the search words occur in each, and its headings. A document holds up to 8,000,000
// characters; drawing all of one takes minutes and gigabytes, reading its text takes milliseconds.

const FENCE = /^ {0,3}(`{3,}|~{3,})/;
// A link's address given apart from its text ([name]: address), which a piece without it would not find.
const DEFINITION = /^ {0,3}\[(?!\^)[^\]\n]+\]:\s*\S/;
const MAX_DEFINITIONS = 500;

// A text up to WHOLE_CHARS is drawn at once, as every document was while none was longer; a longer one in pieces of
// about PIECE_CHARS, of which the window draws those the reader comes to.
export const WHOLE_CHARS = 80_000;
export const PIECE_CHARS = 40_000;

// Where a text is cut into pieces: piece i is text.slice(starts[i], ends[i]), and the line break between two pieces is
// in neither. `shared`: the addresses of links given apart from their text, which go with every piece ('' if none).
export type Layout = { starts: number[]; ends: number[]; shared: string };

// A long Markdown text as pieces that are Markdown of their own: cut at a blank line outside a fenced code block, and
// only before a line that begins at the margin (an indented one may go on with the list item or code before it). A
// table has no blank line inside it, so it stays whole; a list cut between two items goes on as a list that keeps its
// numbers. Addresses of links given apart from their text go with every piece.
export function layout(text: string, whole = WHOLE_CHARS, size = PIECE_CHARS): Layout {
  if (text.length <= whole) return { starts: [0], ends: [text.length], shared: '' };
  const starts = [0];
  const ends: number[] = [];
  const definitions: string[] = [];
  let fence = '';
  let length = 0;
  for (let at = 0; ;) {
    const found = text.indexOf('\n', at);
    const end = found < 0 ? text.length : found;
    const line = text.slice(at, end);
    const mark = FENCE.exec(line)?.[1];
    if (fence) {
      // The block ends with a fence of the same character, at least as long, and nothing after it.
      if (
        mark &&
        mark[0] === fence[0] &&
        mark.length >= fence.length &&
        !line.trim().slice(mark.length)
      )
        fence = '';
    } else if (mark) fence = mark;
    else if (DEFINITION.test(line) && definitions.length <= MAX_DEFINITIONS) definitions.push(line);
    if (found < 0) break;
    length += line.length + 1;
    if (length >= size && !fence && !line.trim() && /\S/.test(text[end + 1] ?? '')) {
      ends.push(end);
      starts.push(end + 1);
      length = 0;
    }
    at = end + 1;
  }
  ends.push(text.length);
  const shared =
    definitions.length && definitions.length <= MAX_DEFINITIONS && starts.length > 1
      ? `\n\n${definitions.join('\n')}\n`
      : '';
  return { starts, ends, shared };
}

// The Markdown of piece `index`, as it is drawn.
export const piece = (text: string, plan: Layout, index: number) =>
  text.slice(plan.starts[index], plan.ends[index]) + plan.shared;

// The piece that holds the character at `offset` (one between two pieces goes with the piece before it).
export function pieceAt(starts: readonly number[], offset: number) {
  let low = 0;
  let high = starts.length - 1;
  while (low < high) {
    const middle = (low + high + 1) >> 1;
    if (starts[middle] <= offset) low = middle;
    else high = middle - 1;
  }
  return low;
}

// What Markdown leaves out of the text it shows: pictures (whose description is not marked), the addresses of links and
// link definitions. Footnotes ([^1]: …) are shown.
const UNSHOWN = /!\[[^\]\n]*\]\([^)\n]*\)|\]\([^)\n]*\)|^ {0,3}\[(?!\^)[^\]\n]+\]:.*$/gm;

// How often `pattern` (the search words' pattern, global) occurs in each piece of `text`. Markdown marks the words in
// the text it shows, which is the text without its syntax; these counts are near that, close enough to say how many
// there are and where they lie before a piece is drawn, and the window counts the marks of a piece once it is.
export function countWords(text: string, plan: Layout, pattern: RegExp) {
  return plan.starts.map((start, index) => {
    const shown = text
      .slice(start, plan.ends[index])
      .replace(UNSHOWN, (found) => (found[0] === ']' ? ']' : ' '));
    let count = 0;
    for (const match of shown.matchAll(pattern)) if (match[0]) count++;
    return count;
  });
}

// A search word in a long text: the piece it is in and its place among that piece's words.
export type WordAt = { piece: number; index: number };

// The search word after (1) or before (-1) the one `at`, by the count of words of each piece, across the pieces and
// around from the last to the first; from none, the first or the last. Null when no piece has any.
export function stepWord(counts: readonly number[], at: WordAt | null, direction: 1 | -1) {
  if (at) {
    const index = at.index + direction;
    if (index >= 0 && index < counts[at.piece]) return { piece: at.piece, index };
  }
  const total = counts.length;
  const from = at ? at.piece : direction > 0 ? -1 : total;
  for (let step = 1; step <= total; step++) {
    const piece = (((from + direction * step) % total) + total) % total;
    if (counts[piece] > 0) return { piece, index: direction > 0 ? 0 : counts[piece] - 1 };
  }
  return null;
}

// The place of the word `at` among all the words of the text, from 0.
export const wordNumber = (counts: readonly number[], at: WordAt) =>
  counts.slice(0, at.piece).reduce((sum, count) => sum + count, 0) + at.index;

// A heading as the PageIndex table of contents names it (backend/agent/pageindex.py headings and inline): links keep
// their text; no emphasis, code marks or escapes; white space as one space; at most 120 characters.
const LINK = /!?\[([^\]]*)\]\([^)]*\)/g;
const ESCAPE = /\\([\\`*_{}[\]()#+\-.!|<>&~=])/g;
const squash = (value: string) => value.split(/\s+/).filter(Boolean).join(' ');
const first = (value: string, count: number) => [...value].slice(0, count).join('');
const inline = (value: string) =>
  value
    .replace(LINK, '$1')
    .replace(/\*\*|__|~~|`+/g, '')
    .replace(ESCAPE, '$1');

// The text of an ATX heading line (up to three spaces, one to six #, then a space, a tab or the line's end, without
// a closing run of # after a space), or null.
function heading(line: string) {
  const body = line.replace(/^ +/, '');
  let level = 0;
  while (body[level] === '#') level++;
  const rest = body.slice(level);
  if (line.length - body.length > 3 || level < 1 || level > 6 || !/^[ \t]?$/.test(rest.slice(0, 1)))
    return null;
  const words = rest.replace(/^[ \t]+|[ \t]+$/g, '');
  const closed = words.replace(/#+$/, '');
  return closed !== words && /[ \t]$/.test(closed) ? closed.replace(/[ \t]+$/, '') : words;
}

// A heading of the text: where its line begins and its title.
export type Heading = { offset: number; title: string };

// The ATX headings outside fenced code blocks, as the backend reads them (pageindex.py headings): the n-th of these is
// the heading the table of contents gives as n.
export function headings(text: string): Heading[] {
  const found: Heading[] = [];
  let fence = '';
  for (let at = 0; at < text.length;) {
    const next = text.indexOf('\n', at);
    const end = next < 0 ? text.length : next;
    const line = text.slice(at, end).replace(/\r$/, '');
    const opening = FENCE.exec(line)?.[1];
    if (fence) {
      if (
        opening &&
        opening[0] === fence[0] &&
        opening.length >= fence.length &&
        [...line.trim()].every((character) => character === fence[0])
      )
        fence = '';
    } else if (opening) fence = opening;
    else {
      const words = heading(line);
      const title = words === null ? '' : first(squash(inline(first(squash(words), 1000))), 120);
      if (title) found.push({ offset: at, title });
    }
    at = end + 1;
  }
  return found;
}

// The heading the table of contents means by its position among the headings and its title: that position when the
// title is the one there, else the first heading of that title. Undefined when the text has none.
export function sectionHeading(
  found: readonly Heading[],
  section: { heading: number | null; title: string },
) {
  const direct = section.heading === null ? undefined : found[section.heading];
  return direct?.title === section.title
    ? direct
    : found.find((item) => item.title === section.title);
}
