// The HTML page of an imported file, prepared for the document window. The backend wrote the page, but it
// still carries the text of the imported file, so it is parsed as untrusted markup: only the elements,
// attributes and CSS of a static page survive, pictures must be embedded data: images, and nothing can
// run, load from elsewhere or reach outside the page.

const XHTML = 'http://www.w3.org/1999/xhtml';
const MATHML = 'http://www.w3.org/1998/Math/MathML';
// Removed with everything in them.
const DROP = new Set([
  'script',
  'style',
  'template',
  'iframe',
  'frame',
  'frameset',
  'object',
  'embed',
  'applet',
  'link',
  'meta',
  'base',
  'title',
  'head',
  'noscript',
  'form',
  'input',
  'button',
  'select',
  'textarea',
  'option',
  'svg',
  'canvas',
  'audio',
  'video',
  'source',
  'track',
  'portal',
  'dialog',
  'slot',
]);
const ELEMENTS = new Set([
  'a',
  'abbr',
  'article',
  'aside',
  'b',
  'bdi',
  'bdo',
  'big',
  'blockquote',
  'br',
  'caption',
  'center',
  'cite',
  'code',
  'col',
  'colgroup',
  'dd',
  'del',
  'details',
  'dfn',
  'div',
  'dl',
  'dt',
  'em',
  'figcaption',
  'figure',
  'font',
  'footer',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'header',
  'hr',
  'i',
  'img',
  'ins',
  'kbd',
  'li',
  'main',
  'mark',
  'nav',
  'ol',
  'p',
  'pre',
  'q',
  'rp',
  'rt',
  'ruby',
  's',
  'samp',
  'section',
  'small',
  'span',
  'strike',
  'strong',
  'sub',
  'summary',
  'sup',
  'table',
  'tbody',
  'td',
  'tfoot',
  'th',
  'thead',
  'time',
  'tr',
  'tt',
  'u',
  'ul',
  'var',
  'wbr',
]);
const MATH_ELEMENTS = new Set([
  'math',
  'mrow',
  'mi',
  'mn',
  'mo',
  'ms',
  'mtext',
  'mspace',
  'msup',
  'msub',
  'msubsup',
  'mfrac',
  'msqrt',
  'mroot',
  'munder',
  'mover',
  'munderover',
  'mmultiscripts',
  'mprescripts',
  'none',
  'mtable',
  'mtr',
  'mtd',
  'mstyle',
  'mpadded',
  'mphantom',
  'semantics',
  'annotation',
]);
const ATTRIBUTES = new Set([
  'style',
  'title',
  'lang',
  'dir',
  'id',
  'class',
  'align',
  'valign',
  'width',
  'height',
  'colspan',
  'rowspan',
  'span',
  'start',
  'type',
  'reversed',
  'value',
  'alt',
  'src',
  'href',
  'bgcolor',
  'color',
  'face',
  'size',
  'border',
  'cellpadding',
  'cellspacing',
  'headers',
  'scope',
  'abbr',
  'datetime',
]);
const MATH_ATTRIBUTES = new Set([
  'alttext',
  'display',
  'displaystyle',
  'scriptlevel',
  'mathvariant',
  'accent',
  'accentunder',
  'fence',
  'separator',
  'stretchy',
  'symmetric',
  'largeop',
  'movablelimits',
  'lspace',
  'rspace',
  'linethickness',
  'columnalign',
  'rowalign',
  'columnspan',
  'rowspan',
  'minsize',
  'maxsize',
  'width',
  'height',
  'depth',
  'voffset',
  'form',
]);
const PICTURE =
  /^data:image\/(?:png|jpeg|gif|webp|bmp|avif|svg\+xml|x-icon|vnd\.microsoft\.icon);base64,[a-z0-9+/=\s]*$/i;
const LINK = /^(?:https?:|mailto:|#)/i;
// The page's CSS is read by the browser's own CSS parser and checked in the text the parser writes back, where comments
// are gone (`:/**/host` is `:host`) and escapes are written out (`u\72l(` is `url(`); the CSS used is that text, the
// one that was checked. A value that loads or runs something goes, one declaration at a time; a backslash still there
// (a custom property keeps its own text, escapes included) goes too.
const LOADS =
  /url\s*\(|image-set|image\s*\(|cross-fade|src\s*\(|expression\s*\(|javascript:|behavior\s*:|-moz-binding|\\/i;
// Rules that reach the host element (:host, or @scope and :scope with no scope of their own), load fonts or import
// stylesheets go whole.
const REACHES_OUT = /:host|:scope|::part/i;
const DROPPED = /^@(?:import|font-face|scope)\b/i;

// Takes from a rule or an element the declarations whose value loads something; false when what is left still would
// (a shorthand with var() is written out whole, and goes with its longhands, which show no value of their own).
function cleanDeclarations(style: CSSStyleDeclaration) {
  for (const name of [...style])
    if (LOADS.test(style.getPropertyValue(name))) style.removeProperty(name);
  if (LOADS.test(style.cssText))
    for (const name of [...style]) if (!style.getPropertyValue(name)) style.removeProperty(name);
  return !LOADS.test(style.cssText);
}

function cleanRules(rules: CSSRuleList, remove: (index: number) => void) {
  for (let index = rules.length - 1; index >= 0; index--) {
    const rule = rules[index];
    const { style, selectorText, cssRules } = rule as CSSRule & {
      style?: CSSStyleDeclaration;
      selectorText?: string;
      cssRules?: CSSRuleList;
    };
    const keep =
      !DROPPED.test(rule.cssText) &&
      !REACHES_OUT.test(selectorText ?? '') &&
      (style ? cleanDeclarations(style) : !!cssRules || !LOADS.test(rule.cssText));
    if (!keep) remove(index);
    else if (cssRules)
      cleanRules(
        cssRules,
        rule instanceof CSSKeyframesRule
          ? (child) => rule.deleteRule((cssRules[child] as CSSKeyframeRule).keyText)
          : (child) => (rule as CSSGroupingRule).deleteRule(child),
      );
  }
}

// A stylesheet of the page, rule by rule.
function safeSheet(text: string) {
  const sheet = new CSSStyleSheet();
  try {
    sheet.replaceSync(text); // @import rules are left out here already
  } catch {
    return '';
  }
  cleanRules(sheet.cssRules, (index) => sheet.deleteRule(index));
  return [...sheet.cssRules].map((rule) => rule.cssText).join('\n');
}

// An element's own style; the many elements of a table that share one are read once.
function cleanStyle(element: Element, seen: Map<string, string>) {
  const value = element.getAttribute('style')!;
  let css = seen.get(value);
  if (css === undefined) {
    const style = (element as Element & Partial<ElementCSSInlineStyle>).style;
    css = style && cleanDeclarations(style) ? style.cssText : '';
    seen.set(value, css);
  }
  if (css) element.setAttribute('style', css);
  else element.removeAttribute('style');
}

function clean(parent: Node, seen: Map<string, string>) {
  for (const node of [...parent.childNodes]) {
    if (node.nodeType === Node.COMMENT_NODE || node.nodeType === Node.PROCESSING_INSTRUCTION_NODE) {
      node.remove();
      continue;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) continue;
    const element = node as Element;
    const name = element.localName;
    const math = element.namespaceURI === MATHML;
    // SVG and other foreign markup go too: the converters write pictures as <img> only.
    if (math ? !MATH_ELEMENTS.has(name) : element.namespaceURI !== XHTML || DROP.has(name)) {
      element.remove();
      continue;
    }
    if (!math && !ELEMENTS.has(name)) {
      // An element of no known use keeps its text, as browsers show unknown tags.
      clean(element, seen);
      element.replaceWith(...element.childNodes);
      continue;
    }
    for (const { name: attribute, value } of [...element.attributes]) {
      const keep =
        attribute.startsWith('data-') ||
        ((math ? MATH_ATTRIBUTES : ATTRIBUTES).has(attribute) &&
          (attribute !== 'href' || LINK.test(value.trim())) &&
          (attribute !== 'src' || (name === 'img' && PICTURE.test(value))));
      if (!keep) element.removeAttribute(attribute);
    }
    if (element.hasAttribute('style')) cleanStyle(element, seen);
    if (name === 'img' && !element.hasAttribute('src')) {
      // A picture from elsewhere is not loaded; its description stays.
      const marker = element.ownerDocument.createElement('span');
      marker.className = 'missing';
      marker.textContent = `[그림${element.getAttribute('alt') ? `: ${element.getAttribute('alt')}` : ''}]`;
      element.replaceWith(marker);
      continue;
    }
    // The search words are marked by the app; a page's own marks would pose as matches.
    element.classList.remove('search-hit', 'current');
    if (!element.classList.length) element.removeAttribute('class');
    if (name === 'a' && element.hasAttribute('href') && !element.hasAttribute('title'))
      element.setAttribute('title', element.getAttribute('href')!);
    clean(element, seen);
  }
}

// The page's own stylesheets and its body, cleaned, in a document of their own until they are shown.
export function sanitize(html: string) {
  const parsed = new DOMParser().parseFromString(html, 'text/html');
  const styles = [...parsed.querySelectorAll('style')]
    .map((style) => safeSheet(style.textContent ?? ''))
    .filter(Boolean);
  clean(parsed.body, new Map());
  return { styles, body: parsed.body };
}

// Marks the search words in the page's text, as the Markdown view does; equations are left alone.
export function markTerms(root: Node, pattern: RegExp) {
  const walker = root.ownerDocument!.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const texts: Text[] = [];
  while (walker.nextNode()) texts.push(walker.currentNode as Text);
  for (const text of texts) {
    const parent = text.parentElement;
    if (!parent || parent.namespaceURI === MATHML || parent.closest('style')) continue;
    const found = [...text.data.matchAll(pattern)];
    if (!found.length) continue;
    const fragment = root.ownerDocument!.createDocumentFragment();
    let last = 0;
    for (const match of found) {
      if (!match[0]) continue;
      if (match.index > last) fragment.append(text.data.slice(last, match.index));
      const mark = root.ownerDocument!.createElement('mark');
      mark.className = 'search-hit';
      mark.textContent = match[0];
      fragment.append(mark);
      last = match.index + match[0].length;
    }
    if (last < text.data.length) fragment.append(text.data.slice(last));
    text.replaceWith(fragment);
  }
}

const size = (bytes: number) =>
  bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)}KB` : `${(bytes / 1024 / 1024).toFixed(1)}MB`;

// The raw HTML as text for reading: a line per block element, and each picture's base64 data cut to its
// beginning and size, so a page of large pictures stays readable. The download keeps the page as stored.
export const readableSource = (html: string) =>
  html
    .replace(
      /(data:[\w.+/-]+;base64,)([A-Za-z0-9+/=]{240,})/g,
      (_, head: string, data: string) =>
        `${head}${data.slice(0, 64)}…(그림 데이터 ${size((data.length * 3) / 4)} 줄임)`,
    )
    .replace(
      /(<\/(?:p|h[1-6]|li|tr|thead|tbody|table|colgroup|ul|ol|div|section|figure|style|title)>|<(?:head|body)>)(?=<)/g,
      '$1\n',
    );
