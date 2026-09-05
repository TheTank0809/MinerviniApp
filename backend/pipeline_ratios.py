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

"Historical average" has no ready-made source (no free API publishes a running
historical average P/E for these indices) — instead of guessing at one or hiding
it, this builds a real one over time: every run appends this week's snapshot to
ratios_history.json, and the historical average shown is the mean of every
snapshot collected so far. It starts as just this week's value and gets more
meaningful every week, the same way docs/data/history/<ticker>.json already
works for individual stock scores — not a fabricated long-run number.

Usage:  python backend/pipeline_ratios.py
"""

import os
import sys
import datetime

import requests

sys.path.insert(0, os.path.dirname(__file__))
import technicals as T
from pipeline import load_json, save_json, DATA_DIR

RATIOS_PATH = os.path.join(DATA_DIR, "ratios.json")
HISTORY_PATH = os.path.join(DATA_DIR, "ratios_history.json")
HISTORY_MAX_POINTS = 260  # ~5 years of weekly snapshots

NSE_INDEX_NAMES = {
    "nifty50": "NIFTY 50",
    "smallcap100": "NIFTY SMALLCAP 100",
    "midcap100": "NIFTY MIDCAP 100",
}


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
        # morning, but this will be Friday's date, since markets were closed
        # Saturday and no new bar exists for it. Used as the stored/displayed
        # date instead of today() (the run date), which would mislabel every
        # snapshot by a day.
        "as_of": str(hist["^GSPC"].index[-1].date()),
    }


def update_history_and_get_avg(history, key, metric, value, as_of):
    """Appends (or overwrites same-day) a snapshot for (key, metric) and returns
    the mean of every snapshot collected so far, including this one. `as_of` is
    the trading date the value actually reflects — the pipeline runs Saturday
    morning but the prices are Friday's close, so this must NOT default to
    today()'s run date or every snapshot would be mislabeled by a day."""
    series = history.setdefault(key, {}).setdefault(metric, [])
    date = as_of
    for i, p in enumerate(series):
        if p.get("date") == date:
            series[i] = {"date": date, "value": value}
            break
    else:
        series.append({"date": date, "value": value})
    history[key][metric] = series[-HISTORY_MAX_POINTS:]
    vals = [p["value"] for p in history[key][metric] if p.get("value") is not None]
    return sum(vals) / len(vals) if vals else None


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
    # every downstream value (By Nifty, historical average) is in the same real unit
    # as the displayed price, not just the display itself.
    GRAMS_PER_TROY_OZ = 31.1034768
    gold_price_usd_10g = yahoo["gold_price_usd"] * 10 / GRAMS_PER_TROY_OZ
    silver_price_usd_kg = yahoo["silver_price_usd"] * 1000 / GRAMS_PER_TROY_OZ
    gold_price_inr = gold_price_usd_10g * fx
    silver_price_inr = silver_price_usd_kg * fx
    # Ratio is unit-invariant as long as both sides use the same basis — computed
    # from the raw per-troy-oz USD quotes, unaffected by the retail-unit conversion
    # above.
    gold_silver_ratio = (yahoo["gold_price_usd"] / yahoo["silver_price_usd"]
                         if yahoo["silver_price_usd"] else None)

    history = load_json(HISTORY_PATH, {})

    rows = [
        {
            "key": "nifty50", "name": "Nifty 50",
            "price_inr": nifty_price, "price_usd": nifty_price / fx,
            "by_nifty": 1.0,
            "pe": nse["nifty50"]["pe"],
            "hist_avg_pe": update_history_and_get_avg(history, "nifty50", "pe", nse["nifty50"]["pe"], as_of),
        },
        {
            "key": "smallcap100", "name": "Smallcap 100",
            "price_inr": nse["smallcap100"]["price"], "price_usd": nse["smallcap100"]["price"] / fx,
            "by_nifty": nse["smallcap100"]["price"] / nifty_price,
            "pe": nse["smallcap100"]["pe"],
            "hist_avg_pe": update_history_and_get_avg(history, "smallcap100", "pe", nse["smallcap100"]["pe"], as_of),
        },
        {
            "key": "midcap100", "name": "Midcap 100",
            "price_inr": nse["midcap100"]["price"], "price_usd": nse["midcap100"]["price"] / fx,
            "by_nifty": nse["midcap100"]["price"] / nifty_price,
            "pe": nse["midcap100"]["pe"],
            "hist_avg_pe": update_history_and_get_avg(history, "midcap100", "pe", nse["midcap100"]["pe"], as_of),
        },
        {
            "key": "sp500", "name": "S&P 500",
            "price_inr": sp500_price_inr, "price_usd": yahoo["sp500_price_usd"],
            "by_nifty": sp500_price_inr / nifty_price,
            "pe": yahoo["sp500_pe"],
            "hist_avg_pe": update_history_and_get_avg(history, "sp500", "pe", yahoo["sp500_pe"], as_of),
        },
        {
            "key": "gold", "name": "Gold (10g)",
            "price_inr": gold_price_inr, "price_usd": gold_price_usd_10g,
            "by_nifty": gold_price_inr / nifty_price,
            "gold_silver_ratio": gold_silver_ratio,
            "hist_avg_price_inr": update_history_and_get_avg(history, "gold", "price_inr", gold_price_inr, as_of),
        },
        {
            "key": "silver", "name": "Silver (kg)",
            "price_inr": silver_price_inr, "price_usd": silver_price_usd_kg,
            "by_nifty": silver_price_inr / nifty_price,
            "gold_silver_ratio": gold_silver_ratio,
            "hist_avg_price_inr": update_history_and_get_avg(history, "silver", "price_inr", silver_price_inr, as_of),
        },
    ]

    save_json(HISTORY_PATH, history)
    save_json(RATIOS_PATH, {
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "as_of": as_of,
        "usd_inr": fx,
        "rows": rows,
    })
    print("done. ratios.json written, fx=%.2f" % fx)
    for r in rows:
        print(" %-14s price=%.2f  by_nifty=%.3f  pe=%s  hist_avg=%s" % (
            r["name"], r["price_inr"], r["by_nifty"],
            r.get("pe"), r.get("hist_avg_pe") or r.get("hist_avg_price_inr")))


if __name__ == "__main__":
    main()
