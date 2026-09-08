"""Ratios pipeline (run by GitHub Actions weekly on Saturdays, or locally): pulls a
small set of macro valuation figures — Nifty 50 / Smallcap 100 / Midcap 100 / S&P
500 / Gold / Silver — completely independent of the Minervini scorecard pipelines.

Sources, chosen after checking each one actually returns real data:
- Nifty 50 / Smallcap 100 / Midcap 100 price + P/E: NSE's own live index API
  (nseindia.com/api/allIndices) — the only source found that publishes index P/E
  at all; Yahoo Finance has price but never P/E for pure indices. Needs a
  homepage-primed session (NSE blocks a bare API request with no prior cookie).
- S&P 500 price: Yahoo Finance (^GSPC). P/E: no direct S&P 500 P/E from Yahoo
  either, so SPY (the S&P 500 ETF) is used as a P/E proxy — SPY tracks the index
  closely enough that its trailing P/E is a reasonable stand-in.
- Gold / Silver: Yahoo Finance (GC=F / SI=F) quotes per troy ounce (31.1034768g),
  the international bullion convention — converted here to the units an Indian
  retail buyer actually means (gold per 10g, silver per kg), then to INR via
  live USD/INR (INR=X).

Weekly price history (docs/data/ratios_pricehist.json): powers the "click price to
see a graph" chart. Nifty 50 / S&P 500 / Gold / Silver get a real one-time 1-year
weekly backfill from Yahoo Finance on first run (see fetch_weekly_backfill below);
every run after that just appends/overwrites this week's point. Nifty Smallcap 100
and Midcap 100 have NO free historical source via automated fetch — Yahoo carries
no matching index ticker for either, and NSE's own historical-index API (which does
have both) blocks requests from outside India with a 503 bot-mitigation page even
with a fully primed session (checked directly) — those two got a one-off manual CSV
backfill instead (see docs/data/ratios_pricehist.json's git history).

Monthly P/E & gold/silver-ratio history (docs/data/ratios_pe_hist.json): P/E moves
slowly enough that a weekly chart is noise, so this bucks by CALENDAR MONTH instead
of by week — each week's run just overwrites the current month's point with the
latest reading, so by month-end it holds the last observed value for that month,
and a new point starts the next month. Same idea as the price history, coarser
grain. No baseline/"historical average" concept anymore — every point is a real
dated observation.

Weekly "By Nifty" history (docs/data/ratios_bynifty_hist.json): price_inr / nifty
price_inr, one point per week — same cadence as the price history, since it's
just a ratio of numbers price history already has. Nifty 50 itself is skipped
(always exactly 1.0 by definition).

Usage:  python backend/pipeline_ratios.py
"""

import os
import sys
import datetime

import requests

sys.path.insert(0, os.path.dirname(__file__))
from pipeline import load_json, save_json, DATA_DIR

RATIOS_PATH = os.path.join(DATA_DIR, "ratios.json")
PRICEHIST_PATH = os.path.join(DATA_DIR, "ratios_pricehist.json")
PRICEHIST_MAX_POINTS = 55  # a little over a year of weekly points
PE_HIST_PATH = os.path.join(DATA_DIR, "ratios_pe_hist.json")
PE_HIST_MAX_POINTS = 420  # 35 years of monthly points — Nifty 50's P/E history goes back to 2000
BYNIFTY_HIST_PATH = os.path.join(DATA_DIR, "ratios_bynifty_hist.json")
BYNIFTY_HIST_MAX_POINTS = 55  # weekly, same window as price — it's just price_inr / nifty_price_inr

GRAMS_PER_TROY_OZ = 31.1034768

NSE_INDEX_NAMES = {
    "nifty50": "NIFTY 50",
    "smallcap100": "NIFTY SMALLCAP 100",
    "midcap100": "NIFTY MIDCAP 100",
}

# Yahoo tickers for the 4 assets that DO have a clean weekly series there —
# used only for the one-time backfill, never for the live weekly figures
# (those keep coming from fetch_nse_indices/fetch_yahoo_values as before).
BACKFILL_TICKERS = ["^NSEI", "^GSPC", "GC=F", "SI=F", "INR=X"]


def fetch_nse_indices():
    """Returns {key: {"price": float, "pe": float}} for the 3 NSE indices above.
    Needs a homepage hit first to pick up NSE's anti-bot cookies — a bare request
    to the API alone gets rejected."""
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "application/json",
    })
    sess.get("https://www.nseindia.com/", timeout=15)
    resp = sess.get("https://www.nseindia.com/api/allIndices", timeout=15)
    resp.raise_for_status()
    rows = {r.get("index"): r for r in resp.json().get("data", [])}
    out = {}
    for key, nse_name in NSE_INDEX_NAMES.items():
        row = rows.get(nse_name)
        if not row:
            raise RuntimeError("NSE allIndices response missing %r" % nse_name)
        out[key] = {"price": float(row["last"]), "pe": float(row["pe"])}
    return out


def fetch_yahoo_values():
    import technicals as T
    hist = T.download_history(["^GSPC", "SPY", "GC=F", "SI=F", "INR=X"], years=1)
    import yfinance as yf
    spy_pe = yf.Ticker("SPY").info.get("trailingPE")
    return {
        "sp500_price_usd": float(hist["^GSPC"]["Close"].iloc[-1]),
        "sp500_pe": float(spy_pe) if spy_pe else None,
        "gold_price_usd": float(hist["GC=F"]["Close"].iloc[-1]),
        "silver_price_usd": float(hist["SI=F"]["Close"].iloc[-1]),
        "usd_inr": float(hist["INR=X"]["Close"].iloc[-1]),
        # The actual trading day this data is from — the pipeline runs Saturday
        # morning, but this will usually be Friday's date, since markets were
        # closed Saturday and no new bar exists for it. Used as the stored/
        # displayed date instead of today() (the run date), which would
        # mislabel every snapshot.
        "as_of": str(hist["^GSPC"].index[-1].date()),
    }


def fetch_weekly_backfill():
    """One-time full 1-year weekly backfill for nifty50/sp500/gold/silver, all
    converted to the same units/currency the live rows use (see module docstring
    for why smallcap100/midcap100 aren't included here)."""
    import yfinance as yf
    import pandas as pd

    data = yf.download(BACKFILL_TICKERS, period="1y", interval="1wk",
                        group_by="ticker", auto_adjust=True, progress=False, threads=True)
    out = {"nifty50": [], "sp500": [], "gold": [], "silver": []}
    idx = data["^NSEI"].index
    for ts in idx:
        try:
            nifty = float(data["^NSEI"]["Close"].loc[ts])
            spx = float(data["^GSPC"]["Close"].loc[ts])
            gold_oz = float(data["GC=F"]["Close"].loc[ts])
            silver_oz = float(data["SI=F"]["Close"].loc[ts])
            fx = float(data["INR=X"]["Close"].loc[ts])
        except (KeyError, ValueError):
            continue
        if any(pd.isna(v) for v in (nifty, spx, gold_oz, silver_oz, fx)):
            continue
        date = str(ts.date())
        gold_10g_usd = gold_oz * 10 / GRAMS_PER_TROY_OZ
        silver_kg_usd = silver_oz * 1000 / GRAMS_PER_TROY_OZ
        out["nifty50"].append({"date": date, "price_inr": nifty, "price_usd": nifty / fx})
        out["sp500"].append({"date": date, "price_inr": spx * fx, "price_usd": spx})
        out["gold"].append({"date": date, "price_inr": gold_10g_usd * fx, "price_usd": gold_10g_usd})
        out["silver"].append({"date": date, "price_inr": silver_kg_usd * fx, "price_usd": silver_kg_usd})
    return out


def upsert_price_point(pricehist, key, date, price_inr, price_usd):
    series = pricehist.setdefault(key, [])
    for i, p in enumerate(series):
        if p.get("date") == date:
            series[i] = {"date": date, "price_inr": price_inr, "price_usd": price_usd}
            break
    else:
        series.append({"date": date, "price_inr": price_inr, "price_usd": price_usd})
    pricehist[key] = series[-PRICEHIST_MAX_POINTS:]


def upsert_monthly_point(pehist, key, date, value):
    """Bucketed by calendar month, not by exact date — the point for the current
    month gets overwritten every run with the latest reading, so by month-end it
    holds the last observed value; a new month starts a new point. `value` of
    None is a no-op (e.g. a run where SPY's trailingPE didn't come through)."""
    if value is None:
        return
    month = date[:7]  # "YYYY-MM"
    series = pehist.setdefault(key, [])
    for i, p in enumerate(series):
        if (p.get("date") or "")[:7] == month:
            series[i] = {"date": date, "value": value}
            break
    else:
        series.append({"date": date, "value": value})
    pehist[key] = series[-PE_HIST_MAX_POINTS:]


def upsert_weekly_value_point(hist, key, date, value):
    """Same weekly by-exact-date upsert as upsert_price_point, but for a single
    scalar value (here: By Nifty, i.e. price_inr / nifty_price_inr)."""
    series = hist.setdefault(key, [])
    for i, p in enumerate(series):
        if p.get("date") == date:
            series[i] = {"date": date, "value": value}
            break
    else:
        series.append({"date": date, "value": value})
    hist[key] = series[-BYNIFTY_HIST_MAX_POINTS:]


def main():
    nse = fetch_nse_indices()
    yahoo = fetch_yahoo_values()
    fx = yahoo["usd_inr"]
    as_of = yahoo["as_of"]

    nifty_price = nse["nifty50"]["price"]
    sp500_price_inr = yahoo["sp500_price_usd"] * fx
    # Yahoo Finance quotes gold/silver (GC=F/SI=F) per troy ounce (31.1034768g) — the
    # international bullion convention, not what a retail Indian buyer means by "gold
    # price" (per 10g) or "silver price" (per kg). Converting at the source here so
    # every downstream value is in the same real unit as the displayed price, not
    # just the display itself.
    gold_price_usd_10g = yahoo["gold_price_usd"] * 10 / GRAMS_PER_TROY_OZ
    silver_price_usd_kg = yahoo["silver_price_usd"] * 1000 / GRAMS_PER_TROY_OZ
    gold_price_inr = gold_price_usd_10g * fx
    silver_price_inr = silver_price_usd_kg * fx
    # Ratio is unit-invariant as long as both sides use the same basis — computed
    # from the raw per-troy-oz USD quotes, unaffected by the retail-unit conversion
    # above.
    gold_silver_ratio = (yahoo["gold_price_usd"] / yahoo["silver_price_usd"]
                         if yahoo["silver_price_usd"] else None)

    pricehist = load_json(PRICEHIST_PATH, {})
    if "nifty50" not in pricehist:
        pricehist.update(fetch_weekly_backfill())

    rows = [
        {
            "key": "nifty50", "name": "Nifty 50",
            "price_inr": nifty_price, "price_usd": nifty_price / fx,
            "by_nifty": 1.0,
            "pe": nse["nifty50"]["pe"],
        },
        {
            "key": "smallcap100", "name": "Smallcap 100",
            "price_inr": nse["smallcap100"]["price"], "price_usd": nse["smallcap100"]["price"] / fx,
            "by_nifty": nse["smallcap100"]["price"] / nifty_price,
            "pe": nse["smallcap100"]["pe"],
        },
        {
            "key": "midcap100", "name": "Midcap 100",
            "price_inr": nse["midcap100"]["price"], "price_usd": nse["midcap100"]["price"] / fx,
            "by_nifty": nse["midcap100"]["price"] / nifty_price,
            "pe": nse["midcap100"]["pe"],
        },
        {
            "key": "sp500", "name": "S&P 500",
            "price_inr": sp500_price_inr, "price_usd": yahoo["sp500_price_usd"],
            "by_nifty": sp500_price_inr / nifty_price,
            "pe": yahoo["sp500_pe"],
        },
        {
            "key": "gold", "name": "Gold (10g)",
            "price_inr": gold_price_inr, "price_usd": gold_price_usd_10g,
            "by_nifty": gold_price_inr / nifty_price,
            "gold_silver_ratio": gold_silver_ratio,
        },
        {
            "key": "silver", "name": "Silver (kg)",
            "price_inr": silver_price_inr, "price_usd": silver_price_usd_kg,
            "by_nifty": silver_price_inr / nifty_price,
            "gold_silver_ratio": gold_silver_ratio,
        },
    ]

    for r in rows:
        upsert_price_point(pricehist, r["key"], as_of, r["price_inr"], r["price_usd"])

    pehist = load_json(PE_HIST_PATH, {})
    upsert_monthly_point(pehist, "nifty50", as_of, nse["nifty50"]["pe"])
    upsert_monthly_point(pehist, "smallcap100", as_of, nse["smallcap100"]["pe"])
    upsert_monthly_point(pehist, "midcap100", as_of, nse["midcap100"]["pe"])
    upsert_monthly_point(pehist, "sp500", as_of, yahoo["sp500_pe"])
    upsert_monthly_point(pehist, "gsratio", as_of, gold_silver_ratio)

    bynifty_hist = load_json(BYNIFTY_HIST_PATH, {})
    for r in rows:
        if r["key"] == "nifty50":
            continue  # always exactly 1.0 by definition — not worth a chart
        upsert_weekly_value_point(bynifty_hist, r["key"], as_of, r["by_nifty"])

    save_json(PRICEHIST_PATH, pricehist)
    save_json(PE_HIST_PATH, pehist)
    save_json(BYNIFTY_HIST_PATH, bynifty_hist)
    save_json(RATIOS_PATH, {
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "as_of": as_of,
        "usd_inr": fx,
        "rows": rows,
    })
    print("done. ratios.json written, fx=%.2f" % fx)
    for r in rows:
        print(" %-14s price=%.2f  by_nifty=%.3f  pe=%s" % (
            r["name"], r["price_inr"], r["by_nifty"], r.get("pe") or r.get("gold_silver_ratio")))


if __name__ == "__main__":
    main()
