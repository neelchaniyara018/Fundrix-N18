"""Starter screens shared by the API server (SQLite) and the static build (presets.json)."""
PRESETS = [
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
