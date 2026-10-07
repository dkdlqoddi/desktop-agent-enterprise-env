import { memo, useEffect, useRef } from 'react';
import { markTerms, sanitize } from '@/lib/html-document';
import { highlighter } from './highlight';

// The app's own marks inside the page; everything else comes from the page's stylesheet.
const MARKS = `
:host { display: block; }
mark.search-hit { padding: 0 1px; border-radius: 2px; background: #ffe58a; color: inherit; }
mark.search-hit.current { background: #ffb02e; box-shadow: 0 0 0 2px #ffb02e66; }
`;

type Props = { html: string; highlight?: readonly string[] };
const same = (a?: readonly string[], b?: readonly string[]) =>
  (a ?? []).length === (b ?? []).length && (a ?? []).every((term, i) => term === b?.[i]);

// Memoized like the Markdown view: a page is prepared again only when it or the search words change.
export default memo(HtmlDocument, (a, b) => a.html === b.html && same(a.highlight, b.highlight));

// An imported file's HTML page, drawn in a shadow root: its styles apply to the page only, the app's
// styles leave it as it was made, and the search words are marked in its text.
function HtmlDocument({ html, highlight }: Props) {
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = host.current;
    if (!element) return;
    const root = element.shadowRoot ?? element.attachShadow({ mode: 'open' });
    const { styles, body } = sanitize(html);
    const page = document.createElement('div');
    page.className = 'page';
    page.append(...document.adoptNode(body).childNodes);
    const pattern = highlighter(highlight);
    if (pattern) markTerms(page, pattern);
    const sheets = [...styles, MARKS].map((text) => {
      const style = document.createElement('style');
      style.textContent = text;
      return style;
    });
    root.replaceChildren(...sheets, page);
  }, [html, highlight]);
  useEffect(() => {
    const root = host.current?.shadowRoot;
    if (!root) return;
    // Links stay in the page: a bookmark or note scrolls to its place, an address shows as its title.
    const follow = (event: Event) => {
      const link = event
        .composedPath()
        .find(
          (node): node is HTMLAnchorElement =>
            node instanceof HTMLAnchorElement && node.hasAttribute('href'),
        );
      if (!link) return;
      event.preventDefault();
      const href = link.getAttribute('href')!;
      if (!href.startsWith('#')) return;
      let id = href.slice(1);
      try {
        id = decodeURIComponent(id);
      } catch {
        // A bookmark name with a stray % is used as written.
      }
      root.getElementById(id)?.scrollIntoView({ block: 'start', behavior: 'smooth' });
    };
    root.addEventListener('click', follow);
    return () => root.removeEventListener('click', follow);
  }, []);
  // The page's CSS can restyle its shadow host (an !important rule inside wins over the app's), never the frame
  // around it, which keeps everything the page draws, fixed layers too, inside its box.
  return (
    <div className="html-document-frame">
      <div ref={host} className="html-document" data-html-document="" />
    </div>
  );
}
