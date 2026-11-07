'use strict';
/* Data: replaces the old API server. app.js still calls api('/screen') etc.; Data.request() answers those calls in the browser.
   - Fund data + analytics: static JSON built daily by pipeline/ (data/*.json), queried by Engine.
   - Saved screens + watchlist: Supabase (RLS-protected tables) when logged in, localStorage when logged out. */
const Data = (() => {
  const CFG = window.FUNDRIX_CONFIG || {};
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  let E = null, META = null, BUILD = '', DATES = [], PRESETS = [], WATCH = new Set();
  const shards = new Map();

  const ls = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode / quota */ } },
  };
  const bad = (msg, status = 400) => { const e = new Error(msg); e.status = status; return e; };
  const cloud = () => !!(CFG.supabaseUrl && CFG.anonKey && typeof Auth !== 'undefined' && Auth.user());

  async function getJSON(url, opts) {
    let r;
    try { r = await fetch(url, opts); } catch { throw bad('Could not load fund data. Check your connection and reload.', 0); }
    if (!r.ok) throw bad(`Could not load fund data (${url.split('?')[0]}: HTTP ${r.status}). Has the data build run yet?`, r.status);
    return r.json();
  }
  async function init() {
    const meta = await getJSON('data/meta.json', { cache: 'no-cache' });
    BUILD = meta.build;
    const get = n => getJSON(`data/${n}.json?v=${BUILD}`);
    const [mx, funds, dates, presets] = await Promise.all([get('metrics'), get('funds'), get('dates'), get('presets')]);
    E = Engine.create({ metrics: mx.metrics, groups: mx.groups, functions: mx.functions, funds, dates, meta });
    META = meta; DATES = dates; PRESETS = presets; shards.clear();
    try { await refreshWatch(); } catch { WATCH = new Set(); }          // an expired login must never stop the site from loading
    return meta;
  }
  async function shard(id) {
    const n = id % META.shards;
    if (!shards.has(n)) shards.set(n, getJSON(`data/nav/${n}.json?v=${BUILD}`).catch(e => { shards.delete(n); throw e; }));
    return shards.get(n);
  }

  /* ---------------- Supabase REST (user's own rows; RLS enforces ownership on the server) */
  async function rest(method, path, body, { prefer, anon } = {}) {
    const tk = anon ? CFG.anonKey : await Auth.token();
    if (!tk) throw bad('Your session has expired. Please log in again.', 401);
    let r;
    try {
      r = await fetch(`${CFG.supabaseUrl}/rest/v1/${path}`, { method, headers: { apikey: CFG.anonKey, Authorization: 'Bearer ' + tk, 'Content-Type': 'application/json', ...(prefer ? { Prefer: prefer } : {}) }, body: body === undefined ? undefined : JSON.stringify(body) });
    } catch { throw bad('Could not reach your account storage. Check your connection.', 0); }
    if (!r.ok) {
      let msg = 'Your account storage rejected the request.'; try { const j = await r.json(); if (j && j.message) msg = j.message; } catch { /* no body */ }
      throw bad(msg, r.status);
    }
    if (r.status === 204) return null;
    try { return await r.json(); } catch { return null; }
  }

  /* ---------------- watchlist */
  async function refreshWatch() {
    if (cloud()) WATCH = new Set((await rest('GET', 'watchlist?select=fund_id')).map(r => r.fund_id));
    else WATCH = new Set((ls.get('fx_watch', []) || []).filter(i => Number.isInteger(i) && E.byIdHas(i)));
  }
  const saveLocalWatch = () => ls.set('fx_watch', [...WATCH]);
  async function addWatch(id) {
    E.getFund(id);
    if (cloud()) await rest('POST', 'watchlist', { fund_id: id }, { prefer: 'resolution=ignore-duplicates,return=minimal' });
    WATCH.add(id); if (!cloud()) saveLocalWatch();
  }
  async function delWatch(id) {
    if (cloud()) await rest('DELETE', `watchlist?fund_id=eq.${encodeURIComponent(id)}`);
    WATCH.delete(id); if (!cloud()) saveLocalWatch();
  }

  /* ---------------- saved screens */
  const localScreens = () => ls.get('fx_screens', []) || [];
  const fromRow = (r, mine) => ({ id: r.id, name: r.name, note: r.note || '', config: r.config, created: r.created_at || '', updated: r.updated_at || '', preset: false, mine });
  const presetScreens = () => PRESETS.map(p => ({ id: p.id, name: p.name, note: p.note, config: p.config, created: '', updated: '', preset: true, mine: false }));
  function checkScreen(b) {
    if (!b || typeof b.name !== 'string' || !b.name.trim() || b.name.trim().length > 80) throw bad('Screen name must be 1-80 characters.', 422);
    if (typeof (b.note || '') !== 'string' || (b.note || '').length > 500) throw bad('Note must be at most 500 characters.', 422);
    if (!b.config || typeof b.config !== 'object' || JSON.stringify(b.config).length > 19000) throw bad('Screen is too large to save.', 422);
  }
  async function listScreens() {
    const mine = cloud() ? (await rest('GET', 'screens?select=id,name,note,config,created_at,updated_at&order=updated_at.desc')).map(r => fromRow(r, true))
      : localScreens().slice().sort((a, b) => (b.updated || '').localeCompare(a.updated || '')).map(s => ({ ...s, preset: false, mine: true }));
    return [...mine, ...presetScreens()];
  }
  async function getScreen(id) {
    const p = presetScreens().find(s => s.id === id); if (p) return p;
    if (id.startsWith('l_')) { const s = localScreens().find(x => x.id === id); if (!s) throw bad('Screen not found', 404); return { ...s, preset: false, mine: true }; }
    if (!UUID.test(id)) throw bad('Screen not found', 404);
    if (cloud()) {
      const own = await rest('GET', `screens?id=eq.${id}&select=id,name,note,config,created_at,updated_at`);
      if (own && own.length) return fromRow(own[0], true);
    }
    const shared = await rest('POST', 'rpc/get_shared_screen', { p_id: id }, { anon: !cloud() });
    if (!shared || !shared.length) throw bad('Screen not found', 404);
    return fromRow(shared[0], false);
  }
  async function createScreen(b) {
    checkScreen(b);
    if (cloud()) { const r = await rest('POST', 'screens', { name: b.name.trim(), note: b.note || '', config: b.config }, { prefer: 'return=representation' }); return fromRow(r[0], true); }
    const now = new Date().toISOString(), s = { id: 'l_' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36), name: b.name.trim(), note: b.note || '', config: b.config, created: now, updated: now };
    ls.set('fx_screens', [s, ...localScreens()]); return { ...s, preset: false, mine: true };
  }
  async function updateScreen(id, b) {
    checkScreen(b);
    if (id.startsWith('preset-')) throw bad('You can only modify your own screens', 403);
    if (cloud() && UUID.test(id)) {
      const r = await rest('PATCH', `screens?id=eq.${id}`, { name: b.name.trim(), note: b.note || '', config: b.config, updated_at: new Date().toISOString() }, { prefer: 'return=representation' });
      if (!r || !r.length) throw bad('You can only modify your own screens', 403);
      return fromRow(r[0], true);
    }
    const all = localScreens(), i = all.findIndex(x => x.id === id);
    if (i < 0) throw bad('You can only modify your own screens', 403);
    all[i] = { ...all[i], name: b.name.trim(), note: b.note || '', config: b.config, updated: new Date().toISOString() }; ls.set('fx_screens', all);
    return { ...all[i], preset: false, mine: true };
  }
  async function deleteScreen(id) {
    if (id.startsWith('preset-')) throw bad('You can only modify your own screens', 403);
    if (cloud() && UUID.test(id)) { await rest('DELETE', `screens?id=eq.${id}`); return; }
    const all = localScreens(); if (!all.some(x => x.id === id)) throw bad('You can only modify your own screens', 403);
    ls.set('fx_screens', all.filter(x => x.id !== id));
  }

  /** After login: move anything saved in this browser into the account (once), then reload the watchlist from the account. */
  async function syncAfterLogin() {
    const out = { watch: 0, screens: 0 };
    if (!cloud()) return out;
    const ids = (ls.get('fx_watch', []) || []).filter(i => Number.isInteger(i) && E.byIdHas(i));
    if (ids.length) { await rest('POST', 'watchlist', ids.map(fund_id => ({ fund_id })), { prefer: 'resolution=ignore-duplicates,return=minimal' }); out.watch = ids.length; }
    ls.set('fx_watch', []);
    for (const s of localScreens()) {                                         // one at a time; each is removed locally as soon as it is safely stored
      await rest('POST', 'screens', { name: s.name, note: s.note || '', config: s.config });
      ls.set('fx_screens', localScreens().filter(x => x.id !== s.id)); out.screens++;
    }
    await refreshWatch();
    return out;
  }

  /* ---------------- share links for browser-only screens: the whole screen is encoded in the URL */
  const b64 = s => btoa(String.fromCharCode(...new TextEncoder().encode(s))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  const unb64 = s => new TextDecoder().decode(Uint8Array.from(atob(s.replace(/-/g, '+').replace(/_/g, '/')), c => c.charCodeAt(0)));
  const encodeShare = s => b64(JSON.stringify({ n: s.name, t: s.note || '', c: s.config }));
  function decodeShare(str) {
    if (typeof str !== 'string' || str.length > 12000) throw bad('This share link is invalid or too long.', 400);
    let o; try { o = JSON.parse(unb64(str)); } catch { throw bad('This share link is invalid.', 400); }
    if (!o || typeof o !== 'object') throw bad('This share link is invalid.', 400);
    return { name: String(o.n || 'Shared screen').slice(0, 80), note: String(o.t || '').slice(0, 500), config: E.sanitizeConfig(o.c) };
  }
  function shareUrl(s) {
    const base = location.origin + location.pathname;
    return (UUID.test(s.id) || s.id.startsWith('preset-')) ? `${base}#/screen/${s.id}` : `${base}#/s/${encodeShare(s)}`;
  }

  /* ---------------- the fake "server" */
  const resp = (data, headers = {}) => ({ ok: true, status: 200, json: async () => data, text: async () => typeof data === 'string' ? data : JSON.stringify(data),
    blob: async () => new Blob([data], { type: 'text/csv;charset=utf-8' }), headers: { get: k => headers[k] ?? null } });

  async function request(path, opts = {}) {
    if (!E) throw bad('Data is not loaded yet.', 503);
    const method = (opts.method || 'GET').toUpperCase(), u = new URL(path, 'http://x'), seg = u.pathname.split('/').filter(Boolean), q = Object.fromEntries(u.searchParams), body = opts.body, ctx = { watch: WATCH };
    switch (seg[0]) {
      case 'meta': return resp(E.metaFor());
      case 'screen': return resp(E.screen(body, ctx));
      case 'export': { const r = E.exportCsv(body, ctx); return resp(r.text, { 'X-Total': String(r.total) }); }
      case 'custom': return resp(E.validate(body && body.expr));
      case 'funds': {
        const id = Number(seg[1]); if (!Number.isInteger(id)) throw bad('Invalid fund', 404);
        if (!seg[2]) { const sh = await shard(id); E.getFund(id); const d = E.fundDetail(id, sh.detail[String(id)]); d.watch = WATCH.has(id); return resp(d); }
        const nav = (await shard(id)).nav[String(id)]; if (!nav) throw bad('Fund not found', 404);
        if (seg[2] === 'nav') return resp({ dates: DATES.slice(-nav.length), nav });
        if (seg[2] === 'calculator') return resp(E.calculator(id, nav, { mode: q.mode, amount: q.amount === undefined ? undefined : Number(q.amount), years: q.years === undefined ? undefined : Number(q.years) }));
        break;
      }
      case 'compare': {
        const ids = String(q.ids || '').split(',').filter(s => s.trim()).map(Number); if (ids.some(i => !Number.isInteger(i))) throw bad('ids must be comma separated integers');
        if (ids.length < 2 || ids.length > 4) throw bad('Compare 2 to 4 funds');
        const navs = await Promise.all(ids.map(async i => { const n = (await shard(i)).nav[String(i)]; if (!n) throw bad('Fund not found', 404); return n; }));
        return resp(E.compare(ids, navs));
      }
      case 'watchlist': {
        if (!seg[1]) return resp({ ids: [...WATCH].sort((a, b) => a - b) });
        const id = Number(seg[1]); if (!Number.isInteger(id)) throw bad('Invalid fund', 404);
        if (method === 'PUT') await addWatch(id); else if (method === 'DELETE') await delWatch(id); else throw bad('Method not allowed', 405);
        return resp({ ok: true });
      }
      case 'screens': {
        if (!seg[1]) { if (method === 'POST') return resp(await createScreen(body)); return resp(await listScreens()); }
        const id = decodeURIComponent(seg[1]);
        if (method === 'GET') return resp(await getScreen(id));
        if (method === 'PUT') return resp(await updateScreen(id, body));
        if (method === 'DELETE') { await deleteScreen(id); return resp({ ok: true }); }
      }
    }
    throw bad('Not found', 404);
  }

  return { init, request, refreshWatch, syncAfterLogin, encodeShare, decodeShare, shareUrl,
    sanitize: c => E.sanitizeConfig(c), watchIds: () => [...WATCH], meta: () => META, cloud };
})();
