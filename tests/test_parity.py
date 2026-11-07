"""The in-browser engine (frontend/engine.js) must give the same answers as the original Python server on the same data."""
import csv, io, json, math, os, shutil, subprocess, tempfile
import pytest
from fastapi.testclient import TestClient
from backend import data as demo
import backend.main as m
from pipeline import build

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node required")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WATCH = [3, 17, 250, 251, 900]

@pytest.fixture(scope="module")
def world():
    tmp = tempfile.mkdtemp(); d = demo.generate(seed=11)
    demo.write_db(d, os.path.join(tmp, "p.db")); static = os.path.join(tmp, "static")
    os.makedirs(static)
    docs, det, navs, dates = build.from_generated(d); build.write_static(static, docs, det, navs, dates, d["as_of"], "demo")
    old = m.store; m.store = m.Store(os.path.join(tmp, "p.db"))
    with TestClient(m.app) as c:
        H = {"X-Client-Id": "parity"}
        for i in WATCH: c.put(f"/api/watchlist/{i}", headers=H)
        yield dict(client=c, H=H, static=static, tmp=tmp, funds=m.store.funds)
    m.store = old

def pick_ids(funds):
    old = next(f["id"] for f in funds if f["age"] >= 10); young = next(f["id"] for f in funds if f["age"] < 2); mid = next(f["id"] for f in funds if 3 <= f["age"] < 5 and f["category"] == "Debt")
    return old, young, mid, funds[-1]["id"], funds[0]["id"]

def build_cases(funds):
    old, young, mid, last, first = pick_ids(funds)
    S = lambda **b: dict(type="screen", body=b)
    GROWTH = [{"key": "plan", "values": ["Growth"]}, {"key": "aum"}, {"key": "cagr_3y"}, {"key": "expense_ratio"}]
    cols = ["aum", "cagr_3y", "expense_ratio", "sharpe", "beta", "ytm", "pe", "exit_load"]
    cases = [S(filters=GROWTH, columns=cols), S(), S(page_size=200, columns=cols), S(page=2, page_size=50, columns=cols), S(page=9999), S(page_size=1, page=1652),
        S(filters=[{"key": "cagr_3y", "min": 15, "max": 25}], columns=["cagr_3y"], page_size=200),
        S(filters=[{"key": "category", "values": ["Equity", "Hybrid"]}, {"key": "expense_ratio", "max": 1.2}, {"key": "sharpe", "min": 0.1}], sort_key="sharpe", columns=["sharpe", "expense_ratio"], page_size=100),
        S(filters=[{"key": "sub_category", "values": ["Mid Cap Fund", "Small Cap Fund"]}, {"key": "plan", "values": []}], sort_key="cagr_5y", sort_dir="asc", columns=["cagr_5y"], page_size=200),
        S(sort_key="name", sort_dir="asc", page_size=100), S(sort_key="name", sort_dir="desc", page_size=100), S(sort_key="amc", sort_dir="asc", page_size=60, columns=[]),
        S(sort_key="cagr_10y", sort_dir="asc", columns=["cagr_10y"], page_size=200), S(sort_key="cagr_10y", sort_dir="desc", columns=["cagr_10y"], page=40, page_size=20),
        S(sort_key="riskometer", sort_dir="desc", page_size=80), S(sort_key="expense_ratio", sort_dir="asc", columns=["expense_ratio"], page_size=200),   # many ties -> id tie-break
        S(search="mid cap", page_size=200), S(search="  ZENITH ", page_size=200), S(search="zzzzqq"), S(search="Mutual"),
        S(filters=[{"key": "age", "min": 5}, {"key": "aum", "min": 5000}], columns=["age", "aum"], page_size=200),
        S(universe="watchlist", columns=["aum"]), S(universe="watchlist", search="zzz"), S(universe="ids", ids=[first, last, old], columns=["aum"]), S(universe="ids", ids=[]),
        S(custom=[{"name": "RPR", "expr": "cagr_3y / std_dev"}], filters=[{"key": "c:RPR", "min": 1}], sort_key="c:RPR", columns=["cagr_3y", "std_dev"], page_size=100),
        S(custom=[{"name": "x y", "expr": "max(cagr_3y, 0) - expense_ratio * 2 + sqrt(abs(beta)) % 3"}, {"name": "p", "expr": "-2 ** 2 + 2 ** -1 + 7 % -3 - (-7) % 3"}], sort_key="c:x y", sort_dir="asc", page_size=50),
        S(custom=[{"name": "dz", "expr": "1 / (aum - aum)"}], filters=[{"key": "c:dz", "min": 0}]),
        S(filters=[{"key": "nope", "min": 1}]), S(sort_key="nope"), S(filters=[{"key": "aum", "min": 5, "max": 1}]), S(filters=[{"key": "category", "min": 1}]),
        S(page=0), S(page_size=5000), S(page_size=0), S(custom=[{"name": "<b>", "expr": "aum"}]), S(custom=[{"name": "ok", "expr": "aum +"}]), S(custom=[{"name": "ok", "expr": "__import__('os')"}]),
        S(universe="bogus"), S(sort_dir="up"),
        dict(type="export", body=dict(filters=GROWTH, columns=cols, page_size=20)), dict(type="export", body=dict(columns=[], custom=[{"name": "c1", "expr": "aum / 2"}], sort_key="name", sort_dir="asc")),
        dict(type="export", body=dict(universe="watchlist", columns=["aum"]), watch=WATCH), dict(type="meta"),
    ]
    for e in ["cagr_3y / std_dev", "max(cagr_3y, 0) - expense_ratio * 2 + sqrt(abs(beta))", "log(aum) * 2", "log(cagr_3y)", "sqrt(max_drawdown)", "aum ** 2", "sharpe ** 0.5", "2 ** 3 ** 2 + sharpe",
              "-sharpe + +sharpe * 2", "abs(1, 2)", "min(sharpe)", "7 % 0 + sharpe", "(sharpe + beta) * (alpha - 1) / 3.5e0", ".5 + sharpe", "ytm", "aum % 7.5",
              "__import__('os')", "nope + 1", "cagr_3y +", "", "1 +", "(1", "a.b", "[1]", "sharpe if 1 else 2", "x" * 300, "abs(sharpe) abs", "True + 1", "max(sharpe, key=abs)", "'a'*3", "sharpe // 2", "1 2"]:
        cases.append(dict(type="validate", expr=e))
    for i in (old, young, mid, last, first, 999999): cases.append(dict(type="detail", id=i))
    for i in (old, mid, young):
        for mode in ("sip", "lumpsum"):
            for yrs in (1, 3, 5, 10): cases.append(dict(type="calc", id=i, params=dict(mode=mode, amount=12345.5, years=yrs)))
    cases += [dict(type="calc", id=old, params=dict(mode="sip", amount=-1, years=3)), dict(type="calc", id=old, params=dict(mode="sip", amount=5000, years=11)),
              dict(type="calc", id=old, params=dict(mode="weekly", amount=5000, years=3)), dict(type="calc", id=old, params=dict(amount=0, years=3)), dict(type="calc", id=old, params={})]
    for ids in ([old, mid], [old, mid, last, first], [old], [old, mid, last, first, young], [old, 999999]): cases.append(dict(type="compare", ids=ids))
    for k in cases: k.setdefault("watch", WATCH)          # same watchlist on both sides
    return cases

def py_expected(world, cases):
    c, H, out = world["client"], world["H"], []
    for k in cases:
        t = k["type"]
        if t == "screen": r = c.post("/api/screen", json=k["body"], headers=H)
        elif t == "export": r = c.post("/api/export", json=k["body"], headers=H)
        elif t == "validate": r = c.post("/api/custom/validate", json={"expr": k["expr"]})
        elif t == "detail": r = c.get(f"/api/funds/{k['id']}", headers=H)
        elif t == "calc": r = c.get(f"/api/funds/{k['id']}/calculator", params=k["params"])
        elif t == "compare": r = c.get("/api/compare", params={"ids": ",".join(map(str, k["ids"]))})
        elif t == "meta": r = c.get("/api/meta")
        out.append(dict(ok=r.status_code == 200, status=r.status_code, data=(r.text if t == "export" else r.json()) if r.status_code == 200 else None))
    return out

@pytest.fixture(scope="module")
def results(world):
    cases = build_cases(world["funds"]); p = os.path.join(world["tmp"], "cases.json"); o = os.path.join(world["tmp"], "out.json")
    json.dump(cases, open(p, "w")); subprocess.run(["node", "tests/parity_run.mjs", world["static"], p, o], cwd=ROOT, check=True, timeout=120)
    return cases, py_expected(world, cases), json.load(open(o))

def clean(x):
    if isinstance(x, dict): return {k: clean(v) for k, v in x.items() if v is not None}
    if isinstance(x, list): return [clean(v) for v in x]
    return x

def same(a, b, path="", tol=0.0101):
    if isinstance(a, dict):
        assert isinstance(b, dict) and set(a) == set(b), (path, sorted(set(a) ^ set(b)) if isinstance(b, dict) else b)
        for k in a: same(a[k], b[k], f"{path}.{k}", tol)
    elif isinstance(a, list):
        assert isinstance(b, list) and len(a) == len(b), (path, len(a), len(b) if isinstance(b, list) else b)
        for i, (x, y) in enumerate(zip(a, b)): same(x, y, f"{path}[{i}]", tol)
    elif isinstance(a, bool) or isinstance(a, str) or a is None: assert a == b, (path, a, b)
    elif isinstance(a, (int, float)):
        assert isinstance(b, (int, float)) and not isinstance(b, bool), (path, a, b)
        assert math.isclose(a, b, abs_tol=tol if ("category_avg" in path or "series" in path or path.split(".")[-1] in ("value", "gain", "invested", "xirr", "abs_return", "median", "min", "max")) else 1e-9, rel_tol=1e-9), (path, a, b)
    else: assert a == b, (path, a, b)

def test_same_number_of_cases(results):
    cases, py, js = results; assert len(cases) == len(py) == len(js) > 100

def test_every_case_matches(results):
    cases, py, js = results; fails = []
    for i, (k, p, j) in enumerate(zip(cases, py, js)):
        label = f"#{i} {k['type']} {json.dumps({x: y for x, y in k.items() if x != 'type'})[:110]}"
        try:
            assert p["ok"] == j["ok"], f"python status {p['status']} vs js ok={j['ok']} {j.get('message')}"
            if not p["ok"]:
                assert p["status"] == j["status"], f"status {p['status']} vs {j['status']} ({j['message']})"; continue
            if k["type"] == "export":
                rp, rj = list(csv.reader(io.StringIO(p["data"]))), list(csv.reader(io.StringIO(j["data"])))
                assert rp[0] == rj[0] and len(rp) == len(rj), "csv shape"
                for a, b in zip(rp[1:], rj[1:]):
                    assert len(a) == len(b)
                    for x, y in zip(a, b):
                        try: assert math.isclose(float(x), float(y), abs_tol=1e-4)
                        except ValueError: assert x == y
            else:
                dp, dj = clean(p["data"]), clean(j["data"])
                if k["type"] == "detail": dp.pop("watch", None)               # added by the storage layer, not the engine
                if k["type"] == "meta": dp.pop("generated_at", None); [dj.pop(x, None) for x in ("has_aum", "has_expense")]
                same(dp, dj)
        except AssertionError as e:
            fails.append(f"{label}\n      {str(e)[:300]}")
    assert not fails, f"{len(fails)} of {len(cases)} cases differ:\n" + "\n".join(fails[:8])
