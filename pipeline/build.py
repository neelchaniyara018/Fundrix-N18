"""Build the static data files the website loads.

  python -m pipeline.build --mode demo --out frontend/data
  python -m pipeline.build --mode real --state state --out frontend/data [--extras data/extras.csv] [--backfill-weeks 530]
Real mode: download today's AMFI NAVAll.txt (or --from-file), merge into the persistent state, compute analytics, write JSON.
Exits non-zero (so a CI run fails and nothing is deployed) if the download looks broken or no fund has enough history.
"""
import argparse
import collections
import csv
import datetime as dt
import hashlib
import json
import os
import shutil
import sys
import warnings

import numpy as np

from backend import data as demo
from backend.metrics import BY_KEY, GROUPS, METRICS, NUM_KEYS
from backend.presets import PRESETS
from . import amfi, state as stmod

SHARDS = 256
NULL_WHEN_NO_BENCH = ("beta", "alpha", "tracking_error", "info_ratio", "up_capture", "down_capture", "treynor")


def load_extras(path):
    if not path or not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return {r["scheme_code"].strip(): r for r in csv.DictReader(fh) if r.get("scheme_code")}


def _num(x):
    try:
        return float(x) if x not in (None, "") else None
    except ValueError:
        return None


def real_docs(st, extras=None, min_weeks=21):
    extras = extras or {}
    L, anchor = stmod.GRID, dt.date.fromisoformat(st.latest)
    grid = [(anchor - dt.timedelta(days=7 * (L - 1 - k))).isoformat() for k in range(L)]
    ser = {}
    for code, f in st.funds.items():
        res = stmod.anchored_series(f, st.dates, anchor)
        if not res:
            continue
        arr, (xs, ys) = res
        valid = np.flatnonzero(~np.isnan(arr))
        if len(valid) and L - valid[0] >= min_weeks:
            ser[code] = dict(arr=arr[valid[0]:], start=int(valid[0]), xs=xs, ys=ys, f=f)
    groups = collections.defaultdict(list)
    for code, s in ser.items():
        full = np.full(L - 1, np.nan)
        full[s["start"]:] = s["arr"][1:] / s["arr"][:-1] - 1
        s["key"] = (s["f"]["m"]["category"], s["f"]["m"]["sub"])
        groups[s["key"]].append(full)
    bench = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for k, rows in groups.items():
            bench[k] = np.nan_to_num(np.nanmean(np.vstack(rows), axis=0)) if len(rows) >= 3 else None
    base_ytd = dt.date(anchor.year - 1, 12, 31)
    docs, details, navs = [], [], []
    for code in sorted(ser, key=int):
        s, m = ser[code], ser[code]["f"]["m"]
        r = s["arr"][1:] / s["arr"][:-1] - 1
        b = bench[s["key"]]
        mt, _ = demo.compute_metrics(r, b[s["start"]:] if b is not None else r, 0)
        mt["nav"] = demo.rnd(s["ys"][-1], 4)
        base = stmod.nav_on(s["xs"], s["ys"], base_ytd)
        mt["ret_ytd"] = None if np.isnan(base) else demo.rnd((s["ys"][-1] / base - 1) * 100)
        if b is None:
            for k in NULL_WHEN_NO_BENCH:
                mt[k] = None
        name, plan, option = amfi.parse_name(m["name"])
        ex = extras.get(code, {})
        lock = _num(ex.get("lock_in"))
        if lock is None:
            lock = 3 if m["sub"].startswith("ELSS") else 5 if any(w in (m["sub"] + name) for w in ("Retirement", "Children")) else 0
        doc = dict(id=int(code), name=name, amc=m["amc"], category=m["category"], sub_category=m["sub"], plan=plan, option=option,
                   benchmark=ex.get("benchmark") or "Category average", riskometer=ex.get("riskometer") or "Not available",
                   lock_in=lock, **{k: _num(ex.get(k)) for k in ("aum", "expense_ratio", "min_sip", "min_lumpsum", "exit_load")})
        doc.update(mt)
        for k in NUM_KEYS:
            doc.setdefault(k, None)
        docs.append(doc)
        details.append({})
        navs.append([demo.rnd(x, 4) for x in s["arr"]])
    return docs, details, navs, grid


def from_generated(d):
    return d["funds"], d["details"], d["navs"], d["dates"]


def adapt_presets(presets, docs):
    """Keep only starter screens that make sense for this dataset (names exist, and at least one fund matches)."""
    vals = collections.defaultdict(set)

    def match(doc, flt):
        for f in flt:
            v = doc.get(f["key"])
            if "values" in f:
                if v not in f["values"]:
                    return False
            elif v is None or (f.get("min") is not None and v < f["min"]) or (f.get("max") is not None and v > f["max"]):
                return False
        return True
    out = []
    for pid, name, note, cfg in presets:
        flt, ok = [], True
        for f in cfg["filters"]:
            if "values" in f:
                if f["key"] not in vals:
                    vals[f["key"]] = {d.get(f["key"]) for d in docs}
                keep = [v for v in f["values"] if v in vals[f["key"]]]
                if not keep:
                    ok = False
                    break
                f = {**f, "values": keep}
            flt.append(f)
        if ok and any(match(d, flt) for d in docs):
            out.append(dict(id=pid, name=name, note=note, config={"universe": "all", "custom": [], "sort_dir": "desc", **cfg, "filters": flt}))
    return out


def _dump(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"), ensure_ascii=False)


def write_static(out, docs, details, navs, dates, as_of, source):
    shutil.rmtree(os.path.join(out, "nav"), ignore_errors=True)
    os.makedirs(os.path.join(out, "nav"), exist_ok=True)
    clean = [{k: v for k, v in d.items() if v is not None} for d in docs]
    _dump(os.path.join(out, "funds.json"), clean)
    _dump(os.path.join(out, "dates.json"), dates)
    shards = collections.defaultdict(lambda: dict(nav={}, detail={}))
    for d, de, nv in zip(docs, details, navs):
        sh = shards[d["id"] % SHARDS]
        sh["nav"][str(d["id"])] = nv
        if de:
            sh["detail"][str(d["id"])] = de
    for i, sh in shards.items():
        _dump(os.path.join(out, "nav", f"{i}.json"), sh)
    _dump(os.path.join(out, "metrics.json"), dict(metrics=METRICS, groups=GROUPS, functions=["abs", "min", "max", "sqrt", "log"]))
    _dump(os.path.join(out, "presets.json"), adapt_presets(PRESETS, docs))
    gen = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    _dump(os.path.join(out, "meta.json"), dict(
        build=hashlib.sha1(f"{as_of}{len(docs)}{gen}".encode()).hexdigest()[:10], as_of=as_of, source=source, count=len(docs), shards=SHARDS,
        has_aum=any(d.get("aum") is not None for d in docs), has_expense=any(d.get("expense_ratio") is not None for d in docs), generated_at=gen))
    return len(docs)


def run_demo(out, seed=7, today=None):
    d = demo.generate(seed=seed, today=today)
    docs, det, navs, dates = from_generated(d)
    return write_static(out, docs, det, navs, dates, d["as_of"], "demo")


def run_real(out, state_dir, extras=None, from_file=None, backfill_weeks=0, pause=0.3, min_records=1000, fetch=None, fetch_day=None):
    text = open(from_file, encoding="utf-8", errors="replace").read() if from_file else (fetch or amfi.fetch_text)(amfi.NAV_ALL_URL)
    recs = amfi.parse(text)
    if len(recs) < min_records:
        raise SystemExit(f"Only {len(recs)} NAV rows parsed (expected >= {min_records}); the AMFI file format may have changed. Nothing published.")
    keep = lambda r: r["open_ended"] and amfi.parse_name(r["name"])[1] == "Growth"
    recs = [r for r in recs if keep(r)]
    st = stmod.State.load(state_dir)
    latest = stmod.update_daily(st, recs)
    print(f"NAV date {latest}: {len(recs)} Growth-plan schemes, {len(st.funds)} tracked")
    if backfill_weeks:
        stmod.backfill(st, fetch_day or amfi.fetch_day, amfi.parse, keep, weeks=backfill_weeks, pause=pause, save_cb=lambda: st.save(state_dir))
    st.save(state_dir)
    docs, det, navs, dates = real_docs(st, load_extras(extras))
    if not docs:
        raise SystemExit("No scheme has 21+ weeks of NAV history yet. Run once with --backfill-weeks 530 to load history. Nothing published.")
    n = write_static(out, docs, det, navs, dates, st.latest, "real")
    print(f"wrote {n} funds to {out}")
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["demo", "real"], required=True)
    ap.add_argument("--out", default="frontend/data")
    ap.add_argument("--state", default="state")
    ap.add_argument("--extras", help="CSV: scheme_code,aum,expense_ratio,min_sip,min_lumpsum,exit_load,lock_in,benchmark,riskometer")
    ap.add_argument("--from-file", help="use a saved NAVAll.txt instead of downloading")
    ap.add_argument("--backfill-weeks", type=int, default=0)
    ap.add_argument("--pause", type=float, default=0.3, help="seconds between history requests")
    ap.add_argument("--min-records", type=int, default=1000)
    a = ap.parse_args(argv)
    if a.mode == "demo":
        print("wrote", run_demo(a.out), "demo funds to", a.out)
    else:
        run_real(a.out, a.state, a.extras, a.from_file, a.backfill_weeks, a.pause, a.min_records)


if __name__ == "__main__":
    main()
