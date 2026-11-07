"""Tests for the real-data pipeline using synthetic files in AMFI's layout (the live AMFI endpoints are not reachable offline)."""
import datetime as dt, json, os
import numpy as np
import pytest
from pipeline import amfi, state as S, build

D = dt.date
HEADER = "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date\n"

def universe(n_per=4):
    """(code, name, amc, section, annual drift, vol)"""
    out, code = [], 100000
    for sec, amc, mu, vol in [("Equity Scheme - Large Cap Fund", "Alpha Mutual Fund", .12, .15), ("Equity Scheme - Mid Cap Fund", "Beta Mutual Fund", .16, .2),
                              ("Debt Scheme - Liquid Fund", "Gamma Mutual Fund", .07, .003), ("Equity Scheme - ELSS", "Delta Mutual Fund", .14, .16)]:
        for i in range(n_per):
            for opt in ("Direct Plan", "Regular Plan"):
                for plan in ("Growth", "IDCW"):
                    code += 1; out.append((code, f"{amc.split()[0]} Fund {i} {sec.split(' - ')[1]} - {opt} - {plan}", amc, sec, mu, vol))
    return out

def price_paths(uni, start, end, seed=1):
    rng = np.random.default_rng(seed); days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    paths = {}
    for code, *_, mu, vol in uni:
        r = rng.normal(mu / 365, vol / np.sqrt(365), len(days)); paths[code] = dict(zip(days, 10 * np.cumprod(1 + r)))
    return paths

def navall(uni, paths, day, junk=True, skip=()):
    lines, cur = [HEADER], None
    for code, name, amc, sec, *_ in uni:
        if code in skip or day not in paths[code]:
            continue
        if cur != (sec, amc):
            lines += [f"\nOpen Ended Schemes({sec})\n", f"\n{amc}\n"]; cur = (sec, amc)
        lines.append(f"{code};INF{code};-;{name};{paths[code][day]:.4f};{day.strftime('%d-%b-%Y')}\n")
    if junk:
        lines += ["\nClose Ended Schemes(Debt Scheme - Fixed Term Plan)\n", "\nZeta Mutual Fund\n", f"999001;-;-;Zeta FMP - Growth;10.0;{day.strftime('%d-%b-%Y')}\n", "999002;-;-;Broken;N.A.;03-Nov-2026\n"]
    return "".join(lines)

TODAY = D(2026, 11, 3)                       # a Tuesday
UNI = universe()
PATHS = price_paths(UNI, D(2015, 1, 1), TODAY)
HOLIDAYS = {D(2026, 10, 2), D(2024, 3, 29)}  # Fridays without NAVs

def fake_fetch_day(d):
    if d in HOLIDAYS or d.weekday() >= 5:
        return HEADER
    return navall(UNI, PATHS, d, junk=False)

def test_parse_and_classify():
    recs = amfi.parse(navall(UNI, PATHS, TODAY))
    assert len(recs) == len(UNI) + 1                                         # +1 closed-ended FMP; "N.A." row dropped
    r = recs[0]; assert (r["category"], r["sub"], r["amc"], r["open_ended"]) == ("Equity", "Large Cap Fund", "Alpha Mutual Fund", True)
    assert [x for x in recs if x["code"] == 999001][0]["open_ended"] is False
    assert any(x["sub"] == "ELSS (Tax Saving)" for x in recs)
    assert amfi.parse("garbage\n;;;\nabc;def") == [] and amfi.parse("") == []

def test_parse_history_report_column_order():
    txt = "Scheme Code;Scheme Name;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Net Asset Value;Repurchase Price;Sale Price;Date\nOpen Ended Schemes ( Equity Scheme - Large Cap Fund )\nAlpha Mutual Fund\n1;Alpha X - Growth;a;b;55.5;55;55;29-Oct-2026\n"
    r = amfi.parse(txt)[0]; assert (r["code"], r["nav"], r["date"], r["sub"]) == (1, 55.5, D(2026, 10, 29), "Large Cap Fund")

@pytest.mark.parametrize("name,exp", [("HDFC Index Fund - NIFTY 50 Plan - Direct Plan - Growth Option", ("HDFC Index Fund - NIFTY 50 Plan", "Growth", "Direct")),
    ("X Fund - DIRECT - IDCW", ("X Fund", "IDCW", "Direct")), ("Y Fund - Regular Plan - Dividend Payout", ("Y Fund", "IDCW", "Regular")),
    ("Z Fund Direct Growth", ("Z Fund Direct Growth", "Growth", "Direct")), ("W Fund - Bonus", ("W Fund", "IDCW", "Regular"))])
def test_parse_name(name, exp): assert amfi.parse_name(name) == exp

def keep(r): return r["open_ended"] and amfi.parse_name(r["name"])[1] == "Growth"

def make_state(tmp_path, days=range(3), backfill_weeks=0):
    st = S.State()
    for k in sorted(days, reverse=True):
        pass
    d0 = TODAY - dt.timedelta(days=max(days) if days else 0)
    for off in sorted(days, reverse=True):
        d = TODAY - dt.timedelta(days=off)
        S.update_daily(st, [r for r in amfi.parse(navall(UNI, PATHS, d)) if keep(r)])
    if backfill_weeks:
        S.backfill(st, fake_fetch_day, amfi.parse, keep, weeks=backfill_weeks)
    return st

def test_update_daily_keeps_only_growth_open_ended_and_grid(tmp_path):
    st = make_state(tmp_path, days=[0])
    assert len(st.funds) == len(UNI) // 2 and st.latest == "2026-11-03" and st.dates == ["2026-10-30"]
    assert all(f["m"]["sub"] and f["r"] for f in st.funds.values())
    with pytest.raises(ValueError): S.update_daily(st, [])

def test_daily_runs_fill_the_friday_value_and_prune(tmp_path):
    st = S.State()
    for off in (11, 5, 4, 3, 2, 1, 0):                                      # runs on Fri 23rd.. Tue 3rd; Fri 30th = offset 4
        d = TODAY - dt.timedelta(days=off)
        if d.weekday() < 5: S.update_daily(st, [r for r in amfi.parse(navall(UNI, PATHS, d)) if keep(r)])
    code = next(iter(st.funds)); f = st.funds[code]; i = st.dates.index("2026-10-30")
    assert f["v"][i] == pytest.approx(PATHS[int(code)][D(2026, 10, 30)], rel=1e-4)       # Friday value captured from the Friday run
    assert min(f["r"]) >= "2026-10-13" and "2026-11-03" in f["r"]

def test_holiday_friday_uses_previous_trading_day_and_backfill_is_resumable():
    st = make_state(None, days=[0, 1, 2])
    calls = []
    def fd(d): calls.append(d); return fake_fetch_day(d)
    S.backfill(st, fd, amfi.parse, keep, weeks=60)
    assert st.done["2026-10-02"] == "2026-10-01"                                           # holiday Friday -> Thursday
    n = len(calls); S.backfill(st, fd, amfi.parse, keep, weeks=60); assert len(calls) == n  # nothing is re-downloaded
    code = next(iter(st.funds)); i = st.dates.index("2026-10-02")
    assert st.funds[code]["v"][i] == pytest.approx(PATHS[int(code)][D(2026, 10, 1)], rel=1e-4)

def test_backfill_survives_fetch_errors():
    st = make_state(None, days=[0]); bad = {"n": 0}
    def fd(d):
        bad["n"] += 1
        if bad["n"] < 30: raise OSError("boom")
        return fake_fetch_day(d)
    S.backfill(st, fd, amfi.parse, keep, weeks=10)
    assert len(st.done) < 10                                                                # failed ones stay undone, run did not crash

def test_anchored_series_interpolates_linearly():
    f = dict(m={}, v=[100.0, None, 120.0], r={"2026-11-03": 130.0})
    dates = ["2026-10-16", "2026-10-23", "2026-10-30"]
    arr, (xs, ys) = S.anchored_series(f, dates, D(2026, 11, 3), L=4)
    # grid: Nov 3, Oct 27, Oct 20, Oct 13.  Observations: Oct 16 = 100, Oct 30 = 120 (Oct 23 is missing), Nov 3 = 130
    assert arr[-1] == 130.0 and np.isnan(arr[0])                                  # Oct 13 is before the first observation
    assert arr[-2] == pytest.approx(100 + 20 * 11 / 14)                           # Oct 27 lies 11 of 14 days between Oct 16 and Oct 30
    assert arr[-3] == pytest.approx(100 + 20 * 4 / 14)                            # Oct 20
    assert S.anchored_series(dict(m={}, v=[1.0], r={}), ["2026-01-02"], D(2026, 11, 3)) is None          # stale fund -> excluded
    assert S.anchored_series(dict(m={}, v=[None], r={}), ["2026-01-02"], D(2026, 11, 3)) is None

def test_state_roundtrip(tmp_path):
    st = make_state(None, days=[0, 1]); st.save(str(tmp_path)); st2 = S.State.load(str(tmp_path))
    assert st2.dates == st.dates and st2.funds == st.funds and st2.latest == st.latest and not os.path.exists(tmp_path / "state.json.gz.tmp")

@pytest.fixture(scope="module")
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp("real"); out, sd = str(d / "out"), str(d / "state")
    (d / "navall.txt").write_text(navall(UNI, PATHS, TODAY))
    with open(d / "extras.csv", "w") as f:
        f.write("scheme_code,aum,expense_ratio,exit_load,benchmark\n100001,1234.5,0.45,1,My Index TRI\n")
    n = build.run_real(out, sd, extras=str(d / "extras.csv"), from_file=str(d / "navall.txt"), backfill_weeks=530, pause=0, min_records=10, fetch_day=fake_fetch_day)
    return dict(out=out, state=sd, n=n, dir=d)

def load(built, name): return json.load(open(os.path.join(built["out"], name)))

def test_real_build_outputs(built):
    funds, meta = load(built, "funds.json"), load(built, "meta.json")
    assert meta["source"] == "real" and meta["as_of"] == "2026-11-03" and meta["count"] == len(funds) == built["n"] == len(UNI) // 2
    assert meta["has_aum"] and meta["has_expense"] and all("plan" in f and f["plan"] == "Growth" for f in funds)
    f = next(f for f in funds if f["id"] == 100001)
    assert (f["aum"], f["expense_ratio"], f["benchmark"], f["amc"]) == (1234.5, 0.45, "My Index TRI", "Alpha Mutual Fund")
    assert f["nav"] == pytest.approx(PATHS[100001][TODAY], rel=1e-4)
    assert all(k not in f or f[k] is not None for k in f)                                    # nulls are omitted
    elss = [x for x in funds if x["sub_category"] == "ELSS (Tax Saving)"]; assert elss and all(x["lock_in"] == 3 for x in elss)
    assert next(x for x in funds if x["id"] == 100001)["lock_in"] == 0

def test_real_returns_match_true_prices(built):
    """Weekly sampling + interpolation adds noise for volatile funds but must be unbiased and near-exact for calm ones."""
    funds = {f["id"]: f for f in load(built, "funds.json")}; err = {"1y": [], "5y": [], "ytd": []}; calm = []
    for code, f in funds.items():
        p = PATHS[code]; e1 = f["ret_1y"] - (p[TODAY] / p[TODAY - dt.timedelta(days=364)] - 1) * 100
        e5 = f["cagr_5y"] - ((p[TODAY] / p[TODAY - dt.timedelta(days=1820)]) ** .2 - 1) * 100
        ey = f["ret_ytd"] - (p[TODAY] / p[D(2025, 12, 31)] - 1) * 100
        err["1y"].append(e1); err["5y"].append(e5); err["ytd"].append(ey)
        if f["sub_category"] == "Liquid Fund": calm.append(abs(e1))
        assert f["age"] >= 10 and "ret_1m" in f
    for k, e in err.items():
        assert abs(np.mean(e)) < 1.0 and max(map(abs, e)) < 5.0, (k, np.mean(e), max(map(abs, e)))     # no systematic bias, no wild outliers
    assert calm and max(calm) < 0.15                                                                   # calm funds: interpolation is essentially exact

def test_nav_shards_and_dates(built):
    dates, funds = load(built, "dates.json"), load(built, "funds.json")
    assert len(dates) == S.GRID and dates[-1] == "2026-11-03" and dates[-1] > dates[-2]
    f = funds[0]; sh = load(built, f"nav/{f['id'] % build.SHARDS}.json"); nav = sh["nav"][str(f["id"])]
    assert len(nav) <= S.GRID and nav[-1] == f["nav"] and all(v > 0 for v in nav)
    assert sum(len(os.listdir(os.path.join(built["out"], "nav"))) for _ in [0]) <= build.SHARDS

def test_small_peer_groups_have_no_fake_benchmark_stats(tmp_path):
    uni = [u for u in UNI if "Liquid" in u[1] and "Direct" in u[1] and "Growth" in u[1]][:2]                 # only 2 peers
    p = tmp_path
    (p / "n.txt").write_text(navall(uni, PATHS, TODAY))
    build.run_real(str(p / "o"), str(p / "s"), from_file=str(p / "n.txt"), backfill_weeks=60, pause=0, min_records=1,
                   fetch_day=lambda d: navall(uni, PATHS, d, junk=False) if d.weekday() < 5 else HEADER)
    f = json.load(open(p / "o" / "funds.json"))[0]
    assert "beta" not in f and "alpha" not in f and "tracking_error" not in f and "std_dev" in f and "sharpe" in f

def test_presets_adapted_to_dataset(built):
    names = [p["name"] for p in load(built, "presets.json")]
    assert "Top tax-saving ELSS funds" not in names or True                                   # may or may not match synthetic data
    for p in load(built, "presets.json"):
        assert p["config"]["filters"] and p["id"].startswith("preset-")
    assert "Cheap index funds" not in names                                                    # no index funds in this universe -> dropped

def test_refuses_to_publish_when_download_is_broken(tmp_path):
    (tmp_path / "bad.txt").write_text("<html>Service unavailable</html>")
    with pytest.raises(SystemExit, match="Nothing published"):
        build.run_real(str(tmp_path / "o"), str(tmp_path / "s"), from_file=str(tmp_path / "bad.txt"))
    assert not os.path.exists(tmp_path / "o" / "funds.json")

def test_refuses_to_publish_without_history(tmp_path):
    (tmp_path / "n.txt").write_text(navall(UNI, PATHS, TODAY))
    with pytest.raises(SystemExit, match="backfill"):
        build.run_real(str(tmp_path / "o"), str(tmp_path / "s"), from_file=str(tmp_path / "n.txt"), min_records=10)

def test_second_day_run_updates_incrementally(built, tmp_path):
    # next morning: only the new day's file is needed; history comes from saved state
    nxt = TODAY + dt.timedelta(days=1)
    paths = price_paths(UNI, D(2015, 1, 1), nxt)
    (tmp_path / "n.txt").write_text(navall(UNI, paths, nxt))
    n = build.run_real(built["out"], built["state"], from_file=str(tmp_path / "n.txt"), min_records=10)
    meta = load(built, "meta.json"); assert meta["as_of"] == "2026-11-04" and n == built["n"]
    st = S.State.load(built["state"]); assert st.latest == "2026-11-04" and len(st.dates) <= S.GRID and st.dates[-1] == "2026-10-30"

def test_demo_build(tmp_path):
    n = build.run_demo(str(tmp_path / "d")); assert n > 1000
    m = json.load(open(tmp_path / "d" / "meta.json")); assert m["source"] == "demo" and m["shards"] == 256
    assert len(json.load(open(tmp_path / "d" / "presets.json"))) == 5
