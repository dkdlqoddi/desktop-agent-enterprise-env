// Reads the ads, products and results a search page shows, for the web records of the desktop app (Playwright's
// page.evaluate) and the Android app (WebView.evaluateJavascript). It only reads the page and marks the elements it
// reported (data-pa-capture), so the click watcher (click-watch.js) can tell an ad the user clicked; it never clicks,
// submits, scrolls or navigates. Site markup changes without notice: each site's selectors come first, and a generic
// pass finds ads by their label ("광고", "Sponsored", ...) when the selectors find none. The result is plain JSON.
(options) => {
  const settings = Object.assign({ site: 'generic', maxItems: 80, token: '' }, options || {});
  const AD_LABEL =
    /^(광고|AD|Ad|ad|Sponsored|SPONSORED|Sponsored result|스폰서|후원|파워링크|파워컨텐츠|비즈사이트|Anzeige|広告|スポンサー)$/;
  const PRICE = /(?:₩|\$|US\$|¥|€|£)\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:원|円)/;
  // A robot check (CAPTCHA) the page shows: its widget, or Google's and Amazon's check pages, block a capture outright.
  // Its words alone do only on a known site's page with nothing to record: a search for "captcha" shows them among
  // results, and any other page may be about them.
  const CHALLENGE = [
    '.g-recaptcha',
    '.h-captcha',
    '.cf-turnstile',
    'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
    'iframe[src*="challenges.cloudflare.com"]',
    'form[action*="validateCaptcha"]',
    '#captcha-form',
  ];
  const BLOCKED = [
    /unusual traffic/i,
    /not a robot/i,
    /robot check/i,
    /captcha/i,
    /자동입력\s*방지/,
    /로봇이\s*아닙니다/,
    /비정상적인\s*(접근|트래픽)/,
    /access denied/i,
  ];
  const SITES = {
    coupang: {
      cards: [
        'li.search-product',
        '#product-list > li',
        'ul#productList > li',
        'li[class*="ProductUnit"]',
      ],
      title: ['.name', '[class*="productName"]', '[class*="ProductUnit_productName"]', 'img[alt]'],
      price: [
        '.price-value',
        '[class*="PriceArea"] strong',
        '[class*="price"] strong',
        '[class*="Price"]',
      ],
      link: ['a[href*="/vp/products/"]', 'a[href]'],
      adMarks: [
        '.ad-badge',
        '.search-product__ad-badge',
        '[class*="AdMark"]',
        '[class*="ad-badge"]',
      ],
      plain: 'product',
    },
    amazon: {
      cards: ['div[data-component-type="s-search-result"]', 'div[data-asin][data-component-type]'],
      title: ['h2 a span', 'h2 span', 'h2'],
      price: ['.a-price .a-offscreen', '.a-price'],
      link: ['h2 a[href]', 'a.a-link-normal[href*="/dp/"]', 'a[href]'],
      adMarks: [
        '.puis-sponsored-label-text',
        '.s-sponsored-label-text',
        '[aria-label="Sponsored"]',
        '[data-component-type="sp-sponsored-result"]',
      ],
      plain: 'product',
    },
    naver: {
      cards: [
        '#power_link_body li',
        '.power_link li',
        '[class*="powerlink"] li',
        '#ad_section li',
        '.ad_section li',
        '.lst_type > li',
        '[class*="product_item"]',
        '.shop_list li',
      ],
      title: [
        '.lnk_head',
        '.lnk_tit',
        'a.tit',
        '.tit_area a',
        '[class*="product_title"]',
        '.title',
        'a',
      ],
      price: ['[class*="price"]', '.price'],
      link: ['a.lnk_head', 'a.lnk_tit', '[class*="product_title"] a', 'a[href]'],
      displayUrl: ['.lnk_url', '.url_area', '.url', 'cite'],
      adMarks: [
        '.ico_ad',
        '.ad_badge',
        '[class*="ad_label"]',
        '[class*="adBadge"]',
        '[class*="ad_badge"]',
      ],
      adSections: [
        '#power_link_body',
        '.power_link',
        '[class*="powerlink"]',
        '#ad_section',
        '.ad_section',
      ],
      plain: 'result',
    },
    google: {
      cards: [
        '#tads [data-text-ad]',
        '#bottomads [data-text-ad]',
        '[data-text-ad]',
        '.commercial-unit-desktop-top .pla-unit',
        '.pla-unit',
        '#rso .g',
      ],
      title: ['[role="heading"]', 'h3', 'a'],
      price: ['[class*="price"]'],
      link: ['a[data-rw]', 'a[href]'],
      displayUrl: ['cite', '[data-dtld]', '.x2VHCd'],
      adMarks: [],
      adSections: [
        '#tads',
        '#bottomads',
        '#tvcap',
        '.commercial-unit-desktop-top',
        '.cu-container',
      ],
      plain: 'result',
    },
  };
  const rule = SITES[settings.site] || null;
  const GENERIC_TITLE = ['h1', 'h2', 'h3', '[role="heading"]', 'a'];
  const PRICE_ALONE = new RegExp(`^(?:${PRICE.source})$`);

  const clean = (value, limit = 300) =>
    String(value || '')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, limit);
  const textOf = (element) => clean(element && (element.innerText || element.textContent));
  // querySelectorAll that answers no elements for a selector this browser does not know.
  const all = (root, selector) => {
    try {
      return Array.from(root.querySelectorAll(selector));
    } catch {
      return [];
    }
  };
  const first = (root, selectors) => {
    for (const selector of selectors || []) {
      const [found] = all(root, selector);
      if (found) return found;
    }
    return null;
  };
  const absolute = (href) => {
    try {
      const url = new URL(href, location.href);
      return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : '';
    } catch {
      return '';
    }
  };
  // An SVG link names its address in href or, in older markup, xlink:href.
  const hrefOf = (link) =>
    link ? absolute(link.getAttribute('href') || link.getAttribute('xlink:href') || '') : '';
  // The words of a title without what the generic pass found it by: the ad label and a price in elements of their own.
  const bareTitle = (node) => {
    const copy = node.cloneNode(true);
    for (const leaf of Array.from(copy.querySelectorAll('*'))) {
      const words = clean(leaf.textContent);
      if (leaf.children.length === 0 && (AD_LABEL.test(words) || PRICE_ALONE.test(words)))
        leaf.remove();
    }
    return clean(copy.textContent, 200);
  };
  const shown = (element) => {
    const box = element.getBoundingClientRect();
    if (box.width < 2 || box.height < 2) return false;
    const style = getComputedStyle(element);
    return style.visibility !== 'hidden' && style.display !== 'none';
  };
  // The badge of a check that asks nothing: reCAPTCHA v3 and the invisible v2 put one on every page of a site that
  // uses them, and a page with it is read like any other.
  const badge = (element) =>
    Boolean(element.closest('.grecaptcha-badge')) ||
    /[?&]size=invisible\b/.test(element.getAttribute('src') || '');
  const labelled = (root) => {
    if (AD_LABEL.test(clean(root.getAttribute && root.getAttribute('aria-label')))) return true;
    for (const node of root.querySelectorAll(
      'span, em, i, div, p, strong, b, small, mark, a, label',
    )) {
      if (node.children.length === 0 && AD_LABEL.test(textOf(node))) return true;
      if (AD_LABEL.test(clean(node.getAttribute('aria-label')))) return true;
    }
    return false;
  };
  const inSection = (element, selectors) =>
    (selectors || []).some((selector) => {
      try {
        return Boolean(element.closest(selector));
      } catch {
        return false;
      }
    });

  // A page recorded again is marked anew, under this capture's token: the marks of the one before go.
  for (const marked of all(document, '[data-pa-capture]')) {
    marked.removeAttribute('data-pa-capture');
    marked.removeAttribute('data-pa-kind');
    marked.removeAttribute('data-pa-capture-id');
  }

  const items = [];
  const seen = new Set();
  const add = (card, kind, generic = false) => {
    if (items.length >= settings.maxItems || card.hasAttribute('data-pa-capture') || !shown(card))
      return;
    const link =
      first(card, rule && !generic ? rule.link : []) ||
      first(card, ['a[href]']) ||
      card.closest('a[href]');
    // The site's own title first; any heading or link when its markup changed.
    const titleNode = first(card, rule && !generic ? rule.title : []) || first(card, GENERIC_TITLE);
    const title = !titleNode
      ? ''
      : generic
        ? bareTitle(titleNode)
        : clean(titleNode.getAttribute('alt') || titleNode.innerText || titleNode.textContent, 200);
    const href = hrefOf(link);
    if (!title && !href) return;
    const key = `${kind}|${href}|${title}`;
    if (seen.has(key)) return;
    seen.add(key);
    const priceNode = first(card, rule ? rule.price : []);
    const priceMatch = (priceNode ? textOf(priceNode) : textOf(card)).match(PRICE);
    const image = card.querySelector('img[src]');
    const shownUrl = first(card, (rule && rule.displayUrl) || []);
    const box = card.getBoundingClientRect();
    // ref stays the item's name when the list is sorted below: the click watcher reports it.
    const ref = items.length;
    card.setAttribute('data-pa-capture', String(ref));
    card.setAttribute('data-pa-kind', kind);
    if (settings.token) card.setAttribute('data-pa-capture-id', settings.token);
    items.push({
      ref,
      kind,
      position: ref + 1,
      title,
      price: priceMatch ? clean(priceMatch[0], 40) : '',
      displayUrl: shownUrl ? textOf(shownUrl).slice(0, 200) : '',
      link: href,
      image: image ? absolute(image.getAttribute('src')) : '',
      text: textOf(card).slice(0, 400),
      top: Math.round(box.top + scrollY),
    });
  };

  if (rule) {
    for (const selector of rule.cards) {
      for (const card of all(document, selector)) {
        const ad =
          inSection(card, rule.adSections) ||
          Boolean(first(card, rule.adMarks)) ||
          labelled(card) ||
          card.hasAttribute('data-text-ad');
        add(card, ad ? 'ad' : rule.plain);
      }
    }
  }
  // Ads the site's selectors missed: an element that reads "광고" or "Sponsored", and the nearest box around it that
  // holds a link with a title.
  if (!items.some((item) => item.kind === 'ad')) {
    const viewport = innerWidth * innerHeight;
    for (const node of document.querySelectorAll(
      'span, em, i, div, p, strong, b, small, mark, label',
    )) {
      if (items.length >= settings.maxItems) break;
      if (node.children.length !== 0 || !AD_LABEL.test(textOf(node))) continue;
      let box = node.parentElement;
      for (let depth = 0; box && depth < 6; depth += 1, box = box.parentElement) {
        const area = box.getBoundingClientRect();
        if (area.width * area.height > viewport * 0.6) break;
        const anchor = box.querySelector('a[href]');
        if (anchor && clean(anchor.innerText || anchor.textContent).length > 3) {
          add(box, 'ad', true);
          break;
        }
      }
    }
  }

  const body = textOf(document.body).slice(0, 4000);
  const challenged =
    location.pathname.startsWith('/sorry/') ||
    location.pathname.startsWith('/errors/validateCaptcha') ||
    CHALLENGE.some((selector) =>
      all(document, selector).some((element) => shown(element) && !badge(element)),
    );
  const blocked =
    challenged ||
    (Boolean(rule) && items.length === 0 && BLOCKED.some((pattern) => pattern.test(body)));
  const counts = { ad: 0, product: 0, result: 0 };
  for (const item of items) counts[item.kind] = (counts[item.kind] || 0) + 1;
  items.sort((left, right) => left.top - right.top);
  items.forEach((item, index) => {
    item.position = index + 1;
  });
  return { url: location.href, title: clean(document.title, 200), blocked, counts, items };
};
