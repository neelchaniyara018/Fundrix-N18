import numpy as np
import pytest
from backend import data, expr
from backend.metrics import NUM_KEYS

def scr(client, H=None, **kw):
    r = client.post("/api/screen", json=kw, headers=H or {})
    assert r.status_code == 200, r.text
    return r.json()

# ---------- meta
def test_meta(client):
    m = client.get("/api/meta").json()
    assert m["total"] > 1000
    assert {"Equity", "Debt", "Hybrid", "Commodity", "Other"} == set(m["categories"])
    assert [g for g in m["groups"]] == ["Scheme Info", "Returns", "Risk", "Portfolio Composition", "Ratios"]
    assert len([x for x in m["metrics"] if x["type"] == "num"]) >= 40
    aum = next(x for x in m["metrics"] if x["key"] == "aum")
    assert aum["min"] < aum["max"]

# ---------- screening
def test_pagination_and_default_sort(client):
    r = scr(client, page=1, page_size=20)
    assert len(r["results"]) == 20 and r["pages"] == -(-r["total"] // 20)
    aums = [x.get("aum") for x in scr(client, columns=["aum"], page_size=20)["results"]]
    assert aums == sorted(aums, reverse=True)
    p2 = scr(client, page=2, page_size=20)["results"]
    assert not {x["id"] for x in p2} & {x["id"] for x in r["results"]}

def test_last_page_partial(client):
    t = scr(client, page_size=20)["total"]
    last = scr(client, page=-(-t // 20), page_size=20)
    assert len(last["results"]) == (t % 20 or 20)
    assert scr(client, page=9999)["results"] == []

def test_numeric_range_inclusive_and_nulls_excluded(client):
    r = scr(client, filters=[{"key": "cagr_3y", "min": 15, "max": 25}], columns=["cagr_3y"], page_size=200)
    assert r["total"] > 0 and all(15 <= x["cagr_3y"] <= 25 for x in r["results"])
    allr = scr(client, filters=[], page_size=1)["total"]
    nn = scr(client, filters=[{"key": "cagr_3y", "min": -1000}], page_size=1)["total"]
    assert nn < allr   # funds younger than 3Y have no CAGR 3Y and are excluded once the filter is applied

def test_categorical_and_combined(client):
    r = scr(client, filters=[{"key": "category", "values": ["Equity"]}, {"key": "plan", "values": ["Growth"]},
                             {"key": "expense_ratio", "max": 1.0}], columns=["expense_ratio"], page_size=200)
    assert r["total"] > 0
    assert all(x["category"] == "Equity" and x["plan"] == "Growth" and x["expense_ratio"] <= 1 for x in r["results"])
    multi = scr(client, filters=[{"key": "sub_category", "values": ["Mid Cap Fund", "Small Cap Fund"]}], page_size=200)
    assert {x["sub_category"] for x in multi["results"]} == {"Mid Cap Fund", "Small Cap Fund"}

def test_sort_asc_desc_nulls_last_and_name(client):
    r = scr(client, sort_key="cagr_5y", sort_dir="asc", columns=["cagr_5y"], page_size=200)["results"]
    v = [x["cagr_5y"] for x in r if x.get("cagr_5y") is not None]
    assert v == sorted(v)
    last = scr(client, sort_key="cagr_5y", sort_dir="desc", page=99, page_size=20)["results"]
    n = scr(client, sort_key="name", sort_dir="asc", page_size=50)["results"]
    names = [x["name"].lower() for x in n]
    assert names == sorted(names)

def test_nulls_sorted_last_both_directions(client):
    for d in ("asc", "desc"):
        t = scr(client, sort_key="cagr_10y", sort_dir=d, page_size=1)["total"]
        rows = scr(client, sort_key="cagr_10y", sort_dir=d, columns=["cagr_10y"], page_size=200, page=1)["results"]
        seen_none = False
        for x in rows:
            if x.get("cagr_10y") is None: seen_none = True
            else: assert not seen_none

def test_search(client):
    r = scr(client, search="mid cap", page_size=200)
    assert r["total"] > 0 and all("mid cap" in (x["name"] + x["sub_category"]).lower() for x in r["results"])
    assert scr(client, search="zzzzqqq")["total"] == 0

def test_bad_inputs(client):
    for body in ({"filters": [{"key": "nope", "min": 1}]}, {"sort_key": "nope"}, {"filters": [{"key": "aum", "min": 5, "max": 1}]},
                 {"filters": [{"key": "category", "min": 1}]}):
        assert client.post("/api/screen", json=body).status_code == 400, body
    assert client.post("/api/screen", json={"page": 0}).status_code == 422
    assert client.post("/api/screen", json={"page_size": 5000}).status_code == 422

# ---------- custom filters
def test_custom_filter_values(client):
    body = dict(custom=[{"name": "ret per risk", "expr": "cagr_3y / std_dev"}],
                filters=[{"key": "c:ret per risk", "min": 1.0}], columns=["cagr_3y", "std_dev"], page_size=200)
    r = scr(client, **body)
    assert r["total"] > 0
    for x in r["results"]:
        assert x["c:ret per risk"] >= 1.0
        assert x["c:ret per risk"] == pytest.approx(x["cagr_3y"] / x["std_dev"])

def test_custom_sort(client):
    r = scr(client, custom=[{"name": "x", "expr": "sharpe * 2"}], sort_key="c:x", sort_dir="desc", page_size=10)["results"]
    v = [a["c:x"] for a in r]
    assert v == sorted(v, reverse=True)

@pytest.mark.parametrize("bad", ["__import__('os').system('id')", "open('/etc/passwd')", "().__class__", "cagr_3y.real",
                                 "[1,2]", "lambda: 1", "cagr_3y if 1 else 2", "x" * 300, "sharpe ** 9999", "unknown_metric + 1", "", "1 +", "'a'*3",
                                 "max(sharpe, key=abs)", "abs.__call__(1)", "True + 1"])
def test_custom_expression_rejected_or_harmless(client, bad):
    r = client.post("/api/custom/validate", json={"expr": bad})
    assert r.status_code == 400, (bad, r.text)

def test_custom_validate_ok_and_fn(client):
    r = client.post("/api/custom/validate", json={"expr": "max(cagr_3y, 0) - expense_ratio * 2 + sqrt(abs(beta))"}).json()
    assert r["ok"] and r["count"] > 0 and r["min"] <= r["median"] <= r["max"]

def test_custom_name_validation(client):
    r = client.post("/api/screen", json={"custom": [{"name": "<script>", "expr": "sharpe"}]})
    assert r.status_code == 400

def test_divzero_is_none():
    node = expr.parse("1 / x", {"x"})
    assert expr.evaluate(node, {"x": 0}) is None and expr.evaluate(node, {"x": None}) is None and expr.evaluate(node, {"x": 4}) == .25

# ---------- export
def test_export_csv_all_rows(client):
    t = scr(client, filters=[{"key": "plan", "values": ["Growth"]}])["total"]
    r = client.post("/api/export", json={"filters": [{"key": "plan", "values": ["Growth"]}], "columns": ["aum", "cagr_3y"],
                                         "custom": [{"name": "c1", "expr": "aum / 2"}]})
    lines = r.text.strip().splitlines()
    assert r.headers["content-type"].startswith("text/csv") and len(lines) == t + 1 > 21
    assert lines[0] == "Name,Sub Category,Plan,AUM (Cr),CAGR 3Y (%),c1"

def test_export_formula_injection_guard(client):
    from backend.main import get_store
    st = get_store(); f = st.by_id[0]; orig = f["name"]
    try:
        f["name"] = '=HYPERLINK("http://evil","x")'
        r = client.post("/api/export", json={"universe": "ids", "ids": [0], "columns": []})
        row = r.text.strip().splitlines()[1]
        assert row.startswith("\"'=HYPERLINK") or row.startswith("'=HYPERLINK")
    finally:
        f["name"] = orig

# ---------- fund detail
def test_fund_detail_and_nav(client):
    top = scr(client, page_size=1)["results"][0]["id"]
    d = client.get(f"/api/funds/{top}").json()
    assert d["fund"]["id"] == top and d["detail"]["holdings"] and d["category_avg"] and d["group_size"] >= 1
    assert all(0 <= v <= 100 for v in d["rank"].values())
    n = client.get(f"/api/funds/{top}/nav").json()
    assert len(n["dates"]) == len(n["nav"]) and n["nav"][-1] == pytest.approx(d["fund"]["nav"], abs=.01)
    assert client.get("/api/funds/999999").status_code == 404

def test_metrics_are_derived_from_nav(client):
    """CAGR/returns stored on the fund must match the NAV series (the analytics are real, not random)."""
    f = client.get("/api/funds/160").json()["fund"]
    nav = client.get("/api/funds/160/nav").json()["nav"]
    if f["cagr_3y"] is not None:
        assert ((nav[-1] / nav[-1 - 156]) ** (1 / 3) - 1) * 100 == pytest.approx(f["cagr_3y"], abs=.02)
    assert (nav[-1] / nav[-53] - 1) * 100 == pytest.approx(f["ret_1y"], abs=.02)

def test_compute_metrics_known_values():
    r = np.full(260, 0.001); b = np.full(260, 0.001)
    m, nav = data.compute_metrics(r, b, 10)
    assert m["cagr_3y"] == pytest.approx(((1.001) ** 52 - 1) * 100, abs=.02)
    assert m["max_drawdown"] == 0 or abs(m["max_drawdown"]) < 1e-6
    rng = np.random.default_rng(1); b = rng.normal(.002, .02, 300); r = 1.3 * b + rng.normal(0, .002, 300)
    m, _ = data.compute_metrics(r, b, 10)
    assert m["beta"] == pytest.approx(1.3, abs=.05)
    assert m["tracking_error"] > 0 and m["up_capture"] > 100 and m["down_capture"] > 100

def test_all_numeric_values_finite(client):
    r = scr(client, columns=NUM_KEYS, page_size=200)["results"]
    for x in r:
        for k in NUM_KEYS:
            v = x.get(k)
            assert v is None or np.isfinite(v)

# ---------- calculator / compare
def test_calculator(client):
    fid = scr(client, filters=[{"key": "age", "min": 5}], page_size=1)["results"][0]["id"]
    s = client.get(f"/api/funds/{fid}/calculator", params={"mode": "sip", "amount": 5000, "years": 3}).json()
    assert s["invested"] == 5000 * 36 and s["value"] > 0 and -100 < s["xirr"] < 2000
    l = client.get(f"/api/funds/{fid}/calculator", params={"mode": "lumpsum", "amount": 100000, "years": 5}).json()
    cagr = client.get(f"/api/funds/{fid}").json()["fund"]["cagr_5y"]
    assert l["xirr"] == pytest.approx(cagr, abs=.1) and l["invested"] == 100000
    assert client.get(f"/api/funds/{fid}/calculator", params={"years": 99}).status_code == 422
    assert client.get(f"/api/funds/{fid}/calculator", params={"amount": -1}).status_code == 422

def test_calculator_history_too_short(client):
    young = scr(client, filters=[{"key": "age", "max": 2}], page_size=1)["results"][0]["id"]
    assert client.get(f"/api/funds/{young}/calculator", params={"years": 5}).status_code == 400

def test_compare(client):
    ids = [x["id"] for x in scr(client, filters=[{"key": "age", "min": 5}], page_size=3)["results"]]
    r = client.get("/api/compare", params={"ids": ",".join(map(str, ids))}).json()
    assert len(r["funds"]) == 3 and all(s[0] == 100 for s in r["series"]) and len(r["dates"]) == len(r["series"][0])
    assert client.get("/api/compare", params={"ids": "1"}).status_code == 400
    assert client.get("/api/compare", params={"ids": "1,2,3,4,5"}).status_code == 400
    assert client.get("/api/compare", params={"ids": "a,b"}).status_code == 400

# ---------- watchlist & screens
def test_watchlist_flow(client, H):
    assert client.get("/api/watchlist", headers=H).json()["ids"] == []
    assert client.put("/api/watchlist/5", headers=H).status_code == 200
    client.put("/api/watchlist/5", headers=H); client.put("/api/watchlist/9", headers=H)
    assert client.get("/api/watchlist", headers=H).json()["ids"] == [5, 9]
    r = scr(client, H, universe="watchlist")
    assert r["total"] == 2 and all(x["watch"] for x in r["results"])
    # isolation between clients
    assert client.get("/api/watchlist", headers={"X-Client-Id": "other"}).json()["ids"] == []
    client.delete("/api/watchlist/5", headers=H)
    assert scr(client, H, universe="watchlist")["total"] == 1
    assert client.put("/api/watchlist/999999", headers=H).status_code == 404

def test_universe_ids(client):
    assert scr(client, universe="ids", ids=[1, 2, 3])["total"] == 3

def test_screens_crud_permissions(client, H):
    cfg = {"filters": [{"key": "aum", "min": 1000}], "sort_key": "aum"}
    r = client.post("/api/screens", json={"name": "My screen", "note": "why", "config": cfg}, headers=H)
    assert r.status_code == 201
    sid = r.json()["id"]
    lst = client.get("/api/screens", headers=H).json()
    assert any(s["id"] == sid and s["mine"] for s in lst) and any(s["preset"] for s in lst)
    other = {"X-Client-Id": "stranger"}
    assert client.get(f"/api/screens/{sid}", headers=other).json()["config"] == cfg          # share link works
    assert client.put(f"/api/screens/{sid}", json={"name": "x", "config": {}}, headers=other).status_code == 403
    assert client.delete(f"/api/screens/{sid}", headers=other).status_code == 403
    assert not any(s["id"] == sid for s in client.get("/api/screens", headers=other).json())  # not listed for others
    u = client.put(f"/api/screens/{sid}", json={"name": "Renamed", "note": "n2", "config": cfg}, headers=H).json()
    assert u["name"] == "Renamed" and u["note"] == "n2"
    assert client.delete(f"/api/screens/{sid}", headers=H).status_code == 200
    assert client.get(f"/api/screens/{sid}").status_code == 404
    assert client.post("/api/screens", json={"name": "", "config": {}}, headers=H).status_code == 422

def test_presets_are_valid_and_read_only(client, H):
    for s in [s for s in client.get("/api/screens", headers=H).json() if s["preset"]]:
        cfg = s["config"]
        r = scr(client, filters=cfg["filters"], sort_key=cfg["sort_key"], sort_dir=cfg["sort_dir"])
        assert r["total"] > 0, s["name"]
        assert client.delete(f"/api/screens/{s['id']}", headers=H).status_code == 403

def test_static_index(client):
    assert client.get("/").status_code == 200
