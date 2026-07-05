"""Seed docs/data with sample scorecards so the site renders before the first real
scan. Also serves as a smoke test: it runs the real scorecard engine end-to-end
on synthetic payloads. The pipeline discards this data on its first run.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
import scorecard as SC  # noqa: E402

CFG = {"min_median_daily_traded_value_cr": 5, "max_promoter_pledge_pct": 15,
       "buy_zone_extension_pct": 5, "max_stop_pct": 10,
       "account_risk_pct_full_conviction": 1.25, "account_risk_pct_half": 0.75,
       "max_single_position_pct": 25}

REGIME = {"score": 5, "label": "CONFIRMED_UPTREND",
          "checks": {"close_above_50dma": True, "close_above_200dma": True,
                     "50dma_above_200dma": True, "distribution_days_25": 3,
                     "distribution_days_ok": True, "pct_universe_above_200dma": 62.0,
                     "universe_above_200dma_ok": True, "net_new_highs_positive": False},
          "exposure_guidance": "New buys allowed; full risk budget."}


def tech(price, pivot, rs, off_high, above_low, in_base=True, depth=18.0,
         contractions=(14.0, 8.5, 4.2), vol_dry=-38.0, tight=1.1, bases=2):
    return {
        "price": price, "as_of": "2026-07-03",
        "dma_50": price * 0.95, "dma_150": price * 0.88, "dma_200": price * 0.84,
        "dma_200_slope_21d": "rising", "dma_200_rising_months": 5, "dma_50_rising": True,
        "high_52w": price / (1 - off_high / 100.0), "low_52w": price / (1 + above_low / 100.0),
        "pct_above_52w_low": above_low, "pct_below_52w_high": off_high,
        "rs_percentile": rs, "rs_percentile_prev_week": rs - 2,
        "industry_group_rs_quartile": None,
        "avg_volume_50d": 800000, "median_daily_traded_value_50d": 42e7,
        "up_down_volume_ratio_50d": 1.45,
        "base": {"in_base": in_base, "weeks_in_base": 7.4, "base_depth_pct": depth,
                 "base_count_since_stage2": bases,
                 "contractions": [{"depth_pct": d} for d in contractions],
                 "final_contraction_volume_vs_50d_avg_pct": vol_dry,
                 "weekly_close_tightness_pct": tight, "pivot": pivot,
                 "pct_from_pivot": round((price / pivot - 1) * 100, 2)},
        "breakout_today": {"occurred": False, "volume_vs_50d_avg_pct": None},
    }


def fund(eps_yoy, sales_yoy, roe=22.0, de=0.2):
    return {
        "eps_quarterly": [4, 5, 6, 7, 8, 10, 12, 15],
        "eps_yoy_last4": eps_yoy, "sales_yoy_last4": sales_yoy,
        "net_profit_quarterly": [40, 50, 60, 70, 80, 100, 120, 150],
        "other_income_pct_pbt_last8": [5, 4, 6, 5, 4, 5, 6, 4],
        "net_margin_latest": 14.2, "net_margin_yoy_delta_bps": 120,
        "opm_quarterly": None,
        "eps_annual": [10, 14, 19, 26, 35], "eps_annual_growth_latest": 34.6,
        "eps_cagr_3y": 36.0, "sales_cagr_3y": 24.0,
        "roe": roe, "roce": roe + 3, "ocf_to_pat_3y": 0.92,
        "fcf_positive_years_of_3": None,
        "debt_to_equity": de, "interest_coverage": 14.0,
        "share_count_growth_2y_pct": 0.4,
        "promoter_holding_last4": [61.0, 61.0, 61.2, 61.2],
        "institutional_holding_last4": [12.1, 12.9, 13.8, 15.2],
        "promoter_pledge_pct": None, "pe": 44.2,
        "unverified_fields": ["fcf", "mf_scheme_count"],
    }


SAMPLES = [
    ("SAMPLE-A", "Sample Alpha Industries", tech(980, 1000, 94, 4.5, 88),
     fund([120, 90, 60, 40], [32, 28, 22, 18]), "2026-05-10"),
    ("SAMPLE-B", "Sample Beta Pharma", tech(455, 470, 86, 8.0, 55, depth=22.0,
     contractions=(18.0, 11.0), vol_dry=-15.0, tight=2.4),
     fund([45, 52, 30, 25], [18, 15, 16, 12]), "2026-06-14"),
    ("SAMPLE-C", "Sample Gamma Cables", tech(2140, 2050, 91, 1.0, 130, in_base=False),
     fund([70, 55, 48, 30], [26, 24, 20, 15]), "2026-07-05"),
    ("SAMPLE-D", "Sample Delta Finance", tech(310, 340, 58, 12.0, 42),
     fund([12, 18, 25, 30], [8, 9, 11, 14], roe=13.0, de=1.2), "2026-04-05"),
]

DROPPED = [("SAMPLE-X", "Sample Exit Motors", tech(760, 900, 40, 28.0, 25, in_base=False),
            fund([-5, 8, 20, 35], [4, 6, 10, 15], roe=11.0), "2026-02-01", "2026-06-21")]


def main():
    active = []
    for code, name, t, f, joined in SAMPLES:
        card = SC.evaluate(code, name, t, f, REGIME, CFG, mode="FULL")
        active.append({"ticker": code, "name": name, "joined_date": joined,
                       "last_updated": "2026-07-05", "scorecard": card})
        s = card.get("scores")
        print("%-10s %-22s %-6s %s" % (code, card["status"],
                                       s["total"] if s else "-", card["action_bucket"]))
    dropped = []
    for code, name, t, f, joined, left in DROPPED:
        card = SC.evaluate(code, name, t, f, REGIME, CFG, mode="FULL")
        dropped.append({"ticker": code, "name": name, "joined_date": joined,
                        "dropped_date": left, "frozen": True, "scorecard": card})

    base = os.path.join(ROOT, "docs", "data", "india", "minervini-filter")
    os.makedirs(base, exist_ok=True)
    with open(os.path.join(base, "active.json"), "w") as fh:
        json.dump({"generated_at": "2026-07-05", "sample": True,
                   "stocks": sorted(active, key=lambda r: -(r["scorecard"]["scores"] or {"total": 0})["total"]
                                    if r["scorecard"]["scores"] else 0)}, fh, indent=1)
    with open(os.path.join(base, "dropped.json"), "w") as fh:
        json.dump({"sample": True, "stocks": dropped}, fh, indent=1)
    with open(os.path.join(base, "runs.json"), "w") as fh:
        json.dump({"runs": [{"run_date": "2026-07-05", "mode": "WEEKLY", "regime": REGIME,
                             "counts": {"active": len(active), "new": 1, "dropped": 1, "errors": 0},
                             "new_tickers": ["SAMPLE-C"], "dropped_tickers": ["SAMPLE-X"],
                             "actionable_now": [], "alerts": [], "errors": []}]}, fh, indent=1)
    with open(os.path.join(ROOT, "docs", "data", "manifest.json"), "w") as fh:
        json.dump({"generated_at": "2026-07-05T00:00:00Z", "sample": True,
                   "screens": [{"universe": "india", "screen": "minervini-filter",
                                "label": "Minervini Screener", "universe_label": "India",
                                "counts": {"active": len(active), "new": 1, "dropped": 1, "errors": 0},
                                "regime": {"label": REGIME["label"], "score": REGIME["score"]}}]}, fh, indent=1)
    print("sample data written to docs/data/")


if __name__ == "__main__":
    main()
