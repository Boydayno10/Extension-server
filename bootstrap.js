/*
  ACFH Bootstrap Loader (local dev)
  - Fetches the current page HTML from the Flask server (/site/<path>) which proxies Supabase.
  - Injects head/body and loads scripts in order, rewriting asset URLs to the Flask origin.
  - Delays Google Ads script injection until after render.
*/

(() => {
  const DEFAULT_RENDER_ORIGIN = 'https://extension-server-lv3j.onrender.com';
  const REVEAL_TIMEOUT_MS = 12000;
  const OVERLAY_ID = 'acfh-overlay-root';
  // Session-only cache: survives reloads, cleared when the tab/browser session ends.
  const HTML_CACHE_PREFIX = 'ACFH_HTML_CACHE_SESSION_V1:';

  const SHELL_STYLE_ID = 'acfh-shell-skeleton-style';

  function captureShellSkeletonCss() {
    try {
      const styles = Array.from(document.querySelectorAll('style'));
      const picked = [];
      for (const s of styles) {
        const text = String(s.textContent || '');
        if (!text) continue;
        // Heuristic: keep only the shell skeleton CSS.
        if (text.includes('.acfh-skeleton-') || text.includes('.acfh-loading-overlay') || text.includes('@keyframes shimmer') || text.includes('.sk')) {
          picked.push(text);
        }
      }
      return picked.join('\n\n');
    } catch {
      return '';
    }
  }

  function ensureShellSkeletonStyle(cssText) {
    try {
      if (!cssText) return;
      let styleEl = document.getElementById(SHELL_STYLE_ID);
      if (!styleEl) {
        styleEl = document.createElement('style');
        styleEl.id = SHELL_STYLE_ID;
        document.head.appendChild(styleEl);
      }
      if (styleEl.textContent !== cssText) {
        styleEl.textContent = cssText;
      }
    } catch {
      // ignore
    }
  }

  function waitForAppReadySignal(timeoutMs) {
    // Wait until the injected app finishes its initial UI work.
    // The options app toggles window.acfhHoldProcessing and may also dispatch an explicit event.
    return new Promise((resolve) => {
      const start = Date.now();
      let done = false;

      const finish = () => {
        if (done) return;
        done = true;
        try { window.removeEventListener('acfh:app-ready', onReady); } catch { /* ignore */ }
        resolve();
      };

      const onReady = () => finish();
      try { window.addEventListener('acfh:app-ready', onReady, { once: true }); } catch { /* ignore */ }

      const tick = () => {
        if (done) return;
        const now = Date.now();
        if (now - start >= timeoutMs) {
          finish();
          return;
        }
        // If the app is not holding processing anymore, we can proceed.
        if (window.acfhHoldProcessing === false) {
          finish();
          return;
        }
        setTimeout(tick, 60);
      };

      // If the app never sets the flag, don't block.
      if (typeof window.acfhHoldProcessing === 'undefined') {
        setTimeout(finish, 250);
        return;
      }

      tick();
    });
  }

  function getScriptOrigin() {
    try {
      const src = document.currentScript && document.currentScript.src;
      if (!src) return '';
      return new URL(src).origin;
    } catch {
      return '';
    }
  }

  function getFlaskOrigin() {
    // Allow overriding without code changes.
    const fromStorage = window.localStorage && localStorage.getItem('ACFH_FLASK_ORIGIN');

    // Prefer the origin that served this bootstrap.js (works for Render and local Flask).
    const fromScript = getScriptOrigin();

    // If this shell is hosted on the same origin as Flask, this also works.
    const fromLocation = (window.location && /^https?:$/.test(window.location.protocol)) ? window.location.origin : '';

    return (fromStorage || fromScript || fromLocation || DEFAULT_RENDER_ORIGIN).replace(/\/+$/, '');
  }

  function isAbsoluteUrl(u) {
    return /^(?:[a-z]+:)?\/\//i.test(u) || /^(?:data|blob):/i.test(u);
  }

  function encodePathSegments(path) {
    return path
      .split('/')
      .map(seg => encodeURIComponent(seg))
      .join('/');
  }

  function getCurrentKey() {
    // Use the current browser path as the key inside Supabase.
    // Examples:
    //  - /web/options.html -> options.html (backward-compat; strips leading web/)
    //  - /Auto_Click_Iframe-main/options.html -> Auto_Click_Iframe-main/options.html
    let p = (window.location.pathname || '/').replace(/^\/+/, '');

    // Vercel / local static hosting often serves shells under /web/*.
    // When Supabase objects are uploaded without that prefix, strip it here.
    if (p.startsWith('web/')) p = p.slice('web/'.length);

    // Treat directory paths as index.html.
    if (!p || p.endsWith('/')) p = (p || '') + 'index.html';

    return p || 'index.html';
  }

  function resolveKey(baseKey, relativeUrl) {
    // baseKey: e.g. web/options.html
    // relativeUrl: e.g. options.css or ../images/logo.png
    const baseDir = baseKey.replace(/[^/]*$/, '');
    const fakeBase = 'http://local/' + baseDir;
    const resolved = new URL(relativeUrl, fakeBase);
    return resolved.pathname.replace(/^\/+/, '');
  }

  function rewriteUrlAttr(flaskOrigin, baseKey, element, attrName) {
    const raw = element.getAttribute(attrName);
    if (!raw || isAbsoluteUrl(raw) || raw.startsWith('#')) return;

    const key = resolveKey(baseKey, raw);
    element.setAttribute(attrName, `${flaskOrigin}/site/${encodePathSegments(key)}`);
  }

  function shouldRewriteHref(element) {
    const tag = (element.tagName || '').toUpperCase();
    if (tag === 'A') {
      // Preserve navigation links so the user stays on the local shell pages.
      return false;
    }
    if (tag === 'LINK') {
      // Typical asset links: stylesheet, icon, preload.
      return true;
    }
    // Default: don't rewrite other hrefs.
    return false;
  }

  function collectScripts(doc) {
    // Preserve relative order: head scripts first, then body.
    const scripts = [];
    for (const s of doc.head.querySelectorAll('script')) scripts.push(s);
    for (const s of doc.body.querySelectorAll('script')) scripts.push(s);
    return scripts;
  }

  function removeScriptsFromDom(doc) {
    for (const s of doc.querySelectorAll('script')) s.remove();
  }

  function shouldDelayAdsScript(src) {
    return typeof src === 'string' && src.includes('pagead2.googlesyndication.com/pagead/js/adsbygoogle.js');
  }

  function loadExternalScript(src, { type, nomodule, referrerPolicy, crossOrigin } = {}) {
    return new Promise((resolve, reject) => {
      const s = document.createElement('script');
      if (type) s.type = type;
      if (nomodule) s.noModule = true;
      if (referrerPolicy) s.referrerPolicy = referrerPolicy;
      if (crossOrigin) s.crossOrigin = crossOrigin;
      s.async = false; // preserve execution order
      s.src = src;
      s.onload = () => resolve();
      s.onerror = () => reject(new Error('Failed to load script: ' + src));
      document.head.appendChild(s);
    });
  }

  function runInlineScript(code, { type } = {}) {
    const s = document.createElement('script');
    if (type) s.type = type;
    s.textContent = code;
    document.body.appendChild(s);
  }

  function showLoadingOverlay() {
    const already = document.getElementById(OVERLAY_ID);
    if (already) return already;

    // Prefer an immediate skeleton overlay from the shell HTML.
    const shellOverlay = document.querySelector('.acfh-loading-overlay');
    if (shellOverlay) {
      shellOverlay.id = OVERLAY_ID;

      // Ensure it survives body replacement.
      try {
        if (shellOverlay.parentElement !== document.documentElement) {
          document.documentElement.appendChild(shellOverlay);
        }
      } catch {
        // ignore
      }

      // Remove any duplicates.
      for (const el of document.querySelectorAll('.acfh-loading-overlay')) {
        if (el !== shellOverlay) {
          try { el.remove(); } catch { /* ignore */ }
        }
      }

      return shellOverlay;
    }

    // Fallback: create a minimal overlay.
    const overlay = document.createElement('div');
    overlay.id = OVERLAY_ID;
    overlay.className = 'acfh-loading-overlay';
    overlay.style.cssText = [
      'position:fixed',
      'inset:0',
      'z-index:2147483647',
      'display:flex',
      'align-items:center',
      'justify-content:center',
      'background:#0b1020',
      'color:#e5e7eb',
      'font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif',
      'padding:24px',
    ].join(';');

    overlay.textContent = 'Carregando…';
    document.documentElement.appendChild(overlay);
    return overlay;
  }

  function setBodyHidden(hidden) {
    // Hide everything except the overlay (overlay is attached to <html>, not <body>).
    // Use visibility (not opacity) to avoid making the page background transparent (white flash).
    if (hidden) {
      document.documentElement.style.background = '#0b1020';
      document.body.style.visibility = 'hidden';
      document.body.style.pointerEvents = 'none';
    } else {
      document.documentElement.style.background = '';
      document.body.style.visibility = '';
      document.body.style.pointerEvents = '';
    }
  }

  function setOverlayError(overlay, message) {
    try {
      // If the shell skeleton has a note element, reuse it; otherwise create a small error badge.
      const note = overlay && overlay.querySelector && overlay.querySelector('.acfh-skeleton-note');
      if (note) {
        note.textContent = message;
        return;
      }

      let badge = overlay && overlay.querySelector && overlay.querySelector('#acfh-overlay-error');
      if (!badge && overlay) {
        badge = document.createElement('div');
        badge.id = 'acfh-overlay-error';
        badge.style.cssText = 'margin-top:12px;font-size:12px;opacity:.9;white-space:pre-wrap;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.08);border-radius:12px;padding:10px;max-width:920px;';
        overlay.appendChild(badge);
      }
      if (badge) {
        badge.textContent = message;
      }
    } catch {
      // ignore
    }
  }

  function cacheKeyFor(pageKey) {
    return HTML_CACHE_PREFIX + String(pageKey || '');
  }

  function _storageForHtmlCache() {
    // Prefer sessionStorage so a refresh is instant, but cache is cleared when the tab closes.
    // Fallback to localStorage if sessionStorage is not available.
    try {
      if (window.sessionStorage) return window.sessionStorage;
    } catch {
      // ignore
    }
    try {
      if (window.localStorage) return window.localStorage;
    } catch {
      // ignore
    }
    return null;
  }

  function readCachedHtml(pageKey) {
    try {
      const store = _storageForHtmlCache();
      if (!store) return null;
      const raw = store.getItem(cacheKeyFor(pageKey));
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed.html !== 'string') return null;
      return parsed;
    } catch {
      return null;
    }
  }

  function writeCachedHtml(pageKey, html) {
    try {
      const store = _storageForHtmlCache();
      if (!store) return;
      const payload = JSON.stringify({ html, ts: Date.now() });
      store.setItem(cacheKeyFor(pageKey), payload);
    } catch {
      // ignore quota errors
    }
  }

  function fadeOutAndRemoveOverlay(overlay) {
    try {
      if (!overlay) return;
      overlay.style.willChange = 'opacity';
      overlay.style.transition = 'opacity 220ms ease';
      overlay.style.opacity = '0';
      setTimeout(() => {
        try { overlay.remove(); } catch { /* ignore */ }
      }, 260);
    } catch {
      try { overlay.remove(); } catch { /* ignore */ }
    }
  }

  function waitForStylesheets(timeoutMs) {
    const links = Array.from(document.querySelectorAll('link[rel="stylesheet"][href]'));
    if (!links.length) return Promise.resolve();

    const perLink = links.map((link) => {
      // If it's already loaded, skip.
      // (sheet can be null temporarily for cross-origin; load event is more reliable.)
      return new Promise((resolve) => {
        const done = () => resolve();
        link.addEventListener('load', done, { once: true });
        link.addEventListener('error', done, { once: true });
      });
    });

    return Promise.race([
      Promise.all(perLink).then(() => undefined),
      new Promise((resolve) => setTimeout(resolve, timeoutMs)),
    ]);
  }

  function fireSyntheticReadyEvents() {
    // When running inside a local shell, the browser has already fired DOMContentLoaded/load.
    // Many traditional pages initialize via those events, so we re-fire them after injecting
    // the remote HTML and executing scripts.
    try {
      document.dispatchEvent(new Event('DOMContentLoaded', { bubbles: true }));
    } catch {
      // ignore
    }
    try {
      window.dispatchEvent(new Event('load'));
    } catch {
      // ignore
    }
  }

  function waitForDomToSettle({ quietMs = 350, maxMs = 4000 } = {}) {
    return new Promise((resolve) => {
      let lastMutationAt = Date.now();
      let done = false;

      const finish = () => {
        if (done) return;
        done = true;
        try { obs.disconnect(); } catch { /* ignore */ }
        resolve();
      };

      const obs = new MutationObserver(() => {
        lastMutationAt = Date.now();
      });

      try {
        obs.observe(document.documentElement, { subtree: true, childList: true, attributes: true, characterData: true });
      } catch {
        // If we can't observe, just resolve quickly.
        resolve();
        return;
      }

      const tick = () => {
        if (done) return;
        const now = Date.now();
        if (now - lastMutationAt >= quietMs) {
          finish();
          return;
        }
        if (now - lastMutationAt >= maxMs) {
          finish();
          return;
        }
        setTimeout(tick, 50);
      };

      // Ensure we don't wait forever even if the app keeps mutating.
      setTimeout(finish, maxMs);
      setTimeout(tick, 50);
    });
  }

  async function bootstrap() {
    const flaskOrigin = getFlaskOrigin();
    const pageKey = getCurrentKey();

    // Force-refresh option: add ?acfh_refresh=1 to the shell URL.
    // Useful when iterating locally without needing to close the browser.
    let forceRefresh = false;
    try {
      const sp = new URLSearchParams(window.location.search || '');
      forceRefresh = sp.get('acfh_refresh') === '1';
    } catch {
      forceRefresh = false;
    }

    // Preserve the shell skeleton CSS because we replace <head>.
    const shellSkeletonCss = captureShellSkeletonCss();
    ensureShellSkeletonStyle(shellSkeletonCss);

    const overlay = showLoadingOverlay();
    setBodyHidden(true);

    let html = null;
    let usedCache = false;

    // Cache-first: if we already have HTML cached for this session, use it immediately
    // and skip the network fetch entirely (faster repeated opens/refreshes).
    if (!forceRefresh) {
      const cached = readCachedHtml(pageKey);
      if (cached && cached.html) {
        html = cached.html;
        usedCache = true;
      }
    }

    const htmlUrl = `${flaskOrigin}/site/${encodePathSegments(pageKey)}?t=${Date.now()}`;

    if (!html) {
      try {
        const res = await fetch(htmlUrl, { credentials: 'omit' });
        if (res.ok) {
          html = await res.text();
          writeCachedHtml(pageKey, html);
        } else {
          const cached = readCachedHtml(pageKey);
          if (cached && cached.html) {
            html = cached.html;
            usedCache = true;
            setOverlayError(overlay, 'Sem conexão com o servidor. Exibindo versão em cache…');
          } else {
            setOverlayError(overlay, `Falha ao carregar conteúdo (${res.status}).\nURL: ${htmlUrl}`);
            setBodyHidden(false);
            return;
          }
        }
      } catch (e) {
        const cached = readCachedHtml(pageKey);
        if (cached && cached.html) {
          html = cached.html;
          usedCache = true;
          setOverlayError(overlay, 'Offline. Exibindo versão em cache…');
        } else {
          setOverlayError(overlay, `Falha ao carregar conteúdo.\nURL: ${htmlUrl}\n${String(e && e.message || e)}`);
          setBodyHidden(false);
          return;
        }
      }
    }

    if (usedCache && !forceRefresh) {
      // Keep it subtle; the overlay note is reused for offline errors.
      // When online, we don't show anything here to avoid noisy UI.
    }

    const parsed = new DOMParser().parseFromString(html, 'text/html');

    // Collect scripts before rewriting DOM, then remove them from parsed.
    const scripts = collectScripts(parsed);
    removeScriptsFromDom(parsed);

    // Rewrite asset URLs so everything comes from Flask (/site/...)
    for (const el of parsed.querySelectorAll('[src]')) {
      rewriteUrlAttr(flaskOrigin, pageKey, el, 'src');
    }
    for (const el of parsed.querySelectorAll('[href]')) {
      if (!shouldRewriteHref(el)) continue;
      rewriteUrlAttr(flaskOrigin, pageKey, el, 'href');
    }

    // Replace current document content (scripts will be injected manually).
    document.documentElement.lang = parsed.documentElement.lang || document.documentElement.lang || 'en';
    document.head.innerHTML = parsed.head.innerHTML;
    document.body.innerHTML = parsed.body.innerHTML;

    // Re-inject shell skeleton CSS so the overlay doesn't lose styling.
    ensureShellSkeletonStyle(shellSkeletonCss);

    // Ensure the overlay stays visible and the page stays hidden during initialization.
    document.documentElement.appendChild(overlay);
    setBodyHidden(true);

    // Avoid a flash of unstyled content: wait for stylesheets to load (bounded).
    await waitForStylesheets(6000);

    // Now load scripts in order.
    const delayedAds = [];

    for (const original of scripts) {
      const src = original.getAttribute('src');
      const type = original.getAttribute('type') || undefined;
      const nomodule = original.noModule || original.hasAttribute('nomodule');
      const referrerPolicy = original.getAttribute('referrerpolicy') || undefined;
      const crossOrigin = original.getAttribute('crossorigin') || undefined;

      if (src) {
        // Note: src might have been rewritten in parsed, but we removed scripts before rewriting.
        // Recompute and rewrite here.
        let finalSrc = src;
        if (!isAbsoluteUrl(finalSrc)) {
          const key = resolveKey(pageKey, finalSrc);
          finalSrc = `${flaskOrigin}/site/${encodePathSegments(key)}`;
        }

        if (shouldDelayAdsScript(finalSrc)) {
          delayedAds.push({ finalSrc, type, nomodule, referrerPolicy, crossOrigin });
          continue;
        }

        await loadExternalScript(finalSrc, { type, nomodule, referrerPolicy, crossOrigin });
      } else {
        const code = original.textContent || '';
        if (code.trim()) {
          runInlineScript(code, { type });
        }
      }
    }

    // Delay Ads loading until after the page is fully rendered.
    if (delayedAds.length) {
      // Optional runtime config in Supabase (if present): runtime-config.json
      // { "adsense": { "enabled": true } }
      let adsEnabled = true;
      try {
        const configUrl = `${flaskOrigin}/site/${encodePathSegments('runtime-config.json')}?t=${Date.now()}`;
        const cfgRes = await fetch(configUrl);
        if (cfgRes.ok) {
          const cfg = await cfgRes.json();
          if (cfg && cfg.adsense && typeof cfg.adsense.enabled === 'boolean') {
            adsEnabled = cfg.adsense.enabled;
          }
        }
      } catch {
        // ignore
      }

      if (adsEnabled) {
        await new Promise(r => requestAnimationFrame(() => setTimeout(r, 0)));
        for (const ad of delayedAds) {
          await loadExternalScript(ad.finalSrc, ad);
        }
      }
    }

    // Finally, trigger initialization hooks that many pages depend on.
    fireSyntheticReadyEvents();

    // For app-style pages (like options), wait until initial processing finishes.
    await waitForAppReadySignal(REVEAL_TIMEOUT_MS);

    // Reveal only when everything is ready (bounded).
    await waitForDomToSettle({ quietMs: 300, maxMs: 5000 });
    await Promise.race([
      new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))),
      new Promise(r => setTimeout(r, REVEAL_TIMEOUT_MS)),
    ]);

    setBodyHidden(false);
    // If we loaded from cache, keep the note for a frame then fade.
    if (usedCache) {
      await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    }
    fadeOutAndRemoveOverlay(overlay);
  }

  bootstrap().catch((err) => {
    console.error('[ACFH bootstrap] error', err);
    const overlay = document.getElementById(OVERLAY_ID) || showLoadingOverlay();
    setOverlayError(overlay, String(err && err.stack || err));
    setBodyHidden(false);
  });
})();
