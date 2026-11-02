"""Metric registry: single source of truth for filters, columns, labels and 'better' direction."""
G_INFO, G_RET, G_RISK, G_PORT, G_RAT = "Scheme Info", "Returns", "Risk", "Portfolio Composition", "Ratios"
GROUPS = [G_INFO, G_RET, G_RISK, G_PORT, G_RAT]


def N(key, label, group, unit="", dec=2, better=None, desc=""):
    return dict(key=key, label=label, group=group, unit=unit, dec=dec, better=better, desc=desc, type="num")


def C(key, label, group, desc=""):
    return dict(key=key, label=label, group=group, type="cat", desc=desc)


METRICS = [
    # ---- Scheme info
    C("category", "Category", G_INFO), C("sub_category", "Sub Category", G_INFO),
    C("plan", "Plan", G_INFO, "Growth or IDCW (dividend)"), C("option", "Direct / Regular", G_INFO),
    C("amc", "Fund House", G_INFO), C("benchmark", "Benchmark", G_INFO), C("riskometer", "Riskometer", G_INFO),
    N("aum", "AUM", G_INFO, "Cr", 2, None, "Assets under management in crores"),
    N("nav", "NAV", G_INFO, "₹", 2),
    N("expense_ratio", "Expense Ratio", G_INFO, "%", 2, "low", "Annual fee charged by the fund"),
    N("min_lumpsum", "Minimum Lumpsum", G_INFO, "₹", 0, "low"),
    N("min_sip", "Minimum SIP", G_INFO, "₹", 0, "low"),
    N("exit_load", "Exit Load", G_INFO, "%", 2, "low", "Fee on early redemption"),
    N("lock_in", "Lock-in", G_INFO, "Years", 0, "low"),
    N("age", "Fund Age", G_INFO, "Years", 1, "high"),
    # ---- Returns
    N("ret_1m", "Absolute Ret. - 1M", G_RET, "%", 2, "high"), N("ret_3m", "Absolute Ret. - 3M", G_RET, "%", 2, "high"),
    N("ret_6m", "Absolute Ret. - 6M", G_RET, "%", 2, "high"), N("ret_ytd", "Absolute Ret. - YTD", G_RET, "%", 2, "high"),
    N("ret_1y", "Absolute Ret. - 1Y", G_RET, "%", 2, "high"),
    N("cagr_3y", "CAGR 3Y", G_RET, "%", 2, "high"), N("cagr_5y", "CAGR 5Y", G_RET, "%", 2, "high"),
    N("cagr_10y", "CAGR 10Y", G_RET, "%", 2, "high"),
    N("roll_3y_avg", "Rolling Ret. 3Y - Average", G_RET, "%", 2, "high", "Average of all 3-year rolling annualised returns"),
    N("roll_3y_min", "Rolling Ret. 3Y - Worst", G_RET, "%", 2, "high"),
    # ---- Risk
    N("std_dev", "Standard Deviation", G_RISK, "%", 2, "low", "Annualised volatility (3Y)"),
    N("beta", "Beta", G_RISK, "", 2, None, "Sensitivity to the benchmark"),
    N("alpha", "Alpha", G_RISK, "%", 2, "high", "Annual excess return over benchmark-implied return"),
    N("max_drawdown", "Max Drawdown", G_RISK, "%", 2, "high", "Worst peak-to-trough fall (5Y)"),
    N("tracking_error", "Tracking Error", G_RISK, "%", 2, "low"),
    N("up_capture", "Upside Capture", G_RISK, "%", 1, "high"), N("down_capture", "Downside Capture", G_RISK, "%", 1, "low"),
    # ---- Portfolio composition
    N("num_holdings", "No. of Holdings", G_PORT, "", 0), N("top10_pct", "Top 10 Holdings", G_PORT, "%", 1, "low"),
    N("equity_pct", "Equity", G_PORT, "%", 1), N("debt_pct", "Debt", G_PORT, "%", 1),
    N("cash_pct", "Cash & Equivalents", G_PORT, "%", 1), N("other_pct", "Gold / Others", G_PORT, "%", 1),
    N("large_pct", "Large Cap (of equity)", G_PORT, "%", 1), N("mid_pct", "Mid Cap (of equity)", G_PORT, "%", 1),
    N("small_pct", "Small Cap (of equity)", G_PORT, "%", 1),
    N("pe", "PE Ratio", G_PORT, "", 2), N("pb", "PB Ratio", G_PORT, "", 2),
    N("ytm", "Yield to Maturity", G_PORT, "%", 2, "high"), N("avg_maturity", "Avg. Maturity", G_PORT, "Years", 2),
    N("mod_duration", "Modified Duration", G_PORT, "Years", 2),
    # ---- Ratios
    N("sharpe", "Sharpe Ratio", G_RAT, "", 2, "high", "(Return - risk-free) / standard deviation"),
    N("sortino", "Sortino Ratio", G_RAT, "", 2, "high", "(Return - risk-free) / downside deviation"),
    N("treynor", "Treynor Ratio", G_RAT, "%", 2, "high"), N("info_ratio", "Information Ratio", G_RAT, "", 2, "high"),
    N("calmar", "Calmar Ratio", G_RAT, "", 2, "high"),
]
BY_KEY = {m["key"]: m for m in METRICS}
NUM_KEYS = [m["key"] for m in METRICS if m["type"] == "num"]
CAT_KEYS = [m["key"] for m in METRICS if m["type"] == "cat"]
