"""Fundrix N18 API. Run:  uvicorn backend.main:app --reload"""
import csv
import datetime as dt
import io
import json
import os
import re
import secrets
import sqlite3
import threading
from typing import Literal, Optional

import numpy as np
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import data as datamod
from . import expr as exprmod
from .metrics import BY_KEY, CAT_KEYS, GROUPS, METRICS, NUM_KEYS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("FUNDRIX_DB", os.path.join(ROOT, "data", "funds.db"))
_lock = threading.Lock()


class Store:
    """In-memory copy of the fund table (1.6k rows -> filtering in Python is instant) + SQLite for persistence."""

    def __init__(self, path):
        if not os.path.exists(path):
            datamod.build_db(path)
        self.path = path
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.funds = [json.loads(r["doc"]) for r in self.con.execute("SELECT doc FROM funds ORDER BY id")]
        self.by_id = {f["id"]: f for f in self.funds}
        g = lambda k: self.con.execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone()
        self.dates = json.loads(g("dates")["value"])
        self.as_of = g("as_of")["value"]
        self.source = (g("source") or {"value": "demo"})["value"]
        self._seed_presets()

    def q(self, sql, args=()):
        with _lock:
            return self.con.execute(sql, args).fetchall()

    def x(self, sql, args=()):
        with _lock:
            cur = self.con.execute(sql, args)
            self.con.commit()
            return cur

    def detail(self, fid):
        r = self.q("SELECT detail FROM funds WHERE id=?", (fid,))
        return json.loads(r[0][0]) if r else None

    def nav(self, fid):
        r = self.q("SELECT nav FROM funds WHERE id=?", (fid,))
        return json.loads(r[0][0]) if r else None

    def _seed_presets(self):
        presets = [
            ("preset-elss", "Top tax-saving ELSS funds", "ELSS funds with a 3Y track record, low cost and strong risk-adjusted return.",
             dict(filters=[dict(key="sub_category", values=["ELSS (Tax Saving)"]), dict(key="plan", values=["Growth"]), dict(key="option", values=["Direct"]),
                           dict(key="cagr_3y", min=8), dict(key="sharpe", min=0.3)], sort_key="sharpe")),
            ("preset-midcap", "High-return mid-cap funds", "Mid cap funds with 3Y CAGR above 25% and expense ratio under 1%.",
             dict(filters=[dict(key="sub_category", values=["Mid Cap Fund"]), dict(key="plan", values=["Growth"]), dict(key="cagr_3y", min=25),
                           dict(key="expense_ratio", max=1.0)], sort_key="cagr_3y")),
            ("preset-lowcost-index", "Cheap index funds", "Passive funds with expense ratio below 0.3%.",
             dict(filters=[dict(key="sub_category", values=["Index Fund - Large Cap", "Index Fund - Mid Cap", "Index Fund - Small Cap"]),
                           dict(key="plan", values=["Growth"]), dict(key="expense_ratio", max=0.3)], sort_key="expense_ratio", sort_dir="asc")),
            ("preset-safe-debt", "Safer debt parking", "Liquid / money-market style funds with minimal volatility.",
             dict(filters=[dict(key="category", values=["Debt"]), dict(key="plan", values=["Growth"]), dict(key="std_dev", max=1.0),
                           dict(key="aum", min=1000)], sort_key="aum")),
            ("preset-sip", "My SIP growth picks", "Low minimum SIP, no lock-in, 5Y CAGR > 12%.",
             dict(filters=[dict(key="plan", values=["Growth"]), dict(key="min_sip", max=500), dict(key="lock_in", max=0), dict(key="cagr_5y", min=12)],
                  sort_key="cagr_5y")),
        ]
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat()
        for pid, name, note, cfg in presets:
            self.x("INSERT OR REPLACE INTO screens(id,client_id,name,note,config,created,updated) VALUES (?,?,?,?,?,?,?)",
                   (pid, "preset", name, note, json.dumps({"universe": "all", "custom": [], "sort_dir": "desc", **cfg}), now, now))


store: Optional[Store] = None


def get_store():
    global store
    if store is None:
        store = Store(DB_PATH)
    return store


app = FastAPI(title="Fundrix N18", version="1.0")

# ----------------------------------------------------------------------------- models
NAME_RE = re.compile(r"^[A-Za-z0-9 _\-%/.()]{1,40}$")


class Flt(BaseModel):
    key: str
    min: Optional[float] = None
    max: Optional[float] = None
    values: Optional[list[str]] = None


class Custom(BaseModel):
    name: str
    expr: str


class ScreenReq(BaseModel):
    universe: Literal["all", "watchlist", "ids"] = "all"
    ids: list[int] = []
    filters: list[Flt] = []
    custom: list[Custom] = []
    search: str = Field("", max_length=100)
    sort_key: str = "aum"
    sort_dir: Literal["asc", "desc"] = "desc"
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    columns: list[str] = []


class ScreenSave(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    note: str = Field("", max_length=500)
    config: dict


# ----------------------------------------------------------------------------- screening engine

def _custom_nodes(custom):
    nodes = {}
    allowed = set(NUM_KEYS)
    for c in custom:
        if not NAME_RE.match(c.name):
            raise HTTPException(400, f"Invalid custom filter name '{c.name}'")
        try:
            nodes["c:" + c.name] = exprmod.parse(c.expr, allowed)
        except exprmod.ExprError as e:
            raise HTTPException(400, f"Custom filter '{c.name}': {e}")
    return nodes


def run_screen(req: ScreenReq, client_id: str, paginate=True):
    s = get_store()
    nodes = _custom_nodes(req.custom)
    for f in req.filters:
        if f.key not in BY_KEY and f.key not in nodes:
            raise HTTPException(400, f"Unknown filter '{f.key}'")
        if f.key in BY_KEY and BY_KEY[f.key]["type"] == "cat" and f.values is None and (f.min is not None or f.max is not None):
            raise HTTPException(400, f"'{f.key}' is categorical")
        if f.min is not None and f.max is not None and f.min > f.max:
            raise HTTPException(400, f"Filter '{f.key}': min is greater than max")
    if req.sort_key not in BY_KEY and req.sort_key not in nodes and req.sort_key != "name":
        raise HTTPException(400, f"Unknown sort key '{req.sort_key}'")

    if req.universe == "watchlist":
        ids = {r[0] for r in s.q("SELECT fund_id FROM watchlist WHERE client_id=?", (client_id,))}
        pool = [f for f in s.funds if f["id"] in ids]
    elif req.universe == "ids":
        want = set(req.ids)
        pool = [f for f in s.funds if f["id"] in want]
    else:
        pool = s.funds

    q = req.search.strip().lower()
    out = []
    for f in pool:
        if q and q not in f["name"].lower() and q not in f["amc"].lower() and q not in f["sub_category"].lower():
            continue
        row = f
        if nodes:
            row = dict(f)
            for k, nd in nodes.items():
                row[k] = exprmod.evaluate(nd, f)
        ok = True
        for fl in req.filters:
            v = row.get(fl.key)
            if fl.values is not None:
                if fl.values and v not in fl.values:
                    ok = False
                    break
                continue
            if fl.min is None and fl.max is None:
                continue
            if v is None or (fl.min is not None and v < fl.min) or (fl.max is not None and v > fl.max):
                ok = False
                break
        if ok:
            out.append(row)

    key, rev = req.sort_key, req.sort_dir == "desc"
    have = [r for r in out if r.get(key) is not None]
    miss = [r for r in out if r.get(key) is None]
    have.sort(key=lambda r: (r[key].lower() if isinstance(r[key], str) else r[key], r["id"]), reverse=rev)
    out = have + miss                      # nulls always last
    total = len(out)
    if paginate:
        st = (req.page - 1) * req.page_size
        out = out[st:st + req.page_size]
    return total, out, nodes


def slim(row, cols):
    base = {"id": row["id"], "name": row["name"], "sub_category": row["sub_category"], "category": row["category"],
            "plan": row["plan"], "option": row["option"]}
    for c in cols:
        if c in row:
            base[c] = row[c]
    for k, v in row.items():
        if k.startswith("c:"):
            base[k] = v
    return base


# ----------------------------------------------------------------------------- routes

@app.get("/api/meta")
def meta():
    s = get_store()
    metrics = []
    for m in METRICS:
        m = dict(m)
        if m["type"] == "num":
            vals = [f[m["key"]] for f in s.funds if f.get(m["key"]) is not None]
            m["min"], m["max"] = (min(vals), max(vals)) if vals else (0, 0)
        else:
            m["options"] = sorted({f[m["key"]] for f in s.funds})
        metrics.append(m)
    tree = {}
    for f in s.funds:
        tree.setdefault(f["category"], set()).add(f["sub_category"])
    counts = {}
    for f in s.funds:
        counts[f["category"]] = counts.get(f["category"], 0) + 1
    return dict(metrics=metrics, groups=GROUPS, categories={k: sorted(v) for k, v in tree.items()}, category_counts=counts,
                total=len(s.funds), as_of=s.as_of, source=s.source,
                functions=list(exprmod.FUNCS), generated_at=dt.datetime.now().isoformat(timespec="minutes"))


@app.post("/api/screen")
def screen(req: ScreenReq, x_client_id: str = Header("anon")):
    total, rows, nodes = run_screen(req, x_client_id)
    cols = [c for c in req.columns if c in BY_KEY]
    wl = {r[0] for r in get_store().q("SELECT fund_id FROM watchlist WHERE client_id=?", (x_client_id,))}
    results = []
    for r in rows:
        d = slim(r, cols)
        d["watch"] = r["id"] in wl
        results.append(d)
    return dict(total=total, page=req.page, page_size=req.page_size, pages=max(1, -(-total // req.page_size)), results=results,
                as_of=get_store().as_of)


@app.post("/api/export")
def export(req: ScreenReq, x_client_id: str = Header("anon")):
    total, rows, nodes = run_screen(req, x_client_id, paginate=False)
    cols = [c for c in req.columns if c in BY_KEY and c not in ("sub_category", "plan")] or [m["key"] for m in METRICS if m["key"] not in ("sub_category", "plan")]
    buf = io.StringIO()
    w = csv.writer(buf)
    head = ["Name", "Sub Category", "Plan"] + [BY_KEY[c]["label"] + (f" ({BY_KEY[c]['unit']})" if BY_KEY[c].get("unit") else "") for c in cols]
    head += [k[2:] for k in nodes]

    def safe(v):  # CSV/Excel formula-injection guard
        return "'" + v if isinstance(v, str) and v[:1] in "=+-@\t\r" else v

    w.writerow(head)
    for r in rows:
        w.writerow([safe(r["name"]), r["sub_category"], r["plan"]] + ["" if r.get(c) is None else r[c] for c in cols]
                   + ["" if r.get(k) is None else round(r[k], 4) for k in nodes])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="fundrix-n18-screen.csv"', "X-Total": str(total)})


class ValidateReq(BaseModel):
    expr: str


@app.post("/api/custom/validate")
def validate(req: ValidateReq):
    try:
        node = exprmod.parse(req.expr, set(NUM_KEYS))
    except exprmod.ExprError as e:
        raise HTTPException(400, str(e))
    vals = [v for v in (exprmod.evaluate(node, f) for f in get_store().funds) if v is not None]
    if not vals:
        raise HTTPException(400, "Expression produces no values for any fund")
    return dict(ok=True, count=len(vals), min=round(min(vals), 4), max=round(max(vals), 4), median=round(float(np.median(vals)), 4))


def _fund(fid):
    f = get_store().by_id.get(fid)
    if not f:
        raise HTTPException(404, "Fund not found")
    return f


@app.get("/api/funds/{fid}")
def fund_detail(fid: int, x_client_id: str = Header("anon")):
    s = get_store()
    f = _fund(fid)
    d = s.detail(fid)
    group = [g for g in s.funds if g["sub_category"] == f["sub_category"] and g["plan"] == f["plan"] and g["option"] == f["option"]]
    avg, rank = {}, {}
    for k in NUM_KEYS:
        vals = [g[k] for g in group if g.get(k) is not None]
        if vals:
            avg[k] = round(float(np.mean(vals)), 2)
            if f.get(k) is not None and len(vals) > 1:
                better = BY_KEY[k]["better"]
                if better:
                    beat = sum(1 for v in vals if (v < f[k] if better == "high" else v > f[k]))
                    rank[k] = round(beat / (len(vals) - 1) * 100)
    peers = sorted([g for g in group if g["id"] != fid], key=lambda g: -(g["aum"] or 0))[:6]
    peers = [{k: p.get(k) for k in ("id", "name", "aum", "ret_1y", "cagr_3y", "cagr_5y", "expense_ratio", "sharpe")} for p in peers]
    tax = ("Equity-oriented: gains within 1 year are short-term (taxed at 20%); beyond 1 year long-term (12.5% on gains above ₹1.25 lakh a year)."
           if f["category"] == "Equity" or f["sub_category"] in ("Aggressive Hybrid Fund", "Arbitrage Fund", "Equity Savings Fund")
           else "Non-equity: gains are generally taxed at your income-tax slab rate. Rules change; verify with a tax adviser.")
    wl = bool(s.q("SELECT 1 FROM watchlist WHERE client_id=? AND fund_id=?", (x_client_id, fid)))
    return dict(fund=f, detail=d, category_avg=avg, rank=rank, peers=peers, group_size=len(group), tax=tax, watch=wl, as_of=s.as_of)


@app.get("/api/funds/{fid}/nav")
def fund_nav(fid: int):
    s = get_store()
    _fund(fid)
    nav = s.nav(fid)
    return dict(dates=s.dates[-len(nav):], nav=nav)


@app.get("/api/funds/{fid}/calculator")
def calculator(fid: int, mode: Literal["sip", "lumpsum"] = "sip", amount: float = Query(10000, gt=0, le=1e9), years: int = Query(3, ge=1, le=10)):
    s = get_store()
    f = _fund(fid)
    nav = np.array(s.nav(fid), float)
    w = years * 52
    max_years = (len(nav) - 1) // 52
    if max_years < years:
        raise HTTPException(400, f"Fund has only {f['age']} years of history; choose at most {max_years} year(s)")
    seg = nav[-(w + 1):]
    if mode == "lumpsum":
        value = amount * seg[-1] / seg[0]
        flows = [(0.0, amount)]
    else:
        pos = sorted({int(round(i * 52 / 12)) for i in range(years * 12) if round(i * 52 / 12) <= w})
        flows = [(p / 52, amount) for p in pos]
        value = sum(a / seg[p] for p, a in zip(pos, [amount] * len(pos))) * seg[-1]
    invested = sum(a for _, a in flows)
    T = w / 52
    lo, hi = -0.99, 20.0
    f_ = lambda r: sum(a * (1 + r) ** (T - t) for t, a in flows) - value
    for _ in range(100):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f_(mid) < 0 else (lo, mid)
    xirr = (lo + hi) / 2
    step = max(1, w // 60)
    series = []
    for i in range(0, w + 1, step):
        inv = sum(a for t, a in flows if t * 52 <= i)
        val = sum(a / seg[int(round(t * 52))] for t, a in flows if t * 52 <= i) * seg[i] if inv else 0
        series.append(dict(d=s.dates[-(w + 1) + i], invested=round(inv, 2), value=round(val, 2)))
    return dict(mode=mode, invested=round(invested, 2), value=round(value, 2), gain=round(value - invested, 2),
                abs_return=round((value / invested - 1) * 100, 2), xirr=round(xirr * 100, 2), series=series)


@app.get("/api/compare")
def compare(ids: str = Query(...)):
    try:
        idl = [int(i) for i in ids.split(",") if i.strip()]
    except ValueError:
        raise HTTPException(400, "ids must be comma separated integers")
    if not 2 <= len(idl) <= 4:
        raise HTTPException(400, "Compare 2 to 4 funds")
    s = get_store()
    funds = [_fund(i) for i in idl]
    navs = [s.nav(i) for i in idl]
    n = min(len(x) for x in navs)
    series = [[round(v / x[-n] * 100, 2) for v in x[-n:]] for x in navs]
    return dict(funds=funds, dates=s.dates[-n:], series=series)


# ---- watchlist
@app.get("/api/watchlist")
def wl_get(x_client_id: str = Header("anon")):
    return dict(ids=[r[0] for r in get_store().q("SELECT fund_id FROM watchlist WHERE client_id=? ORDER BY fund_id", (x_client_id,))])


@app.put("/api/watchlist/{fid}")
def wl_add(fid: int, x_client_id: str = Header("anon")):
    _fund(fid)
    get_store().x("INSERT OR IGNORE INTO watchlist VALUES (?,?)", (x_client_id, fid))
    return dict(ok=True)


@app.delete("/api/watchlist/{fid}")
def wl_del(fid: int, x_client_id: str = Header("anon")):
    get_store().x("DELETE FROM watchlist WHERE client_id=? AND fund_id=?", (x_client_id, fid))
    return dict(ok=True)


# ---- saved screens (anonymous, keyed by a client id; anyone with a screen id can open it = share link)
def _screen_row(r, client_id):
    return dict(id=r["id"], name=r["name"], note=r["note"], config=json.loads(r["config"]), created=r["created"], updated=r["updated"],
                preset=r["client_id"] == "preset", mine=r["client_id"] == client_id)


@app.get("/api/screens")
def screens_list(x_client_id: str = Header("anon")):
    rows = get_store().q("SELECT * FROM screens WHERE client_id IN (?, 'preset') ORDER BY (client_id='preset'), updated DESC", (x_client_id,))
    return [_screen_row(r, x_client_id) for r in rows]


@app.get("/api/screens/{sid}")
def screens_get(sid: str, x_client_id: str = Header("anon")):
    r = get_store().q("SELECT * FROM screens WHERE id=?", (sid,))
    if not r:
        raise HTTPException(404, "Screen not found")
    return _screen_row(r[0], x_client_id)


@app.post("/api/screens", status_code=201)
def screens_create(body: ScreenSave, x_client_id: str = Header("anon")):
    sid, now = secrets.token_urlsafe(9), dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat()
    get_store().x("INSERT INTO screens VALUES (?,?,?,?,?,?,?)", (sid, x_client_id, body.name.strip(), body.note, json.dumps(body.config), now, now))
    return screens_get(sid, x_client_id)


def _own(sid, client_id):
    r = get_store().q("SELECT client_id FROM screens WHERE id=?", (sid,))
    if not r:
        raise HTTPException(404, "Screen not found")
    if r[0][0] != client_id:
        raise HTTPException(403, "You can only modify your own screens")


@app.put("/api/screens/{sid}")
def screens_update(sid: str, body: ScreenSave, x_client_id: str = Header("anon")):
    _own(sid, x_client_id)
    get_store().x("UPDATE screens SET name=?, note=?, config=?, updated=? WHERE id=?",
                  (body.name.strip(), body.note, json.dumps(body.config), dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(), sid))
    return screens_get(sid, x_client_id)


@app.delete("/api/screens/{sid}")
def screens_delete(sid: str, x_client_id: str = Header("anon")):
    _own(sid, x_client_id)
    get_store().x("DELETE FROM screens WHERE id=?", (sid,))
    return dict(ok=True)


app.mount("/", StaticFiles(directory=os.path.join(ROOT, "frontend"), html=True), name="static")
