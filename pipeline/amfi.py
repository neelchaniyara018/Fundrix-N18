"""Parsing and fetching of AMFI NAV text files.

NAVAll.txt (daily, official):  https://www.amfiindia.com/spages/NAVAll.txt
Layout: a header row, then blocks of
    Open Ended Schemes(Equity Scheme - Large Cap Fund)      <- section
    Some Mutual Fund                                         <- fund house
    119551;INF...;INF...;Scheme name - Direct Plan - Growth;123.4567;03-Nov-2026
The parser is driven by the header row (column names), so the NAV *history* report - which has a different column order - parses too.
NOTE: written from the documented/observed layout; it could not be run against the live files in the build sandbox.
"""
import datetime as dt
import re
import time
import urllib.request

NAV_ALL_URL = "https://www.amfiindia.com/spages/NAVAll.txt"
HISTORY_URL = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx?frmdt={d}&todt={d}"   # one day at a time; unverified
UA = "Mozilla/5.0 (compatible; fundrix-n18/1.0)"
SECTION_RE = re.compile(r"^(Open|Close|Interval)\s*Ended\s*Schemes?\s*\(\s*(.*?)\s*\)\s*$", re.I)
NOT_GROWTH_RE = re.compile(r"idcw|dividend|payout|reinvest|bonus", re.I)
CATEGORY_MAP = {"Equity": "Equity", "Debt": "Debt", "Hybrid": "Hybrid", "Commodity": "Commodity"}
SUB_RENAME = {"ELSS": "ELSS (Tax Saving)"}
_STOP = re.compile(r"^(direct|regular|growth|idcw|dividend|bonus|payout|reinvest|weekly|daily|monthly|quarterly|half|annual|option|plan)\b", re.I)


def classify_section(sec):
    head, _, sub = sec.partition(" - ")
    cat = CATEGORY_MAP.get(head.replace(" Scheme", "").strip(), "Other")
    sub = sub.strip() or "Other"
    return cat, SUB_RENAME.get(sub, sub)


def parse_name(name):
    """-> (base_name, plan, option). plan is 'Growth' unless the name says IDCW/dividend/payout/bonus."""
    plan = "IDCW" if NOT_GROWTH_RE.search(name) else "Growth"
    option = "Direct" if re.search(r"\bdirect\b", name, re.I) else "Regular"
    keep = []
    for part in re.split(r"\s+-\s+", name.strip()):
        if _STOP.match(part.strip()):
            break
        keep.append(part.strip())
    return (" - ".join(keep) or name.strip()), plan, option


def _date(s):
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            pass
    return None


def parse(text):
    """-> list of dicts: code, name, nav, date, amc, open_ended, category, sub."""
    out, idx, amc, sec = [], dict(code=0, name=3, nav=4, date=5), None, None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if ";" not in line:
            m = SECTION_RE.match(line)
            if m:
                sec = (m.group(1).lower() == "open", classify_section(m.group(2)))
            else:
                amc = line
            continue
        parts = [p.strip() for p in line.split(";")]
        low = [p.lower() for p in parts]
        if low[0].startswith("scheme code"):
            idx = dict(code=0, name=next((i for i, h in enumerate(low) if "scheme name" in h), 3),
                       nav=next((i for i, h in enumerate(low) if "net asset value" in h), 4),
                       date=next((i for i, h in enumerate(low) if h == "date"), len(low) - 1))
            continue
        if len(parts) <= max(idx.values()) or not parts[idx["code"]].isdigit():
            continue
        try:
            nav = float(parts[idx["nav"]])
        except ValueError:
            continue                                   # "N.A." and friends
        d = _date(parts[idx["date"]])
        if d is None or not nav > 0 or sec is None:
            continue
        out.append(dict(code=int(parts[idx["code"]]), name=parts[idx["name"]], nav=nav, date=d, amc=amc or "Unknown",
                        open_ended=sec[0], category=sec[1][0], sub=sec[1][1]))
    return out


def fetch_text(url, timeout=90, retries=3):
    last = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:                          # network / HTTP errors: retry with back-off
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"could not download {url}: {last}")


def fetch_day(d):
    """Full NAV report for one calendar day (may be empty on holidays)."""
    return fetch_text(HISTORY_URL.format(d=d.strftime("%d-%b-%Y")))
