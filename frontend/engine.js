/* Engine: everything the old API server did, running in the browser on static JSON.
   Pure (no DOM, no network) so it can be unit-tested in Node and compared against the Python implementation (tests/test_parity.py).
   Usage: const E = Engine.create({ metrics, groups, functions, funds, dates, meta });  E.screen(req, {watch:Set}) ... */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(); else root.Engine = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /* ---------------------------------------------------------------- safe formula language (no eval) */
  const FUNCS = ['abs', 'min', 'max', 'sqrt', 'log'];
  const isFunc = n => FUNCS.includes(n);
  class ExprError extends Error {}
  const NULLV = { null: true };                         // thrown internally when a value is missing/undefined

  function tokenize(src) {
    const toks = []; let i = 0;
    while (i < src.length) {
      const c = src[i];
      if (/\s/.test(c)) { i++; continue; }
      const rest = src.slice(i); let m;
      if ((m = /^(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?/.exec(rest))) { toks.push({ t: 'num', v: parseFloat(m[0]) }); i += m[0].length; continue; }
      if ((m = /^[A-Za-z_][A-Za-z0-9_]*/.exec(rest))) { toks.push({ t: 'name', v: m[0] }); i += m[0].length; continue; }
      if (rest.startsWith('**')) { toks.push({ t: 'op', v: '**' }); i += 2; continue; }
      if ('+-*/%(),'.includes(c)) { toks.push({ t: 'op', v: c }); i++; continue; }
      throw new ExprError(`Syntax error: unexpected '${c}'`);
    }
    return toks;
  }

  /** Grammar (Python precedence): expr = term (+|- term)* ; term = unary (*|/|% unary)* ; unary = (+|-) unary | power ; power = atom [** unary] */
  function parseExpr(expr, allowed) {
    if (typeof expr !== 'string' || !expr.trim()) throw new ExprError('Expression is empty');
    if (expr.length > 200) throw new ExprError('Expression longer than 200 characters');
    const toks = tokenize(expr.trim()); let p = 0, nodes = 0;
    const peek = () => toks[p], isOp = v => peek() && peek().t === 'op' && peek().v === v;
    const bump = () => { if (++nodes > 60) throw new ExprError('Expression too complex'); };
    const expr_ = () => { let l = term(); while (isOp('+') || isOp('-')) { const op = toks[p++].v, r = term(); bump(); l = { k: 'bin', op, l, r }; } return l; };
    const term = () => { let l = unary(); while (isOp('*') || isOp('/') || isOp('%')) { const op = toks[p++].v, r = unary(); bump(); l = { k: 'bin', op, l, r }; } return l; };
    const unary = () => { if (isOp('+') || isOp('-')) { const op = toks[p++].v; bump(); return { k: 'un', op, x: unary() }; } return power(); };
    const power = () => { const b = atom(); if (isOp('**')) { p++; bump(); return { k: 'bin', op: '**', l: b, r: unary() }; } return b; };
    const atom = () => {
      const t = toks[p++];
      if (!t) throw new ExprError('Syntax error: unexpected end of expression');
      if (t.t === 'num') { bump(); return { k: 'num', v: t.v }; }
      if (t.t === 'name') {
        if (isOp('(')) {
          p++; if (!isFunc(t.v)) throw new ExprError('Only abs, min, max, sqrt, log may be called');
          const args = [];
          if (!isOp(')')) { args.push(expr_()); while (isOp(',')) { p++; args.push(expr_()); } }
          if (!isOp(')')) throw new ExprError("Syntax error: missing ')'");
          p++; bump(); return { k: 'call', f: t.v, args };
        }
        if (!allowed.has(t.v) && !isFunc(t.v)) throw new ExprError(`Unknown metric '${t.v}'`);
        bump(); return { k: 'name', v: t.v };
      }
      if (t.v === '(') { const e = expr_(); if (!isOp(')')) throw new ExprError("Syntax error: missing ')'"); p++; return e; }
      throw new ExprError(`Syntax error: unexpected '${t.v}'`);
    };
    const tree = expr_();
    if (p < toks.length) throw new ExprError(`Syntax error: unexpected '${toks[p].v}'`);
    return tree;
  }

  function ev(n, row) {
    switch (n.k) {
      case 'num': return n.v;
      case 'name': { const v = row[n.v]; if (v == null) throw NULLV; return v; }
      case 'un': { const x = ev(n.x, row); return n.op === '-' ? -x : x; }
      case 'bin': {
        const a = ev(n.l, row), b = ev(n.r, row);
        switch (n.op) {
          case '+': return a + b; case '-': return a - b; case '*': return a * b;
          case '/': if (b === 0) throw NULLV; return a / b;
          case '%': if (b === 0) throw NULLV; return a - b * Math.floor(a / b);          // Python's modulo
          case '**': if (Math.abs(b) > 10 || Math.abs(a) > 1e6) throw NULLV; return Math.pow(a, b);
        }
        throw NULLV;
      }
      case 'call': {
        const a = n.args.map(x => ev(x, row));
        if (n.f === 'min' || n.f === 'max') { if (a.length < 2) throw NULLV; return Math[n.f](...a); }
        if (a.length !== 1) throw NULLV;
        if (n.f === 'abs') return Math.abs(a[0]);
        if (n.f === 'sqrt') { if (a[0] < 0) throw NULLV; return Math.sqrt(a[0]); }
        if (a[0] <= 0) throw NULLV; return Math.log(a[0]);
      }
    }
    throw NULLV;
  }
  /** number or null (null for missing metrics, division by zero, NaN, infinity ...) */
  function evaluate(tree, row) {
    let v;
    try { v = ev(tree, row); } catch (e) { if (e === NULLV) return null; throw e; }
    return typeof v === 'number' && Number.isFinite(v) ? v : null;
  }

  /* ---------------------------------------------------------------- helpers */
  const NAME_RE = /^[A-Za-z0-9 _\-%/.()]{1,40}$/;
  function pyRound(x) {                                   // Python's round(): halves go to the even neighbour
    const f = Math.floor(x), d = x - f;
    return d < .5 ? f : d > .5 ? f + 1 : (f % 2 === 0 ? f : f + 1);
  }
  const round = (x, d) => { const m = Math.pow(10, d); return Math.round((x + Number.EPSILON * Math.sign(x)) * m) / m; };
  const bad = (msg, status = 400) => { const e = new Error(msg); e.status = status; return e; };
  const isNum = x => typeof x === 'number' && Number.isFinite(x);

  function create(data) {
    const metrics = data.metrics, funds = data.funds, dates = data.dates || [];
    const BY_KEY = Object.create(null); metrics.forEach(m => { BY_KEY[m.key] = m; });          // no prototype: '__proto__' / 'constructor' are not metrics
    const NUM_KEYS = metrics.filter(m => m.type === 'num').map(m => m.key), NUM_SET = new Set(NUM_KEYS);
    const byId = new Map(funds.map(f => [f.id, f]));
    const as_of = (data.meta && data.meta.as_of) || '', source = (data.meta && data.meta.source) || 'demo';

    /* ---- meta (same shape as the old /api/meta) */
    function metaFor() {
      const ms = metrics.map(m => {
        const o = { ...m };
        if (m.type === 'num') {
          let mn = Infinity, mx = -Infinity;
          for (const f of funds) { const v = f[m.key]; if (v != null) { if (v < mn) mn = v; if (v > mx) mx = v; } }
          o.min = mn === Infinity ? 0 : mn; o.max = mx === -Infinity ? 0 : mx;
        } else o.options = [...new Set(funds.map(f => f[m.key]).filter(v => v != null))].sort();
        return o;
      });
      const tree = {}, counts = {};
      for (const f of funds) { (tree[f.category] = tree[f.category] || new Set()).add(f.sub_category); counts[f.category] = (counts[f.category] || 0) + 1; }
      const categories = {}; Object.keys(tree).sort().forEach(k => { categories[k] = [...tree[k]].sort(); });
      return { metrics: ms, groups: data.groups, categories, category_counts: counts, total: funds.length, as_of, source, functions: data.functions || FUNCS,
        has_aum: !!(data.meta && data.meta.has_aum !== undefined ? data.meta.has_aum : true), has_expense: !!(data.meta && data.meta.has_expense !== undefined ? data.meta.has_expense : true) };
    }

    /* ---- screening */
    function customNodes(custom) {
      const nodes = Object.create(null);
      for (const c of custom || []) {
        if (!c || typeof c.name !== 'string' || !NAME_RE.test(c.name)) throw bad(`Invalid custom filter name '${c && c.name}'`);
        try { nodes['c:' + c.name] = parseExpr(c.expr, NUM_SET); } catch (e) { if (e instanceof ExprError) throw bad(`Custom filter '${c.name}': ${e.message}`); throw e; }
      }
      return nodes;
    }
    function normReq(r) {
      r = r || {};
      const q = { universe: r.universe || 'all', ids: r.ids || [], filters: r.filters || [], custom: r.custom || [], search: r.search || '', sort_key: r.sort_key || 'aum',
        sort_dir: r.sort_dir || 'desc', page: r.page === undefined ? 1 : r.page, page_size: r.page_size === undefined ? 20 : r.page_size, columns: r.columns || [] };
      if (!['all', 'watchlist', 'ids'].includes(q.universe)) throw bad('Invalid universe', 422);
      if (!['asc', 'desc'].includes(q.sort_dir)) throw bad('Invalid sort direction', 422);
      if (!Number.isInteger(q.page) || q.page < 1) throw bad('page must be >= 1', 422);
      if (!Number.isInteger(q.page_size) || q.page_size < 1 || q.page_size > 200) throw bad('page_size must be 1-200', 422);
      if (typeof q.search !== 'string' || q.search.length > 100) throw bad('search too long', 422);
      if (!Array.isArray(q.filters) || !Array.isArray(q.custom) || !Array.isArray(q.columns) || !Array.isArray(q.ids)) throw bad('Invalid request', 422);
      for (const f of q.filters) {
        if (!f || typeof f.key !== 'string') throw bad('Invalid filter', 422);
        if ((f.min != null && !isNum(f.min)) || (f.max != null && !isNum(f.max)) || (f.values != null && !Array.isArray(f.values))) throw bad('Invalid filter', 422);
      }
      return q;
    }
    function run(req, ctx, paginate) {
      const q = normReq(req), nodes = customNodes(q.custom), nodeKeys = Object.keys(nodes);
      for (const f of q.filters) {
        if (!BY_KEY[f.key] && !nodes[f.key]) throw bad(`Unknown filter '${f.key}'`);
        if (BY_KEY[f.key] && BY_KEY[f.key].type === 'cat' && f.values == null && (f.min != null || f.max != null)) throw bad(`'${f.key}' is categorical`);
        if (f.min != null && f.max != null && f.min > f.max) throw bad(`Filter '${f.key}': min is greater than max`);
      }
      if (!BY_KEY[q.sort_key] && !nodes[q.sort_key] && q.sort_key !== 'name') throw bad(`Unknown sort key '${q.sort_key}'`);
      let pool = funds;
      if (q.universe === 'watchlist') { const w = (ctx && ctx.watch) || new Set(); pool = funds.filter(f => w.has(f.id)); }
      else if (q.universe === 'ids') { const w = new Set(q.ids); pool = funds.filter(f => w.has(f.id)); }
      const term = q.search.trim().toLowerCase();
      let out = [];
      for (const f of pool) {
        if (term && !f.name.toLowerCase().includes(term) && !f.amc.toLowerCase().includes(term) && !f.sub_category.toLowerCase().includes(term)) continue;
        let row = f;
        if (nodeKeys.length) { row = Object.assign({}, f); for (const k of nodeKeys) row[k] = evaluate(nodes[k], f); }
        let ok = true;
        for (const fl of q.filters) {
          const v = row[fl.key];
          if (fl.values != null) { if (fl.values.length && !fl.values.includes(v)) { ok = false; break; } continue; }
          if (fl.min == null && fl.max == null) continue;
          if (v == null || (fl.min != null && v < fl.min) || (fl.max != null && v > fl.max)) { ok = false; break; }
        }
        if (ok) out.push(row);
      }
      const key = q.sort_key, desc = q.sort_dir === 'desc';
      const have = out.filter(r => r[key] != null), miss = out.filter(r => r[key] == null);
      have.sort((a, b) => {
        let x = a[key], y = b[key];
        if (typeof x === 'string') { x = x.toLowerCase(); y = String(y).toLowerCase(); }
        let c = x < y ? -1 : x > y ? 1 : 0;
        if (c === 0) c = a.id - b.id;
        return desc ? -c : c;
      });
      out = have.concat(miss);
      const total = out.length;
      if (paginate) { const st = (q.page - 1) * q.page_size; out = out.slice(st, st + q.page_size); }
      return { total, rows: out, nodes, q };
    }
    function screen(req, ctx) {
      const { total, rows, q } = run(req, ctx, true), cols = q.columns.filter(c => BY_KEY[c]), w = (ctx && ctx.watch) || new Set();
      const results = rows.map(r => {
        const d = { id: r.id, name: r.name, sub_category: r.sub_category, category: r.category, plan: r.plan, option: r.option };
        for (const c of cols) d[c] = r[c] === undefined ? null : r[c];
        for (const k of Object.keys(r)) if (k.startsWith('c:')) d[k] = r[k];
        d.watch = w.has(r.id);
        return d;
      });
      return { total, page: q.page, page_size: q.page_size, pages: Math.max(1, Math.ceil(total / q.page_size)), results, as_of };
    }
    const csvCell = v => { const s = String(v); return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
    function exportCsv(req, ctx) {
      const { total, rows, nodes, q } = run(req, ctx, false);
      const skip = new Set(['sub_category', 'plan']);
      let cols = q.columns.filter(c => BY_KEY[c] && !skip.has(c));
      if (!cols.length) cols = metrics.map(m => m.key).filter(k => !skip.has(k));
      const nk = Object.keys(nodes);
      const head = ['Name', 'Sub Category', 'Plan', ...cols.map(c => BY_KEY[c].label + (BY_KEY[c].unit ? ` (${BY_KEY[c].unit})` : '')), ...nk.map(k => k.slice(2))];
      const safe = v => typeof v === 'string' && v.length && '=+-@\t\r'.includes(v[0]) ? "'" + v : v;     // spreadsheet formula-injection guard
      const lines = [head.map(csvCell).join(',')];
      for (const r of rows) lines.push([safe(r.name), r.sub_category, r.plan, ...cols.map(c => r[c] == null ? '' : r[c]), ...nk.map(k => r[k] == null ? '' : round(r[k], 4))].map(csvCell).join(','));
      return { text: lines.join('\r\n') + '\r\n', total };
    }
    function validate(expr) {
      let node;
      try { node = parseExpr(expr, NUM_SET); } catch (e) { if (e instanceof ExprError) throw bad(e.message); throw e; }
      const vals = funds.map(f => evaluate(node, f)).filter(v => v != null).sort((a, b) => a - b);
      if (!vals.length) throw bad('Expression produces no values for any fund');
      const mid = vals.length >> 1, median = vals.length % 2 ? vals[mid] : (vals[mid - 1] + vals[mid]) / 2;
      return { ok: true, count: vals.length, min: round(vals[0], 4), max: round(vals[vals.length - 1], 4), median: round(median, 4) };
    }

    /* ---- fund detail / calculator / compare */
    function getFund(id) { const f = byId.get(id); if (!f) throw bad('Fund not found', 404); return f; }
    function fundDetail(id, detail) {
      const f = getFund(id);
      const group = funds.filter(g => g.sub_category === f.sub_category && g.plan === f.plan && g.option === f.option);
      const avg = {}, rank = {};
      for (const k of NUM_KEYS) {
        const vals = group.map(g => g[k]).filter(v => v != null);
        if (!vals.length) continue;
        avg[k] = round(vals.reduce((a, b) => a + b, 0) / vals.length, 2);
        if (f[k] != null && vals.length > 1) {
          const better = BY_KEY[k].better;
          if (better) { const beat = vals.filter(v => better === 'high' ? v < f[k] : v > f[k]).length; rank[k] = pyRound(beat / (vals.length - 1) * 100); }
        }
      }
      const peers = group.filter(g => g.id !== id).sort((a, b) => (b.aum || 0) - (a.aum || 0)).slice(0, 6).map(p => {
        const o = {}; for (const k of ['id', 'name', 'aum', 'ret_1y', 'cagr_3y', 'cagr_5y', 'expense_ratio', 'sharpe']) o[k] = p[k] === undefined ? null : p[k]; return o;
      });
      const eq = f.category === 'Equity' || ['Aggressive Hybrid Fund', 'Arbitrage Fund', 'Equity Savings Fund'].includes(f.sub_category);
      const tax = eq ? 'Equity-oriented: gains within 1 year are short-term (taxed at 20%); beyond 1 year long-term (12.5% on gains above ₹1.25 lakh a year).'
        : 'Non-equity: gains are generally taxed at your income-tax slab rate. Rules change; verify with a tax adviser.';
      const d = Object.assign({ manager: [], manager_since: null, launch: null, exit_load_days: 0, holdings: [], sectors: [] }, detail || {});
      return { fund: f, detail: d, category_avg: avg, rank, peers, group_size: group.length, tax, as_of };
    }
    function calculator(id, nav, p) {
      const f = getFund(id), mode = p.mode || 'sip', amount = p.amount === undefined ? 10000 : p.amount, years = p.years === undefined ? 3 : p.years;
      if (!['sip', 'lumpsum'].includes(mode)) throw bad('mode must be sip or lumpsum', 422);
      if (!isNum(amount) || !(amount > 0) || amount > 1e9) throw bad('amount must be between 0 and 1,000,000,000', 422);
      if (!Number.isInteger(years) || years < 1 || years > 10) throw bad('years must be 1-10', 422);
      const maxYears = Math.floor((nav.length - 1) / 52);
      if (maxYears < years) throw bad(`Fund has only ${f.age} years of history; choose at most ${maxYears} year(s)`);
      const w = years * 52, seg = nav.slice(-(w + 1)), T = w / 52;
      let flows, value;
      if (mode === 'lumpsum') { value = amount * seg[seg.length - 1] / seg[0]; flows = [[0, amount]]; }
      else {
        const pos = [...new Set(Array.from({ length: years * 12 }, (_, i) => Math.round(i * 52 / 12)).filter(x => x <= w))].sort((a, b) => a - b);
        flows = pos.map(x => [x / 52, amount]);
        value = pos.reduce((s, x) => s + amount / seg[x], 0) * seg[seg.length - 1];
      }
      const invested = flows.reduce((s, x) => s + x[1], 0);
      let lo = -0.99, hi = 20;
      const g = r => flows.reduce((s, [t, a]) => s + a * Math.pow(1 + r, T - t), 0) - value;
      for (let i = 0; i < 100; i++) { const mid = (lo + hi) / 2; if (g(mid) < 0) lo = mid; else hi = mid; }
      const xirr = (lo + hi) / 2, step = Math.max(1, Math.floor(w / 60)), series = [], all = dates.slice(-nav.length);
      for (let i = 0; i <= w; i += step) {
        const act = flows.filter(([t]) => t * 52 <= i), inv = act.reduce((s, x) => s + x[1], 0);
        const val = inv ? act.reduce((s, [t, a]) => s + a / seg[Math.round(t * 52)], 0) * seg[i] : 0;
        series.push({ d: all[all.length - (w + 1) + i], invested: round(inv, 2), value: round(val, 2) });
      }
      return { mode, invested: round(invested, 2), value: round(value, 2), gain: round(value - invested, 2), abs_return: round((value / invested - 1) * 100, 2), xirr: round(xirr * 100, 2), series };
    }
    function compare(ids, navs) {
      if (ids.length < 2 || ids.length > 4) throw bad('Compare 2 to 4 funds');
      const fs = ids.map(getFund), n = Math.min(...navs.map(x => x.length));
      return { funds: fs, dates: dates.slice(-n), series: navs.map(x => x.slice(-n).map(v => round(v / x[x.length - n] * 100, 2))) };
    }

    /* ---- untrusted screen configs (share links, cloud rows): keep only what the UI understands */
    function sanitizeConfig(c) {
      if (!c || typeof c !== 'object') throw bad('Invalid screen');
      const str = (x, n) => typeof x === 'string' ? x.slice(0, n) : '';
      const out = { universe: c.universe === 'watchlist' ? 'watchlist' : 'all', filters: [], custom: [], sort_key: 'aum', sort_dir: c.sort_dir === 'asc' ? 'asc' : 'desc', extra_cols: [], search: str(c.search, 100) };
      const customs = [];
      for (const x of (Array.isArray(c.custom) ? c.custom : []).slice(0, 10)) {
        if (!x || typeof x.name !== 'string' || !NAME_RE.test(x.name) || typeof x.expr !== 'string' || x.expr.length > 200) continue;
        try { parseExpr(x.expr, NUM_SET); } catch (e) { continue; }
        customs.push(x.name); out.custom.push({ name: x.name, expr: x.expr, min: isNum(x.min) ? x.min : 0, max: isNum(x.max) ? x.max : 1 });
      }
      for (const f of (Array.isArray(c.filters) ? c.filters : []).slice(0, 40)) {
        if (!f || typeof f.key !== 'string') continue;
        const ck = f.key.startsWith('c:') && customs.includes(f.key.slice(2));
        if (!BY_KEY[f.key] && !ck) continue;
        const o = { key: f.key };
        if (Array.isArray(f.values)) o.values = f.values.filter(v => typeof v === 'string').slice(0, 200).map(v => v.slice(0, 80));
        if (isNum(f.min)) o.min = f.min; if (isNum(f.max)) o.max = f.max;
        out.filters.push(o);
      }
      const sk = str(c.sort_key, 60);
      out.sort_key = BY_KEY[sk] || sk === 'name' || (sk.startsWith('c:') && customs.includes(sk.slice(2))) ? sk : 'aum';
      out.extra_cols = (Array.isArray(c.extra_cols) ? c.extra_cols : []).filter(k => typeof k === 'string' && BY_KEY[k]).slice(0, 60);
      return out;
    }

    return { metaFor, screen, exportCsv, validate, fundDetail, calculator, compare, getFund, sanitizeConfig, BY_KEY, NUM_KEYS, byIdHas: id => byId.has(id) };
  }

  return { create, parseExpr, evaluate, ExprError, pyRound, FUNCS };
});
