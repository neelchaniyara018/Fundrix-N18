'use strict';
/* Fundrix N18 SPA – vanilla JS, no build step. Hash routes:
   #/  #/screen/:id  #/fund/:id  #/compare[/1,2,3]  #/screens  #/watchlist */

const $ = (s, e = document) => e.querySelector(s);
const $$ = (s, e = document) => [...e.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const rid = () => Math.random().toString(36).slice(2) + Date.now().toString(36);
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};
const CMP = new Set(store.get('fs_cmp', []));
let M = null;              // meta
let WATCH = new Set();     // watchlist ids

/* ---------- helpers */
/** Answers the same paths the old server did, but in the browser (see data.js). Throws Error(message) on failure. */
const api = (path, opts) => Data.request(path, opts);
const J = async (p, o) => (await api(p, o)).json();
const fnum = (v, d = 2) => v == null ? '–' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
const metric = k => M.by[k] || (k.startsWith('c:') ? { key: k, label: 'ƒ ' + k.slice(2), type: 'num', dec: 2, unit: '', group: 'Custom', custom: true } : null);
const cellText = (k, v) => { const m = metric(k); return !m || m.type === 'cat' ? esc(v ?? '–') : fnum(v, m.dec); };
const tone = (k, v) => v != null && M.by[k] && M.by[k].group === 'Returns' ? (v < 0 ? 'neg' : v > 0 ? 'pos' : '') : '';
const labelOf = k => { const m = metric(k); return m.label + (m.unit ? ` (${m.unit})` : ''); };
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
function toast(msg, ms = 2400) {
  const t = document.createElement('div'); t.className = 'toast'; t.textContent = msg; t.setAttribute('role', 'status');
  document.body.appendChild(t); setTimeout(() => t.remove(), ms);
}
function modal({ title, body, footer = '', onMount }) {
  const bg = document.createElement('div'); bg.className = 'modal-bg';
  bg.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-label="${esc(title)}"><header><h3>${esc(title)}</h3><button class="icon-btn" data-close aria-label="Close">✕</button></header><div class="body">${body}</div>${footer ? `<footer>${footer}</footer>` : ''}</div>`;
  const close = () => { bg.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = e => { if (e.key === 'Escape') close(); };
  bg.addEventListener('mousedown', e => { if (e.target === bg) close(); });
  bg.addEventListener('click', e => { if (e.target.closest('[data-close]')) close(); });
  document.addEventListener('keydown', onKey);
  document.body.appendChild(bg);
  onMount && onMount(bg, close);
  const first = $('input,textarea,select,button.primary', bg); first && first.focus();
  return { el: bg, close };
}
const greeting = () => { const h = new Date().getHours(); return h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening'; };

/* ---------- screener state */
const defaultCfg = () => {
  const aum = !M || M.has_aum !== false, exp = !M || M.has_expense !== false;      // real data has no AUM / expense ratio until you supply them
  return { universe: 'all', filters: [{ key: 'plan', values: ['Growth'] }, ...(aum ? [{ key: 'aum' }] : []), { key: 'cagr_3y' }, ...(exp ? [{ key: 'expense_ratio' }] : [])],
    custom: [], sort_key: aum ? 'aum' : 'cagr_3y', sort_dir: 'desc', extra_cols: [], search: '' };
};
let S = { ...defaultCfg(), page: 1, size: 20, screenId: null, mine: false, name: '', note: '', total: 0, rows: [], wl: false, seq: 0 };
const resetScreen = (over = {}) => { S = { ...S, ...defaultCfg(), page: 1, screenId: null, mine: false, name: '', note: '', wl: false, shareKey: null, ...over }; };

const baseCols = ['sub_category', 'plan'];
const visibleCols = () => [...new Set([...baseCols, ...S.filters.map(f => f.key).filter(k => !['category', 'sub_category', 'plan'].includes(k)),
  ...S.extra_cols, ...S.custom.map(c => 'c:' + c.name)])].filter(k => metric(k));
const reqBody = (extra = {}) => ({
  universe: S.universe, filters: S.filters.map(f => ({ key: f.key, min: f.min ?? null, max: f.max ?? null, values: f.values ?? null })),
  custom: S.custom.map(c => ({ name: c.name, expr: c.expr })), search: S.search, sort_key: S.sort_key, sort_dir: S.sort_dir,
  page: S.page, page_size: S.size, columns: visibleCols().filter(k => M.by[k]), ...extra,
});
const cfgOut = () => ({ universe: S.universe, filters: S.filters, custom: S.custom, sort_key: S.sort_key, sort_dir: S.sort_dir, extra_cols: S.extra_cols, search: S.search });

/* ---------- sign-in gate helpers (see gate.js; with accounts disabled nothing is ever blocked) */
const blocked = () => typeof Gate !== 'undefined' && Gate.blocked();
const need = what => typeof Gate === 'undefined' || Gate.need(what);
const GATED_ROUTES = { screens: 'screens', watchlist: 'watchlist', compare: 'compare', screen: 'screens', s: 'screens' };

document.addEventListener('click', e => {
  if (!blocked()) return;
  if (e.target.closest('#panel') && !e.target.closest('a')) need('filters');
  const l = e.target.closest('[data-lock]'); if (l) need(l.dataset.lock);
});

/* ---------- router */
let ROUTE = 0;   // monotonically increasing; async pages bail out if a newer navigation started
async function route() {
  const tok = ++ROUTE;
  closePops();
  let h = location.hash.replace(/^#\/?/, '');
  if (GATED_ROUTES[h.split('/')[0]] && blocked()) {                     // guests may not open these pages: remember the target, show the prompt, stay on the screener
    const want = location.hash, key = GATED_ROUTES[h.split('/')[0]]; Gate.remember(want); history.replaceState(null, '', '#/'); h = '';
    setTimeout(() => need(key), 0);
  }
  const [r, arg] = h.split('/');
  if (typeof Gate !== 'undefined') Gate.onRoute();
  $$('#nav a').forEach(a => a.classList.toggle('on', a.dataset.r === (r || 'screener') || (r === 'screen' && a.dataset.r === 'screener') || (r === 'fund' && a.dataset.r === 'screener')));
  const app = $('#app');
  try {
    if (r === 'fund') await pageFund(+arg, tok);
    else if (r === 'compare') await pageCompare(arg, tok);
    else if (r === 'screens') await pageScreens(tok);
    else if (['login', 'signup', 'verify', 'forgot', 'account', 'privacy'].includes(r)) Auth.page(r);
    else if (r === 'watchlist') { if (!S.wl) resetScreen({ universe: 'watchlist', filters: defaultCfg().filters.slice(1), wl: true, name: 'Watchlist' }); pageScreener(); }
    else if (r === 'screen' && arg) {
      if (S.screenId !== arg) { const sc = await J('/screens/' + encodeURIComponent(arg)); if (tok !== ROUTE) return; resetScreen({ ...Data.sanitize(sc.config), screenId: sc.id, mine: sc.mine, name: sc.name, note: sc.note }); }
      pageScreener();
    } else if (r === 's' && arg) {                                  // share link of a browser-only screen: the screen is inside the URL
      if (S.shareKey !== arg) { const sh = Data.decodeShare(arg); resetScreen({ ...sh.config, name: sh.name, note: sh.note }); S.shareKey = arg; }
      pageScreener();
    } else { if (S.wl) resetScreen(); pageScreener(); }
  } catch (e) {
    if (tok !== ROUTE) return;
    app.innerHTML = `<div class="panel empty"><h3>Something went wrong</h3><p>${esc(e.message)}</p><a class="btn" href="#/">Back to screener</a></div>`;
  }
  renderTray();
}
window.addEventListener('hashchange', route);

/* ---------- screener page */
function pageScreener() {
  $('#app').innerHTML = `
  <div class="page-head">
    <div><div class="hello">${greeting()} :)</div><h1>Mutual Funds Screener</h1></div>
    <div class="screen-meta">
      <input id="sname" maxlength="80" placeholder="Untitled screen" aria-label="Screen name" value="${esc(S.name)}">
      <textarea id="snote" maxlength="500" placeholder='Add a note to remember why you created this – e.g. "Top 5 tax-saving ELSS funds"' aria-label="Screen note">${esc(S.note)}</textarea>
    </div>
    <div class="tools" style="display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn" id="bNew">+ New screen</button>
      <button class="btn primary" id="bSave">${S.screenId && S.mine ? 'Save changes' : 'Save screen'}</button>
      ${S.screenId && S.mine ? '<button class="btn" id="bSaveNew">Save as new</button>' : ''}
      ${S.screenId ? '<button class="btn" id="bShare">Copy link</button>' : ''}
    </div>
  </div>
  <button class="btn mob-only" id="bFilt" aria-controls="panel" aria-expanded="false">Filters ▾</button>
  <div class="layout">
    <aside class="panel filters" id="panel" aria-label="Filters"></aside>
    <section class="panel" aria-label="Results">
      <div class="results-head">
        <div class="count" id="count">Loading…</div>
        <div class="tools">
          <input class="field search" id="search" type="search" placeholder="Search name, fund house…" value="${esc(S.search)}" aria-label="Search funds">
          <button class="btn" id="bCols">Columns ▾</button>
          <button class="btn" id="bExport">⬇ Export CSV</button>
        </div>
      </div>
      <div class="tablewrap" id="tbl"></div><div class="pager" id="pager"></div>
      <div class="muted" style="padding:0 16px 12px;font-size:12px" id="asof"></div>
    </section>
  </div>`;
  renderPanel(); loadResults();
  $('#sname').oninput = e => S.name = e.target.value;
  $('#snote').oninput = e => S.note = e.target.value;
  $('#search').oninput = debounce(e => { S.search = e.target.value; S.page = 1; loadResults(); }, 250);
  $('#bFilt').onclick = e => { const o = $('#panel').classList.toggle('open'); e.currentTarget.setAttribute('aria-expanded', o); e.currentTarget.textContent = o ? 'Filters ▴' : 'Filters ▾'; };
  $('#bNew').onclick = () => { resetScreen(); location.hash = '#/'; pageScreener(); };
  $('#bSave').onclick = () => { if (need('save')) saveScreen(false); };
  const sn = $('#bSaveNew'); sn && (sn.onclick = () => { if (need('save')) saveScreen(true); });
  const sh = $('#bShare'); sh && (sh.onclick = async () => { if (!need('save')) return; const u = Data.shareUrl({ id: S.screenId, name: S.name, note: S.note, config: cfgOut() }); try { await navigator.clipboard.writeText(u); toast('Link copied'); } catch { prompt('Copy this link', u); } });
  $('#bExport').onclick = exportCsv;
  $('#bCols').onclick = e => columnsPop(e.currentTarget);
}

async function saveScreen(asNew) {
  const body = { name: (S.name || '').trim() || 'Untitled screen', note: S.note || '', config: cfgOut() };
  try {
    const sc = (S.screenId && S.mine && !asNew) ? await J('/screens/' + S.screenId, { method: 'PUT', body }) : await J('/screens', { method: 'POST', body });
    S.screenId = sc.id; S.mine = true; S.name = sc.name;
    S.shareKey = null; toast(Data.cloud() ? 'Screen saved' : 'Screen saved in this browser. Log in to keep it on all your devices.', Data.cloud() ? 2400 : 4500);
    if ($('#sname')) { history.replaceState(null, '', '#/screen/' + sc.id); pageScreener(); }
  } catch (e) { toast('Could not save: ' + e.message, 4000); }
}

async function exportCsv() {
  try {
    const r = await api('/export', { method: 'POST', body: reqBody({ page: 1, page_size: 200 }) });
    const blob = await r.blob(), a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = (S.name || 'fundrix-n18-screen').replace(/[^\w-]+/g, '_') + '.csv';
    document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    toast(`Exported ${r.headers.get('X-Total')} funds`);
  } catch (e) { toast('Export failed: ' + e.message, 4000); }
}

/* ----- filter panel */
function bounds(f) {
  const m = metric(f.key); let lo, hi;
  if (m.custom) { const c = S.custom.find(c => 'c:' + c.name === f.key) || {}; lo = c.min ?? 0; hi = c.max ?? 1; } else { lo = m.min; hi = m.max; }
  if (!(hi > lo)) hi = lo + 1;
  const span = hi - lo, step = m.dec === 0 ? Math.max(1, Math.round(span / 1000)) : +Math.max(.01, span / 1000).toPrecision(1);
  return { lo, hi, step, dec: m.dec ?? 2 };
}
function filterCard(f) {
  const m = metric(f.key); if (!m) return '';
  const title = `<b>${esc(m.label)}${m.unit ? ` <span class="u">(${esc(m.unit)})</span>` : ''}</b>`;
  const head = `<div class="fh">${title}<span class="acts"><button class="link" data-a="reset">Reset</button><button class="icon-btn" data-a="remove" aria-label="Remove ${esc(m.label)} filter">✕</button></span></div>`;
  if (m.type === 'cat') {
    const sel = new Set(f.values || []);
    let opts;
    if (f.key === 'sub_category') {
      opts = Object.entries(M.categories).map(([c, subs]) => `<div class="grp">${esc(c)}</div>` + subs.map(s => chk(s, sel)).join('')).join('');
    } else if (f.key === 'category') opts = Object.keys(M.categories).map(c => chk(c, sel, M.category_counts[c])).join('');
    else opts = m.options.map(o => chk(o, sel)).join('');
    const srch = m.options.length > 8 ? `<input class="field csearch" placeholder="Search…" aria-label="Search ${esc(m.label)} options">` : '';
    return `<div class="fcard" data-key="${esc(f.key)}">${head}${srch}<div class="checks">${opts}</div></div>`;
  }
  const b = bounds(f), a = f.min ?? b.lo, z = f.max ?? b.hi, pc = v => ((Math.min(Math.max(v, b.lo), b.hi) - b.lo) / (b.hi - b.lo) * 100);
  return `<div class="fcard" data-key="${esc(f.key)}">${head}
    <div class="dual"><div class="track"><div class="fill" style="left:${pc(a)}%;right:${100 - pc(z)}%"></div></div>
      <input type="range" class="lo" min="${b.lo}" max="${b.hi}" step="${b.step}" value="${a}" aria-label="${esc(m.label)} minimum">
      <input type="range" class="hi" min="${b.lo}" max="${b.hi}" step="${b.step}" value="${z}" aria-label="${esc(m.label)} maximum"></div>
    <div class="nums"><input type="number" class="nmin" step="any" value="${a}" aria-label="Minimum ${esc(m.label)}"><span>to</span><input type="number" class="nmax" step="any" value="${z}" aria-label="Maximum ${esc(m.label)}"></div></div>`;
}
const chk = (v, sel, cnt) => `<label data-name="${esc(v.toLowerCase())}"><input type="checkbox" class="cb" value="${esc(v)}" ${sel.has(v) ? 'checked' : ''}> ${esc(v)}${cnt != null ? `<span class="cnt">${cnt}</span>` : ''}</label>`;

function renderPanel() {
  const p = $('#panel'); if (!p) return;
  const lock = blocked();
  const n = S.filters.length;
  p.innerHTML = `<div class="filters-head"><b>${n} filter${n === 1 ? '' : 's'} applied</b><button class="link" data-a="resetall">Reset all</button></div>
    <div class="fcard"><div class="fh"><b>MF Universe</b></div><select class="field" id="univ" aria-label="Fund universe">
      <option value="all" ${S.universe === 'all' ? 'selected' : ''}>All mutual funds (${M.total})</option>
      <option value="watchlist" ${S.universe === 'watchlist' ? 'selected' : ''}>My watchlist (${WATCH.size})</option></select></div>
    ${S.filters.map(filterCard).join('')}
    <div class="add-row"><button class="btn primary" data-a="add">+ Add filter</button><button class="btn" data-a="custom">ƒ Custom filter</button></div>`;
  p.classList.toggle('locked', lock);
  if (lock) {                                                    // guests: controls are inert (no clicks / keys reach them); a click on the panel opens the prompt
    $$('.filters-head, .fcard, .add-row', p).forEach(x => x.setAttribute('inert', ''));
    p.insertAdjacentHTML('afterbegin', '<div class="lockbar">🔒 <a href="#/login">Log in</a> or <a href="#/signup">sign up</a> to edit filters</div>');
  }
}
const reload = () => { S.page = 1; loadResults(); };
const reloadSoon = debounce(reload, 250);

document.addEventListener('click', e => {
  const b = e.target.closest('#panel [data-a]'); if (!b || blocked()) return;
  const card = b.closest('.fcard'), key = card && card.dataset.key, a = b.dataset.a;
  if (a === 'remove') { S.filters = S.filters.filter(f => f.key !== key); if (key.startsWith('c:')) S.custom = S.custom.filter(c => 'c:' + c.name !== key); renderPanel(); reload(); }
  else if (a === 'reset') { const f = S.filters.find(f => f.key === key); delete f.min; delete f.max; if (f.values) f.values = []; renderPanel(); reload(); }
  else if (a === 'resetall') { const d = defaultCfg(); S.filters = d.filters; S.custom = []; S.extra_cols = []; S.universe = 'all'; S.search = ''; $('#search').value = ''; renderPanel(); reload(); }
  else if (a === 'add') addFilterModal('metrics');
  else if (a === 'custom') addFilterModal('custom');
});
document.addEventListener('input', e => {
  const t = e.target, card = t.closest('#panel .fcard'); if (!card || blocked()) return;
  const f = S.filters.find(f => f.key === card.dataset.key);
  if (t.classList.contains('csearch')) { const q = t.value.toLowerCase(); $$('label', card).forEach(l => l.classList.toggle('hidden', !l.dataset.name.includes(q))); return; }
  if (!f || !t.matches('.lo,.hi,.nmin,.nmax')) return;
  const b = bounds(f), typed = t.matches('.nmin,.nmax');
  let a, z;
  if (typed) { a = parseFloat($('.nmin', card).value); z = parseFloat($('.nmax', card).value); if (isNaN(a)) a = b.lo; if (isNaN(z)) z = b.hi; if (a > z) { if (t.classList.contains('nmin')) { z = a; $('.nmax', card).value = z; } else { a = z; $('.nmin', card).value = a; } } }
  else { a = +$('.lo', card).value; z = +$('.hi', card).value; if (t.classList.contains('lo') && a > z) a = z; if (t.classList.contains('hi') && z < a) z = a; a = +a.toFixed(4); z = +z.toFixed(4); }
  const clamp = v => Math.min(Math.max(v, b.lo), b.hi), pc = v => (clamp(v) - b.lo) / (b.hi - b.lo) * 100;
  f.min = a <= b.lo ? undefined : a; f.max = z >= b.hi ? undefined : z;
  $('.lo', card).value = clamp(a); $('.hi', card).value = clamp(z);
  if (!typed) { $('.nmin', card).value = a; $('.nmax', card).value = z; }
  const fill = $('.fill', card); fill.style.left = pc(a) + '%'; fill.style.right = (100 - pc(z)) + '%';
  reloadSoon();
});
document.addEventListener('change', e => {
  const t = e.target;
  if (blocked() && t.closest('#panel')) return;
  if (t.id === 'univ') { S.universe = t.value; reload(); return; }
  const card = t.closest('#panel .fcard');
  if (card && t.classList.contains('cb')) {
    const f = S.filters.find(f => f.key === card.dataset.key);
    f.values = $$('.cb:checked', card).map(c => c.value); reload();
  }
});

/* ----- add filter / custom filter modal */
function addFilterModal(tab) {
  const draw = (bg, tab) => {
    const body = $('.body', bg);
    if (tab === 'metrics') {
      const used = new Set(S.filters.map(f => f.key));
      body.innerHTML = `<div class="tabs"><button class="on" data-t="metrics">Metrics</button><button data-t="custom">Custom filter</button></div>
        <input class="field" id="msearch" placeholder="Search ${M.metrics.length}+ filters…" style="margin-bottom:12px" aria-label="Search filters"><div class="mlist" id="mlist"></div>`;
      const list = (q = '') => {
        $('#mlist', bg).innerHTML = M.groups.map(g => {
          const items = M.metrics.filter(m => m.group === g && (m.label + ' ' + m.desc).toLowerCase().includes(q));
          return items.length ? `<div><h4>${esc(g)}</h4>${items.map(m => `<button class="mitem" data-k="${m.key}" ${used.has(m.key) ? 'disabled' : ''}><span>${esc(m.label)}${m.unit ? ` <span class="muted">(${esc(m.unit)})</span>` : ''}${used.has(m.key) ? ' ✓' : ''}</span><span class="d">${esc(m.desc || '')}</span></button>`).join('')}</div>` : '';
        }).join('') || '<div class="empty">No filters match.</div>';
      };
      list(); $('#msearch', bg).oninput = e => list(e.target.value.toLowerCase());
    } else {
      body.innerHTML = `<div class="tabs"><button data-t="metrics">Metrics</button><button class="on" data-t="custom">Custom filter</button></div>
        <p class="muted" style="margin-top:0">Combine any numeric metrics with <code>+ - * / ** ( )</code> and <code>${M.functions.join(', ')}</code>. Example: <code>cagr_3y / std_dev</code></p>
        <label class="muted" for="cname">Name</label><input class="field" id="cname" maxlength="40" placeholder="e.g. Return per risk" style="margin:4px 0 10px">
        <label class="muted" for="cexpr">Formula</label><textarea class="field" id="cexpr" rows="2" placeholder="cagr_3y / std_dev" style="margin:4px 0 8px"></textarea>
        <div id="cmsg"></div>
        <details style="margin-top:10px"><summary class="muted">Available metric names</summary><div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:8px">${M.metrics.filter(m => m.type === 'num').map(m => `<button class="pill" data-ins="${m.key}" title="${esc(m.label)}">${m.key}</button>`).join('')}</div></details>`;
    }
  };
  const mdl = modal({
    title: 'Add filter', body: '', footer: '<button class="btn" data-close>Cancel</button><button class="btn" id="cval" hidden>Validate</button><button class="btn primary" id="cadd" hidden>Add custom filter</button>',
    onMount: (bg, close) => {
      const show = t => { draw(bg, t); $('#cval', bg).hidden = $('#cadd', bg).hidden = t !== 'custom'; };
      show(tab);
      bg.addEventListener('click', e => {
        const tb = e.target.closest('[data-t]'); if (tb) return show(tb.dataset.t);
        const it = e.target.closest('.mitem'); if (it && !it.disabled) { const k = it.dataset.k; S.filters.push(M.by[k].type === 'cat' ? { key: k, values: [] } : { key: k }); close(); renderPanel(); reload(); return; }
        const ins = e.target.closest('[data-ins]'); if (ins) { const t = $('#cexpr', bg); t.value += (t.value && !t.value.endsWith(' ') ? ' ' : '') + ins.dataset.ins; t.focus(); }
      });
      const validate = async () => {
        const msg = $('#cmsg', bg); msg.className = ''; msg.textContent = '';
        try { const r = await J('/custom/validate', { method: 'POST', body: { expr: $('#cexpr', bg).value } }); msg.className = 'ok'; msg.textContent = `Valid · ${r.count} funds · range ${r.min} to ${r.max} (median ${r.median})`; return r; }
        catch (e) { msg.className = 'err'; msg.textContent = e.message; return null; }
      };
      $('#cval', bg).onclick = validate;
      $('#cadd', bg).onclick = async () => {
        const name = $('#cname', bg).value.trim(), expr = $('#cexpr', bg).value.trim(), msg = $('#cmsg', bg);
        if (!name) { msg.className = 'err'; msg.textContent = 'Give the filter a name.'; return; }
        if (!/^[A-Za-z0-9 _\-%/.()]{1,40}$/.test(name)) { msg.className = 'err'; msg.textContent = 'Name may use letters, digits, spaces and _ - % / . ( ) only.'; return; }
        if (S.custom.some(c => c.name === name)) { msg.className = 'err'; msg.textContent = 'A custom filter with that name already exists.'; return; }
        const r = await validate(); if (!r) return;
        S.custom.push({ name, expr, min: r.min, max: r.max }); S.filters.push({ key: 'c:' + name }); close(); renderPanel(); reload();
      };
    },
  });
  return mdl;
}

/* ----- columns popover */
function closePops() { $$('.pop').forEach(p => p.remove()); }
document.addEventListener('mousedown', e => { if (!e.target.closest('.pop') && !e.target.closest('#bCols')) closePops(); });
function columnsPop(btn) {
  if ($('.pop')) return closePops();
  const filt = new Set(S.filters.map(f => f.key)), p = document.createElement('div'); p.className = 'pop';
  const r = btn.getBoundingClientRect(); p.style.top = (r.bottom + scrollY + 6) + 'px'; p.style.left = Math.max(8, Math.min(r.right + scrollX - 300, innerWidth - 310)) + 'px';
  p.innerHTML = `<div class="mlist">${M.groups.map(g => `<div><h4>${esc(g)}</h4><div class="checks" style="max-height:none">${M.metrics.filter(m => m.group === g && !['category', 'sub_category', 'plan'].includes(m.key)).map(m =>
    `<label><input type="checkbox" value="${m.key}" ${filt.has(m.key) || S.extra_cols.includes(m.key) ? 'checked' : ''} ${filt.has(m.key) ? 'disabled' : ''}> ${esc(m.label)}</label>`).join('')}</div></div>`).join('')}</div>`;
  p.onchange = e => { const k = e.target.value; S.extra_cols = e.target.checked ? [...S.extra_cols, k] : S.extra_cols.filter(c => c !== k); loadResults(); };
  document.body.appendChild(p);
}

/* ----- results table */
async function loadResults() {
  const seq = ++S.seq, tbl = $('#tbl'); if (!tbl) return;
  tbl.classList.add('loading');
  try {
    const d = await J('/screen', { method: 'POST', body: reqBody() });
    if (seq !== S.seq || !$('#tbl')) return;          // superseded, or the user already left the screener
    S.total = d.total; S.rows = d.results; S.pages = d.pages;
    if (S.page > d.pages) { S.page = d.pages; return loadResults(); }
    d.results.forEach(r => r.watch ? WATCH.add(r.id) : 0);
    renderTable(d);
  } catch (e) {
    if (seq !== S.seq || !$('#tbl')) return;
    tbl.innerHTML = `<div class="empty"><h3>Could not load results</h3><p>${esc(e.message)}</p></div>`; $('#pager').innerHTML = '';
  } finally { tbl.classList.remove('loading'); }
}
function renderTable(d) {
  const cols = visibleCols(), from = (d.page - 1) * d.page_size;
  $('#count').innerHTML = d.total ? `Showing <b>${from + 1} - ${from + d.results.length}</b> of <b>${d.total.toLocaleString('en-IN')}</b> results` : 'No results';
  $('#asof').textContent = `NAV data as of ${d.as_of}. Fields shown as – are not available for that fund (for example, 3Y CAGR for funds younger than 3 years).`;
  const th = (k, label, cls) => `<th class="${cls} ${S.sort_key === k ? 'sorted' : ''}" aria-sort="${S.sort_key === k ? (S.sort_dir === 'asc' ? 'ascending' : 'descending') : 'none'}"><button data-sort="${esc(k)}">${esc(label)} <span class="arr">${S.sort_key === k ? (S.sort_dir === 'asc' ? '▲' : '▼') : '↕'}</span></button></th>`;
  if (!d.total) {
    $('#tbl').innerHTML = `<div class="empty"><h3>${S.universe === 'watchlist' && !WATCH.size ? 'Your watchlist is empty' : 'No funds match these filters'}</h3><p>${S.universe === 'watchlist' && !WATCH.size ? 'Tap ☆ next to any fund to add it.' : 'Try widening a range or removing a filter.'}</p></div>`;
    $('#pager').innerHTML = ''; return;
  }
  $('#tbl').innerHTML = `<table class="t"><thead><tr><th style="width:36px"></th>${th('name', `Name (${M.total.toLocaleString('en-IN')} MFs)`, '')}${cols.map(k => th(k, labelOf(k), metric(k).type === 'num' ? 'num' : '')).join('')}</tr></thead><tbody>
    ${d.results.map((r, i) => `<tr data-id="${r.id}"><td>${blocked() ? `<button class="icon-btn lockbtn" data-lock="compare" aria-label="Sign in to compare funds" title="Sign in to compare">🔒</button>` : `<input type="checkbox" class="cmp" ${CMP.has(r.id) ? 'checked' : ''} aria-label="Compare ${esc(r.name)}">`}</td>
      <td class="name"><div class="nm"><span class="idx">${from + i + 1}.</span><a href="#/fund/${r.id}">${esc(r.name)}</a><button class="icon-btn star ${WATCH.has(r.id) ? 'on' : ''}" data-star aria-label="Toggle watchlist" aria-pressed="${WATCH.has(r.id)}">${WATCH.has(r.id) ? '★' : '☆'}</button></div><div class="sub">${esc(r.option)}</div></td>
      ${cols.map(k => { const v = r[k], m = metric(k); return m.type === 'num' ? `<td class="num ${tone(k, v)}">${cellText(k, v)}</td>` : k === 'plan' ? `<td><span class="pill">${esc(v)}</span></td>` : `<td>${esc(v ?? '–')}</td>`; }).join('')}</tr>`).join('')}</tbody></table>`;
  const P = d.pages, c = d.page, set = new Set([1, P, c, c - 1, c + 1, c - 2, c + 2].filter(n => n >= 1 && n <= P)), nums = [...set].sort((a, b) => a - b);
  let pg = `<button data-p="${c - 1}" ${c <= 1 ? 'disabled' : ''} aria-label="Previous page">‹</button>`, prev = 0;
  nums.forEach(n => { if (n - prev > 1) pg += '<span class="muted">…</span>'; pg += `<button data-p="${n}" class="${n === c ? 'on' : ''}" ${n === c ? 'aria-current="page"' : ''}>${n}</button>`; prev = n; });
  pg += `<button data-p="${c + 1}" ${c >= P ? 'disabled' : ''} aria-label="Next page">›</button><select class="field" id="psize" style="width:auto;margin-left:10px" aria-label="Rows per page">${[20, 50, 100].map(n => `<option ${n === S.size ? 'selected' : ''}>${n}</option>`).join('')}</select>`;
  $('#pager').innerHTML = pg;
}
document.addEventListener('click', async e => {
  const sort = e.target.closest('[data-sort]');
  if (sort) { const k = sort.dataset.sort; if (S.sort_key === k) S.sort_dir = S.sort_dir === 'asc' ? 'desc' : 'asc'; else { S.sort_key = k; S.sort_dir = (k === 'name' || metric(k)?.type === 'cat') ? 'asc' : 'desc'; } S.page = 1; return loadResults(); }
  const pg = e.target.closest('#pager [data-p]'); if (pg && !pg.disabled) { S.page = +pg.dataset.p; loadResults(); return scrollTo({ top: 0, behavior: 'smooth' }); }
  const star = e.target.closest('[data-star]'); if (star) { if (!need('watchlist')) return; const id = +star.closest('tr').dataset.id; await toggleWatch(id); return; }
});
document.addEventListener('change', e => {
  if (e.target.id === 'psize') { S.size = +e.target.value; reload(); }
  if (e.target.classList.contains('cmp')) { if (!need('compare')) { e.target.checked = false; return; } } if (e.target.classList.contains('cmp')) toggleCompare(+e.target.closest('tr').dataset.id, e.target);
});
async function toggleWatch(id) {
  const on = !WATCH.has(id);
  try { await api('/watchlist/' + id, { method: on ? 'PUT' : 'DELETE' }); } catch (e) { return toast(e.message); }
  on ? WATCH.add(id) : WATCH.delete(id); toast(on ? 'Added to watchlist' : 'Removed from watchlist');
  if (S.universe === 'watchlist' && $('#tbl')) { renderPanel(); loadResults(); }
  else { $$('tr[data-id]').forEach(tr => { if (+tr.dataset.id === id) { const s = $('[data-star]', tr); s.classList.toggle('on', on); s.textContent = on ? '★' : '☆'; s.setAttribute('aria-pressed', on); } }); const wb = $('#wbtn'); if (wb) wb.textContent = on ? '★ In watchlist' : '☆ Add to watchlist'; const pu = $('#univ'); pu && renderPanel(); }
}
function toggleCompare(id, box) {
  if (CMP.has(id)) CMP.delete(id);
  else { if (CMP.size >= 4) { if (box) box.checked = false; return toast('You can compare up to 4 funds'); } CMP.add(id); }
  store.set('fs_cmp', [...CMP]); renderTray();
  const wb = $('#cbtn'); if (wb) wb.textContent = CMP.has(id) ? '✓ Added to compare' : '+ Compare';
}
function renderTray() {
  const n = blocked() ? 0 : CMP.size, b = $('#cmpBadge'); b.textContent = n; b.classList.toggle('hidden', !n);
  $('#tray').innerHTML = n && !location.hash.startsWith('#/compare') ? `<div class="tray" role="region" aria-label="Compare tray"><span>${n} selected</span><button class="btn primary sm" data-go ${n < 2 ? 'disabled' : ''}>Compare ${n < 2 ? '(pick 1 more)' : ''}</button><button class="btn sm" data-clear>Clear</button></div>` : '';
}
$('#tray').addEventListener('click', e => {
  if (e.target.closest('[data-clear]')) { CMP.clear(); store.set('fs_cmp', []); $$('.cmp').forEach(c => c.checked = false); renderTray(); }
  if (e.target.closest('[data-go]')) location.hash = '#/compare/' + [...CMP].join(',');
});

/* ---------- charts */
const COLORS = ['var(--accent)', 'var(--gold)', '#5b6fb0', '#b4402b'];
function lineChart(host, dates, series, { h = 280, fmt = v => fnum(v, 2), area = false } = {}) {
  const W = 860, H = h, P = { l: 52, r: 12, t: 22, b: 24 }, n = dates.length;
  let mn = Infinity, mx = -Infinity;
  series.forEach(s => s.values.forEach(v => { if (v != null) { mn = Math.min(mn, v); mx = Math.max(mx, v); } }));
  if (!isFinite(mn)) { host.innerHTML = '<div class="empty">No data</div>'; return; }
  const pad = (mx - mn) * .06 || 1; mn -= pad; mx += pad;
  P.l = Math.max(52, 14 + 6.5 * Math.max(fmt(mn).length, fmt(mx).length));   // room for the widest y label
  const X = i => P.l + (W - P.l - P.r) * i / ((n - 1) || 1), Y = v => P.t + (H - P.t - P.b) * (1 - (v - mn) / (mx - mn));
  let g = '';
  for (let i = 0; i <= 4; i++) { const v = mn + (mx - mn) * i / 4, y = Y(v); g += `<line x1="${P.l}" x2="${W - P.r}" y1="${y}" y2="${y}" stroke="var(--rule)" stroke-dasharray="2 4"/><text x="${P.l - 6}" y="${y + 4}" text-anchor="end">${esc(fmt(v))}</text>`; }
  for (let i = 0; i <= 4; i++) { const k = Math.round((n - 1) * i / 4), d = new Date(dates[k]); g += `<text x="${X(k)}" y="${H - 6}" text-anchor="${i === 0 ? 'start' : i === 4 ? 'end' : 'middle'}">${d.toLocaleDateString('en-IN', { month: 'short', year: '2-digit' })}</text>`; }
  const paths = series.map((s, si) => { const pts = s.values.map((v, i) => v == null ? null : `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).filter(Boolean); return (area && si === 0 ? `<path d="M${X(0)},${H - P.b} L${pts.join(' L')} L${X(n - 1)},${H - P.b}Z" style="fill:${COLORS[0]};opacity:.1"/>` : '') + `<polyline points="${pts.join(' ')}" fill="none" style="stroke:${COLORS[si % 4]}" stroke-width="2" stroke-linejoin="round"/>`; }).join('');
  host.innerHTML = `<div class="legend">${series.length > 1 ? series.map((s, i) => `<span><i style="background:${COLORS[i % 4]}"></i>${esc(s.name)}</span>`).join('') : ''}</div>
    <svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Line chart">${g}${paths}<line class="cx" y1="${P.t}" y2="${H - P.b}" stroke="var(--muted)" visibility="hidden"/><text class="tip" x="${P.l}" y="12" style="fill:var(--ink);font-weight:600"></text><rect class="hit" x="${P.l}" y="${P.t}" width="${W - P.l - P.r}" height="${H - P.t - P.b}" fill="transparent"/></svg>`;
  const svg = $('svg', host), cx = $('.cx', svg), tip = $('.tip', svg);
  const move = ev => {
    const r = svg.getBoundingClientRect(), x = (ev.clientX - r.left) / r.width * W, i = Math.max(0, Math.min(n - 1, Math.round((x - P.l) / (W - P.l - P.r) * (n - 1))));
    cx.setAttribute('x1', X(i)); cx.setAttribute('x2', X(i)); cx.setAttribute('visibility', 'visible');
    tip.textContent = new Date(dates[i]).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) + '   ' + series.map(s => (series.length > 1 ? (s.short || s.name.slice(0, 18)) + ': ' : '') + (s.values[i] == null ? '–' : fmt(s.values[i]))).join('   ');
  };
  svg.addEventListener('pointermove', move); svg.addEventListener('pointerleave', () => { cx.setAttribute('visibility', 'hidden'); tip.textContent = ''; });
}

/* ---------- fund page */
async function pageFund(id, tok = ROUTE) {
  if (!Number.isInteger(id)) throw new Error('Invalid fund');
  $('#app').innerHTML = '<div class="empty">Loading fund…</div>';
  const [d, nv] = await Promise.all([J('/funds/' + id), J('/funds/' + id + '/nav')]);
  if (tok !== ROUTE) return;
  const f = d.fund, det = d.detail, avg = d.category_avg, rk = d.rank;
  d.watch ? WATCH.add(id) : WATCH.delete(id);
  const kv = (a, b) => `<div>${a}</div><div>${b}</div>`, V = (k, v = f[k]) => v == null ? '–' : fnum(v, M.by[k].dec) + (M.by[k].unit === '%' ? '%' : '');
  const retRows = [['ret_1m', '1M'], ['ret_3m', '3M'], ['ret_6m', '6M'], ['ret_ytd', 'YTD'], ['ret_1y', '1Y (absolute)'], ['cagr_3y', '3Y CAGR'], ['cagr_5y', '5Y CAGR'], ['cagr_10y', '10Y CAGR']].filter(([k]) => f[k] != null);
  const keyM = ['std_dev', 'beta', 'alpha', 'max_drawdown', 'tracking_error', 'up_capture', 'down_capture', 'sharpe', 'sortino', 'treynor', 'info_ratio', 'calmar'].filter(k => f[k] != null);
  const alloc = [['Equity', f.equity_pct, 'var(--accent)'], ['Debt', f.debt_pct, '#5b6fb0'], ['Gold / Others', f.other_pct, 'var(--gold)'], ['Cash', f.cash_pct, 'var(--muted)']].filter(a => a[1]);
  const caps = f.large_pct != null ? [['Large', f.large_pct, 'var(--accent)'], ['Mid', f.mid_pct, 'var(--gold)'], ['Small', f.small_pct, '#b4402b']].filter(a => a[1]) : [];
  const stack = a => `<div class="stack">${a.map(x => `<i style="width:${x[1]}%;background:${x[2]}" title="${x[0]} ${fnum(x[1], 1)}%"></i>`).join('')}</div><div class="legend">${a.map(x => `<span><i style="background:${x[2]}"></i>${x[0]} ${fnum(x[1], 1)}%</span>`).join('')}</div>`;
  const maxSec = Math.max(...det.sectors.map(s => s.weight), 1);
  $('#app').innerHTML = `
  <a class="crumb" href="#/">← Back to screener</a>
  <div class="fund-head"><div><h1>${esc(f.name)}</h1><div class="tags"><span class="pill accent">${esc(f.sub_category)}</span><span class="pill">${esc(f.category)}</span><span class="pill">${esc(f.plan)}</span><span class="pill">${esc(f.option)}</span><span class="pill">Risk: ${esc(f.riskometer)}</span></div><div class="muted" style="margin-top:6px">${esc(f.amc)}</div></div>
    <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn" id="wbtn">${WATCH.has(id) ? '★ In watchlist' : '☆ Add to watchlist'}</button><button class="btn" id="cbtn">${CMP.has(id) ? '✓ Added to compare' : '+ Compare'}</button></div></div>
  <div class="strip"><div><small>NAV (₹)</small><b>${fnum(f.nav)}</b></div><div><small>AUM (Cr)</small><b>${fnum(f.aum)}</b></div><div><small>Expense ratio</small><b>${V('expense_ratio')}</b></div><div><small>Min SIP (₹)</small><b>${fnum(f.min_sip, 0)}</b></div><div><small>Min lumpsum (₹)</small><b>${fnum(f.min_lumpsum, 0)}</b></div><div><small>Exit load</small><b>${V('exit_load')}</b></div><div><small>Fund age</small><b>${fnum(f.age, 1)} yrs</b></div></div>
  <div class="panel card" style="margin-bottom:18px"><div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px"><h2>NAV history</h2><div class="range-btns" id="ranges"></div></div><div id="navchart"></div></div>
  <div class="grid2">
    <section class="panel card"><h2>Returns vs category</h2><table class="t" style="min-width:0"><thead><tr><th>Period</th><th class="num">This fund</th><th class="num">Category avg</th><th class="num">Difference</th></tr></thead><tbody>
      ${retRows.map(([k, l]) => { const a = avg[k], df = a != null ? f[k] - a : null; return `<tr><td>${l}</td><td class="num ${tone(k, f[k])}">${fnum(f[k])}%</td><td class="num">${a != null ? fnum(a) + '%' : '–'}</td><td class="num ${df == null ? '' : df >= 0 ? 'pos' : 'neg'}">${df == null ? '–' : (df >= 0 ? '+' : '') + fnum(df)}</td></tr>`; }).join('')}</tbody></table>
      <p class="muted" style="font-size:12px;margin-bottom:0">Category average is across ${d.group_size} ${esc(f.option)} ${esc(f.plan)} plans in ${esc(f.sub_category)}.</p></section>
    <section class="panel card"><h2>Scheme details</h2><div class="kv">
      ${kv('Fund house', esc(f.amc))}${kv('Fund manager' + (det.manager.length > 1 ? 's' : ''), det.manager.length ? esc(det.manager.join(', ')) + (det.manager_since ? ` <span class="muted">(since ${det.manager_since})</span>` : '') : '<span class="muted">Not available</span>')}${kv(det.launch ? 'Launch date' : 'NAV history', det.launch ? new Date(det.launch).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) : `${fnum(f.age, 1)} yrs on record`)}
      ${kv('Benchmark', esc(f.benchmark))}${kv('Exit load', f.exit_load == null ? '<span class="muted">Not available</span>' : f.exit_load ? `${fnum(f.exit_load)}%${det.exit_load_days ? ` if redeemed within ${det.exit_load_days} days` : ''}` : 'Nil')}${kv('Lock-in', f.lock_in ? f.lock_in + ' years' : 'None')}
      ${kv('Minimum SIP / lumpsum', f.min_sip == null && f.min_lumpsum == null ? '<span class="muted">Not available</span>' : `₹${fnum(f.min_sip, 0)} / ₹${fnum(f.min_lumpsum, 0)}`)}${f.num_holdings != null ? kv('Holdings', `${f.num_holdings} (top 10 = ${fnum(f.top10_pct, 1)}%)`) : ''}</div>
      <p class="muted" style="font-size:12px;margin-bottom:0"><b>Tax:</b> ${esc(d.tax)}</p></section></div>
  <section class="panel card" style="margin-bottom:18px"><h2>Risk &amp; ratios</h2><div class="mgrid">${keyM.map(k => { const m = M.by[k]; return `<div class="mcell" title="${esc(m.desc)}"><small>${esc(m.label)}</small><b class="${m.better && k === 'alpha' ? (f[k] >= 0 ? 'pos' : 'neg') : ''}">${V(k)}</b><div class="cmp">Category avg ${avg[k] != null ? fnum(avg[k], m.dec) : '–'}</div>${rk[k] != null ? `<div class="rk">Better than ${rk[k]}% of peers</div>` : ''}</div>`; }).join('') || '<div class="muted">Needs at least 1 year of history.</div>'}</div></section>
  <div class="grid2">
    <section class="panel card"><h2>Portfolio</h2>${alloc.length ? `<div class="muted">Asset allocation</div>${stack(alloc)}` : '<p class="muted">Portfolio composition is not available for this fund yet.</p>'}
      ${caps.length ? `<div class="muted" style="margin-top:10px">Market-cap split (of equity)</div>${stack(caps)}` : ''}
      <div class="kv" style="margin-top:12px">${[['pe', 'PE'], ['pb', 'PB'], ['ytm', 'Yield to maturity'], ['mod_duration', 'Modified duration'], ['avg_maturity', 'Avg. maturity']].filter(([k]) => f[k] != null).map(([k, l]) => kv(l, V(k) + (M.by[k].unit === 'Years' ? ' yrs' : ''))).join('')}</div>
      ${det.sectors.length ? '<h3 style="margin:16px 0 6px;font-size:16px">Sector / issuer mix</h3>' : ''}${det.sectors.map(s => `<div class="bar-row"><span>${esc(s.sector)}</span><div class="bar"><i style="width:${s.weight / maxSec * 100}%"></i></div><span class="num">${fnum(s.weight, 1)}%</span></div>`).join('')}</section>
    <section class="panel card"><h2>Top holdings</h2><table class="t" style="min-width:0"><thead><tr><th>Name</th><th>Sector</th><th class="num">Weight</th></tr></thead><tbody>${det.holdings.map(h => `<tr><td>${esc(h.name)}</td><td class="muted">${esc(h.sector)}</td><td class="num">${fnum(h.weight)}%</td></tr>`).join('') || '<tr><td colspan="3" class="muted">Holdings are not available for this fund yet.</td></tr>'}</tbody></table></section></div>
  <section class="panel card" style="margin-bottom:18px"><h2>Returns calculator</h2>
    <div class="calc-form"><div class="seg" role="group" aria-label="Mode"><button data-m="sip" class="on">Monthly SIP</button><button data-m="lumpsum">Lumpsum</button></div>
      <label>Amount (₹)<input class="field" id="camt" type="number" min="100" step="100" value="10000" style="width:130px"></label>
      <label>Period<select class="field" id="cyrs"></select></label></div><div id="cres"></div></section>
  <section class="panel card"><h2>Peers in ${esc(f.sub_category)}</h2>${d.peers.length ? `<div class="tablewrap"><table class="t"><thead><tr><th>Fund</th><th class="num">AUM (Cr)</th><th class="num">1Y (%)</th><th class="num">3Y CAGR (%)</th><th class="num">5Y CAGR (%)</th><th class="num">Expense (%)</th><th class="num">Sharpe</th></tr></thead><tbody>${d.peers.map(p => `<tr><td><a href="#/fund/${p.id}"><b>${esc(p.name)}</b></a></td><td class="num">${fnum(p.aum)}</td><td class="num ${tone('ret_1y', p.ret_1y)}">${fnum(p.ret_1y)}</td><td class="num ${tone('cagr_3y', p.cagr_3y)}">${fnum(p.cagr_3y)}</td><td class="num ${tone('cagr_5y', p.cagr_5y)}">${fnum(p.cagr_5y)}</td><td class="num">${fnum(p.expense_ratio)}</td><td class="num">${fnum(p.sharpe)}</td></tr>`).join('')}</tbody></table></div>` : '<div class="muted">No peers.</div>'}</section>`;
  $('#wbtn').onclick = () => { if (need('watchlist')) toggleWatch(id); }; $('#cbtn').onclick = () => { if (need('compare')) toggleCompare(id); };
  // NAV chart with range buttons
  const R = [['1M', 4], ['6M', 26], ['1Y', 52], ['3Y', 156], ['5Y', 260], ['MAX', 1e9]], N = nv.nav.length - 1;
  $('#ranges').innerHTML = R.filter(([l, w]) => w <= N || l === 'MAX').map(([l]) => `<button data-r="${l}" class="${l === (N >= 156 ? '3Y' : 'MAX') ? 'on' : ''}">${l}</button>`).join('');
  const draw = l => { const w = Math.min(R.find(r => r[0] === l)[1], N), vals = nv.nav.slice(-(w + 1)), dts = nv.dates.slice(-(w + 1)); const ch = (vals.at(-1) / vals[0] - 1) * 100; lineChart($('#navchart'), dts, [{ name: f.name, values: vals }], { area: true }); $('#navchart').insertAdjacentHTML('afterbegin', `<div class="${ch >= 0 ? 'pos' : 'neg'}" style="font:600 18px var(--serif)">${ch >= 0 ? '+' : ''}${fnum(ch)}% <span class="muted" style="font:12px var(--sans)">over ${l === 'MAX' ? 'full history' : l}</span></div>`); };
  $('#ranges').onclick = e => { const b = e.target.closest('[data-r]'); if (!b) return; $$('#ranges button').forEach(x => x.classList.toggle('on', x === b)); draw(b.dataset.r); };
  draw(N >= 156 ? '3Y' : 'MAX');
  // calculator
  const maxY = Math.min(10, Math.floor(N / 52));
  const ysel = $('#cyrs'); ysel.innerHTML = maxY < 1 ? '<option value="0">Not enough history</option>' : Array.from({ length: maxY }, (_, i) => `<option value="${i + 1}" ${i + 1 === Math.min(3, maxY) ? 'selected' : ''}>${i + 1} year${i ? 's' : ''}</option>`).join('');
  let mode = 'sip';
  const calc = async () => {
    const out = $('#cres'), yrs = +ysel.value, amt = +$('#camt').value;
    if (!yrs) { out.innerHTML = '<div class="muted">This fund has less than 1 year of NAV history.</div>'; return; }
    if (!(amt > 0)) { out.innerHTML = '<div class="err">Enter a positive amount.</div>'; return; }
    try {
      const r = await J(`/funds/${id}/calculator?mode=${mode}&amount=${amt}&years=${yrs}`);
      out.innerHTML = `<div class="strip" style="margin-bottom:10px"><div><small>Invested</small><b>₹${fnum(r.invested, 0)}</b></div><div><small>Value today</small><b>₹${fnum(r.value, 0)}</b></div><div><small>Gain</small><b class="${r.gain >= 0 ? 'pos' : 'neg'}">₹${fnum(r.gain, 0)}</b></div><div><small>Absolute return</small><b class="${r.abs_return >= 0 ? 'pos' : 'neg'}">${fnum(r.abs_return)}%</b></div><div><small>${mode === 'sip' ? 'XIRR' : 'CAGR'}</small><b class="${r.xirr >= 0 ? 'pos' : 'neg'}">${fnum(r.xirr)}%</b></div></div><div id="cchart"></div>`;
      lineChart($('#cchart'), r.series.map(s => s.d), [{ name: 'Invested', values: r.series.map(s => s.invested) }, { name: 'Value', values: r.series.map(s => s.value) }], { h: 240, fmt: v => '₹' + fnum(v, 0) });
    } catch (e) { out.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  };
  $('.seg').onclick = e => { const b = e.target.closest('[data-m]'); if (!b) return; mode = b.dataset.m; $$('.seg button').forEach(x => x.classList.toggle('on', x === b)); calc(); };
  $('#camt').oninput = debounce(calc, 300); ysel.onchange = calc; calc();
}

/* ---------- compare page */
async function pageCompare(arg, tok = ROUTE) {
  let ids = arg ? arg.split(',').map(Number).filter(Number.isInteger) : [...CMP];
  ids = [...new Set(ids)].slice(0, 4);
  if (arg) { CMP.clear(); ids.forEach(i => CMP.add(i)); store.set('fs_cmp', ids); }
  if (ids.length < 2) { $('#app').innerHTML = `<div class="page-head"><h1>Compare funds</h1></div><div class="panel empty"><h3>Pick at least two funds</h3><p>Tick the checkbox beside funds in the screener (up to 4), then press Compare.</p><a class="btn primary" href="#/">Go to screener</a></div>`; return; }
  const d = await J('/compare?ids=' + ids.join(','));
  if (tok !== ROUTE) return;
  const rows = M.groups.map(g => {
    const ms = M.metrics.filter(m => m.group === g && !['sub_category'].includes(m.key) && d.funds.some(f => f[m.key] != null));
    if (!ms.length) return '';
    return `<tr class="grp"><td colspan="${d.funds.length + 1}">${esc(g)}</td></tr>` + ms.map(m => {
      const vals = d.funds.map(f => f[m.key]); let best = null;
      if (m.type === 'num' && m.better) { const nn = vals.filter(v => v != null); if (nn.length > 1) best = m.better === 'high' ? Math.max(...nn) : Math.min(...nn); }
      return `<tr><td>${esc(m.label)}${m.unit ? ` <span class="muted">(${esc(m.unit)})</span>` : ''}</td>${vals.map(v => `<td class="${best != null && v === best ? 'best' : ''}">${m.type === 'num' ? fnum(v, m.dec) : esc(v ?? '–')}</td>`).join('')}</tr>`;
    }).join('');
  }).join('');
  $('#app').innerHTML = `<div class="page-head"><div><a class="crumb" href="#/">← Back to screener</a><h1>Compare funds</h1></div><button class="btn" id="cclear">Clear all</button></div>
    <section class="panel card" style="margin-bottom:18px"><h2>Growth of 100 (common history of ${fnum(d.dates.length / 52, 1)} yrs)</h2><div id="cchart"></div></section>
    <section class="panel card cmp-table"><table><thead><tr><th></th>${d.funds.map(f => `<th><a href="#/fund/${f.id}"><b>${esc(f.name)}</b></a><div class="muted" style="font-weight:400">${esc(f.option)} · ${esc(f.plan)}</div><button class="link" data-rm="${f.id}">Remove</button></th>`).join('')}</tr></thead><tbody>${rows}</tbody></table>
    <p class="muted" style="font-size:12px">Green marks the best value in each row where a higher or lower number is clearly better.</p></section>`;
  lineChart($('#cchart'), d.dates, d.funds.map((f, i) => ({ name: `${i + 1}. ${f.name} · ${f.option} ${f.plan}`, short: String(i + 1), values: d.series[i] })));
  $('#cclear').onclick = () => { CMP.clear(); store.set('fs_cmp', []); location.hash = '#/compare'; };
  $('.cmp-table').onclick = e => { const b = e.target.closest('[data-rm]'); if (!b) return; CMP.delete(+b.dataset.rm); store.set('fs_cmp', [...CMP]); location.hash = '#/compare/' + [...CMP].join(','); };
}

/* ---------- saved screens page */
async function pageScreens(tok = ROUTE) {
  const list = await J('/screens');
  if (tok !== ROUTE) return;
  const byId = Object.fromEntries(list.map(s => [s.id, s]));
  const local = !Data.cloud() && typeof Auth !== 'undefined' && Auth.enabled;
  const card = s => `<div class="panel scard"><div><span class="pill ${s.preset ? 'accent' : ''}">${s.preset ? 'Preset' : 'Mine'}</span></div><h3>${esc(s.name)}</h3><div class="muted">${esc(s.note) || '—'}</div>
    <div class="muted" style="font-size:12px">${(s.config.filters || []).length} filters · sorted by ${esc(metric(s.config.sort_key)?.label || s.config.sort_key)}</div>
    <div class="row"><a class="btn primary sm" href="#/screen/${esc(s.id)}">Open</a><button class="btn sm" data-copy="${esc(s.id)}">Copy link</button>${s.mine ? `<button class="btn sm danger" data-del="${esc(s.id)}">Delete</button>` : ''}</div></div>`;
  const mine = list.filter(s => !s.preset), pre = list.filter(s => s.preset);
  $('#app').innerHTML = `<div class="page-head"><div><div class="hello">${greeting()} :)</div><h1>Saved screens</h1></div><button class="btn primary" id="nsc">+ New screen</button></div>
    <h2 style="margin:8px 0 12px">My screens</h2>${local ? '<p class="muted" style="margin-top:-6px">Saved in this browser only. <a href="#/login"><b>Log in</b></a> to keep them on every device.</p>' : ''}${mine.length ? `<div class="screens-grid">${mine.map(card).join('')}</div>` : '<div class="panel empty"><p>You have no saved screens yet. Build one in the screener and press <b>Save screen</b>.</p></div>'}
    <h2 style="margin:26px 0 12px">Starter screens</h2><div class="screens-grid">${pre.map(card).join('')}</div>`;
  $('#nsc').onclick = () => { resetScreen(); location.hash = '#/'; };
  $('#app').onclick = async e => {
    const c = e.target.closest('[data-copy]'), x = e.target.closest('[data-del]');
    if (c) { const u = Data.shareUrl(byId[c.dataset.copy]); try { await navigator.clipboard.writeText(u); toast('Link copied'); } catch { prompt('Copy this link', u); } }
    if (x && confirm('Delete this screen?')) { try { await api('/screens/' + x.dataset.del, { method: 'DELETE' }); if (S.screenId === x.dataset.del) resetScreen(); toast('Deleted'); pageScreens(); } catch (er) { toast(er.message); } }
  };
}

/* ---------- boot */
/* login / logout: switch between browser storage and the account, moving anything saved in this browser into the account once */
window.addEventListener('fx-auth', async () => {
  if (!M) return;
  try {
    const n = await Data.syncAfterLogin();
    if (n.screens || n.watch) toast(`Moved ${n.screens} saved screen${n.screens === 1 ? '' : 's'} and ${n.watch} watchlist fund${n.watch === 1 ? '' : 's'} into your account`, 5000);
    else await Data.refreshWatch();
  } catch (e) { toast('Could not load your saved items: ' + e.message, 5000); }
  WATCH = new Set(Data.watchIds()); renderTray();
  const hadScreen = !!S.screenId; S.screenId = null; S.mine = false;
  if (/^#\/(login|signup|verify|forgot)/.test(location.hash)) return;
  if ($('#tbl') && !hadScreen) {                                  // screener already open: refresh in place so we never steal focus from a field being typed in
    if (!$('#panel').contains(document.activeElement)) renderPanel();
    loadResults();
  } else route();
});

(async function boot() {
  const th = store.get('fs_theme', null); if (th) document.documentElement.dataset.theme = th;
  $('#theme').onclick = () => { const cur = document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'); const nx = cur === 'dark' ? 'light' : 'dark'; document.documentElement.dataset.theme = nx; store.set('fs_theme', nx); };
  try {
    await Data.init();
    M = await J('/meta'); M.by = Object.assign(Object.create(null), Object.fromEntries(M.metrics.map(m => [m.key, m])));
    try { (await J('/watchlist')).ids.forEach(i => WATCH.add(i)); } catch { /* ignore */ }
    const demo = M.source === 'demo', asOf = new Date(M.as_of + 'T00:00:00').toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
    const d = $('#demo');                                          // the top banner exists ONLY as a safeguard when generated demo data is being served
    d.classList.toggle('hidden', !demo);
    d.textContent = demo ? `Demo dataset: fund houses, NAVs and holdings are generated for illustration (NAV as of ${asOf}). Not real market data and not investment advice.` : '';
    const f = $('#foot'); f.classList.remove('hidden');
    f.textContent = demo ? 'Demo data. Not investment advice.'
      : `NAV data: AMFI India, as of ${asOf}. Returns, risk and ratios are computed by Fundrix N18 from NAV history${M.has_aum ? '' : '; AUM, expense ratio and holdings are not included yet'}. For information only; not investment advice.`;
    resetScreen();
    route();
  } catch (e) { $('#app').innerHTML = `<div class="panel empty"><h3>Could not load the fund data</h3><p>${esc(e.message)}</p></div>`; }
})();
