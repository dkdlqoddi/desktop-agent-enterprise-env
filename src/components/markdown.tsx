import { memo } from 'react';
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
  const pattern = highlighter(highlight);
  return (
    <div className="markdown">
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
    </div>
  );
}
