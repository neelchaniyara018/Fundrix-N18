"""Build the database from REAL NAV data (public mfapi.in endpoints) instead of the demo universe.

    python -m backend.ingest_real --out data/funds.db --limit 300 --match "flexi cap|mid cap"
    python -m backend.ingest_real --out data/funds.db --extras extras.csv

What you get: real names, fund houses, categories, plan/option and NAV-derived analytics (returns, CAGR, risk, ratios) computed by the
same compute_metrics() used for the demo data.
What you do NOT get from NAV history alone: AUM, expense ratio, exit load, holdings, riskometer. Supply them with --extras (CSV with
header: scheme_code,aum,expense_ratio,min_sip,min_lumpsum,exit_load,lock_in,benchmark,riskometer) – e.g. compiled from AMFI/AMC
factsheets. Missing values stay empty and are simply excluded when you filter on them.
Benchmark = equal-weighted average of the fund's sub-category (a stand-in; real index TRI series are not in this API).
NOTE: network access is required and the endpoint format is assumed from the public API docs; the parsing/DB logic is unit-tested
offline (tests/test_ingest.py) but the live fetch could not be exercised in the build sandbox."""
import argparse, bisect, csv, datetime as dt, json, os, re, sqlite3, time, urllib.request

import numpy as np

from .data import WK, compute_metrics, rnd
from .metrics import NUM_KEYS

API = "https://api.mfapi.in/mf"
L = 531


def _get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "fundrix-n18/1.0"}), timeout=30) as r:
        return json.load(r)


def classify(meta):
    cat = (meta.get("scheme_category") or "Other Scheme - Other").strip()
    head, _, sub = cat.partition(" - ")
    category = head.replace(" Scheme", "").strip()
    category = category if category in ("Equity", "Debt", "Hybrid", "Commodity") else "Other"
    name = meta.get("scheme_name") or ""
    plan = "IDCW" if re.search(r"idcw|dividend|payout|reinvest", name, re.I) else "Growth"
    option = "Direct" if re.search(r"direct", name, re.I) else "Regular"
    base = re.split(r"\s+-\s+(direct|regular)|\s+(direct|regular)\s+plan|\s+-\s*(growth|idcw|dividend)", name, flags=re.I)[0].strip()
    return category, (sub.strip() or "Other"), plan, option, base or name


def weekly(points, grid):
    """points: [(date, nav)] ascending -> NAV on each grid Friday (last observation on/before), None before first obs."""
    ds = [p[0] for p in points]; out = []
    for g in grid:
        i = bisect.bisect_right(ds, g) - 1
        out.append(points[i][1] if i >= 0 else None)
    return out


def build_from_payloads(payloads, path, extras=None, today=None):
    extras = extras or {}
    today = today or dt.date.today()
    end = today - dt.timedelta(days=(today.weekday() - 4) % 7)
    grid = [end - dt.timedelta(weeks=L - 1 - i) for i in range(L)]
    ytd_weeks = sum(1 for d in grid if d.year == end.year) - 1
    rows = []
    for p in payloads:
        meta, data = p["meta"], p["data"]
        pts = sorted((dt.datetime.strptime(d["date"], "%d-%m-%Y").date(), float(d["nav"])) for d in data if d.get("nav") not in (None, "", "N.A."))
        pts = [x for x in pts if x[1] > 0]
        if len(pts) < 60: continue
        w = weekly(pts, grid); first = next((i for i, v in enumerate(w) if v is not None), None)
        if first is None or L - first < 21: continue
        nav = np.array(w[first:], float)
        rows.append(dict(meta=meta, nav=nav, ret=nav[1:] / nav[:-1] - 1, start=first))
    # peer-average benchmark per (category, sub_category)
    groups = {}
    for r in rows:
        category, sub, *_ = classify(r["meta"]); r["key"] = (category, sub)
        full = np.full(L - 1, np.nan); full[r["start"]:] = r["ret"]; groups.setdefault(r["key"], []).append(full)
    bench = {k: np.nan_to_num(np.nanmean(np.vstack(v), axis=0)) for k, v in groups.items()}
    funds, details, navs = [], [], []
    for fid, r in enumerate(rows):
        meta = r["meta"]; category, sub, plan, option, base = classify(meta); A = len(r["ret"]); ex = extras.get(str(meta.get("scheme_code")), {})
        mt, nav = compute_metrics(r["ret"], bench[r["key"]][-A:], ytd_weeks)
        f = dict(id=fid, name=base, amc=meta.get("fund_house") or "Unknown", category=category, sub_category=sub, plan=plan, option=option,
                 benchmark=ex.get("benchmark") or "Peer average", riskometer=ex.get("riskometer") or "Unknown", scheme_code=meta.get("scheme_code"))
        for k in NUM_KEYS: f.setdefault(k, None)
        f.update(mt)
        for k in ("aum", "expense_ratio", "min_sip", "min_lumpsum", "exit_load", "lock_in"):
            f[k] = float(ex[k]) if ex.get(k) not in (None, "") else (0 if k == "lock_in" else None)
        funds.append(f)
        details.append(dict(manager=[], manager_since=0, launch=grid[r["start"]].isoformat(), exit_load_days=0, holdings=[], sectors=[], ytd_weeks=ytd_weeks))
        navs.append([rnd(x, 4) for x in nav])
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if os.path.exists(path): os.remove(path)
    con = sqlite3.connect(path); con.executescript(open(os.path.join(os.path.dirname(__file__), "schema.sql")).read())
    con.executemany("INSERT INTO funds(id,doc,detail,nav) VALUES (?,?,?,?)", [(f["id"], json.dumps(f), json.dumps(d), json.dumps(n)) for f, d, n in zip(funds, details, navs)])
    con.executemany("INSERT INTO meta VALUES (?,?)", [("dates", json.dumps([d.isoformat() for d in grid])), ("as_of", end.isoformat()), ("source", "real")])
    con.commit(); con.close()
    return len(funds)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="data/funds.db"); ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--match", default="", help="regex on scheme name"); ap.add_argument("--extras"); ap.add_argument("--delay", type=float, default=.2)
    a = ap.parse_args()
    lst = _get(API); rx = re.compile(a.match, re.I) if a.match else None
    lst = [s for s in lst if not rx or rx.search(s["schemeName"])][:a.limit]
    extras = {}
    if a.extras:
        with open(a.extras, newline="", encoding="utf-8") as fh:
            extras = {r["scheme_code"]: r for r in csv.DictReader(fh)}
    payloads = []
    for i, s in enumerate(lst, 1):
        try: payloads.append(_get(f"{API}/{s['schemeCode']}"))
        except Exception as e: print("skip", s["schemeCode"], e)
        print(f"\r{i}/{len(lst)}", end="", flush=True); time.sleep(a.delay)
    print("\nwrote", build_from_payloads(payloads, a.out, extras), "funds ->", a.out)


if __name__ == "__main__":
    main()
