"""Compute the technical payload (see PROMPT.md — "Technical payload") from OHLCV.

The app computes numbers; the scorecard engine gates and scores them.
Data source: Yahoo Finance daily bars via yfinance.
"""

import math
import pandas as pd
import numpy as np
import yfinance as yf


def download_history(symbols, years=2):
    """Batch-download daily OHLCV. Returns {symbol: DataFrame} (missing symbols omitted)."""
    if not symbols:
        return {}
    data = yf.download(
        symbols, period="%dy" % years, interval="1d",
        group_by="ticker", auto_adjust=True, progress=False, threads=True)
    out = {}
    if isinstance(data.columns, pd.MultiIndex):
        for sym in symbols:
            if sym in data.columns.get_level_values(0):
                df = data[sym].dropna(how="all")
                if len(df) > 30:
                    out[sym] = df
    else:  # single symbol
        df = data.dropna(how="all")
        if len(df) > 30:
            out[symbols[0]] = df
    return out


def yahoo_symbol_candidates(code, suffixes):
    """screener.in company codes are NSE tickers or BSE numeric codes."""
    if code.isdigit():
        return [code + ".BO"]
    return [code + s for s in suffixes]


# --------------------------------------------------------------------------- helpers

def _sma(close, n):
    if len(close) < n:
        return None
    return float(close.rolling(n).mean().iloc[-1])


def _sma_series(close, n):
    return close.rolling(n).mean()


def _rs_raw(close):
    """Minervini/IBD-style RS raw score:
    2*(3m return) + (6m return) + (9m return) + (12m return); periods in trading days."""
    def ret(days):
        if len(close) <= days:
            return None
        past = close.iloc[-days - 1]
        return (close.iloc[-1] / past - 1.0) if past else None
    r3, r6, r9, r12 = ret(63), ret(126), ret(189), ret(252)
    if r3 is None or r6 is None:
        return None
    r9 = r9 if r9 is not None else r6
    r12 = r12 if r12 is not None else r9
    return 2 * r3 + r6 + r9 + r12


def rs_percentiles(rs_raw_by_symbol):
    """Percentile-rank RS raw scores across the tracked universe (0-99)."""
    items = [(s, v) for s, v in rs_raw_by_symbol.items() if v is not None]
    n = len(items)
    if n == 0:
        return {}
    items.sort(key=lambda kv: kv[1])
    return {s: int(round(rank / max(n - 1, 1) * 99)) for rank, (s, v) in enumerate(items)}


# --------------------------------------------------------------------------- base detection

def _detect_base(df):
    """Heuristic base/VCP detection from daily bars.

    A 'base' = consolidation since the most recent 52-week closing high, at least
    3 weeks long and no deeper than 40%. Contractions = successive pullback waves
    inside the base, measured peak-to-trough.
    """
    close, high, low, vol = df["Close"], df["High"], df["Low"], df["Volume"]
    price = float(close.iloc[-1])
    win = df.tail(252)
    hi_52w = float(win["High"].max())

    # base start = last session that printed the 52w high
    hi_idx = win["High"].idxmax()
    base_df = df.loc[hi_idx:]
    weeks_in_base = len(base_df) / 5.0
    base_low = float(base_df["Low"].min())
    depth_pct = (hi_52w - base_low) / hi_52w * 100.0 if hi_52w else 0.0
    in_base = weeks_in_base >= 3 and depth_pct <= 40.0 and price < hi_52w * 1.005

    base = {
        "in_base": bool(in_base),
        "weeks_in_base": round(weeks_in_base, 1),
        "base_depth_pct": round(depth_pct, 1),
        "base_count_since_stage2": None,
        "contractions": [],
        "final_contraction_volume_vs_50d_avg_pct": None,
        "weekly_close_tightness_pct": None,
        "pivot": round(hi_52w, 2),
        "pct_from_pivot": round((price / hi_52w - 1.0) * 100.0, 2) if hi_52w else None,
    }
    if not in_base:
        return base

    # contractions: walk pullback waves inside the base using 5-day swing points
    b_high = base_df["High"].values
    b_low = base_df["Low"].values
    n = len(base_df)
    contractions = []
    i, cur_peak, trough = 0, b_high[0], b_high[0]
    direction = "down"
    for i in range(1, n):
        if direction == "down":
            trough = min(trough, b_low[i])
            # recovery of >1/3 of the decline ends the contraction
            if cur_peak > 0 and b_high[i] > trough + (cur_peak - trough) * 0.5 and cur_peak > trough:
                d = (cur_peak - trough) / cur_peak * 100.0
                if d >= 1.5:
                    contractions.append({"depth_pct": round(d, 1)})
                direction = "up"
                cur_peak = b_high[i]
        else:
            if b_high[i] >= cur_peak:
                cur_peak = b_high[i]
                trough = b_low[i]
            elif b_low[i] < cur_peak * 0.985:
                direction = "down"
                trough = b_low[i]
    # close any open contraction
    if direction == "down" and cur_peak > trough and cur_peak > 0:
        d = (cur_peak - trough) / cur_peak * 100.0
        if d >= 1.5:
            contractions.append({"depth_pct": round(d, 1)})
    base["contractions"] = contractions[-5:]

    vol_50 = float(vol.rolling(50).mean().iloc[-1]) if len(vol) >= 50 else None
    if vol_50:
        recent_vol = float(base_df["Volume"].tail(10).mean())
        base["final_contraction_volume_vs_50d_avg_pct"] = round(
            (recent_vol / vol_50 - 1.0) * 100.0, 1)

    weekly = close.resample("W-FRI").last().dropna()
    if len(weekly) >= 3:
        last3 = weekly.tail(3)
        base["weekly_close_tightness_pct"] = round(
            (float(last3.max()) - float(last3.min())) / float(last3.max()) * 100.0, 2)

    # pivot refinement: high of the final contraction wave (last 6 weeks) if below base high
    recent_high = float(base_df["High"].tail(30).max())
    if recent_high < hi_52w * 0.99:
        base["pivot"] = round(recent_high, 2)
        base["pct_from_pivot"] = round((price / recent_high - 1.0) * 100.0, 2)
    return base


def _count_bases_since_stage2(df):
    """Approximate base count: consolidations (>=8% pullback, >=15 sessions) followed
    by new highs, counted since price last reclaimed its 200 DMA and held 21 sessions."""
    close = df["Close"]
    if len(close) < 260:
        return None
    sma200 = _sma_series(close, 200)
    above = close > sma200
    start = None
    run = 0
    for i in range(len(close) - 1, -1, -1):
        if bool(above.iloc[i]):
            run += 1
            start = i
        else:
            if run >= 21:
                break
            run = 0
            start = None
    if start is None:
        return None
    seg = close.iloc[start:]
    if len(seg) < 30:
        return 1
    count, peak, trough, in_pullback = 0, float(seg.iloc[0]), None, False
    for v in seg:
        v = float(v)
        if not in_pullback:
            if v > peak:
                peak = v
            elif v < peak * 0.92:
                in_pullback, trough = True, v
        else:
            trough = min(trough, v)
            if v >= peak:  # new high completes the base
                count += 1
                peak, in_pullback = v, False
    return max(count + (1 if in_pullback else 0), 1)


# --------------------------------------------------------------------------- payload

def build_technical_payload(df, rs_percentile=None, rs_percentile_prev=None):
    close, high, low, vol = df["Close"], df["High"], df["Low"], df["Volume"]
    price = float(close.iloc[-1])
    as_of = str(df.index[-1].date())

    d50, d150, d200 = _sma(close, 50), _sma(close, 150), _sma(close, 200)

    sma200 = _sma_series(close, 200).dropna()
    slope_label, rising_months = "unverified", None
    if len(sma200) >= 22:
        chg = float(sma200.iloc[-1] / sma200.iloc[-22] - 1.0)
        slope_label = "rising" if chg > 0.001 else ("falling" if chg < -0.001 else "flat")
        months = 0
        step = 21
        for k in range(1, int(len(sma200) / step)):
            a, b = sma200.iloc[-1 - (k - 1) * step], sma200.iloc[-1 - k * step]
            if float(a) > float(b):
                months += 1
            else:
                break
        rising_months = months

    win = df.tail(252)
    hi52, lo52 = float(win["High"].max()), float(win["Low"].min())

    up_vol = float(vol.tail(50)[close.diff().tail(50) > 0].sum())
    dn_vol = float(vol.tail(50)[close.diff().tail(50) < 0].sum())
    udr = round(up_vol / dn_vol, 2) if dn_vol > 0 else None

    avg_vol_50 = float(vol.rolling(50).mean().iloc[-1]) if len(vol) >= 50 else None
    median_traded_value = None
    if len(df) >= 50:
        traded = (close.tail(50) * vol.tail(50))
        median_traded_value = float(traded.median())  # in listing currency units

    base = _detect_base(df)
    base["base_count_since_stage2"] = _count_bases_since_stage2(df)

    breakout = {"occurred": False, "volume_vs_50d_avg_pct": None}
    if base.get("pivot") and avg_vol_50:
        prev_close = float(close.iloc[-2]) if len(close) > 1 else price
        if prev_close <= base["pivot"] < price:
            breakout = {
                "occurred": True,
                "volume_vs_50d_avg_pct": round((float(vol.iloc[-1]) / avg_vol_50 - 1) * 100, 1),
            }

    return {
        "price": round(price, 2), "as_of": as_of,
        "dma_50": round(d50, 2) if d50 else None,
        "dma_150": round(d150, 2) if d150 else None,
        "dma_200": round(d200, 2) if d200 else None,
        "dma_200_slope_21d": slope_label,
        "dma_200_rising_months": rising_months,
        "dma_50_rising": bool(len(close) >= 55 and
                              _sma(close, 50) > float(_sma_series(close, 50).iloc[-6])),
        "high_52w": round(hi52, 2), "low_52w": round(lo52, 2),
        "pct_above_52w_low": round((price / lo52 - 1) * 100, 1) if lo52 else None,
        "pct_below_52w_high": round((1 - price / hi52) * 100, 1) if hi52 else None,
        "rs_percentile": rs_percentile,
        "rs_percentile_prev_week": rs_percentile_prev,
        "industry_group_rs_quartile": None,   # not derivable for free — unverified
        "avg_volume_50d": int(avg_vol_50) if avg_vol_50 else None,
        "median_daily_traded_value_50d": round(median_traded_value, 0) if median_traded_value else None,
        "up_down_volume_ratio_50d": udr,
        "base": base,
        "breakout_today": breakout,
    }


# --------------------------------------------------------------------------- market regime

def market_regime(bench_df, universe_dfs):
    """Stage 0 — score the general market (see PROMPT.md)."""
    checks = {}
    close, vol = bench_df["Close"], bench_df["Volume"]
    price = float(close.iloc[-1])
    d50, d200 = _sma(close, 50), _sma(close, 200)
    checks["close_above_50dma"] = bool(d50 and price > d50)
    checks["close_above_200dma"] = bool(d200 and price > d200)
    checks["50dma_above_200dma"] = bool(d50 and d200 and d50 > d200)

    # distribution days: down >0.2% on higher volume than prior day, last 25 sessions
    dist = None
    if float(vol.tail(25).sum()) > 0:
        d = 0
        c, v = close.tail(26).values, vol.tail(26).values
        for i in range(1, len(c)):
            if c[i] < c[i - 1] * 0.998 and v[i] > v[i - 1]:
                d += 1
        dist = d
    checks["distribution_days_25"] = dist
    checks["distribution_days_ok"] = bool(dist is not None and dist <= 4)

    above200, newhigh, total = 0, 0, 0
    for df in universe_dfs.values():
        c = df["Close"]
        d200u = _sma(c, 200)
        if d200u is None:
            continue
        total += 1
        if float(c.iloc[-1]) > d200u:
            above200 += 1
        win = df.tail(252)
        if float(win["High"].tail(5).max()) >= float(win["High"].max()) * 0.999:
            newhigh += 1
    checks["pct_universe_above_200dma"] = round(above200 / total * 100, 1) if total else None
    checks["universe_above_200dma_ok"] = bool(total and above200 / total > 0.5)
    # proxy: positive if >10% of tracked universe printed a 52w high this week
    checks["net_new_highs_positive"] = bool(total and newhigh / max(total, 1) > 0.1)

    score = sum([
        checks["close_above_50dma"], checks["close_above_200dma"],
        checks["50dma_above_200dma"], checks["distribution_days_ok"],
        checks["universe_above_200dma_ok"], checks["net_new_highs_positive"],
    ])
    if score >= 5:
        label, guidance = "CONFIRMED_UPTREND", "New buys allowed; full risk budget."
    elif score >= 3:
        label, guidance = "CAUTION", "New buys at half risk; only score >=85 setups."
    else:
        label, guidance = "CORRECTION", "No new buys; watchlist maintenance and holdings management only."
    return {"score": int(score), "label": label, "checks": checks, "exposure_guidance": guidance}
