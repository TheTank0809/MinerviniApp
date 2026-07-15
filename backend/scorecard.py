"""Minervini Weekly Scorecard Engine — deterministic implementation of PROMPT.md v3.

The rubric in PROMPT.md is mechanical, so gates and scores are computed in code
(free, reproducible). Prime Directive 2 is enforced throughout: any missing or
unverifiable field scores 0 and is listed in data_quality.unverified_fields.
An optional LLM pass (llm.py) adds the qualitative verdict text for new stocks.
"""


def _uv(unverified, field):
    if field not in unverified:
        unverified.append(field)
    return 0


# ---------------------------------------------------------------- Gate 1: Trend Template

def trend_template(t, unverified):
    p = t.get("price")
    c = {}
    def chk(key, cond, needed):
        missing = any(t.get(f) is None for f in needed)
        if missing:
            for f in needed:
                if t.get(f) is None and f not in unverified:
                    unverified.append(f)
            c[key] = False
        else:
            c[key] = bool(cond())
    chk("c1", lambda: p > t["dma_150"] and p > t["dma_200"], ["price", "dma_150", "dma_200"])
    chk("c2", lambda: t["dma_150"] > t["dma_200"], ["dma_150", "dma_200"])
    chk("c3", lambda: t["dma_200_slope_21d"] == "rising" and (t.get("dma_200_rising_months") or 0) >= 1,
        ["dma_200_slope_21d"])
    chk("c4", lambda: t["dma_50"] > t["dma_150"] and t["dma_50"] > t["dma_200"],
        ["dma_50", "dma_150", "dma_200"])
    chk("c5", lambda: p > t["dma_50"], ["price", "dma_50"])
    chk("c6", lambda: t["pct_above_52w_low"] >= 30, ["pct_above_52w_low"])
    chk("c7", lambda: t["pct_below_52w_high"] <= 25, ["pct_below_52w_high"])
    chk("c8", lambda: t["rs_percentile"] >= 70, ["rs_percentile"])
    c["pass"] = all(c[k] for k in ("c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8"))

    notes = []
    if not c["c7"] and t.get("pct_below_52w_high") is not None and t["pct_below_52w_high"] <= 30:
        notes.append("c7 near miss: %.1f%% below 52w high" % t["pct_below_52w_high"])
    if not c["c8"] and t.get("rs_percentile") is not None and t["rs_percentile"] >= 60:
        notes.append("c8 near miss: RS %d" % t["rs_percentile"])
    if not c["c6"] and t.get("pct_above_52w_low") is not None and t["pct_above_52w_low"] >= 20:
        notes.append("c6 near miss: %.1f%% above 52w low" % t["pct_above_52w_low"])
    c["near_miss_notes"] = "; ".join(notes)
    return c


# ---------------------------------------------------------------- Gate 2: Investability

def investability(t, f, cfg, unverified, llm_checks=None):
    g = {}
    mtv = t.get("median_daily_traded_value_50d")
    min_cr = cfg.get("min_median_daily_traded_value_cr", 5)
    if mtv is None:
        unverified.append("median_daily_traded_value")
        g["liquidity"] = False
    else:
        g["liquidity"] = mtv >= min_cr * 1e7  # ₹ crore -> ₹
    pledge = f.get("promoter_pledge_pct")
    if pledge is None:
        # genuine parse failure (the ratios box itself didn't load) — screener.in
        # omitting just the pledge line is already normalized to 0.0 upstream
        g["pledge"] = True
        if "promoter_pledge_pct" not in unverified:
            unverified.append("promoter_pledge_pct")
    else:
        g["pledge"] = pledge <= cfg.get("max_promoter_pledge_pct", 15)
    # Governance events (auditor resignation, SEBI action, fraud investigation) have no
    # structured field on screener.in — best-effort only, via the LLM's own knowledge of
    # recent news when a check was actually run this cycle (see llm.py). No check yet run
    # for this stock -> stays unverified rather than assumed clean.
    if llm_checks is not None and "governance_flag" in llm_checks:
        g["governance"] = not llm_checks.get("governance_flag")
        g["governance_note"] = llm_checks.get("governance_note", "")
    else:
        g["governance"] = True
        if "governance_events" not in unverified:
            unverified.append("governance_events")
    g["pass"] = g["liquidity"] and g["pledge"] and g["governance"]
    return g


# ---------------------------------------------------------------- Scoring A–H

def _score_A(f, uv, flags):
    s = {}
    yoy = f.get("eps_yoy_last4")  # [latest, prev, prev2, prev3]
    if not yoy or yoy[0] is None:
        s["A1"] = _uv(uv, "eps_yoy_latest")
    else:
        g = yoy[0]
        s["A1"] = 8 if g >= 100 else 6 if g >= 50 else 4 if g >= 25 else 2 if g >= 15 else 0
    if not yoy or any(v is None for v in yoy[:4]):
        s["A2"] = _uv(uv, "eps_acceleration")
    else:
        rising = sum(1 for i in range(3) if yoy[i] > yoy[i + 1])
        s["A2"] = 5 if rising == 3 else 3 if rising == 2 else 1 if rising == 1 else 0
        if yoy[0] < yoy[1] < yoy[2]:
            flags.append("EPS_DECELERATION")
            s["A2"] = 0
    g = f.get("eps_annual_growth_latest")
    s["A3"] = _uv(uv, "eps_annual_growth") if g is None else (4 if g >= 25 else 2 if g >= 15 else 0)
    g = f.get("eps_cagr_3y")
    s["A4"] = _uv(uv, "eps_cagr_3y") if g is None else (4 if g >= 25 else 2 if g >= 15 else 0)
    a5 = 0
    eq = f.get("eps_quarterly")
    if eq and len(eq) >= 8 and all(v is not None and v > 0 for v in eq[-8:]):
        a5 += 2
    elif not eq:
        _uv(uv, "eps_quarterly")
    oi = f.get("other_income_pct_pbt_last8")
    if oi and all(v is None or v <= 30 for v in oi):
        a5 += 2
    elif oi and any(v is not None and v > 30 for v in oi):
        flags.append("OTHER_INCOME_DRIVEN")
    s["A5"] = a5
    s["subtotal"] = sum(v for k, v in s.items() if k != "subtotal")
    return s


def _score_B(f, uv, flags):
    s = {}
    yoy = f.get("sales_yoy_last4")
    if not yoy or yoy[0] is None:
        s["B1"] = _uv(uv, "sales_yoy_latest")
    else:
        g = yoy[0]
        s["B1"] = 6 if g >= 25 else 4 if g >= 20 else 2 if g >= 10 else 0
    if not yoy or any(v is None for v in yoy[:4]):
        s["B2"] = _uv(uv, "sales_acceleration")
    else:
        rising = sum(1 for i in range(3) if yoy[i] > yoy[i + 1])
        s["B2"] = 4 if rising == 3 else 2 if rising == 2 else 0
    g = f.get("sales_cagr_3y")
    s["B3"] = _uv(uv, "sales_cagr_3y") if g is None else (3 if g >= 20 else 1 if g >= 10 else 0)
    # B4: EPS growth supported by revenue+margin, not other income
    eps_g = (f.get("eps_yoy_last4") or [None])[0]
    sales_g = (yoy or [None])[0]
    oi = f.get("other_income_pct_pbt_last8")
    oi_latest = oi[-1] if oi else None
    if eps_g is None or sales_g is None:
        s["B4"] = _uv(uv, "eps_revenue_support")
    else:
        supported = eps_g > 0 and sales_g > 0 and (oi_latest is None or oi_latest <= 30)
        s["B4"] = 2 if supported else 0
    s["subtotal"] = sum(v for k, v in s.items() if k != "subtotal")
    return s


def _score_C(f, uv, flags):
    s = {}
    roe = f.get("roe")
    s["C1"] = _uv(uv, "roe") if roe is None else (3 if roe >= 17 else 1 if roe >= 12 else 0)
    d = f.get("net_margin_yoy_delta_bps")
    s["C2"] = _uv(uv, "net_margin_trend") if d is None else (3 if d > 50 else 1 if d >= -50 else 0)
    cc = f.get("ocf_to_pat_3y")
    if cc is None:
        s["C3"] = _uv(uv, "ocf_to_pat_3y")
    else:
        s["C3"] = 2 if cc >= 0.8 else 0
        if cc < 0.5:
            flags.append("POOR_CASH_CONVERSION")
    fcf_count = f.get("fcf_estimated_positive_count")
    if fcf_count is None:
        s["C4"] = _uv(uv, "fcf")
    else:
        s["C4"] = 2 if fcf_count >= 2 else 0
        flags.append("FCF_ESTIMATED")  # OCF - approximate capex, not a verified figure
    s["subtotal"] = sum(v for k, v in s.items() if k != "subtotal")
    return s


def _score_D(f, uv, flags):
    s = {}
    de = f.get("debt_to_equity")
    s["D1"] = _uv(uv, "debt_to_equity") if de is None else (2 if de <= 0.5 else 1 if de <= 1.0 else 0)
    ic = f.get("interest_coverage")
    s["D2"] = _uv(uv, "interest_coverage") if ic is None else (1 if ic >= 5 else 0)
    sg = f.get("share_count_growth_2y_pct")
    s["D3"] = _uv(uv, "share_count_growth") if sg is None else (1 if sg <= 5 else 0)
    pledge = f.get("promoter_pledge_pct")
    if pledge is None:
        s["D4"] = 1  # screener omits the line when pledge is zero
    else:
        s["D4"] = 1 if pledge == 0 else 0
        if pledge > 10:
            flags.append("PROMOTER_PLEDGE")
    s["subtotal"] = sum(v for k, v in s.items() if k != "subtotal")
    return s


def _score_E(f, uv, flags):
    s = {}
    inst = f.get("institutional_holding_last4")
    if not inst or len(inst) < 2:
        s["E1"] = _uv(uv, "institutional_holding")
    else:
        rising2 = len(inst) >= 3 and inst[-1] > inst[-2] > inst[-3]
        rising1 = inst[-1] > inst[-2]
        s["E1"] = 2 if rising2 else 1 if rising1 else 0
    s["E2"] = _uv(uv, "mf_scheme_count")       # not available for free
    s["E3"] = _uv(uv, "marquee_institution")   # requires holder-level data
    prom = f.get("promoter_holding_last4")
    if not prom or len(prom) < 2 or any(v is None for v in prom):
        s["E4"] = _uv(uv, "promoter_holding_trend")
    else:
        s["E4"] = 1 if prom[-1] >= prom[0] - 0.5 else 0
    s["subtotal"] = sum(v for k, v in s.items() if k != "subtotal")
    return s


def _score_F(t, uv, flags):
    s = {}
    rs = t.get("rs_percentile")
    s["F1"] = _uv(uv, "rs_percentile") if rs is None else (7 if rs >= 90 else 5 if rs >= 80 else 3 if rs >= 70 else 0)
    d = t.get("pct_below_52w_high")
    s["F2"] = _uv(uv, "pct_below_52w_high") if d is None else (4 if d <= 5 else 2 if d <= 15 else 1 if d <= 25 else 0)
    p, d50 = t.get("price"), t.get("dma_50")
    if p is None or d50 is None:
        s["F3"] = _uv(uv, "dma_50")
    else:
        s["F3"] = 2 if (p > d50 and t.get("dma_50_rising")) else 0
    udr = t.get("up_down_volume_ratio_50d")
    s["F4"] = _uv(uv, "up_down_volume_ratio") if udr is None else (2 if udr >= 1.2 else 0)
    s["subtotal"] = sum(v for k, v in s.items() if k != "subtotal")
    return s


def _score_G(t, tt_pass, uv, flags):
    s = {}
    b = t.get("base") or {}
    s["G1"] = 3 if (tt_pass and (t.get("pct_above_52w_low") or 0) >= 30) else 0
    bc = b.get("base_count_since_stage2")
    if bc is None:
        s["G2"] = _uv(uv, "base_count")
    else:
        s["G2"] = 4 if bc <= 2 else 2 if bc == 3 else 0
        if bc >= 4:
            flags.append("LATE_STAGE_BASE")
    if not b.get("in_base"):
        s["G3"] = s["G4"] = s["G5"] = s["G6"] = 0
    else:
        d = b.get("base_depth_pct")
        s["G3"] = 0 if d is None else (4 if d <= 15 else 3 if d <= 25 else 1 if d <= 35 else 0)
        cons = [c["depth_pct"] for c in (b.get("contractions") or [])]
        if len(cons) >= 2:
            shallower = all(cons[i] > cons[i + 1] for i in range(len(cons) - 1))
            final_ok = cons[-1] <= 10
            s["G4"] = 4 if shallower and final_ok else 2 if shallower else 0
        else:
            s["G4"] = 0
        vd = b.get("final_contraction_volume_vs_50d_avg_pct")
        s["G5"] = 0 if vd is None else (3 if vd <= -30 else 1 if vd <= -10 else 0)
        tight = b.get("weekly_close_tightness_pct")
        s["G6"] = 2 if (tight is not None and tight <= 1.5) else 0
    s["subtotal"] = sum(v for k, v in s.items() if k != "subtotal")
    return s


def _score_H(t, uv, flags, llm_h=None):
    s = {}
    q = t.get("industry_group_rs_quartile")
    s["H1"] = _uv(uv, "industry_group_rs") if q is None else (2 if q == 1 else 0)
    s["H2"] = _uv(uv, "group_leadership_rank")
    s["H3"] = 0
    if llm_h and llm_h.get("h3_catalyst_found") and llm_h.get("h3_citation"):
        s["H3"] = 1
    else:
        _uv(uv, "new_catalyst")
    s["subtotal"] = sum(v for k, v in s.items() if k != "subtotal")
    return s


# ---------------------------------------------------------------- classification / plan

def quality_band(total):
    if total >= 90: return "Elite"
    if total >= 80: return "High Conviction"
    if total >= 70: return "Watchlist A"
    if total >= 60: return "Watchlist B"
    return "Reject"


def action_bucket(total, t, regime_label, risk_level, cfg):
    b = t.get("base") or {}
    pfp = b.get("pct_from_pivot")
    ext = cfg.get("buy_zone_extension_pct", 5)
    if total < 60:
        return "AVOID"
    if total < 80:
        return "WATCH"
    if risk_level == "HIGH":
        return "BUY_ON_BREAKOUT"
    if pfp is not None and 0 <= pfp <= ext and regime_label != "CORRECTION":
        return "ACTIONABLE_NOW"
    if pfp is not None and pfp > ext:
        return "EXTENDED"
    return "BUY_ON_BREAKOUT"


def trade_plan(t, total, regime_label, cfg):
    b = t.get("base") or {}
    pivot = b.get("pivot")
    if not pivot:
        return None
    ext = cfg.get("buy_zone_extension_pct", 5)
    low_ref = None
    if b.get("in_base"):
        low_ref = pivot * (1 - min((b.get("contractions") or [{"depth_pct": 8}])[-1]["depth_pct"], 12) / 100.0)
    stop = round(low_ref, 2) if low_ref else round(pivot * 0.93, 2)
    stop_pct = round((pivot - stop) / pivot * 100, 1)
    if stop_pct > cfg.get("max_stop_pct", 10):
        return {"buy_range": [round(pivot, 2), round(pivot * (1 + ext / 100), 2)],
                "stop": stop, "stop_pct": stop_pct, "risk_pct": 0,
                "position_size_pct_equity": 0, "earnings_risk": False,
                "notes": "Natural stop >10% from entry — setup invalid per rules; skip."}
    full = total >= 90 and regime_label == "CONFIRMED_UPTREND"
    risk_pct = cfg.get("account_risk_pct_full_conviction", 1.25) if full else cfg.get("account_risk_pct_half", 0.75)
    pos_pct = min(round(risk_pct / (stop_pct / 100.0), 1), cfg.get("max_single_position_pct", 25))
    return {"buy_range": [round(pivot, 2), round(pivot * (1 + ext / 100), 2)],
            "stop": stop, "stop_pct": stop_pct, "risk_pct": risk_pct,
            "position_size_pct_equity": pos_pct, "earnings_risk": False,
            "notes": "shares = (equity x %.2f%%) / (entry - stop). Move stop to breakeven at 2R." % risk_pct}


# ---------------------------------------------------------------- risk flags

def assess_risk(f, t, flags):
    level = "LOW"
    if "POOR_CASH_CONVERSION" in flags or "OTHER_INCOME_DRIVEN" in flags:
        level = "MEDIUM"
    if "PROMOTER_PLEDGE" in flags:
        level = "HIGH"
    de = f.get("debt_to_equity")
    if de is not None and de > 1.5 and level == "LOW":
        level = "MEDIUM"
    return level


# ---------------------------------------------------------------- main entry

def evaluate(ticker, name, tech, fund, regime, cfg, mode="FULL", prior=None, llm_verdict=None):
    unverified = list(fund.get("unverified_fields") or [])
    flags = []

    tt = trend_template(tech, unverified)
    inv = investability(tech, fund, cfg, unverified, llm_checks=llm_verdict)
    # Raw H3/governance assessment, persisted so a stock not rechecked this run (see
    # pipeline.py's recheck cadence/budget) still carries forward its last known result
    # instead of reverting to unverified every week in between checks.
    llm_checks = {
        "h3_catalyst_found": bool(llm_verdict.get("h3_catalyst_found")),
        "h3_citation": llm_verdict.get("h3_citation", ""),
        "governance_flag": bool(llm_verdict.get("governance_flag")),
        "governance_note": llm_verdict.get("governance_note", ""),
    } if llm_verdict else None

    card = {
        "ticker": ticker, "name": name, "as_of": tech.get("as_of"), "mode": mode,
        "gates": {"trend_template": tt, "investability": inv},
        "llm_checks": llm_checks,
        "scores": None, "quality_band": None, "action_bucket": None,
        "red_flags": [], "risk_level": None,
        "valuation_context": {"pe": fund.get("pe"), "peg": None, "vs_own_5yr": "",
                              "note": "Valuation is context only, never a criterion."},
        "base": {"pattern_label": "", "pivot": (tech.get("base") or {}).get("pivot"),
                 "pct_from_pivot": (tech.get("base") or {}).get("pct_from_pivot"),
                 "base_count": (tech.get("base") or {}).get("base_count_since_stage2"),
                 "depth_pct": (tech.get("base") or {}).get("base_depth_pct"),
                 "weeks": (tech.get("base") or {}).get("weeks_in_base")},
        "trade_plan": None,
        "delta": {"score_change": 0, "gate_flips": [], "bucket_change": "", "alerts": []},
        "violations": {"count": 0, "items": [], "recommendation": ""},
        "verdict": {"summary": "", "strengths": [], "weaknesses": [], "catalysts": [],
                    "biggest_risk": "", "conviction_0_10": 0},
        "data_quality": {"unverified_fields": [], "sources": [
            {"field": "technicals", "source": "Yahoo Finance OHLCV", "as_of": tech.get("as_of")},
            {"field": "fundamentals", "source": "screener.in", "as_of": tech.get("as_of")},
        ]},
        "technicals": {  # surfaced for the website columns
            "price": tech.get("price"), "rs_percentile": tech.get("rs_percentile"),
            "pct_below_52w_high": tech.get("pct_below_52w_high"),
            "pct_above_52w_low": tech.get("pct_above_52w_low"),
            "dma_50": tech.get("dma_50"), "dma_200": tech.get("dma_200"),
            "up_down_volume_ratio_50d": tech.get("up_down_volume_ratio_50d"),
        },
    }

    if not inv["pass"]:
        fails = [k.upper() for k in ("liquidity", "pledge", "governance") if not inv[k]]
        card["status"] = "FAIL_" + fails[0] if fails else "FAIL_INVESTABILITY"
        card["quality_band"] = "Reject"
        card["action_bucket"] = "AVOID"
        summary = "Fails investability gate: %s." % ", ".join(fails)
        if not inv.get("governance", True) and inv.get("governance_note"):
            summary += " Governance: %s" % inv["governance_note"]
        card["verdict"]["summary"] = summary
        card["data_quality"]["unverified_fields"] = sorted(set(unverified))
        _apply_delta(card, prior)
        return card

    # Gate 1 (Trend Template) no longer terminates scoring — a stock that hasn't
    # confirmed Stage 2 yet (e.g. RS still below 70) still gets a full A-H score, so
    # fundamentally strong names stay visible while their technical setup develops.
    # The gate still blocks the buy/breakout buckets and the trade plan outright —
    # a high score never overrides a failed gate.
    card["status"] = "SCORED" if tt["pass"] else "SCORED_NO_TREND"
    s = {
        "earnings": _score_A(fund, unverified, flags),
        "revenue": _score_B(fund, unverified, flags),
        "profitability": _score_C(fund, unverified, flags),
        "balance_sheet": _score_D(fund, unverified, flags),
        "sponsorship": _score_E(fund, unverified, flags),
        "rs_trend": _score_F(tech, unverified, flags),
        "base_structure": _score_G(tech, tt["pass"], unverified, flags),
        "leadership": _score_H(tech, unverified, flags, llm_h=llm_verdict),
    }
    total = sum(sec["subtotal"] for sec in s.values())
    s["total"] = total
    card["scores"] = s
    card["red_flags"] = sorted(set(flags))
    card["risk_level"] = assess_risk(fund, tech, card["red_flags"])
    card["quality_band"] = quality_band(total)
    if not tt["pass"]:
        card["action_bucket"] = "AVOID"
    else:
        card["action_bucket"] = action_bucket(total, tech, regime["label"], card["risk_level"], cfg)
        if card["action_bucket"] in ("ACTIONABLE_NOW", "BUY_ON_BREAKOUT"):
            card["trade_plan"] = trade_plan(tech, total, regime["label"], cfg)
    card["data_quality"]["unverified_fields"] = sorted(set(unverified))

    # rule-derived verdict (LLM may overwrite with richer text)
    if llm_verdict and llm_verdict.get("verdict"):
        card["verdict"] = llm_verdict["verdict"]
    else:
        card["verdict"] = _auto_verdict(card, tech, fund)

    _apply_delta(card, prior)
    return card


def _auto_verdict(card, tech, fund):
    s = card["scores"]
    tt = card["gates"]["trend_template"]
    strengths, weaknesses = [], []
    if s["rs_trend"]["F1"] >= 5: strengths.append("RS percentile %s" % tech.get("rs_percentile"))
    if s["earnings"]["A1"] >= 6: strengths.append("Latest EPS YoY >=50%")
    if s["base_structure"]["subtotal"] >= 12: strengths.append("Constructive base structure")
    if s["earnings"]["subtotal"] <= 10: weaknesses.append("Earnings block weak (%d/25)" % s["earnings"]["subtotal"])
    if s["base_structure"]["subtotal"] <= 6: weaknesses.append("No valid base yet (%d/20)" % s["base_structure"]["subtotal"])
    uv = card["data_quality"]["unverified_fields"]
    if uv: weaknesses.append("%d unverified fields scored 0" % len(uv))
    summary = "%s (%d/100) — %s / %s." % (card["ticker"], s["total"], card["quality_band"], card["action_bucket"])
    if not tt["pass"]:
        failed = ", ".join(k for k in ("c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8") if not tt[k])
        weaknesses.insert(0, "Fails Trend Template: %s" % failed)
        summary += " Score is informational only — Gate 1 not confirmed, so not an actionable setup yet."
    conviction = 0 if not tt["pass"] else max(0, min(10, round((s["total"] - 40) / 6)))
    return {"summary": summary,
            "strengths": strengths, "weaknesses": weaknesses, "catalysts": [],
            "biggest_risk": card["red_flags"][0] if card["red_flags"] else "None flagged",
            "conviction_0_10": conviction}


# ---------------------------------------------------------------- weekly delta

def _apply_delta(card, prior):
    if not prior:
        return
    d = card["delta"]
    p_scores = prior.get("scores") or {}
    p_total = p_scores.get("total", 0) if p_scores else 0
    n_total = (card.get("scores") or {}).get("total", 0)
    d["score_change"] = n_total - p_total

    p_tt = ((prior.get("gates") or {}).get("trend_template") or {})
    n_tt = card["gates"]["trend_template"]
    for k in ("c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8"):
        if k in p_tt and p_tt.get(k) != n_tt.get(k):
            d["gate_flips"].append("%s: %s -> %s" % (k, p_tt.get(k), n_tt.get(k)))

    if prior.get("action_bucket") and prior["action_bucket"] != card["action_bucket"]:
        d["bucket_change"] = "%s -> %s" % (prior["action_bucket"], card["action_bucket"])
        if card["action_bucket"] == "ACTIONABLE_NOW":
            d["alerts"].append("ENTERED_BUY_ZONE")
        if prior["action_bucket"] == "ACTIONABLE_NOW" and card["action_bucket"] == "EXTENDED":
            d["alerts"].append("LEFT_BUY_ZONE_EXTENDED")

    p_rs = (prior.get("technicals") or {}).get("rs_percentile")
    n_rs = (card.get("technicals") or {}).get("rs_percentile")
    if p_rs is not None and n_rs is not None and (p_rs - n_rs >= 10 or (p_rs >= 70 > n_rs)):
        d["alerts"].append("RS_DOWNGRADE")

    t = card.get("technicals") or {}
    if t.get("price") is not None and t.get("dma_50") is not None and t["price"] < t["dma_50"]:
        p_t = prior.get("technicals") or {}
        if p_t.get("price") is not None and p_t.get("dma_50") is not None and p_t["price"] >= p_t["dma_50"]:
            d["alerts"].append("CLOSED_BELOW_50DMA")

    if "EPS_DECELERATION" in card.get("red_flags", []) and "EPS_DECELERATION" not in (prior.get("red_flags") or []):
        d["alerts"].append("EPS_DECELERATION")
