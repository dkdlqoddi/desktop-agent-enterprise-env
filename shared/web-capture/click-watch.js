// Reports the user's own clicks on the items capture.js marked (data-pa-capture), so the landing page of an ad they
// opened is recorded with it. Only trusted events count, a person's click or tap and never a script's, and it never
// clicks, follows or opens anything itself. The desktop app installs it on every page (Playwright's add_init_script,
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
  const report = (event) => {
    if (!event.isTrusted || !(event.target instanceof Element)) return;
    if (event.type === 'auxclick' && event.button !== 1) return; // A middle click opens a tab; others open nothing.
    const card = event.target.closest('[data-pa-capture]');
    if (!card) return;
    const anchor = event.target.closest('a[href]') || card.querySelector('a[href]');
    const message = JSON.stringify({
      token: card.getAttribute('data-pa-capture-id') || '',
      ref: Number(card.getAttribute('data-pa-capture')),
      kind: card.getAttribute('data-pa-kind') || '',
      href: hrefOf(anchor),
      at: new Date().toISOString(),
    });
    try {
      if (typeof window.__paReportClick === 'function') window.__paReportClick(message);
      else if (window.PaBridge && typeof window.PaBridge.reportClick === 'function')
        window.PaBridge.reportClick(message);
    } catch {
      // The page goes on as the user meant either way; only the record is missing.
    }
  };
  document.addEventListener('click', report, true);
  document.addEventListener('auxclick', report, true);
})();
