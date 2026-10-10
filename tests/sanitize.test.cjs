const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const ts = require('typescript');

// src/lib/html-document.ts, which prepares the HTML page of an imported file for the document window, run here in a
// DOM of jsdom against pages written to attack it. Its markup is parsed by jsdom's HTML parser (parse5), which parses
// as browsers do; its CSS goes through jsdom's CSS objects, so the CSS cases check the rules the sanitizer applies to
// whatever a parser writes back, while tests/office.spec.ts checks the same page in Chromium itself. jsdom loads no
// resource and runs no script here, so nothing below reaches the network.
const { window } = new JSDOM('<!doctype html><html><body></body></html>');
const DOM = {
  DOMParser: window.DOMParser,
  Node: window.Node,
  NodeFilter: window.NodeFilter,
  CSSStyleSheet: window.CSSStyleSheet,
  CSSKeyframesRule: window.CSSKeyframesRule,
};

// The module as CommonJS, with the page's globals it reads given as the function's parameters.
function load(file) {
  const source = fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const module = { exports: {} };
  new Function('module', 'exports', 'require', ...Object.keys(DOM), outputText)(
    module,
    module.exports,
    require,
    ...Object.values(DOM),
  );
  return module.exports;
}
const { sanitize } = load('src/lib/html-document.ts');

const PNG =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
const EVIL = 'https://evil.example';

// Pages that try to run something, load something or reach outside the page. Each is a whole page or a part of one,
// as an imported file's HTML may be written.
const CORPUS = {
  // Script elements and the ways around a filter that looks for them.
  'script element': '<script>alert(1)</script>',
  'script with a src': `<SCRIPT SRC=${EVIL}/x.js></SCRIPT>`,
  'script with a data: src': '<script/xss src="data:text/javascript,alert(1)"></script>',
  'module script': `<script type="module">import('${EVIL}/x.js')</script>`,
  'script in the head': '<html><head><script>alert(1)</script></head><body><p>x</p></body></html>',
  'script split by another': '<scr<script>ipt>alert(1)</scr</script>ipt>',
  'script after a broken attribute': '<img """><script>alert(1)</script>">',
  'script after an attribute breakout': '"><script>alert(1)</script>',
  'script in svg': '<svg><script>alert(1)</script></svg>',
  'doubled angle brackets': '<<script>script>alert(1)<</script>/script>',
  // Event handler attributes.
  'img onerror': '<img src=x onerror=alert(1)>',
  'allowed picture with onload': `<img src="data:image/png;base64,${PNG}" onload="alert(1)" alt="점">`,
  'body onload': '<body onload=alert(1)><p>x</p></body>',
  'div onclick': '<div onclick="alert(1)">x</div>',
  'mixed case handler': '<div OnMouseOver="alert(1)">x</div>',
  'handler after a slash': '<p/onmouseover=alert(1)>x</p>',
  'handler with whitespace': '<div onclick\n\t=\n"alert(1)">x</div>',
  'focus handler with autofocus': '<a href="#x" onfocus=alert(1) autofocus tabindex=1>x</a>',
  'details ontoggle': '<details open ontoggle=alert(1)><summary>x</summary></details>',
  'unknown element with a handler': '<marquee onstart=alert(1)>x</marquee>',
  'handler with a NUL byte in its name': '<img src=x on\u0000error=alert(1)>',
  'source onerror': '<video><source onerror=alert(1)></video>',
  // javascript:, vbscript: and data: addresses where an address is read.
  'javascript: link': '<a href="javascript:alert(1)">x</a>',
  'mixed case javascript: link': '<a href="JaVaScRiPt:alert(1)">x</a>',
  'javascript: link after spaces': '<a href="  javascript:alert(1)">x</a>',
  'javascript: link split by a tab': '<a href="java&#x09;script:alert(1)">x</a>',
  'javascript: link split by a NUL': '<a href="java\u0000script:alert(1)">x</a>',
  'javascript: link as entities':
    '<a href="&#106;&#97;&#118;&#97;&#115;&#99;&#114;&#105;&#112;&#116;&#58;alert(1)">x</a>',
  'javascript: link as hex entities': '<a href="&#x6A;avascript&colon;alert(1)">x</a>',
  'vbscript: link': '<a href="vbscript:msgbox(1)">x</a>',
  'data: HTML link': '<a href="data:text/html,<script>alert(1)</script>">x</a>',
  'javascript: picture': '<img src="javascript:alert(1)">',
  'data: HTML picture': '<img src="data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==">',
  'data: SVG picture not in base64': '<img src="data:image/svg+xml,<svg onload=alert(1)>">',
  'xlink:href in svg': '<svg><a xlink:href="javascript:alert(1)"><text>x</text></a></svg>',
  'form action and formaction':
    '<form action="javascript:alert(1)"><button formaction="javascript:alert(1)">x</button></form>',
  'iframe srcdoc': '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
  'href on MathML': '<math href="javascript:alert(1)"><mi>x</mi></math>',
  'xlink:href on MathML': '<math><mi xlink:href="javascript:alert(1)">x</mi></math>',
  // SVG and MathML, and markup that changes meaning between parses (mutation XSS).
  'svg onload': '<svg onload=alert(1)>',
  'svg animate onbegin': '<svg><animate onbegin=alert(1) attributeName=x dur=1s></animate></svg>',
  'svg foreignObject': '<svg><foreignObject><img src=x onerror=alert(1)></foreignObject></svg>',
  'MathML maction': `<math><maction actiontype="statusline#${EVIL}">x</maction></math>`,
  'MathML annotation-xml as HTML':
    '<math><annotation-xml encoding="text/html"><img src=x onerror=alert(1)></annotation-xml></math>',
  'MathML mglyph style': '<math><mtext><table><mglyph><style><img src=x onerror=alert(1)>',
  'MathML textarea mutation':
    '<math><mi><mglyph><svg><mtext><textarea><path id="</textarea><img onerror=alert(1) src=1>">',
  'svg style mutation': '<svg></p><style><a id="</style><img src=1 onerror=alert(1)>">',
  'noscript mutation': '<noscript><p title="</noscript><img src=x onerror=alert(1)>"></noscript>',
  'noembed mutation': '<noembed><img title="</noembed><img src=x onerror=alert(1)>"></noembed>',
  // Frames, plugins and elements that reach outside the page.
  iframe: `<iframe src="${EVIL}/"></iframe>`,
  'object data': '<object data="javascript:alert(1)"></object>',
  embed: `<embed src="${EVIL}/x.swf">`,
  frameset: '<frameset><frame src="javascript:alert(1)"></frameset>',
  base: `<base href="${EVIL}/"><a href="x">x</a>`,
  'meta refresh': '<meta http-equiv="refresh" content="0;url=javascript:alert(1)">',
  'link import': `<link rel="import" href="${EVIL}/x.html">`,
  'link stylesheet': `<link rel="stylesheet" href="${EVIL}/x.css">`,
  portal: `<portal src="${EVIL}/"></portal>`,
  template: '<template><img src=x onerror=alert(1)></template>',
  'form controls':
    '<input type="image" src="x" onerror=alert(1)><textarea>x</textarea><select><option>x',
  // CSS that runs or loads something, inline and in the page's stylesheets.
  'CSS expression()': '<div style="width: expression(alert(1))">x</div>',
  'CSS url(javascript:)': '<div style="background-image: url(javascript:alert(1))">x</div>',
  'CSS url() of an image': `<div style="background: url('${EVIL}/t.png')">x</div>`,
  'CSS behavior': '<div style="behavior: url(x.htc)">x</div>',
  'CSS -moz-binding': `<div style="-moz-binding: url('${EVIL}/x.xml#x')">x</div>`,
  'CSS image-set()': `<div style="background-image: image-set('${EVIL}/x.png' 1x)">x</div>`,
  'CSS custom property with url()': `<div style="--x: url(${EVIL}/x.png); background-image: var(--x)">x</div>`,
  'stylesheet @import': `<style>@import url('${EVIL}/x.css'); p { color: red }</style><p>x</p>`,
  'stylesheet @import of a string': `<style>@import "${EVIL}/x.css"; p { color: red }</style><p>x</p>`,
  'stylesheet url()': `<style>body { background: url(${EVIL}/x.png) }</style>`,
  'stylesheet @font-face': `<style>@font-face { font-family: x; src: url(${EVIL}/f.woff) }</style>`,
  'stylesheet escaped url()': `<style>p { background: u\\72l(${EVIL}/x.png) }</style><p>x</p>`,
  'stylesheet url() split by a comment': `<style>p { background: ur/**/l(${EVIL}/x.png) }</style>`,
  'stylesheet list-style-image': `<style>li { list-style-image: url(${EVIL}/x.png) }</style>`,
  'stylesheet url() in @media': `<style>@media screen { p { background: url(${EVIL}/x.png) } }</style>`,
  'stylesheet url() in @keyframes': `<style>@keyframes k { from { background: url(${EVIL}/x.png) } }</style>`,
  'stylesheet :host': '<style>:host { position: fixed; inset: 0 }</style>',
  // Encodings: entities and UTF-7 stay text, and the page's own charset has no say over a string already decoded.
  'escaped script': '&lt;script&gt;alert(1)&lt;/script&gt;',
  'UTF-7 script': '<meta charset="utf-7">+ADw-script+AD4-alert(1)+ADw-/script+AD4-',
  // Nested and malformed markup, comments and processing instructions.
  'unclosed tags': '<div><p>unclosed <b>bold <i>both</div><img src=x onerror=alert(1)',
  'handler in a comment': '<!--<img src=x onerror=alert(1)>--><p>x</p>',
  'comment in the body': '<p>x<!--<img src=x onerror=alert(1)>--></p>',
  'conditional comment': '<p><!--[if IE]><script>alert(1)</script><![endif]--></p>',
  'CDATA in HTML': '<![CDATA[<img src=x onerror=alert(1)>]]><p>x</p>',
  'processing instruction': `<?xml-stylesheet href="${EVIL}/x.xsl"?><p>x</p>`,
  'NUL in a tag name': '<scr\u0000ipt>alert(1)</scr\u0000ipt>',
  'raw text in xmp': '<xmp><img src=x onerror=alert(1)></xmp>',
  'title in the body': '<title><img src=x onerror=alert(1)></title>',
  // Pictures from elsewhere: the app loads none.
  'external picture': `<img src="${EVIL}/pixel.png" alt="추적">`,
  'protocol-relative picture': '<img src="//evil.example/pixel.png">',
  'srcset beside an embedded picture': `<img srcset="${EVIL}/a.png 1x" src="data:image/png;base64,${PNG}">`,
  'picture element sources': `<picture><source srcset="${EVIL}/a.png"><img src="data:image/png;base64,${PNG}"></picture>`,
  'image element': `<image src="${EVIL}/x.png">`,
  'table background': `<table background="${EVIL}/x.png"><tr><td background="${EVIL}/y.png">x</td></tr></table>`,
  'video poster': `<video poster="${EVIL}/x.png"></video>`,
};

// Elements that run, load, submit or reach outside the page; none may be in a cleaned page.
const ACTIVE = new Set(
  'script style link meta base iframe frame frameset object embed applet portal form input button textarea select option template noscript noembed svg canvas audio video source track image picture maction annotation-xml'.split(
    ' ',
  ),
);
// Attributes that load or run something, or send the page elsewhere.
const ACTIVE_ATTRIBUTES = new Set(
  'srcset srcdoc action formaction background poster data codebase classid archive ping lowsrc dynsrc longdesc usemap cite profile manifest xlink:href xmlns:xlink http-equiv content target'.split(
    ' ',
  ),
);
// What loads or runs in CSS, after comments and escapes.
const ACTIVE_CSS =
  /url\s*\(|@import|expression\s*\(|javascript:|behavior\s*:|-moz-binding|image-set|@font-face|:host|\\/i;
// A scheme that runs something, with what browsers ignore in a URL (spaces, tabs, line breaks, NUL) taken out.
const RUNS = /javascript:|vbscript:|data:(?!image\/)/i;
const bare = (value) => value.replace(/[\u0000-\u0020\u007f\ufffd]/g, '');

// Everything that makes a cleaned page unsafe, as messages: empty for a safe one. The screens show the body's children
// (src/components/html-document.tsx): the body's own attributes never reach the page.
function problems({ styles, body }) {
  const found = [];
  for (const element of body.querySelectorAll('*')) {
    const name = element.localName;
    if (ACTIVE.has(name)) found.push(`element <${name}>`);
    if (element.namespaceURI === 'http://www.w3.org/2000/svg') found.push(`SVG <${name}>`);
    for (const { name: attribute, value } of element.attributes) {
      const where = `<${name} ${attribute}="${value}">`;
      if (/^on/i.test(attribute) || /[^\w:-]/.test(attribute)) found.push(`handler ${where}`);
      if (ACTIVE_ATTRIBUTES.has(attribute)) found.push(`attribute ${where}`);
      if (RUNS.test(bare(value))) found.push(`script address ${where}`);
      if (attribute === 'href' && !/^(?:https?:|mailto:|#)/i.test(value.trim()))
        found.push(`link ${where}`);
      if (attribute === 'src' && (name !== 'img' || !/^data:image\//i.test(value)))
        found.push(`resource ${where}`);
      if (attribute === 'style' && ACTIVE_CSS.test(value)) found.push(`style ${where}`);
    }
  }
  const walker = body.ownerDocument.createTreeWalker(body, DOM.NodeFilter.SHOW_COMMENT);
  if (walker.nextNode()) found.push('comment');
  for (const sheet of styles) if (ACTIVE_CSS.test(sheet)) found.push(`stylesheet ${sheet}`);
  return found;
}

test(`no page of the corpus (${Object.keys(CORPUS).length}) keeps anything that runs, loads or leaves the page`, () => {
  assert.ok(Object.keys(CORPUS).length >= 40);
  for (const [name, html] of Object.entries(CORPUS))
    assert.deepEqual(problems(sanitize(html)), [], `${name}: ${html}`);
});

test('the corpus is really dangerous before it is cleaned', () => {
  // The same checks over the page as parsed, uncleaned: each case must trip them, or it tests nothing. The cases the
  // HTML parser already disarms stay in the corpus, so that the sanitizer keeps them disarmed.
  const raw = (html) => {
    const document = new DOM.DOMParser().parseFromString(html, 'text/html');
    const styles = [...document.querySelectorAll('style')].map((style) => style.textContent);
    const body = document.createElement('div');
    body.append(...document.head.childNodes, ...document.body.childNodes);
    return problems({ styles, body });
  };
  const inert = [
    // Text, not markup.
    'escaped script',
    'raw text in xmp',
    'title in the body',
    // Tag names that are no script (scr<script, scr\uFFFDipt) and a last tag the end of the page cuts off.
    'script split by another',
    'NUL in a tag name',
    'unclosed tags',
    // Comments, which the parser puts before the page.
    'handler in a comment',
    'CDATA in HTML',
    'processing instruction',
    // The body's own attributes, which the screens never take, and an SVG picture, which runs nothing in an <img>.
    'body onload',
    'data: SVG picture not in base64',
  ];
  for (const [name, html] of Object.entries(CORPUS))
    if (!inert.includes(name)) assert.notDeepEqual(raw(html), [], name);
});

test('what runs or loads goes with everything in it', () => {
  const { body } = sanitize(
    `<p>남는 글</p><script>alert(1)</script><style>p { color: red }</style><template><p>틀</p></template>
<iframe src="${EVIL}/">대신 보일 글</iframe><noscript><p>스크립트 없이</p></noscript><object>대체</object>
<form><p>양식 안</p></form><textarea>입력란</textarea><svg><text>그림 글</text></svg><title>제목</title>`,
  );
  assert.equal(body.textContent.trim(), '남는 글');
});

test('text that only looks like markup stays text', () => {
  const { body } = sanitize('&lt;script&gt;alert(1)&lt;/script&gt; +ADw-script+AD4-');
  assert.equal(body.textContent, '<script>alert(1)</script> +ADw-script+AD4-');
  assert.equal(body.children.length, 0);
  // An unknown element keeps its text, as browsers show unknown tags; raw text stays text.
  assert.equal(sanitize('<marquee onstart=alert(1)>안녕</marquee>').body.innerHTML, '안녕');
  assert.equal(
    sanitize('<xmp><img src=x onerror=alert(1)></xmp>').body.textContent,
    '<img src=x onerror=alert(1)>',
  );
});

test('a picture from elsewhere is not loaded: its description stays in its place', () => {
  const { body } = sanitize(
    `<p>앞<img src="${EVIL}/pixel.png" alt="추적">뒤</p><p><img src="//evil.example/x.png"></p>`,
  );
  assert.equal(body.querySelectorAll('img').length, 0);
  assert.deepEqual(
    [...body.querySelectorAll('span.missing')].map((marker) => marker.textContent),
    ['[그림: 추적]', '[그림]'],
  );
  assert.equal(body.querySelector('p').textContent, '앞[그림: 추적]뒤');
});

test('an embedded picture stays, without its handler or its other sources', () => {
  const { body } = sanitize(
    `<img src="data:image/png;base64,${PNG}" onload="alert(1)" srcset="${EVIL}/a.png 1x" alt="빨간 점" width="1" height="1">`,
  );
  const image = body.querySelector('img');
  assert.equal(image.getAttribute('src'), `data:image/png;base64,${PNG}`);
  assert.deepEqual(image.getAttributeNames().sort(), ['alt', 'height', 'src', 'width']);
});

test('a static page keeps its structure, links, pictures, equations and styles', () => {
  const page = `<!doctype html><html><head><title>보고서</title>
<style>h1 { color: #2f5496 } td { background-color: #fff2cc } @media print { p { margin: 0 } }</style></head>
<body>
<h1 id="top">개요</h1><h2>세부</h2>
<p style="color: #c00000; font-weight: bold">강조된 <b>문단</b>과 <i>기울임</i>,
<a href="#notes">각주로</a>, <a href="https://example.com/wiki">위키</a>, <a href="mailto:someone@example.com">메일</a></p>
<ul><li>하나</li><li>둘<ol start="3"><li>둘-하나</li></ol></li></ul>
<table border="1"><caption>표</caption><thead><tr><th scope="col">이름</th><th>값</th></tr></thead>
<tbody><tr><td colspan="2">셀</td></tr></tbody></table>
<figure><img src="data:image/png;base64,${PNG}" alt="빨간 점" width="1" height="1"><figcaption>그림 1</figcaption></figure>
<math display="block"><msup><mi>x</mi><mn>2</mn></msup></math>
<p id="notes" data-note="1" class="note search-hit">각주 <mark class="current">표시</mark></p>
<pre><code>const x = 1;</code></pre>
<details><summary>더 보기</summary><p>숨긴 내용</p></details>
</body></html>`;
  const { styles, body } = sanitize(page);
  assert.deepEqual(problems({ styles, body }), []);
  assert.equal(body.querySelector('h1#top').textContent, '개요');
  assert.equal(body.querySelector('h2').textContent, '세부');
  const strong = body.querySelector('p[style]');
  assert.match(strong.getAttribute('style'), /color/);
  assert.match(strong.getAttribute('style'), /font-weight: bold/);
  // Links stay, each showing its address as its title: within the page, to the web, by mail.
  assert.deepEqual(
    [...body.querySelectorAll('a')].map((link) => [link.getAttribute('href'), link.title]),
    [
      ['#notes', '#notes'],
      ['https://example.com/wiki', 'https://example.com/wiki'],
      ['mailto:someone@example.com', 'mailto:someone@example.com'],
    ],
  );
  assert.equal(body.querySelectorAll('ul > li').length, 2);
  assert.equal(body.querySelector('ol').getAttribute('start'), '3');
  assert.equal(body.querySelector('table caption').textContent, '표');
  assert.equal(body.querySelector('th').getAttribute('scope'), 'col');
  assert.equal(body.querySelector('td').getAttribute('colspan'), '2');
  assert.equal(body.querySelector('table').getAttribute('border'), '1');
  const image = body.querySelector('figure img');
  assert.equal(image.getAttribute('src'), `data:image/png;base64,${PNG}`);
  assert.equal(image.alt, '빨간 점');
  assert.equal(body.querySelector('figcaption').textContent, '그림 1');
  assert.equal(body.querySelector('math').getAttribute('display'), 'block');
  assert.equal(body.querySelector('math msup mi').textContent, 'x');
  // The page's own marks would pose as the app's search matches: those two classes go, the rest stays.
  const notes = body.querySelector('#notes');
  assert.equal(notes.dataset.note, '1');
  assert.equal(notes.className, 'note');
  assert.equal(body.querySelector('mark').hasAttribute('class'), false);
  assert.equal(body.querySelector('pre > code').textContent, 'const x = 1;');
  assert.equal(body.querySelector('details > summary').textContent, '더 보기');
  // The page's stylesheet, rule by rule; the title and the stylesheet's element itself do not reach the body.
  assert.equal(body.querySelector('title, style'), null);
  const css = styles.join('\n');
  assert.match(css, /h1\s*\{[^}]*color/);
  assert.match(css, /td\s*\{[^}]*background-color/);
  assert.match(css, /@media print/);
});

test('a stylesheet loses what loads, rule by rule and declaration by declaration, and keeps the rest', () => {
  const { styles } = sanitize(
    `<style>@import url('${EVIL}/x.css'); @font-face { font-family: x; src: url(${EVIL}/f.woff) }
p { color: red; background: url(${EVIL}/x.png) } :host { position: fixed } h2 { margin-top: 4px }</style>`,
  );
  const css = styles.join('\n');
  assert.doesNotMatch(css, /evil\.example|@import|@font-face|:host|url\(/);
  assert.match(css, /p\s*\{[^}]*color: red/);
  assert.match(css, /h2\s*\{[^}]*margin-top: 4px/);
});
