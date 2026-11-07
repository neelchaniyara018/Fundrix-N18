"""Dataset generation and metric computation.

compute_metrics() is the real analytics code: every return / risk / ratio is derived from a
weekly NAV series versus a benchmark series. The same function can be fed real NAVs
(see ingest_real.py). generate() builds a demo universe with FICTIONAL fund houses.
"""
import datetime as dt
import json
import math
import sqlite3

import numpy as np

RF = 0.065  # risk-free rate (annual)
WK = 52

# ----------------------------------------------------------------------------- analytics


def rnd(x, d=2):
    if x is None:
        return None
    x = float(x)
    return None if (math.isnan(x) or math.isinf(x)) else round(x, d)


def compute_metrics(r, b, ytd_weeks, rf=RF):
    """r, b: weekly simple returns of fund and benchmark (equal length A). Returns metric dict."""
    r, b = np.asarray(r, float), np.asarray(b, float)
    A = len(r)
    nav = np.concatenate([[10.0], 10.0 * np.cumprod(1 + r)])
    o = {"nav": rnd(nav[-1]), "age": rnd(A / WK, 1)}

    def ret(w):
        return rnd((nav[-1] / nav[-1 - w] - 1) * 100) if A >= w else None

    def cagr(y):
        w = WK * y
        return rnd(((nav[-1] / nav[-1 - w]) ** (1 / y) - 1) * 100) if A >= w else None

    o.update(ret_1m=ret(4), ret_3m=ret(13), ret_6m=ret(26), ret_ytd=ret(ytd_weeks) if 0 < ytd_weeks <= A else None,
             ret_1y=ret(52), cagr_3y=cagr(3), cagr_5y=cagr(5), cagr_10y=cagr(10))
    if A >= 156 + 26:
        x = (nav[156:] / nav[:-156]) ** (1 / 3) - 1
        o["roll_3y_avg"], o["roll_3y_min"] = rnd(x.mean() * 100), rnd(x.min() * 100)
    else:
        o["roll_3y_avg"] = o["roll_3y_min"] = None

    keys = ["std_dev", "beta", "alpha", "max_drawdown", "tracking_error", "up_capture", "down_capture",
            "sharpe", "sortino", "treynor", "info_ratio", "calmar"]
    o.update({k: None for k in keys})
    if A >= WK:
        W = min(A, 156)
        rr, bb = r[-W:], b[-W:]
        ann = rr.mean() * WK
        sd = rr.std(ddof=1) * math.sqrt(WK)
        o["std_dev"] = rnd(sd * 100)
        if sd > 1e-6:
            o["sharpe"] = rnd(max(-15, min(15, (ann - rf) / sd)))
        dd = math.sqrt(np.mean(np.minimum(rr - rf / WK, 0) ** 2)) * math.sqrt(WK)
        if dd > 1e-6:
            o["sortino"] = rnd(max(-15, min(15, (ann - rf) / dd)))
        vb = bb.var(ddof=1)
        if vb > 1e-12:
            beta = np.cov(rr, bb)[0, 1] / vb
            o["beta"] = rnd(beta)
            o["alpha"] = rnd(((rr.mean() - rf / WK) - beta * (bb.mean() - rf / WK)) * WK * 100)
            if beta > 0.05:
                o["treynor"] = rnd((ann - rf) / beta * 100)
        te = (rr - bb).std(ddof=1) * math.sqrt(WK)
        o["tracking_error"] = rnd(te * 100)
        if te > 1e-4:
            o["info_ratio"] = rnd((rr - bb).mean() * WK / te)
        up, dn = bb > 0, bb < 0
        if up.sum() > 3 and bb[up].mean() != 0:
            o["up_capture"] = rnd(rr[up].mean() / bb[up].mean() * 100, 1)
        if dn.sum() > 3 and bb[dn].mean() != 0:
            o["down_capture"] = rnd(rr[dn].mean() / bb[dn].mean() * 100, 1)
        seg = nav[-(min(A, 260) + 1):]
        mdd = (seg / np.maximum.accumulate(seg) - 1).min() * 100
        o["max_drawdown"] = rnd(mdd)
        if o["cagr_3y"] is not None and mdd < -0.05:
            o["calmar"] = rnd(o["cagr_3y"] / abs(mdd))
    return o, nav


# ----------------------------------------------------------------------------- universe spec

def S(cat, name, kind, mu, vol, rho=0.85, caps=None, eq=None, risk="Very High", bench=None, lock=0,
      passive=False, n=8, dur=None, extra=None):
    return dict(cat=cat, name=name, kind=kind, mu=mu, vol=vol, rho=rho, caps=caps, eq=eq, risk=risk,
                bench=bench or f"{name.split(' - ')[-1].replace(' Fund', '')} Index TRI", lock=lock,
                passive=passive, n=n, dur=dur, extra=extra or {})


EQ = "Equity"
SPECS = [
    S(EQ, "Large Cap Fund", "eq", .125, .145, caps=(80, 15, 5), n=10, bench="Large Cap Index TRI"),
    S(EQ, "Large & Mid Cap Fund", "eq", .14, .16, caps=(45, 40, 15), n=10, bench="Large-Mid Cap Index TRI"),
    S(EQ, "Mid Cap Fund", "eq", .17, .19, caps=(10, 70, 20), n=10, bench="Mid Cap Index TRI"),
    S(EQ, "Small Cap Fund", "eq", .19, .22, caps=(5, 20, 75), n=10, bench="Small Cap Index TRI"),
    S(EQ, "Flexi Cap Fund", "eq", .14, .15, caps=(55, 25, 20), n=12, bench="Broad Market Index TRI"),
    S(EQ, "Multi Cap Fund", "eq", .15, .17, caps=(35, 35, 30), n=10, bench="Multi Cap Index TRI"),
    S(EQ, "Focused Fund", "eq", .14, .16, caps=(60, 25, 15), n=10, bench="Broad Market Index TRI", extra=dict(hold=(20, 30))),
    S(EQ, "Value Fund", "eq", .155, .16, caps=(50, 30, 20), n=10, bench="Value Index TRI"),
    S(EQ, "Contra Fund", "eq", .155, .16, caps=(50, 30, 20), n=6, bench="Broad Market Index TRI"),
    S(EQ, "Dividend Yield Fund", "eq", .135, .14, caps=(65, 25, 10), n=6, bench="Dividend Yield Index TRI"),
    S(EQ, "ELSS (Tax Saving)", "eq", .145, .16, caps=(55, 25, 20), n=12, lock=3, bench="Broad Market Index TRI"),
    S(EQ, "Sectoral - Banking", "eq", .12, .19, caps=(70, 20, 10), n=8, bench="Bank Index TRI", extra=dict(sector="Banks")),
    S(EQ, "Sectoral - Technology", "eq", .15, .20, caps=(65, 25, 10), n=8, bench="IT Index TRI", extra=dict(sector="IT")),
    S(EQ, "Sectoral - Healthcare", "eq", .145, .17, caps=(50, 30, 20), n=8, bench="Healthcare Index TRI", extra=dict(sector="Pharma")),
    S(EQ, "Thematic - Infrastructure", "eq", .165, .21, caps=(40, 35, 25), n=8, bench="Infra Index TRI", extra=dict(sector="Capital Goods")),
    S(EQ, "Thematic - Consumption", "eq", .14, .16, caps=(60, 25, 15), n=8, bench="Consumption Index TRI", extra=dict(sector="FMCG")),
    S(EQ, "Thematic - Energy", "eq", .15, .20, caps=(55, 25, 20), n=8, bench="Energy Index TRI", extra=dict(sector="Energy")),
    S(EQ, "Index Fund - Large Cap", "eq", .125, .145, caps=(97, 3, 0), n=10, passive=True, bench="Large Cap Index TRI", extra=dict(hold=(50, 50))),
    S(EQ, "Index Fund - Mid Cap", "eq", .165, .19, caps=(0, 95, 5), n=8, passive=True, bench="Mid Cap Index TRI", extra=dict(hold=(100, 150))),
    S(EQ, "Index Fund - Small Cap", "eq", .185, .22, caps=(0, 0, 100), n=6, passive=True, bench="Small Cap Index TRI", extra=dict(hold=(100, 250))),
    S(EQ, "International Equity (FoF)", "eq", .11, .17, rho=.45, caps=(90, 8, 2), n=8, bench="Global Index TRI"),
    S(EQ, "Quant / Factor Fund", "eq", .15, .17, caps=(45, 30, 25), n=8, bench="Broad Market Index TRI"),
    S(EQ, "ESG Fund", "eq", .12, .15, caps=(75, 20, 5), n=6, bench="ESG Index TRI"),
]
_DEBT = [  # name, mu, vol, duration, rho, extra
    ("Liquid Fund", .069, .002, .1, .02), ("Overnight Fund", .063, .0007, .01, .01),
    ("Ultra Short Duration Fund", .072, .003, .4, .03), ("Low Duration Fund", .074, .004, .8, .04),
    ("Money Market Fund", .073, .003, .6, .03), ("Short Duration Fund", .076, .007, 2.5, .05),
    ("Medium Duration Fund", .078, .011, 3.5, .08), ("Medium to Long Duration Fund", .08, .016, 5, .08),
    ("Long Duration Fund", .082, .03, 10, .1), ("Dynamic Bond Fund", .08, .02, 6, .1),
    ("Corporate Bond Fund", .077, .008, 3, .06), ("Credit Risk Fund", .085, .012, 2.5, .25),
    ("Banking & PSU Fund", .075, .007, 2.8, .05), ("Gilt Fund", .08, .025, 8, .08),
    ("Gilt Fund with 10Y Constant Duration", .082, .03, 9, .08), ("Floater Fund", .075, .006, 2, .05),
    ("Target Maturity Fund", .076, .01, 4, .06), ("Index Fund - Debt", .075, .009, 4, .06),
    ("Fund of Funds - Debt", .074, .008, 3, .05), ("Fixed Maturity Plan", .072, .003, 1, .03),
]
for nm, mu, vol, du, rho in _DEBT:
    risk = "Low" if du < .2 else "Low to Moderate" if du < 1.5 else "Moderate" if "Credit" not in nm else "Moderately High"
    SPECS.append(S("Debt", nm, "debt", mu, vol, rho=rho, risk=risk, n=6, dur=du, bench=f"{nm.replace(' Fund', '')} Index",
                   passive=("Index" in nm or "Target" in nm)))
SPECS += [
    S("Hybrid", "Aggressive Hybrid Fund", "hyb", .13, .11, rho=.8, eq=75, risk="Very High", n=10, bench="Hybrid 65:35 Index"),
    S("Hybrid", "Conservative Hybrid Fund", "hyb", .095, .04, rho=.6, eq=20, risk="Moderately High", n=8, bench="Hybrid 15:85 Index"),
    S("Hybrid", "Balanced Advantage Fund", "hyb", .115, .08, rho=.75, eq=55, risk="High", n=10, bench="Hybrid 50:50 Index"),
    S("Hybrid", "Equity Savings Fund", "hyb", .085, .04, rho=.6, eq=35, risk="Moderate", n=8, bench="Equity Savings Index"),
    S("Hybrid", "Arbitrage Fund", "hyb", .07, .006, rho=.1, eq=70, risk="Low", n=8, bench="Arbitrage Index", extra=dict(arb=1)),
    S("Hybrid", "Multi Asset Allocation Fund", "hyb", .13, .09, rho=.65, eq=50, risk="Very High", n=8, bench="Multi Asset Index", extra=dict(gold=15)),
    S("Hybrid", "Retirement Fund", "hyb", .11, .09, rho=.75, eq=50, risk="High", n=6, lock=5, bench="Hybrid 50:50 Index"),
    S("Hybrid", "Children's Fund", "hyb", .11, .09, rho=.75, eq=50, risk="High", n=6, lock=5, bench="Hybrid 50:50 Index"),
    S("Commodity", "Gold Fund (FoF / ETF)", "gold", .11, .13, rho=.1, risk="High", n=5, bench="Domestic Gold Price"),
    S("Commodity", "Silver Fund (FoF / ETF)", "gold", .12, .28, rho=.15, risk="Very High", n=3, bench="Domestic Silver Price"),
    S("Other", "Equity ETF", "eq", .125, .145, caps=(97, 3, 0), n=4, passive=True, bench="Large Cap Index TRI", extra=dict(hold=(50, 50))),
    S("Other", "Debt ETF", "debt", .075, .009, rho=.06, risk="Low to Moderate", n=3, dur=3.5, passive=True, bench="Debt ETF Index"),
    S("Other", "Fund of Funds - Domestic", "hyb", .12, .12, rho=.8, eq=70, n=3, bench="Hybrid 65:35 Index"),
    S("Other", "Fund of Funds - Overseas", "eq", .115, .17, rho=.45, caps=(90, 8, 2), n=3, bench="Global Index TRI"),
    S("Other", "REIT / InvIT Fund", "hyb", .09, .10, rho=.4, eq=40, risk="Moderately High", n=2, bench="REIT Index"),
    S("Other", "Multi-Manager Fund of Funds", "hyb", .12, .13, rho=.8, eq=65, n=3, bench="Hybrid 65:35 Index"),
    S("Other", "Index Fund - Other", "eq", .12, .15, caps=(70, 20, 10), n=3, passive=True, bench="Broad Market Index TRI"),
]

AMCS = ["Aurora", "Banyan", "Cedar", "Deccan Crest", "Everest Capital", "Falcon", "Gangotri", "Halcyon",
        "Indus Trail", "Juniper", "Kaveri", "Lotus Peak", "Meridian", "Narmada", "Orion", "Pinnacle",
        "Quartz", "Rajmahal", "Sahyadri", "Trident", "Udaan", "Vertex", "Waverly", "Zenith"]
MANAGERS = ["A. Rao", "B. Mehta", "C. Iyer", "D. Nair", "E. Kapoor", "F. Shah", "G. Menon", "H. Banerjee",
            "I. Joshi", "J. Pillai", "K. Desai", "L. Reddy", "M. Verma", "N. Kulkarni", "O. Sethi", "P. Bhatt"]
SECTORS = [("Banks", "Bank"), ("IT", "Infotech"), ("Pharma", "Pharma"), ("Auto", "Motors"), ("FMCG", "Consumer"),
           ("Energy", "Power"), ("Metals", "Steel"), ("Capital Goods", "Engineering"), ("Telecom", "Telecom"),
           ("Realty", "Estates"), ("Chemicals", "Chemicals"), ("Consumer Durables", "Appliances"),
           ("Insurance", "Life"), ("NBFC", "Finance")]
PREFIX = ["Arya", "Bharat", "Cosmo", "Dhruva", "Ekta", "Gaurav", "Hind", "Ishan", "Jyoti", "Kiran"]
STOCKS = [(f"{PREFIX[(i * 3) % 10]} {SECTORS[i % 14][1]}" + ("" if i < 14 else f" {['II', 'III', 'IV'][i // 14 - 1]}"),
           SECTORS[i % 14][0]) for i in range(56)]
ISSUERS = ["Govt. of India", "State Dev. Loan", "Aurora Finance", "Banyan Housing", "Cedar Power", "Falcon Infra",
           "Meridian Bank", "Orion Capital", "Quartz Leasing", "Trident Rail", "Zenith Ports", "Sahyadri Bank"]
INSTR = ["G-Sec", "SDL", "NCD", "CP", "CD", "T-Bill"]


def _last_friday(d):
    return d - dt.timedelta(days=(d.weekday() - 4) % 7)


def _holdings(rng, spec, n_hold):
    kind = spec["kind"]
    k = min(n_hold, 10)
    if kind == "gold":
        return [dict(name="Physical Gold" if "Gold" in spec["name"] else "Physical Silver", sector="Commodity", weight=99.0)], \
               [dict(sector="Commodity", weight=99.0)]
    w = np.sort(rng.pareto(1.6 if kind != "debt" else 2.2, k) + 1)[::-1]
    w = w / w.sum() * (rng.uniform(22, 48) if kind in ("eq", "hyb") else rng.uniform(30, 70))
    hold = []
    if kind == "debt":
        for i in range(k):
            hold.append(dict(name=f"{ISSUERS[i % len(ISSUERS)]} {rng.choice(INSTR)}", sector=str(rng.choice(INSTR)),
                             weight=round(float(w[i]), 2)))
        return hold, [dict(sector=s, weight=round(float(x), 1)) for s, x in
                      zip(["G-Sec/SDL", "Corporate Bonds", "Money Market", "Cash"], np.array([.35, .4, .2, .05]) * 100)]
    pool = [s for s in STOCKS if spec["extra"].get("sector") in (None, s[1])] or STOCKS
    idx = rng.choice(len(pool), size=min(k, len(pool)), replace=False)
    for i, j in enumerate(idx):
        hold.append(dict(name=pool[j][0], sector=pool[j][1], weight=round(float(w[i]), 2)))
    sect = {}
    for h in hold:
        sect[h["sector"]] = sect.get(h["sector"], 0) + h["weight"]
    rest = max(0, 100 - sum(sect.values()))
    for s in rng.choice([x[0] for x in SECTORS], 4, replace=False):
        v = rest * rng.uniform(.1, .35)
        sect[s] = sect.get(s, 0) + v
    tot = sum(sect.values())
    return hold, sorted([dict(sector=s, weight=round(v / tot * 100, 1)) for s, v in sect.items()],
                        key=lambda x: -x["weight"])[:8]


def generate(seed=7, today=None):
    rng = np.random.default_rng(seed)
    today = today or dt.date.today()
    end = _last_friday(today)
    L = 531  # weekly points (> 10Y)
    dates = [end - dt.timedelta(weeks=L - 1 - i) for i in range(L)]
    ytd_weeks = sum(1 for d in dates if d.year == end.year) - 1
    m = rng.normal(0, 1, L - 1)
    m[L - 1 - 205:L - 1 - 199] = -3.2          # synthetic market crash ~3.9 years ago ...
    m[L - 1 - 199:L - 1 - 191] = 1.8           # ... and rebound
    for a, b_ in ((0, 156), (156, 312), (312, L - 1)):   # keep every 3Y block's drift ~ the stated mu
        lo, hi = max(0, L - 1 - b_), L - 1 - a
        m[lo:hi] -= m[lo:hi].mean()
    zm = (m - m.mean()) / m.std()
    funds, details, navs = [], [], []
    fid = 0
    for si, sp in enumerate(SPECS):
        zi = rng.normal(0, 1, L - 1)
        rho = sp["rho"]
        idx_r = sp["mu"] / WK + sp["vol"] / math.sqrt(WK) * (rho * zm + math.sqrt(1 - rho ** 2) * zi)
        amcs = rng.choice(len(AMCS), size=sp["n"], replace=False)
        for ai in amcs:
            A = int(rng.integers(60, 520)) if rng.random() < .85 else int(rng.integers(20, 110))
            if rng.random() < .25:
                A = 520
            A = max(20, A)
            passive = sp["passive"]
            beta_f = rng.normal(1.0 if not passive else .998, .08 if not passive else .003)
            te_w = (.005 if passive else (.03 if sp["kind"] != "debt" else .006)) / math.sqrt(WK)
            alpha_w = 0 if passive else rng.normal(.01 if sp["kind"] != "debt" else .002, .025 if sp["kind"] != "debt" else .005) / WK
            noise = rng.normal(0, te_w, A)
            gross = beta_f * idx_r[-A:] + alpha_w + noise
            base_aum = float(np.clip(np.exp(rng.normal(math.log(1800), 1.25)), 25, 130000))
            kind = sp["kind"]
            lo, hi = {"eq": (1.4, 2.3), "debt": (.3, 1.3), "hyb": (1.3, 2.1), "gold": (.4, .9)}[kind]
            if sp["name"] in ("Liquid Fund", "Overnight Fund"):
                lo, hi = .15, .35
            if passive:
                lo, hi = .25, .7
            reg_exp = rng.uniform(lo, hi)
            hold_rng = sp["extra"].get("hold") or {"eq": (25, 70), "debt": (15, 60), "hyb": (30, 90), "gold": (1, 1)}[kind]
            n_hold = int(rng.integers(hold_rng[0], hold_rng[1] + 1))
            holdings, sectors = _holdings(rng, sp, n_hold)
            top10 = round(sum(h["weight"] for h in holdings), 1) if kind != "gold" else 99.0
            # portfolio composition (shared by the 4 variants)
            eqp = {"eq": rng.uniform(93, 99.5), "hyb": (sp["eq"] or 0) + rng.uniform(-4, 4), "debt": 0, "gold": 0}[kind]
            if sp["extra"].get("arb"):
                eqp = rng.uniform(65, 75)
            gold = sp["extra"].get("gold", 0) + (rng.uniform(-3, 3) if sp["extra"].get("gold") else 0)
            if kind == "gold":
                gold, eqp = 97.0, 0
            cash = rng.uniform(.5, 6) if kind != "debt" else rng.uniform(1, 12)
            debt = max(0, 100 - eqp - gold - cash) if kind in ("hyb", "debt") else rng.uniform(0, 1.5 if kind == "eq" else 0)
            tot = eqp + debt + cash + gold
            pf = dict(equity_pct=eqp / tot * 100, debt_pct=debt / tot * 100, cash_pct=cash / tot * 100, other_pct=gold / tot * 100)
            if pf["equity_pct"] >= 35 and sp["caps"] or (kind == "hyb" and pf["equity_pct"] > 0):
                caps = np.array(sp["caps"] or (65, 25, 10), float) + rng.normal(0, 3, 3)
                caps = np.clip(caps, 0, None)
                caps = caps / caps.sum() * 100
                pf.update(large_pct=caps[0], mid_pct=caps[1], small_pct=caps[2])
                pe = (caps[0] * 23 + caps[1] * 36 + caps[2] * 40) / 100 + rng.normal(0, 2)
                pf.update(pe=pe, pb=pe / rng.uniform(4.5, 6.5))
            if kind in ("debt",) or (kind == "hyb" and pf["debt_pct"] >= 20):
                du = (sp["dur"] if sp["dur"] is not None else 2.5) * rng.uniform(.8, 1.2)
                pf.update(ytm=sp["mu"] * 100 - .3 + rng.normal(0, .25), mod_duration=du, avg_maturity=du * rng.uniform(1.05, 1.3))
            exit_pct = 0.0 if (passive or kind == "debt" and sp["dur"] and sp["dur"] < 1) else float(rng.choice([0, 0.5, 1.0, 1.0, 1.0]))
            if sp["extra"].get("arb"):
                exit_pct = .25
            exit_days = 365 if exit_pct >= 1 else (30 if exit_pct else 0)
            minl = int(rng.choice([100, 500, 1000, 5000]))
            mins = int(rng.choice([100, 500, 1000]))
            launch = dates[-(A + 1)]
            mgr = [str(x) for x in rng.choice(MANAGERS, 2 if rng.random() < .35 else 1, replace=False)]
            for plan in ("Growth", "IDCW"):
                for option in ("Regular", "Direct"):
                    exp = reg_exp if option == "Regular" else max(.05, reg_exp - rng.uniform(.5, 1.0) * (.5 if passive else 1))
                    drag = (0 if plan == "Growth" else (.04 if kind == "debt" else .03)) / WK
                    r = gross - exp / 100 / WK - drag
                    mt, nav = compute_metrics(r, idx_r[-A:], ytd_weeks)
                    # AUM: direct plans smaller, IDCW much smaller
                    aum = base_aum * (1 if option == "Regular" else .45) * (1 if plan == "Growth" else .12)
                    f = dict(id=fid, name=f"{AMCS[ai]} {sp['name']}", amc=f"{AMCS[ai]} Mutual Fund", category=sp["cat"],
                             sub_category=sp["name"], plan=plan, option=option, benchmark=sp["bench"],
                             riskometer=sp["risk"], aum=rnd(aum), expense_ratio=rnd(exp), min_lumpsum=minl,
                             min_sip=mins, exit_load=exit_pct, lock_in=sp["lock"], num_holdings=n_hold, top10_pct=top10)
                    f.update(mt)
                    for k, v in pf.items():
                        f[k] = rnd(v, 2)
                    funds.append(f)
                    det = dict(manager=mgr, manager_since=int(launch.year + rng.integers(0, max(1, end.year - launch.year))),
                               launch=launch.isoformat(), exit_load_days=exit_days, holdings=holdings, sectors=sectors,
                               ytd_weeks=ytd_weeks, base=si)
                    details.append(det)
                    navs.append([rnd(x, 4) for x in nav])
                    fid += 1
    for f, d in zip(funds, details):
        d["manager_since"] = min(max(d["manager_since"], int(d["launch"][:4])), end.year)
    return dict(funds=funds, details=details, navs=navs, dates=[d.isoformat() for d in dates], as_of=end.isoformat())


def write_db(d, path):
    """Persist a generate() result as the SQLite file used by the (dev/test) API server."""
    import os
    if os.path.exists(path):
        os.remove(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(open(os.path.join(os.path.dirname(__file__), "schema.sql")).read())
    con.executemany("INSERT INTO funds(id, doc, detail, nav) VALUES (?,?,?,?)",
                    [(f["id"], json.dumps(f), json.dumps(de), json.dumps(nv))
                     for f, de, nv in zip(d["funds"], d["details"], d["navs"])])
    con.execute("INSERT INTO meta(key,value) VALUES ('dates',?)", (json.dumps(d["dates"]),))
    con.execute("INSERT INTO meta(key,value) VALUES ('as_of',?)", (d["as_of"],))
    con.execute("INSERT INTO meta(key,value) VALUES ('source','demo')")
    con.commit()
    con.close()
    return len(d["funds"])


def build_db(path, **kw):
    return write_db(generate(**kw), path)


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "data/funds.db"
    print("generated", build_db(out), "funds ->", out)
