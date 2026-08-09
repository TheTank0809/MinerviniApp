"""Convert raw screener.in company tables into the fundamental payload used by the
scorecard engine (see PROMPT.md — "Fundamental payload").

Anything that cannot be derived is left as None and later reported in
data_quality.unverified_fields (Prime Directive 2: unverified = 0 points).
"""


def _row(table, *labels):
    if not table:
        return None
    rows = table["rows"]
    for label in labels:
        for key, values in rows.items():
            if key.lower().startswith(label.lower()):
                return values
    return None


def _yoy(series, idx):
    """YoY % growth for quarterly series at index (needs idx-4)."""
    if series is None or idx < 4 or idx >= len(series):
        return None
    prev, cur = series[idx - 4], series[idx]
    if prev is None or cur is None or prev == 0:
        return None
    if prev < 0:
        return None  # growth off a negative base is not meaningful
    return (cur - prev) / abs(prev) * 100.0


def _cagr(first, last, years):
    if first is None or last is None or first <= 0 or last <= 0 or years <= 0:
        return None
    return ((last / first) ** (1.0 / years) - 1.0) * 100.0


def _pct_from_ratio_text(text):
    if not text:
        return None
    import re
    m = re.search(r"([\d.]+)\s*%", text)
    return float(m.group(1)) if m else None


def _estimate_fcf_years(bs, pl, cf, n_years=3):
    """Estimated FCF per year = OCF - (delta Net Block + Depreciation), an indirect proxy
    for capex. screener.in's free Cash Flow section doesn't isolate capex as its own line,
    so this is an ESTIMATE, not a verified figure — callers must flag it as such (see
    scorecard.py's FCF_ESTIMATED red flag) rather than presenting it as a hard number.

    Note: screener.in's balance sheet row is labeled "Fixed Assets", never "Net Block" —
    kept as a fallback label only in case some page variant differs; searching for "Net
    Block" alone previously meant this silently failed for every company, always.

    Returns (results, reason) — reason is a short human string set only when the estimate
    is impossible outright (a required row or enough history of it is missing), not when
    the calculation ran but every candidate year got filtered out for other reasons.
    """
    net_block = _row(bs, "Fixed Assets", "Net Block")
    depreciation = _row(pl, "Depreciation")
    ocf = _row(cf, "Cash from Operating")
    missing = []
    if not net_block:
        missing.append("Fixed Assets (Balance Sheet)")
    elif len(net_block) < 2:
        missing.append("Fixed Assets history — need 2+ years, screener.in has %d" % len(net_block))
    if not depreciation:
        missing.append("Depreciation (P&L)")
    if not ocf:
        missing.append("Cash from Operating (Cash Flow)")
    if missing:
        return None, "missing " + " & ".join(missing)
    results = []
    for k in range(1, n_years + 1):
        ocf_idx, nb_idx, dep_idx = len(ocf) - k, len(net_block) - k, len(depreciation) - k
        if ocf_idx < 0 or nb_idx < 1 or dep_idx < 0:
            break
        nb_now, nb_prev, dep, ocf_v = net_block[nb_idx], net_block[nb_idx - 1], depreciation[dep_idx], ocf[ocf_idx]
        if None in (nb_now, nb_prev, dep, ocf_v):
            continue
        capex_est = (nb_now - nb_prev) + dep
        results.append(ocf_v - capex_est)
    if not results:
        return None, "the required rows exist but had no year with all values present"
    return results, None


def build_fundamental_payload(raw):
    """raw = output of ScreenerClient.fetch_company"""
    unverified = []
    unverified_reasons = {}  # field -> short human reason, populated only where useful
    q = raw.get("quarters")
    pl = raw.get("profit_loss")
    bs = raw.get("balance_sheet")
    cf = raw.get("cash_flow")
    sh = raw.get("shareholding")
    ratios = raw.get("top_ratios") or {}

    payload = {}
    payload["sector"] = raw.get("sector")

    # ---- Quarterly EPS & sales -------------------------------------------
    eps_q = _row(q, "EPS in Rs", "EPS")
    sales_q = _row(q, "Sales", "Revenue")
    np_q = _row(q, "Net Profit", "Profit after tax")
    other_income_q = _row(q, "Other Income")
    pbt_q = _row(q, "Profit before tax")
    opm_q = _row(q, "OPM %", "Financing Margin %")

    def yoy_series(series, n=4):
        if series is None:
            return None
        out = []
        for k in range(n):
            idx = len(series) - 1 - k
            out.append(_yoy(series, idx))
        return out  # [latest, prev, prev2, prev3]

    payload["eps_quarterly"] = eps_q[-8:] if eps_q else None
    payload["eps_yoy_last4"] = yoy_series(eps_q)
    payload["sales_yoy_last4"] = yoy_series(sales_q)
    payload["net_profit_quarterly"] = np_q[-8:] if np_q else None

    if other_income_q and pbt_q and len(other_income_q) == len(pbt_q):
        ratios_oi = []
        for oi, pbt in zip(other_income_q[-8:], pbt_q[-8:]):
            if oi is None or pbt is None or pbt == 0:
                ratios_oi.append(None)
            else:
                ratios_oi.append(oi / pbt * 100.0)
        payload["other_income_pct_pbt_last8"] = ratios_oi
    else:
        payload["other_income_pct_pbt_last8"] = None
        unverified.append("other_income_pct_pbt")

    # Net margin trend: latest quarter net margin vs same quarter last year
    if np_q and sales_q and len(np_q) >= 5 and len(sales_q) >= 5:
        def margin(i):
            if np_q[i] is None or sales_q[i] is None or sales_q[i] == 0:
                return None
            return np_q[i] / sales_q[i] * 100.0
        m_now, m_prev = margin(len(np_q) - 1), margin(len(np_q) - 5)
        payload["net_margin_latest"] = m_now
        payload["net_margin_yoy_delta_bps"] = (
            (m_now - m_prev) * 100.0 if m_now is not None and m_prev is not None else None)
    else:
        payload["net_margin_yoy_delta_bps"] = None
        unverified.append("net_margin_trend")

    payload["opm_quarterly"] = opm_q[-8:] if opm_q else None

    # ---- Annual EPS -------------------------------------------------------
    eps_a = _row(pl, "EPS in Rs", "EPS")
    if eps_a:
        eps_a = [v for v in eps_a if v is not None]
        # screener annual tables often end with a TTM column; use as-is, latest last
        payload["eps_annual"] = eps_a[-6:]
        if len(eps_a) >= 2:
            prev, last = eps_a[-2], eps_a[-1]
            payload["eps_annual_growth_latest"] = (
                (last - prev) / abs(prev) * 100.0 if prev and prev > 0 else None)
        else:
            payload["eps_annual_growth_latest"] = None
        payload["eps_cagr_3y"] = _cagr(eps_a[-4], eps_a[-1], 3) if len(eps_a) >= 4 else None
    else:
        payload["eps_annual"] = None
        payload["eps_annual_growth_latest"] = None
        payload["eps_cagr_3y"] = None
        unverified += ["eps_annual_growth", "eps_cagr_3y"]

    sales_a = _row(pl, "Sales", "Revenue")
    if sales_a:
        sales_a = [v for v in sales_a if v is not None]
        payload["sales_cagr_3y"] = _cagr(sales_a[-4], sales_a[-1], 3) if len(sales_a) >= 4 else None
    else:
        payload["sales_cagr_3y"] = None
        unverified.append("sales_cagr_3y")

    # ---- Profitability / quality -----------------------------------------
    payload["roe"] = _pct_from_ratio_text(ratios.get("ROE"))
    payload["roce"] = _pct_from_ratio_text(ratios.get("ROCE"))
    if payload["roe"] is None:
        unverified.append("roe")

    ocf = _row(cf, "Cash from Operating")
    pat_a = _row(pl, "Net Profit")
    if ocf and pat_a:
        ocf3 = [v for v in ocf[-3:] if v is not None]
        pat3 = [v for v in pat_a[-4:-1] if v is not None] or [v for v in pat_a[-3:] if v is not None]
        if ocf3 and pat3 and sum(pat3) > 0:
            payload["ocf_to_pat_3y"] = sum(ocf3) / sum(pat3)
        else:
            payload["ocf_to_pat_3y"] = None
    else:
        payload["ocf_to_pat_3y"] = None
        unverified.append("ocf_to_pat_3y")

    # screener.in publishes an actual "Free Cash Flow" row directly on the Cash Flows
    # table for many (not all) companies — same table already scraped for OCF, so this
    # is free. That's a real reported figure, so it's always preferred over our own
    # OCF-minus-approximate-capex estimate; the estimate is only a fallback for
    # companies (or periods) where screener.in doesn't have it — see
    # _estimate_fcf_years' docstring for why that path can only ever be an estimate.
    # fcf_source lets scorecard.py tag the FCF_ESTIMATED red flag only when the figure
    # actually came from the estimate, not when it's screener.in's own number.
    # fcf_latest_cr is the most recent year's actual figure (Rs crore) — the score itself
    # (C4) only ever reflects a positive-years count, so without this the real number was
    # never actually visible anywhere, just its derived 0/2.
    payload["fcf_source"] = None
    payload["fcf_latest_cr"] = None
    fcf_direct = _row(cf, "Free Cash Flow")
    if fcf_direct:
        fcf_recent = [v for v in fcf_direct[-3:] if v is not None]
        if fcf_recent:
            payload["fcf_positive_count"] = sum(1 for v in fcf_recent if v > 0)
            payload["fcf_years_count"] = len(fcf_recent)
            payload["fcf_source"] = "reported"
            payload["fcf_latest_cr"] = fcf_recent[-1]  # chronological: latest is last
    if payload["fcf_source"] is None:
        fcf_years, fcf_missing_reason = _estimate_fcf_years(bs, pl, cf)
        if fcf_years:
            payload["fcf_positive_count"] = sum(1 for v in fcf_years if v > 0)
            payload["fcf_years_count"] = len(fcf_years)
            payload["fcf_source"] = "estimated"
            payload["fcf_latest_cr"] = fcf_years[0]  # reverse-chronological: latest is first
        else:
            payload["fcf_positive_count"] = None
            unverified.append("fcf")
            unverified_reasons["fcf"] = "no direct Free Cash Flow row on screener.in, and the " \
                "estimate also failed: " + fcf_missing_reason

    # ---- Balance sheet ----------------------------------------------------
    borrow = _row(bs, "Borrowings")
    equity = _row(bs, "Equity Capital", "Share Capital")
    reserves = _row(bs, "Reserves")
    if borrow and equity and reserves:
        try:
            b = borrow[-1] or 0.0
            e = (equity[-1] or 0.0) + (reserves[-1] or 0.0)
            payload["debt_to_equity"] = (b / e) if e > 0 else None
        except Exception:
            payload["debt_to_equity"] = None
    else:
        payload["debt_to_equity"] = None
        unverified.append("debt_to_equity")

    interest_a = _row(pl, "Interest")
    op_profit_a = _row(pl, "Operating Profit")
    if interest_a and op_profit_a and interest_a[-1] and op_profit_a[-1] is not None:
        payload["interest_coverage"] = (
            op_profit_a[-1] / interest_a[-1] if interest_a[-1] > 0 else 99.0)
    else:
        payload["interest_coverage"] = None
        unverified.append("interest_coverage")

    if equity and len([v for v in equity if v is not None]) >= 3:
        vals = [v for v in equity if v is not None]
        first, last = vals[-3], vals[-1]
        payload["share_count_growth_2y_pct"] = (
            (last - first) / first * 100.0 if first and first > 0 else None)
    else:
        payload["share_count_growth_2y_pct"] = None
        unverified.append("share_count_growth")

    # ---- Shareholding -----------------------------------------------------
    prom = _row(sh, "Promoters")
    fii = _row(sh, "FIIs")
    dii = _row(sh, "DIIs")
    payload["promoter_holding_last4"] = prom[-4:] if prom else None
    if fii and dii:
        n = min(len(fii), len(dii))
        inst = [((fii[i] or 0) + (dii[i] or 0)) for i in range(n)]
        payload["institutional_holding_last4"] = inst[-4:]
    else:
        payload["institutional_holding_last4"] = None
        unverified.append("institutional_holding")

    # screener.in omits the "Pledged percentage" ratio entirely when pledge is zero,
    # rather than showing 0% — so its absence is a confirmed zero, not a missing value,
    # as long as the ratios box itself parsed (i.e. this isn't a page-structure failure).
    if ratios:
        pledge = _pct_from_ratio_text(ratios.get("Pledged percentage"))
        if pledge is None:
            pledge = 0.0
    else:
        pledge = None
        unverified.append("promoter_pledge_pct")
    payload["promoter_pledge_pct"] = pledge

    # valuation context (never scored — PROMPT.md purity rules)
    payload["pe"] = None
    try:
        import re as _re
        pe_txt = ratios.get("Stock P/E") or ratios.get("P/E")
        if pe_txt:
            m = _re.search(r"([\d.]+)", pe_txt.replace(",", ""))
            payload["pe"] = float(m.group(1)) if m else None
    except Exception:
        pass

    # RSI (Wilder's 14-day momentum oscillator, from screener.in's ratios box) — never
    # scored, and never a substitute for rs_percentile: RSI is self-referential (a
    # stock's own overbought/oversold reading), while rs_percentile ranks price
    # performance against other stocks. Shown for context only, clearly separate from
    # the RS pct tile in the UI, precisely because the two are easy to conflate.
    payload["rsi"] = None
    try:
        import re as _re
        rsi_txt = ratios.get("RSI")
        if rsi_txt:
            m = _re.search(r"([\d.]+)", rsi_txt.replace(",", ""))
            payload["rsi"] = float(m.group(1)) if m else None
    except Exception:
        pass

    payload["unverified_fields"] = sorted(set(unverified))
    payload["unverified_reasons"] = unverified_reasons
    return payload
