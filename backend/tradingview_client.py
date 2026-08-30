"""TradingView scanner client for the Global universe.

Unlike screener.in, TradingView's REST scanner endpoint (scanner.tradingview.com)
needs no authentication at all for cross-market ("Entire world") queries — the
sign-in wall you see on tradingview.com's own screener page is a website-UI
restriction only, verified directly against the live endpoint. Field names below
were reverse-engineered by testing against real data (no official field catalog is
published), matched against TradingView's own UI output where possible — see
config.yaml's `global` universe block for the query criteria this builds from.
"""

import requests

SCAN_URL = "https://scanner.tradingview.com/global/scan"

# Raw scanner columns this pulls per company, in a fixed order — index positions
# are relied on by fundamentals_tradingview.py, so don't reorder without updating
# both places.
COLUMNS = [
    "name", "description", "country", "exchange", "sector", "currency",
    "close", "market_cap_basic",
    "earnings_per_share_diluted_yoy_growth_ttm",
    "earnings_per_share_diluted_fq_h",   # quarterly EPS history, most-recent-first
    "total_revenue_fq_h",                # quarterly revenue history, most-recent-first
    "total_revenue_fy_h",                # annual revenue history, most-recent-first
    "net_income_fy_h",                   # annual net income history, most-recent-first
    "free_cash_flow_fy_h",               # annual FCF history, most-recent-first
    "debt_to_equity_fy",
    "return_on_equity",
    "return_on_invested_capital",
    "net_margin_ttm", "gross_margin_ttm", "operating_margin_ttm",
    "price_earnings_ttm",
    "float_shares_percent_current",
    "number_of_employees",
]


class TradingViewError(Exception):
    pass


def fetch_universe(exclude_countries, min_market_cap, min_eps_growth_pct, max_debt_to_equity,
                    limit=1000):
    """Returns a list of raw scanner rows (dict of column -> value) matching the
    configured growth-screen criteria, primary listings only, excluding the given
    countries. `exclude_countries` filtering happens in the query itself (native
    TradingView filter support — not a post-fetch step), so it can never be
    accidentally bypassed by a code change elsewhere."""
    filters = [
        {"left": "is_primary", "operation": "equal", "right": True},
        {"left": "market_cap_basic", "operation": "greater", "right": min_market_cap},
        {"left": "earnings_per_share_diluted_yoy_growth_ttm", "operation": "greater",
         "right": min_eps_growth_pct},
        {"left": "debt_to_equity_fy", "operation": "less", "right": max_debt_to_equity},
    ]
    for country in exclude_countries:
        filters.append({"left": "country", "operation": "nequal", "right": country})

    body = {
        "columns": COLUMNS,
        "filter": filters,
        "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
        "range": [0, limit],
    }
    resp = requests.post(SCAN_URL, json=body, timeout=30)
    if resp.status_code != 200:
        raise TradingViewError("TradingView scanner returned %s: %s" % (resp.status_code, resp.text[:300]))
    data = resp.json()
    rows = []
    for item in data.get("data", []):
        symbol_full = item.get("s", "")  # e.g. "NASDAQ:NVDA"
        values = dict(zip(COLUMNS, item.get("d", [])))
        values["_symbol"] = symbol_full
        rows.append(values)
    return rows
