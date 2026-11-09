'use strict';
/* Gate: makes signing in compulsory.
   - Guests may look around for `loginWallSeconds` (default 30). After that a centered sign-in pop-up covers the site and cannot be dismissed.
   - Until then, filters, saved screens, compare and watchlist are locked and ask the visitor to sign in.
   Active only when accounts are configured (Supabase settings) and config.requireLogin !== false.
   IMPORTANT: this is a front-end gate (user experience). The fund data files are static and public; real protection applies only to
   each user's own saved data (Row Level Security in Supabase). */
const Gate = (() => {
  const CFG = window.FUNDRIX_CONFIG || {};
  const enabled = typeof Auth !== 'undefined' && Auth.enabled && CFG.requireLogin !== false;
  const limitMs = Math.max(0, Number.isFinite(CFG.loginWallSeconds) ? CFG.loginWallSeconds : 30) * 1000;
  const AUTH_ROUTES = ['login', 'signup', 'verify', 'forgot', 'privacy'];
  const LOCKED_NAV = ['screens', 'watchlist', 'compare'];
  const T0 = 'fx_gate_t0', RET = 'fx_return';
  const ss = {
    get(k) { try { return sessionStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { v == null ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, String(v)); } catch { /* private mode */ } },
  };
  let timer = null, el = null, mode = null, lastFocus = null;
  if (!ss.get(T0)) ss.set(T0, Date.now());                   // visit start; survives reloads of this tab, so refreshing never buys more time
  const elapsed = () => Date.now() - Number(ss.get(T0) || Date.now());

  const blocked = () => enabled && !Auth.user();             // guest: some features locked
  const walled = () => blocked() && elapsed() >= limitMs;    // guest whose free look is over
  const routeOf = h => String(h || location.hash).replace(/^#\/?/, '').split('/')[0];
  const isAuthRoute = h => AUTH_ROUTES.includes(routeOf(h));

  /* ---- where to send people after they sign in */
  function remember(hash) { if (hash && !isAuthRoute(hash) && hash !== '#/' && hash !== '#') ss.set(RET, hash); }
  function returnHash() {
    const h = ss.get(RET); ss.set(RET, null);
    return h && /^#\/[\w\-\/.=,%]*$/.test(h) && h.length < 12000 ? h : '#/';
  }

  /* ---- pop-up */
  const FEATURES = { filters: 'edit filters', save: 'save screens', screens: 'open saved screens', compare: 'compare funds', watchlist: 'use your watchlist' };
  const INERT = ['.top', 'main', '#tray', '#demo', '#foot'];
  const setInert = on => INERT.forEach(sel => { const n = document.querySelector(sel); if (n) { on ? n.setAttribute('inert', '') : n.removeAttribute('inert'); } });

  function close() {
    if (!el) return;
    el.remove(); el = null; mode = null; setInert(false); document.body.style.overflow = '';
    document.removeEventListener('keydown', onKey, true);
    if (lastFocus && document.contains(lastFocus)) { try { lastFocus.focus(); } catch { /* element gone */ } } lastFocus = null;
  }
  function onKey(e) {
    if (!el) return;
    if (e.key === 'Escape') { if (mode === 'prompt') { e.preventDefault(); close(); } else e.preventDefault(); return; }   // the wall cannot be escaped
    if (e.key !== 'Tab') return;
    const f = [...el.querySelectorAll('a[href], button:not([disabled])')];
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1], a = document.activeElement;
    if (!el.contains(a)) { e.preventDefault(); first.focus(); }
    else if (e.shiftKey && a === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && a === last) { e.preventDefault(); first.focus(); }
  }
  function open(kind, feature) {
    if (el && mode === 'wall') return;                       // the wall wins over a prompt
    if (el) close();
    mode = kind; lastFocus = document.activeElement;
    const wall = kind === 'wall';
    el = document.createElement('div'); el.id = 'gate'; el.className = 'gate-bg';
    el.innerHTML = `<div class="gate-card" role="dialog" aria-modal="true" aria-labelledby="gate-title" aria-describedby="gate-text">
      ${wall ? '' : '<button class="icon-btn gate-x" data-gate-close aria-label="Close">✕</button>'}
      <svg viewBox="0 0 32 32" class="gate-mark" aria-hidden="true"><circle cx="16" cy="16" r="14" fill="currentColor"/><path d="M16 16V2a14 14 0 0 1 12 7z" fill="var(--gold)"/></svg>
      <h2 id="gate-title">${wall ? 'Sign in to continue' : `Sign in to ${feature}`}</h2>
      <p id="gate-text">${wall ? 'Fundrix N18 is free, but you need an account to keep using it. Log in, or create an account in a minute.'
        : 'This needs a free account. Log in, or create one in a minute.'}</p>
      <div class="gate-actions"><a class="btn primary wide" href="#/login" data-gate-go>Log in</a><a class="btn wide" href="#/signup" data-gate-go>Create account</a></div>
      ${wall ? '' : '<button class="link gate-later" data-gate-close>Not now</button>'}</div>`;
    el.addEventListener('mousedown', e => { if (!wall && e.target === el) close(); });
    el.addEventListener('click', e => {
      if (e.target.closest('[data-gate-close]')) return close();
      if (e.target.closest('[data-gate-go]')) { remember(location.hash); lastFocus = null; close(); }          // the link then navigates to the login / signup page
    });
    document.body.appendChild(el); document.body.style.overflow = 'hidden'; setInert(true);
    document.addEventListener('keydown', onKey, true);
    (el.querySelector('a') || el).focus();
  }

  /** Call before a gated action. Returns true if the visitor may proceed; otherwise shows the pop-up and returns false. */
  function need(key) {
    if (!blocked()) return true;
    open(walled() ? 'wall' : 'prompt', FEATURES[key] || key);
    return false;
  }

  /* ---- router hook: show/hide the wall for the current page */
  function refreshNav() {
    document.querySelectorAll('#nav a').forEach(a => a.classList.toggle('locked', blocked() && LOCKED_NAV.includes(a.dataset.r)));
  }
  function onRoute() {
    refreshNav();
    if (walled() && !isAuthRoute()) open('wall'); else if (mode === 'wall') close();
    schedule();
  }
  function schedule() {
    clearTimeout(timer);
    if (!blocked()) return;
    const left = limitMs - elapsed();
    if (left > 0) timer = setTimeout(onRoute, left + 20);
  }
  window.addEventListener('fx-auth', () => { if (!blocked()) { close(); clearTimeout(timer); } onRoute(); });      // sign-in / sign-out: re-check right now (logging out on the home page triggers no route change)
  if (enabled) { refreshNav(); schedule(); }

  return { enabled, limitMs, blocked, walled, need, onRoute, remember, returnHash, isAuthRoute, routeOf, close, refreshNav };
})();
