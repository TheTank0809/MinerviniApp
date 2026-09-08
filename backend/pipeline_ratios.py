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

Baseline P/E (docs/data/ratios_baseline.json): a ONE-TIME reference value, not a
rolling average — captured on whichever run first sees a given (key, metric) and
then left untouched forever after, so it stays a fixed comparison point. Every
other field (price, P/E, By Nifty) still refreshes every run.

Weekly price history (docs/data/ratios_pricehist.json): powers the "click price to
see a graph" chart. Nifty 50 / S&P 500 / Gold / Silver get a real one-time 1-year
weekly backfill from Yahoo Finance on first run (see fetch_weekly_backfill below);
every run after that just appends/overwrites this week's point. Nifty Smallcap 100
and Midcap 100 have NO free historical source — Yahoo carries no matching index
ticker for either, and NSE's own historical-index API (which does have both) blocks
requests from outside India with a 503 bot-mitigation page even with a fully primed
session (checked directly). So those two series simply start now and build up one
point per week going forward, same as everything else did before this pipeline
existed.

Usage:  python backend/pipeline_ratios.py
"""

import os
import sys
import datetime

import requests

sys.path.insert(0, os.path.dirname(__file__))
from pipeline import load_json, save_json, DATA_DIR

RATIOS_PATH = os.path.join(DATA_DIR, "ratios.json")
BASELINE_PATH = os.path.join(DATA_DIR, "ratios_baseline.json")
PRICEHIST_PATH = os.path.join(DATA_DIR, "ratios_pricehist.json")
PRICEHIST_MAX_POINTS = 55  # a little over a year of weekly points

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


def get_or_set_baseline(baseline, key, metric, value, as_of):
    """A one-time reference value: captured the first run that ever sees this
    (key, metric) and left untouched on every run after — a fixed comparison
    point, not a rolling average."""
    entry = baseline.setdefault(key, {})
    if metric not in entry or entry[metric].get("value") is None:
        entry[metric] = {"value": value, "captured_on": as_of}
    return entry[metric]["value"]


def upsert_price_point(pricehist, key, date, price_inr, price_usd):
    series = pricehist.setdefault(key, [])
    for i, p in enumerate(series):
        if p.get("date") == date:
            series[i] = {"date": date, "price_inr": price_inr, "price_usd": price_usd}
            break
    else:
        series.append({"date": date, "price_inr": price_inr, "price_usd": price_usd})
    pricehist[key] = series[-PRICEHIST_MAX_POINTS:]


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

    baseline = load_json(BASELINE_PATH, {})
    pricehist = load_json(PRICEHIST_PATH, {})
    if "nifty50" not in pricehist:
        pricehist.update(fetch_weekly_backfill())

    rows = [
        {
            "key": "nifty50", "name": "Nifty 50",
            "price_inr": nifty_price, "price_usd": nifty_price / fx,
            "by_nifty": 1.0,
            "pe": nse["nifty50"]["pe"],
            "baseline_pe": get_or_set_baseline(baseline, "nifty50", "pe", nse["nifty50"]["pe"], as_of),
        },
        {
            "key": "smallcap100", "name": "Smallcap 100",
            "price_inr": nse["smallcap100"]["price"], "price_usd": nse["smallcap100"]["price"] / fx,
            "by_nifty": nse["smallcap100"]["price"] / nifty_price,
            "pe": nse["smallcap100"]["pe"],
            "baseline_pe": get_or_set_baseline(baseline, "smallcap100", "pe", nse["smallcap100"]["pe"], as_of),
        },
        {
            "key": "midcap100", "name": "Midcap 100",
            "price_inr": nse["midcap100"]["price"], "price_usd": nse["midcap100"]["price"] / fx,
            "by_nifty": nse["midcap100"]["price"] / nifty_price,
            "pe": nse["midcap100"]["pe"],
            "baseline_pe": get_or_set_baseline(baseline, "midcap100", "pe", nse["midcap100"]["pe"], as_of),
        },
        {
            "key": "sp500", "name": "S&P 500",
            "price_inr": sp500_price_inr, "price_usd": yahoo["sp500_price_usd"],
            "by_nifty": sp500_price_inr / nifty_price,
            "pe": yahoo["sp500_pe"],
            "baseline_pe": get_or_set_baseline(baseline, "sp500", "pe", yahoo["sp500_pe"], as_of),
        },
        {
            "key": "gold", "name": "Gold (10g)",
            "price_inr": gold_price_inr, "price_usd": gold_price_usd_10g,
            "by_nifty": gold_price_inr / nifty_price,
            "gold_silver_ratio": gold_silver_ratio,
            "baseline_price_inr": get_or_set_baseline(baseline, "gold", "price_inr", gold_price_inr, as_of),
        },
        {
            "key": "silver", "name": "Silver (kg)",
            "price_inr": silver_price_inr, "price_usd": silver_price_usd_kg,
            "by_nifty": silver_price_inr / nifty_price,
            "gold_silver_ratio": gold_silver_ratio,
            "baseline_price_inr": get_or_set_baseline(baseline, "silver", "price_inr", silver_price_inr, as_of),
        },
    ]

    for r in rows:
        upsert_price_point(pricehist, r["key"], as_of, r["price_inr"], r["price_usd"])

    save_json(BASELINE_PATH, baseline)
    save_json(PRICEHIST_PATH, pricehist)
    save_json(RATIOS_PATH, {
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "as_of": as_of,
        "usd_inr": fx,
        "rows": rows,
    })
    print("done. ratios.json written, fx=%.2f" % fx)
    for r in rows:
        print(" %-14s price=%.2f  by_nifty=%.3f  pe=%s  baseline=%s" % (
            r["name"], r["price_inr"], r["by_nifty"],
            r.get("pe"), r.get("baseline_pe") or r.get("baseline_price_inr")))


if __name__ == "__main__":
    main()
