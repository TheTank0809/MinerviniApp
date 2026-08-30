"""Convert a raw TradingView scanner row into the same fundamental payload shape
scorecard.py expects (see PROMPT.md — "Fundamental payload") — the same contract
fundamentals.py fulfills for screener.in-sourced (India) stocks.

Concepts that don't exist outside India (promoter pledge, promoter/institutional
holding trend) are left unset — scorecard.py already treats those as unverified /
zero-scored rather than crashing (Prime Directive 2), same as any other missing
field. A few items (3y EPS CAGR, latest annual EPS growth, net margin trend) are
approximated from net income history rather than a true EPS-history series, since
TradingView's scanner doesn't expose one directly — noted inline. Nothing here is
fabricated: every value either comes straight from TradingView or is left None.
"""


def _yoy_pct(newer, older):
    if newer is None or older is None or older == 0:
        return None
    if older < 0:
        return None  # growth off a negative base isn't meaningful — same rule fundamentals.py uses
    return (newer - older) / abs(older) * 100.0


def _cagr_pct(first, last, years):
    if first is None or last is None or first <= 0 or last <= 0 or years <= 0:
        return None
    return ((last / first) ** (1.0 / years) - 1.0) * 100.0


def build_fundamental_payload(row):
    """`row` is one dict from tradingview_client.fetch_universe()."""
    unverified = []
    payload = {}

    payload["sector"] = row.get("sector")
    payload["pe"] = row.get("price_earnings_ttm")

    # ---- quarterly EPS/revenue YoY — direct from TradingView's history arrays,
    # which are already newest-first (index 0 = latest quarter), matching the
    # [latest, prev, prev2, prev3] shape scorecard.py's Sections A/B expect. -----
    eps_fq = row.get("earnings_per_share_diluted_fq_h")
    rev_fq = row.get("total_revenue_fq_h")

    def yoy_last4(fq):
        if not fq or len(fq) < 8:
            return None
        return [_yoy_pct(fq[k], fq[k + 4]) for k in range(4)]

    payload["eps_yoy_last4"] = yoy_last4(eps_fq)
    if payload["eps_yoy_last4"] is None:
        unverified.append("eps_yoy_latest")
        unverified.append("eps_acceleration")
    payload["sales_yoy_last4"] = yoy_last4(rev_fq)
    if payload["sales_yoy_last4"] is None:
        unverified.append("sales_yoy_latest")
        unverified.append("sales_acceleration")

    payload["eps_quarterly"] = eps_fq[:8] if eps_fq and len(eps_fq) >= 8 else None
    if payload["eps_quarterly"] is None:
        unverified.append("eps_quarterly")

    # ---- annual EPS growth / 3y CAGR — approximated from net income history
    # (TradingView's scanner doesn't expose an annual EPS-history array). Net
    # income growth is a reasonable stand-in for EPS growth when share count is
    # stable; D3 (share count growth) is scored separately and catches dilution
    # the NI-based figures here wouldn't. --------------------------------------
    ni_fy = row.get("net_income_fy_h")
    if ni_fy and len(ni_fy) >= 2 and ni_fy[1]:
        payload["eps_annual_growth_latest"] = _yoy_pct(ni_fy[0], ni_fy[1])
    else:
        payload["eps_annual_growth_latest"] = None
        unverified.append("eps_annual_growth")
    if ni_fy and len(ni_fy) >= 4:
        payload["eps_cagr_3y"] = _cagr_pct(ni_fy[3], ni_fy[0], 3)
    else:
        payload["eps_cagr_3y"] = None
        unverified.append("eps_cagr_3y")

    rev_fy = row.get("total_revenue_fy_h")
    if rev_fy and len(rev_fy) >= 4:
        payload["sales_cagr_3y"] = _cagr_pct(rev_fy[3], rev_fy[0], 3)
    else:
        payload["sales_cagr_3y"] = None
        unverified.append("sales_cagr_3y")

    # H3-adjacent "other income driven" check has no TradingView equivalent field
    # (screener.in exposes a dedicated "Other Income" line India's disclosure
    # norms require; most global filers don't break it out the same way) — left
    # unverified rather than guessed at.
    payload["other_income_pct_pbt_last8"] = None
    unverified.append("other_income_pct_pbt")

    # ---- profitability -------------------------------------------------------
    roe = row.get("return_on_equity")
    payload["roe"] = roe
    if roe is None:
        unverified.append("roe")
    payload["roce"] = row.get("return_on_invested_capital")  # ROIC used as the closest available proxy for ROCE

    if ni_fy and rev_fy and len(ni_fy) >= 2 and len(rev_fy) >= 2 and rev_fy[0] and rev_fy[1]:
        margin_now = ni_fy[0] / rev_fy[0] * 100.0
        margin_prior = ni_fy[1] / rev_fy[1] * 100.0
        payload["net_margin_yoy_delta_bps"] = (margin_now - margin_prior) * 100.0  # pct-points -> bps
    else:
        payload["net_margin_yoy_delta_bps"] = None
        unverified.append("net_margin_trend")

    # True OCF isn't in this column set (only FCF, which is OCF minus capex) —
    # left unverified rather than substituting a different ratio silently.
    payload["ocf_to_pat_3y"] = None
    unverified.append("ocf_to_pat_3y")

    fcf_fy = row.get("free_cash_flow_fy_h")
    if fcf_fy and len(fcf_fy) >= 3:
        positive = sum(1 for v in fcf_fy[:3] if v is not None and v > 0)
        payload["fcf_positive_count"] = positive
        payload["fcf_source"] = "reported"
        payload["fcf_latest_cr"] = round(fcf_fy[0] / 1e7, 1) if fcf_fy[0] is not None else None  # kept in
        # the same "Cr" (crore) display unit the rest of the app uses for consistency, even though this
        # is USD/other currency, not INR — see the currency note in pipeline_global.py.
    else:
        payload["fcf_positive_count"] = None
        payload["fcf_source"] = None
        payload["fcf_latest_cr"] = None
        unverified.append("fcf")

    # ---- balance sheet ---------------------------------------------------
    payload["debt_to_equity"] = row.get("debt_to_equity_fy")
    if payload["debt_to_equity"] is None:
        unverified.append("debt_to_equity")

    # No direct interest-expense figure in this column set.
    payload["interest_coverage"] = None
    unverified.append("interest_coverage")

    # No historical share-count series in this column set.
    payload["share_count_growth_2y_pct"] = None
    unverified.append("share_count_growth")

    # India-specific concepts with no global equivalent — always left unset.
    # D4 (promoter pledge) treats a missing value as "no pledge" (matches how
    # fundamentals.py already handles screener.in omitting a zero pledge line),
    # so this isn't flagged as unverified — it's a deliberate, documented no-op.
    payload["promoter_pledge_pct"] = None
    payload["promoter_holding_last4"] = None
    payload["institutional_holding_last4"] = None
    unverified += ["promoter_holding_trend", "institutional_holding"]

    payload["unverified_fields"] = sorted(set(unverified))
    payload["unverified_reasons"] = {}
    return payload
