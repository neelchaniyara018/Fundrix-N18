import datetime as dt, os, sys, tempfile
import numpy as np
from backend.ingest_real import build_from_payloads, classify

def payload(code, name, cat, seed, years=6):
    rng = np.random.default_rng(seed); d0 = dt.date.today() - dt.timedelta(days=365 * years)
    nav, pts = 10.0, []
    for i in range(365 * years):
        nav *= 1 + rng.normal(.0004, .01); d = d0 + dt.timedelta(days=i)
        if d.weekday() < 5: pts.append({"date": d.strftime("%d-%m-%Y"), "nav": f"{nav:.4f}"})
    pts.append({"date": "01-01-2020", "nav": "N.A."})            # junk row must be ignored
    return {"meta": {"scheme_code": code, "scheme_name": name, "fund_house": "Test MF", "scheme_category": cat}, "data": pts[::-1]}

def test_classify():
    assert classify({"scheme_name": "Foo Flexi Cap Fund - Direct Plan - Growth", "scheme_category": "Equity Scheme - Flexi Cap Fund"}) == ("Equity", "Flexi Cap Fund", "Growth", "Direct", "Foo Flexi Cap Fund")
    assert classify({"scheme_name": "Bar Liquid Fund - IDCW Payout", "scheme_category": "Debt Scheme - Liquid Fund"})[2:4] == ("IDCW", "Regular")
    assert classify({"scheme_name": "X", "scheme_category": "Solution Oriented Scheme - Retirement Fund"})[0] == "Other"

def test_build_and_serve(monkeypatch):
    path = os.path.join(tempfile.mkdtemp(), "real.db")
    pl = [payload(1, "Alpha Flexi Cap Fund - Direct Plan - Growth", "Equity Scheme - Flexi Cap Fund", 1),
          payload(2, "Beta Flexi Cap Fund - Growth", "Equity Scheme - Flexi Cap Fund", 2),
          payload(3, "Gamma Liquid Fund", "Debt Scheme - Liquid Fund", 3), payload(4, "Short", "Equity Scheme - Large Cap Fund", 4, years=0)]
    n = build_from_payloads(pl, path, extras={"1": {"aum": "1234.5", "expense_ratio": "0.6"}})
    assert n == 3                                           # the 'Short' fund (no history) is skipped
    from fastapi.testclient import TestClient
    import backend.main as m
    monkeypatch.setattr(m, "store", None); monkeypatch.setattr(m, "DB_PATH", path)
    with TestClient(m.app) as c:
        meta = c.get("/api/meta").json(); assert meta["source"] == "real" and meta["total"] == 3
        r = c.post("/api/screen", json={"sort_key": "cagr_3y", "columns": ["cagr_3y", "aum", "sharpe"]}).json()
        assert r["total"] == 3 and any(x.get("aum") == 1234.5 for x in r["results"])
        assert c.post("/api/screen", json={"filters": [{"key": "aum", "min": 1000}]}).json()["total"] == 1   # missing AUM excluded
        d = c.get("/api/funds/0").json(); assert d["detail"]["holdings"] == [] and d["peers"] is not None   # no crash with sparse data
        assert c.post("/api/export", json={}).status_code == 200
