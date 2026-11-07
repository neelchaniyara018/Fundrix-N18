"""Persistent weekly-NAV history so each daily run only needs that day's NAVAll.txt.

state.json.gz = { "dates": [Friday ISO...], "latest": ISO, "done": {friday: date_used},
                  "funds": { code: {"m": meta, "v": [nav per Friday | null], "r": {iso: nav}  # last ~3 weeks of daily NAVs
                                   } } }
The long history is weekly (Friday); `r` keeps recent daily values so returns are up to date to the day. Analytics are computed on a
weekly grid ANCHORED at the latest NAV date by linear interpolation (anchored_series), so "1Y return" really spans 365 days.
"""
import collections
import datetime as dt
import gzip
import json
import os

import numpy as np

GRID = 531                      # weekly points kept (> 10 years)
KEEP_DAYS = 21                  # daily values retained in "r"
FILE = "state.json.gz"


def last_friday(d):
    return d - dt.timedelta(days=(d.weekday() - 4) % 7)


def iso(d):
    return d.isoformat()


class State:
    def __init__(self):
        self.dates, self.funds, self.latest, self.done = [], {}, None, {}

    # ---- persistence
    @classmethod
    def load(cls, directory):
        st, path = cls(), os.path.join(directory, FILE)
        if os.path.exists(path):
            with gzip.open(path, "rt", encoding="utf-8") as f:
                d = json.load(f)
            st.dates, st.funds, st.latest, st.done = d["dates"], d["funds"], d.get("latest"), d.get("done", {})
        return st

    def save(self, directory):
        os.makedirs(directory, exist_ok=True)
        path, tmp = os.path.join(directory, FILE), os.path.join(directory, FILE + ".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as f:
            json.dump(dict(dates=self.dates, funds=self.funds, latest=self.latest, done=self.done), f, separators=(",", ":"))
        os.replace(tmp, path)

    # ---- grid
    def ensure_grid(self, first, last):
        """Make self.dates cover Fridays first..last (both Fridays); existing per-fund arrays are padded with None."""
        if not self.dates:
            n = (last - first).days // 7 + 1
            self.dates = [iso(first + dt.timedelta(weeks=i)) for i in range(n)]
            for f in self.funds.values():
                f["v"] = [None] * n
            return
        while dt.date.fromisoformat(self.dates[0]) > first:
            self.dates.insert(0, iso(dt.date.fromisoformat(self.dates[0]) - dt.timedelta(weeks=1)))
            for f in self.funds.values():
                f["v"].insert(0, None)
        while dt.date.fromisoformat(self.dates[-1]) < last:
            self.dates.append(iso(dt.date.fromisoformat(self.dates[-1]) + dt.timedelta(weeks=1)))
            for f in self.funds.values():
                f["v"].append(None)
        extra = len(self.dates) - GRID
        if extra > 0:
            del self.dates[:extra]
            for f in self.funds.values():
                del f["v"][:extra]


def update_daily(st, records):
    """Merge one day's records (already filtered to the wanted schemes). Returns the latest NAV date."""
    if not records:
        raise ValueError("no records")
    latest = collections.Counter(r["date"] for r in records).most_common(1)[0][0]       # mode, robust to a few laggards
    lf = last_friday(latest)
    st.ensure_grid(lf, lf) if not st.dates else st.ensure_grid(dt.date.fromisoformat(st.dates[0]), lf)
    for r in records:
        f = st.funds.setdefault(str(r["code"]), dict(m={}, v=[None] * len(st.dates), r={}))
        f["m"] = dict(name=r["name"], amc=r["amc"], category=r["category"], sub=r["sub"])
        f["r"][iso(r["date"])] = r["nav"]
    n = len(st.dates)
    for f in st.funds.values():
        for i in range(max(0, n - 3), n):                                                # fill the last 3 weeks from daily values
            if f["v"][i] is None:
                w = dt.date.fromisoformat(st.dates[i])
                cands = [d for d in f["r"] if w - dt.timedelta(days=7) < dt.date.fromisoformat(d) <= w]
                if cands:
                    f["v"][i] = f["r"][max(cands)]
        f["r"] = {d: v for d, v in f["r"].items() if dt.date.fromisoformat(d) >= latest - dt.timedelta(days=KEEP_DAYS)}
    st.latest = iso(max(latest, dt.date.fromisoformat(st.latest)) if st.latest else latest)
    for code in [c for c, f in st.funds.items() if not f["r"] and not any(v is not None for v in f["v"][-9:])]:
        del st.funds[code]                                                                 # dead scheme: nothing for ~2 months
    return latest


def backfill(st, fetch_day, parse, keep, weeks=GRID, pause=0.0, save_cb=None, sleep=None):
    """Fill historical Friday values for schemes already in `st`. fetch_day(date)->text. Resumable via st.done."""
    import time
    end = last_friday(dt.date.fromisoformat(st.latest))
    fridays = [end - dt.timedelta(weeks=k) for k in range(weeks - 1, -1, -1)]
    st.ensure_grid(fridays[0], end)
    index = {d: i for i, d in enumerate(st.dates)}
    fetched = 0
    for F in fridays:
        if iso(F) in st.done:
            continue
        for back in range(5):                                     # holiday Friday -> use the previous trading day
            d = F - dt.timedelta(days=back)
            try:
                text = fetch_day(d)
            except Exception as e:                                # network failure: leave undone, try again next run
                print(f"  {d}: fetch failed ({e})")
                break
            recs = [r for r in parse(text) if r["date"] == d and keep(r)]
            if recs:
                i = index[iso(F)]
                for r in recs:
                    f = st.funds.get(str(r["code"]))
                    if f is not None:
                        f["v"][i] = r["nav"]
                st.done[iso(F)] = iso(d)
                break
        fetched += 1
        if save_cb and fetched % 25 == 0:
            save_cb()
        if pause:
            (sleep or time.sleep)(pause)
    if save_cb:
        save_cb()


def points(f, dates):
    """Sorted unique (ordinal, nav) observations for one fund."""
    obs = {dt.date.fromisoformat(d).toordinal(): v for d, v in zip(dates, f["v"]) if v is not None}
    obs.update({dt.date.fromisoformat(d).toordinal(): v for d, v in f["r"].items()})
    xs = sorted(obs)
    return xs, [obs[x] for x in xs]


def anchored_series(f, dates, anchor, L=GRID, max_stale=10):
    """NAV on a weekly grid ending at `anchor` (linear interpolation between observations).
    Returns (array of length L, nan before the fund's first observation) or None if the fund is stale / empty."""
    xs, ys = points(f, dates)
    if not xs or xs[-1] < anchor.toordinal() - max_stale:
        return None
    g = anchor.toordinal() - 7 * np.arange(L - 1, -1, -1)
    return np.interp(g, xs, ys, left=np.nan, right=ys[-1]), (xs, ys)


def nav_on(xs, ys, d):
    """Interpolated NAV on date d, nan if before the first observation."""
    return float(np.interp(d.toordinal(), xs, ys, left=np.nan, right=ys[-1]))
