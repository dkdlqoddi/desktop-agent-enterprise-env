import { memo, useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { highlighter, rehypeHighlight } from './highlight';

type Props = { children: string; highlight?: readonly string[] };
const same = (a?: readonly string[], b?: readonly string[]) =>
  (a ?? []).length === (b ?? []).length && (a ?? []).every((term, i) => term === b?.[i]);

// Memoized: a long document is parsed again only when its text or search words change, and the
// search word being shown keeps its mark.
export default memo(
  Markdown,
  (a, b) => a.children === b.children && same(a.highlight, b.highlight),
);

function Markdown({ children, highlight }: Props) {
  return (
    <div className="markdown">
      <Blocks highlight={highlight}>{children}</Blocks>
    </div>
  );
}

// The blocks of a Markdown text, without an element around them.
const Blocks = memo(
  function Blocks({ children, highlight }: Props) {
    const pattern = highlighter(highlight);
    return (
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={pattern ? [[rehypeHighlight, pattern]] : []}
        components={{
          img: ({ alt }) => <span className="muted">[이미지: {alt || '외부 이미지'}]</span>,
          a: ({ children, href }) => (
            <span className="markdown-link" title={href}>
              {children}
            </span>
          ),
          // Code and tables scroll sideways when wide; focusable, a keyboard can scroll them too.
          pre: ({ children }) => <pre tabIndex={0}>{children}</pre>,
          table: ({ children }) => <table tabIndex={0}>{children}</table>,
        }}
      >
        {children}
      </ReactMarkdown>
    );
  },
  (a, b) => a.children === b.children && same(a.highlight, b.highlight),
);

// A document is one text however long (up to 8,000,000 characters). One up to WHOLE_CHARS is drawn at once, as every
// document was while none was longer; a longer one in pieces of about PIECE_CHARS, so that the window shows its
// beginning at once and stays usable while the rest is drawn.
const WHOLE_CHARS = 80_000;
const PIECE_CHARS = 40_000;
const FENCE = /^ {0,3}(`{3,}|~{3,})/;
// A link's address given apart from its text ([name]: address), which a piece without it would not find.
const DEFINITION = /^ {0,3}\[(?!\^)[^\]\n]+\]:\s*\S/;
const MAX_DEFINITIONS = 500;

// A long Markdown text as pieces that are Markdown of their own: cut at a blank line outside a fenced code block, and
// only before a line that begins at the margin (an indented one may go on with the list item or code before it). A
// table has no blank line inside it, so it stays whole; a list cut between two items goes on as a list that keeps its
// numbers. Addresses of links given apart from their text go with every piece.
export function pieces(text: string, whole = WHOLE_CHARS, size = PIECE_CHARS): string[] {
  if (text.length <= whole) return [text];
  const lines = text.split('\n');
  const found: string[] = [];
  const definitions: string[] = [];
  let fence = '';
  let start = 0;
  let length = 0;
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
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
    length += line.length + 1;
    const next = lines[index + 1];
    if (length >= size && !fence && !line.trim() && next !== undefined && /^\S/.test(next)) {
      found.push(lines.slice(start, index + 1).join('\n'));
      start = index + 1;
      length = 0;
    }
  }
  if (start < lines.length) found.push(lines.slice(start).join('\n'));
  if (!definitions.length || definitions.length > MAX_DEFINITIONS || found.length < 2) return found;
  const shared = `\n\n${definitions.join('\n')}\n`;
  return found.map((piece) => piece + shared);
}

const later: (run: () => void) => () => void =
  typeof requestIdleCallback === 'function'
    ? (run) => {
        const id = requestIdleCallback(run, { timeout: 200 });
        return () => cancelIdleCallback(id);
      }
    : (run) => {
        const id = setTimeout(run, 0);
        return () => clearTimeout(id);
      };

// How far below what the reader sees a long text is drawn ahead, in pixels.
const AHEAD = 3000;

// The element that scrolls `node`: the nearest one above it whose content scrolls down.
function scroller(node: HTMLElement) {
  for (let parent = node.parentElement; parent; parent = parent.parentElement) {
    const overflow = getComputedStyle(parent).overflowY;
    if (overflow === 'auto' || overflow === 'scroll') return parent;
  }
  return null;
}

// A document's text, drawn as Markdown does it, in one element. A long one is drawn piece after piece (pieces): on
// as the reader comes near the end of what is drawn, so that a text of millions of characters opens at once and
// costs what is read of it; or on to its end without waiting, piece by piece between the reader's own doings, when
// all of it is wanted (`whole`: search words to find throughout, a section further down to go to) or asked for
// (전체 표시). `onDrawn` hears whether all is drawn and how many pieces are, at first and whenever that changes.
export function LongMarkdown({
  children,
  highlight,
  whole = false,
  onDrawn,
}: Props & { whole?: boolean; onDrawn?: (drawn: boolean, shown: number) => void }) {
  const parts = useMemo(() => pieces(children), [children]);
  const [progress, setProgress] = useState({ parts, shown: 1, all: false });
  // Another text begins again with its first piece, within this very render.
  const { shown, all } = progress.parts === parts ? progress : { shown: 1, all: false };
  const drawn = shown >= parts.length;
  const more = useRef<HTMLParagraphElement>(null);
  const hurried = whole || all;
  useEffect(() => {
    if (drawn) return;
    const next = () => setProgress({ parts, shown: shown + 1, all });
    const note = more.current;
    if (hurried || !note || typeof IntersectionObserver !== 'function') return later(next);
    // Seen again with every piece drawn: the next one follows at once while the end is still near.
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) next();
      },
      { root: scroller(note), rootMargin: `0px 0px ${AHEAD}px 0px` },
    );
    observer.observe(note);
    return () => observer.disconnect();
  }, [drawn, parts, shown, all, hurried]);
  useEffect(() => {
    onDrawn?.(drawn, shown);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- told when it changes, whatever function hears it
  }, [drawn, shown]);
  return (
    <div className="markdown">
      {parts.slice(0, shown).map((part, index) => (
        <Blocks key={index} highlight={highlight}>
          {part}
        </Blocks>
      ))}
      {!drawn && (
        <p className="muted long-document-more" role="status" ref={more}>
          {hurried
            ? '긴 문서의 나머지를 표시하는 중…'
            : '긴 문서입니다. 아래로 내리면 이어서 표시합니다.'}{' '}
          ({shown}/{parts.length})
          {!hurried && (
            <button
              type="button"
              className="text-button"
              onClick={() => setProgress({ parts, shown, all: true })}
            >
              전체 표시
            </button>
          )}
        </p>
      )}
    </div>
  );
}
