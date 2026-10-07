import { useEffect, useState, type RefObject } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';

const escape = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// A case-insensitive pattern of the search words, longest first so "캐시 도입" wins over "캐시".
export function highlighter(terms: readonly string[] = []) {
  const words = [...new Set(terms.map((term) => term.trim()).filter(Boolean))]
    .sort((a, b) => b.length - a.length)
    .slice(0, 24);
  return words.length ? new RegExp(words.map(escape).join('|'), 'giu') : null;
}

function parts(text: string, pattern: RegExp) {
  const found: { text: string; hit: boolean }[] = [];
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > last) found.push({ text: text.slice(last, match.index), hit: false });
    found.push({ text: match[0], hit: true });
    last = match.index + match[0].length;
  }
  if (last < text.length) found.push({ text: text.slice(last), hit: false });
  return found;
}

// Text with the search words marked.
export default function Highlight({ text, terms }: { text: string; terms?: readonly string[] }) {
  const pattern = highlighter(terms);
  if (!pattern) return text;
  return parts(text, pattern).map((part, i) =>
    part.hit ? (
      <mark key={i} className="search-hit">
        {part.text}
      </mark>
    ) : (
      part.text
    ),
  );
}

// The rehype (HTML syntax tree) nodes the Markdown highlighter touches.
type Node = {
  type: string;
  value?: string;
  tagName?: string;
  properties?: Record<string, unknown>;
  children?: Node[];
};

// A rehype plugin marking the search words in rendered Markdown, code included.
export function rehypeHighlight(pattern: RegExp) {
  return (tree: Node) => {
    const visit = (node: Node) => {
      node.children = node.children?.flatMap((child) => {
        if (child.type !== 'text' || !child.value) {
          visit(child);
          return [child];
        }
        return parts(child.value, pattern).map((part) =>
          part.hit
            ? {
                type: 'element',
                tagName: 'mark',
                properties: { className: ['search-hit'] },
                children: [{ type: 'text', value: part.text }],
              }
            : { type: 'text', value: part.text },
        );
      });
    };
    visit(tree);
  };
}

// The elements matching `selector` inside `container`, including those of the HTML pages shown in it,
// which live in shadow roots.
export function findAll(container: HTMLElement | null, selector: string) {
  if (!container) return [];
  const pages = [...container.querySelectorAll<HTMLElement>('[data-html-document]')]
    .map((host) => host.shadowRoot)
    .filter((root): root is ShadowRoot => !!root);
  return [container, ...pages].flatMap((root) => [...root.querySelectorAll<HTMLElement>(selector)]);
}

// Counts the marked search words inside `container` and moves between them. `content` identifies the
// rendered text, so the count follows when it changes.
export function useMatches(container: RefObject<HTMLElement | null>, content: unknown) {
  const [count, setCount] = useState(0);
  const [current, setCurrent] = useState(-1);
  useEffect(() => {
    setCount(findAll(container.current, 'mark.search-hit').length);
    setCurrent(-1);
  }, [container, content]);
  function go(step: 1 | -1) {
    const marks = findAll(container.current, 'mark.search-hit');
    if (!marks.length) return;
    const next =
      current < 0
        ? step > 0
          ? 0
          : marks.length - 1
        : (current + step + marks.length) % marks.length;
    marks.forEach((mark, i) => mark.classList.toggle('current', i === next));
    marks[next].scrollIntoView({ block: 'center', behavior: 'smooth' });
    setCurrent(next);
  }
  return { count, current, go };
}

export function MatchNavigator({
  matches,
  terms,
}: {
  matches: ReturnType<typeof useMatches>;
  terms: readonly string[];
}) {
  if (!terms.length) return null;
  const { count, current, go } = matches;
  return (
    <div className="match-nav" role="group" aria-label="검색어 위치 이동">
      <span>
        검색어 <strong>{terms.slice(0, 3).join(', ')}</strong>
        {terms.length > 3 && ` 외 ${terms.length - 3}개`} ·{' '}
        {count ? `${current < 0 ? '' : `${current + 1}/`}${count}곳` : '본문에 없음'}
      </span>
      <button
        type="button"
        className="icon-button"
        aria-label="이전 검색어 위치"
        disabled={!count}
        onClick={() => go(-1)}
      >
        <ChevronUp size={16} />
      </button>
      <button
        type="button"
        className="icon-button"
        aria-label="다음 검색어 위치"
        disabled={!count}
        onClick={() => go(1)}
      >
        <ChevronDown size={16} />
      </button>
    </div>
  );
}
