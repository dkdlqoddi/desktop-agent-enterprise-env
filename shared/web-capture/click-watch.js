// Reports the user's own clicks on the links of the items capture.js marked (data-pa-capture), so the landing page of
// an ad they opened is recorded with it. Only trusted events count, a person's click or tap and never a script's, and
// only on a link of the item that leaves the page: a click on another part of the item (a wish-list button, an empty
// spot) or on a link to a script or to a place on the same page opens no page and is not reported. It never clicks,
// follows or opens anything itself. The desktop app installs it on every page (Playwright's add_init_script,
// reporting through the __paReportClick binding); the Android app runs it after each page load (reporting through
// the PaBridge JavaScript interface).
(() => {
  if (window.__paClickWatch) return;
  window.__paClickWatch = true;
  // An SVG link's href is an object (SVGAnimatedString): its attribute, made absolute, reads as an HTML link's does.
  const hrefOf = (anchor) => {
    const raw = anchor && (anchor.getAttribute('href') || anchor.getAttribute('xlink:href'));
    try {
      return raw ? new URL(raw, location.href).href : '';
    } catch {
      return '';
    }
  };
  // Whether following a link leaves the page: not a script (javascript:), not a place on this same page (#...).
  const leaves = (href) => {
    if (!href || /^javascript:/i.test(href)) return false;
    return !(href.includes('#') && href.split('#')[0] === location.href.split('#')[0]);
  };
  // Whether the click opens its page in a tab or window of its own rather than in this one.
  const elsewhere = (event, anchor) => {
    const base = document.querySelector('base[target]');
    const target = (
      anchor.getAttribute('target') ||
      (base && base.getAttribute('target')) ||
      ''
    ).toLowerCase();
    return (
      event.type === 'auxclick' ||
      event.ctrlKey ||
      event.metaKey ||
      event.shiftKey ||
      (target !== '' && target !== '_self' && target !== '_parent' && target !== '_top')
    );
  };
  const send = (report) => {
    try {
      const message = JSON.stringify(report);
      if (typeof window.__paReportClick === 'function') window.__paReportClick(message);
      else if (window.PaBridge && typeof window.PaBridge.reportClick === 'function')
        window.PaBridge.reportClick(message);
    } catch {
      // The page goes on as the user meant either way; only the record is missing.
    }
  };
  const report = (event) => {
    if (!event.isTrusted || !(event.target instanceof Element)) return;
    if (event.type === 'auxclick' && event.button !== 1) return; // A middle click opens a tab; others open nothing.
    const card = event.target.closest('[data-pa-capture]');
    // The link the click is on, inside the item or around it (a card that is all one link).
    const anchor = card && event.target.closest('a[href], a[*|href]');
    const href = hrefOf(anchor);
    if (!anchor || !leaves(href)) return;
    const item = {
      token: card.getAttribute('data-pa-capture-id') || '',
      ref: Number(card.getAttribute('data-pa-capture')),
      kind: card.getAttribute('data-pa-kind') || '',
    };
    send({ ...item, href, tab: elsewhere(event, anchor), at: new Date().toISOString() });
    // A site's script may point the link to another address as the click goes (a click counter's): once the page's
    // own handlers are through, the address the link then has is the one its page opens, and a follow-up report tells
    // it. The click reaches the window last, before its page starts to load; one a handler stopped on its way is
    // looked at right after the click instead.
    let looked = false;
    const follow = () => {
      if (looked) return;
      looked = true;
      const after = hrefOf(anchor);
      if (!event.defaultPrevented && after !== href && leaves(after))
        send({ ...item, href: after, follow: true, at: new Date().toISOString() });
    };
    window.addEventListener(event.type, follow, { once: true });
    setTimeout(() => {
      window.removeEventListener(event.type, follow);
      follow();
    }, 0);
  };
  document.addEventListener('click', report, true);
  document.addEventListener('auxclick', report, true);
})();
