import {
  memo,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  countWords,
  headings,
  layout,
  piece,
  pieceAt,
  sectionHeading,
  stepWord,
  wordNumber,
  type Layout,
  type WordAt,
} from '@/lib/long-text';
import { highlighter, reach, rehypeHighlight } from './highlight';

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

// How far around what the reader sees a long text is drawn, in pixels: below it, and above it.
const AHEAD = 3000;
const BEHIND = 1500;
// How much of a long text stays drawn, in characters: further from the reader, pieces give their place back to an
// empty box of their height and are drawn again when the reader comes back. 전체 표시 keeps every piece.
const KEPT_CHARS = 1_000_000;
// The height of a character, in pixels, for the place of a piece not drawn yet, until drawn pieces tell.
const PIXELS_PER_CHAR = 0.35;

// The element that scrolls `node`: the nearest one above it whose content scrolls down.
function scroller(node: HTMLElement) {
  for (let parent = node.parentElement; parent; parent = parent.parentElement) {
    const overflow = getComputedStyle(parent).overflowY;
    if (overflow === 'auto' || overflow === 'scroll') return parent;
  }
  return null;
}

const squash = (value: string) => value.split(/\s+/).filter(Boolean).join(' ');

// What a long text is to show once the piece it is in is drawn: a search word (its place among the piece's marks, or
// the piece's last when stepping back into it, as the marks may be more or fewer than the text counted), or a heading
// of the table of contents (its place among the piece's headings, as its title may be found in more than one).
type Wanted =
  | { kind: 'word'; piece: number; index: number | 'last'; direction: 1 | -1; tried: number }
  | { kind: 'heading'; piece: number; ordinal: number; title: string };

// What a document window asks of its long text.
export type LongMarkdownHandle = {
  // The next (1) or previous (-1) search word, drawn first if it is not.
  step: (direction: 1 | -1) => void;
  // The heading of a section of the PageIndex table of contents (its position among the text's headings and its
  // title), drawn first if it is not; false when the text has no such heading.
  show: (section: { heading: number | null; title: string }) => boolean;
  // Where the reader is in the text: the beginning of the piece at the top of the view.
  reading: () => number;
};
// The search words of a long text: how many (counted in the text where not drawn yet), and the one shown (-1: none).
export type Found = { count: number; current: number };

// A document's text, drawn as Markdown does it, in one element. A long one (layout) is drawn piece by piece where the
// reader is: on opening its beginning, then the pieces the reader scrolls to, while the others keep their place as
// empty boxes of their height (estimated until drawn once), so a text of millions of characters opens and jumps at
// once and costs about what is in view. Pieces drawn above the view keep what the reader sees where it is. The search
// words are counted in the text and the headings of the table of contents found there, so going to either draws only
// the piece it is in. 전체 표시 draws all of it, piece by piece between the reader's own doings.
export function LongMarkdown({
  children: text,
  highlight,
  onFound,
  ref,
}: Props & { onFound?: (found: Found) => void; ref?: React.Ref<LongMarkdownHandle> }) {
  const plan = useMemo(() => layout(text), [text]);
  const total = plan.starts.length;
  // Each piece's Markdown, the same string from one drawing to the next.
  const texts = useMemo(
    () => plan.starts.map((_, index) => piece(text, plan, index)),
    [text, plan],
  );
  const terms = (highlight ?? []).join('\n');
  const pattern = useMemo(() => highlighter(terms ? terms.split('\n') : []), [terms]);
  // The search words of each piece as the text has them, and as the marks of each drawn piece count them.
  const counted = useMemo(
    () => (pattern ? countWords(text, plan, pattern) : null),
    [text, plan, pattern],
  );
  const [marked, setMarked] = useState({ counted, pieces: new Map<number, number>() });
  const exact = useMemo(
    () => (marked.counted === counted ? marked.pieces : new Map<number, number>()),
    [marked, counted],
  );
  const counts = useMemo(
    () => counted?.map((count, index) => exact.get(index) ?? count) ?? [],
    [counted, exact],
  );
  const [current, setCurrent] = useState<{ counted: typeof counted; at: WordAt | null }>({
    counted,
    at: null,
  });
  const at = current.counted === counted ? current.at : null;
  // The pieces drawn, and whether all are asked for (전체 표시). Another text begins again with its first piece.
  const first = (of: Layout) => of.starts.map((_, index) => index === 0);
  const [state, setState] = useState(() => ({ plan, drawn: first(plan), all: false }));
  const { drawn, all } = state.plan === plan ? state : { drawn: first(plan), all: false };
  const draw = (next: boolean[], everything = all) =>
    setState({ plan, drawn: next, all: everything });

  const root = useRef<HTMLDivElement>(null);
  const view = useRef<HTMLElement | null>(null);
  const elements = useRef<(HTMLDivElement | null)[]>([]);
  // How many pieces this text has, for the listeners that outlive a drawing.
  const pieceCount = useRef(total);
  pieceCount.current = total;
  // The height of each piece once drawn, which its place keeps when it is taken down again, and the height of a
  // character in the pieces drawn, for the places of those not drawn yet.
  const measured = useRef<{ plan: Layout; heights: (number | undefined)[]; ratio: number }>({
    plan,
    heights: [],
    ratio: PIXELS_PER_CHAR,
  });
  if (measured.current.plan !== plan)
    measured.current = { plan, heights: [], ratio: measured.current.ratio };
  // The piece at the top of the view and how far below the view's top it begins, kept where it is when pieces above
  // it change their height.
  const anchor = useRef<{ piece: number; top: number } | null>(null);
  const wanted = useRef<Wanted | null>(null);
  const headingList = useRef<{ text: string; found: ReturnType<typeof headings> } | null>(null);

  const chars = (index: number) => plan.ends[index] - plan.starts[index];
  // The piece the reader sees, with how far below the view's top it begins: the first drawn one in the view, else
  // the first one in it. Not the empty box of a piece about to be drawn, whose height is a guess: what the reader
  // reads stays where it is as that box becomes the text it held.
  const topPiece = () => {
    const scroll = view.current;
    const parts = elements.current.slice(0, pieceCount.current);
    if (!scroll || !parts.length) return null;
    const { top, bottom } = scroll.getBoundingClientRect();
    let low = 0;
    let high = parts.length - 1;
    while (low < high) {
      const middle = (low + high) >> 1;
      const box = parts[middle]?.getBoundingClientRect();
      if (!box || box.bottom > top) high = middle;
      else low = middle + 1;
    }
    for (let index = low; index < parts.length; index++) {
      const box = parts[index]?.getBoundingClientRect();
      if (!box || box.top >= bottom) break;
      if (!parts[index]!.hasAttribute('data-pending')) return { piece: index, top: box.top - top };
    }
    const box = parts[low]?.getBoundingClientRect();
    return box ? { piece: low, top: box.top - top } : null;
  };
  const hold = () => {
    anchor.current = topPiece();
  };

  // Draws, one piece at a time between the reader's own doings, the pieces in and around the view (all of them with
  // 전체 표시), nearest first; and takes down those furthest from the view while more than KEPT_CHARS are drawn.
  const pump = useRef(() => {});
  pump.current = () => {
    const scroll = view.current;
    if (!scroll) return;
    const box = scroll.getBoundingClientRect();
    const middle = (box.top + box.bottom) / 2;
    const near: { index: number; distance: number }[] = [];
    const far: { index: number; distance: number }[] = [];
    elements.current.slice(0, total).forEach((element, index) => {
      if (!element) return;
      const place = element.getBoundingClientRect();
      const distance = Math.max(0, place.top - middle, middle - place.bottom);
      const inside = place.bottom >= box.top - BEHIND && place.top <= box.bottom + AHEAD;
      (inside ? near : far).push({ index, distance });
    });
    const byDistance = (a: { distance: number }, b: { distance: number }) =>
      a.distance - b.distance;
    const next = [...near, ...(all ? far : [])].sort(byDistance).find(({ index }) => !drawn[index]);
    if (!next) return;
    const update = drawn.slice();
    update[next.index] = true;
    if (!all) {
      // What the reader holds (a selection, the focus) stays drawn.
      const selection = document.getSelection();
      const held = (element: Element | null | undefined) =>
        !!element &&
        (element.contains(document.activeElement) ||
          (!!selection?.rangeCount &&
            (element.contains(selection.anchorNode) || element.contains(selection.focusNode))));
      let kept = update.reduce((sum, on, index) => (on ? sum + chars(index) : sum), 0);
      for (const { index } of far.sort(byDistance).reverse()) {
        if (kept <= KEPT_CHARS) break;
        if (!update[index] || held(elements.current[index])) continue;
        update[index] = false;
        kept -= chars(index);
      }
    }
    hold();
    draw(update);
  };

  // The view's scrolling keeps the anchor and draws what comes into view.
  useEffect(() => {
    const element = root.current;
    const scroll = element && scroller(element);
    view.current = scroll;
    if (!scroll || total < 2) return;
    let cancel = () => {};
    // Kept as the reader scrolls (once a frame), so a drawing between two scrolls keeps what they show.
    const scrolled = () => {
      hold();
      cancel();
      cancel = later(() => pump.current());
    };
    scroll.addEventListener('scroll', scrolled, { passive: true });
    // A wider or narrower window lays the text out again: what the reader sees stays where it is.
    const resized = new ResizeObserver(() => {
      restore();
      cancel();
      cancel = later(() => pump.current());
    });
    resized.observe(scroll);
    return () => {
      scroll.removeEventListener('scroll', scrolled);
      resized.disconnect();
      cancel();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- follows the view of this text; hold and pump read refs
  }, [total]);

  // The piece at the top of the view back where it was, after pieces above it changed their height.
  function restore() {
    const scroll = view.current;
    const held = anchor.current;
    const element = held && elements.current[held.piece];
    if (!scroll || !held || !element) return;
    const moved =
      element.getBoundingClientRect().top - scroll.getBoundingClientRect().top - held.top;
    if (Math.abs(moved) >= 1) scroll.scrollTop += moved;
  }

  // The marks of a drawn piece, counted.
  const marks = (index: number) => [
    ...(elements.current[index]?.querySelectorAll<HTMLElement>('mark.search-hit') ?? []),
  ];

  // The search word or heading asked for, once its piece is drawn.
  function reachWanted() {
    const target = wanted.current;
    const scroll = view.current;
    if (!target || !drawn[target.piece] || !scroll) return;
    if (target.kind === 'heading') {
      wanted.current = null;
      const element = elements.current[target.piece];
      if (!element) return;
      const found = [...element.querySelectorAll('h1, h2, h3, h4, h5, h6')];
      const named = (item: Element | undefined) =>
        !!item && squash(item.textContent ?? '').startsWith(target.title);
      const heading =
        (named(found[target.ordinal]) ? found[target.ordinal] : found.find(named)) ?? element;
      reach(scroll, heading, 'start');
      hold();
      return;
    }
    const found = marks(target.piece);
    if (!found.length) {
      // The text counted words the piece does not show (in a link's address, say): on to the next piece with any.
      const fixed = counts.slice();
      fixed[target.piece] = 0;
      const next =
        target.tried < total &&
        stepWord(
          fixed,
          { piece: target.piece, index: target.direction > 0 ? -1 : 0 },
          target.direction,
        );
      wanted.current = next
        ? {
            ...target,
            piece: next.piece,
            index: target.direction > 0 ? 0 : 'last',
            tried: target.tried + 1,
          }
        : null;
      if (next && !drawn[next.piece]) {
        const update = drawn.slice();
        update[next.piece] = true;
        draw(update);
      } else if (next) reachWanted();
      return;
    }
    wanted.current = null;
    const index =
      target.index === 'last' ? found.length - 1 : Math.min(target.index, found.length - 1);
    root.current
      ?.querySelectorAll('mark.search-hit.current')
      .forEach((mark) => mark.classList.remove('current'));
    found[index].classList.add('current');
    setCurrent({ counted, at: { piece: target.piece, index } });
    reach(scroll, found[index], 'center');
    hold();
  }

  // The height of a character the places of the pieces not drawn yet have in this drawing.
  const { heights, ratio } = measured.current;
  // After each drawing: the heights measured, the view kept where it was, the drawn pieces' marks counted, what was
  // asked for shown, and the next piece drawn. Only a drawing that changed the pieces or their places moves the view
  // back: between two scrolls the anchor is where the reader left it.
  const last = useRef({ drawn, ratio });
  useLayoutEffect(() => {
    const record = measured.current;
    let pixels = 0;
    let characters = 0;
    drawn.forEach((on, index) => {
      const element = elements.current[index];
      if (!on || !element) return;
      record.heights[index] = element.getBoundingClientRect().height;
      pixels += record.heights[index];
      characters += chars(index);
    });
    if (characters) record.ratio = pixels / characters;
    if (last.current.drawn !== drawn || last.current.ratio !== ratio) restore();
    last.current = { drawn, ratio };
    if (counted) {
      let changed = false;
      const pieces = new Map(exact);
      drawn.forEach((on, index) => {
        if (!on || pieces.has(index)) return;
        pieces.set(index, marks(index).length);
        changed = true;
      });
      if (changed) setMarked({ counted, pieces });
    }
    // The word shown keeps its mark when its piece is drawn again.
    if (at && drawn[at.piece]) marks(at.piece)[at.index]?.classList.add('current');
    reachWanted();
    if (total < 2) return;
    return later(() => pump.current());
    // eslint-disable-next-line react-hooks/exhaustive-deps -- after a drawing or a new count; the rest is this render's
  }, [drawn, all, ratio, counted]);

  const count = counts.reduce((sum, value) => sum + value, 0);
  const number = at ? wordNumber(counts, at) : -1;
  useEffect(() => {
    onFound?.({ count, current: number });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- told when they change, whatever function hears them
  }, [count, number]);

  const go = (target: Wanted) => {
    wanted.current = target;
    if (drawn[target.piece]) return reachWanted();
    const update = drawn.slice();
    update[target.piece] = true;
    hold();
    draw(update);
  };
  useImperativeHandle(ref, () => ({
    step(direction) {
      const next = counts.length ? stepWord(counts, at, direction) : null;
      if (next) go({ kind: 'word', ...next, direction, tried: 0 });
    },
    show(section) {
      if (headingList.current?.text !== text) headingList.current = { text, found: headings(text) };
      const { found } = headingList.current;
      const heading = sectionHeading(found, section);
      if (!heading) return false;
      const index = pieceAt(plan.starts, heading.offset);
      const ordinal = found.filter(
        (item) => item.offset >= plan.starts[index] && item.offset < heading.offset,
      ).length;
      go({ kind: 'heading', piece: index, ordinal, title: heading.title });
      return true;
    },
    reading() {
      const top = topPiece();
      return plan.starts[top?.piece ?? 0];
    },
  }));

  const shown = drawn.filter(Boolean).length;
  return (
    <div className={total > 1 ? 'markdown long-markdown' : 'markdown'} ref={root}>
      {shown < total && (
        <p className="muted long-document-more" role="status">
          {all
            ? `긴 문서의 나머지를 표시하는 중… (${shown}/${total})`
            : '긴 문서는 읽는 자리 근처만 표시합니다.'}
          {!all && (
            <button type="button" className="text-button" onClick={() => draw(drawn, true)}>
              전체 표시
            </button>
          )}
        </p>
      )}
      {plan.starts.map((_, index) => (
        <div
          key={index}
          ref={(element) => {
            elements.current[index] = element;
          }}
          className="long-document-part"
          data-pending={drawn[index] ? undefined : ''}
          style={
            drawn[index]
              ? undefined
              : { height: heights[index] ?? Math.round(chars(index) * ratio) }
          }
        >
          {drawn[index] ? (
            <Blocks highlight={highlight}>{texts[index]}</Blocks>
          ) : (
            <p className="long-document-pending">
              긴 문서의 {index + 1}/{total}번째 부분입니다. 이 자리에 오면 표시합니다.
            </p>
          )}
        </div>
      ))}
    </div>
  );
}
