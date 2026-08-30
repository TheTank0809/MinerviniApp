"""Global pipeline (run by GitHub Actions monthly, or locally): same Minervini
Scorecard Engine v3 as the other pipelines, but the tracked universe and
fundamentals both come from a TradingView growth-screen query instead of
screener.in — see tradingview_client.py and config.yaml's `global` universe block
for exactly what the screen filters on (EPS growth, revenue growth, D/E, primary
listings only, excluding whichever countries are configured).

Unlike India-Nifty's fixed index-constituent list, this IS a threshold-based
screen re-evaluated every run, same as India-S's screener.in screens — a company
genuinely joins when it starts clearing the bar and drops when it stops, so
new/dropped tracking (joined_date, "Left the screen", frozen records) works the
same way it does for India-S, not the bootstrap-suppressed India-Nifty pattern.

Technicals still come from Yahoo Finance, mapped from each company's TradingView
exchange to a Yahoo ticker suffix (see EXCHANGE_TO_YAHOO_SUFFIX below) — coverage
is necessarily partial for a truly global universe; anything on an unmapped
exchange is skipped with a clear per-stock error, same as any other data gap
elsewhere in this app, rather than the whole run failing.

RS percentile and industry-group ranking are both computed within the tracked
Global universe itself — there's no broad external reference list the way
India's Nifty 500 CSV serves India-S/India-Nifty, so this always uses the
tracked-only fallback already built into technicals.py/the RS system.

Known limitations (v1 — see the module docstring in fundamentals_tradingview.py
for the fundamentals side): the market regime benchmark is the S&P 500 (^GSPC) —
a reasonable single proxy, not a true "whole world" index; the frontend still
displays every price with a "Rs" symbol regardless of the stock's real currency
(TradingView reports it per-stock, just not wired into the UI yet).

Usage:  DEEPSEEK_API_KEY=... python backend/pipeline_global.py
"""

import os
import sys
import datetime
import traceback

import yaml

sys.path.insert(0, os.path.dirname(__file__))
import technicals as T
import scorecard as SC
import llm as LLM
import tradingview_client as TV
from fundamentals_tradingview import build_fundamental_payload
from pipeline import load_json, save_json, today, update_history, DATA_DIR

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPT_PATH = os.path.join(ROOT, "PROMPT.md")

# TradingView exchange code -> Yahoo Finance ticker suffix. Built from the actual
# exchanges present in a real query at the configured market-cap floor (verified
# during development), not a guess at global coverage — expand as needed; an
# unmapped exchange just means that stock gets skipped with a clear error, not a
# broken run. Keyed by (exchange, country) for exchanges that need the country to
# disambiguate (Euronext spans several countries with different Yahoo suffixes).
EXCHANGE_TO_YAHOO_SUFFIX = {
    "NASDAQ": "", "NYSE": "", "AMEX": "",
    "TSX": ".TO", "TSXV": ".V",
    "HKEX": ".HK",
    "TSE": ".T",
    "ASX": ".AX",
    "XETR": ".DE",
    "KRX": ".KS",
    "TWSE": ".TW", "TPEX": ".TWO",
    "OMXSTO": ".ST",
    "MIL": ".MI",
    "JSE": ".JO",
    "SET": ".BK",
    "BIST": ".IS",
    "LSE": ".L", "LSIN": ".L",
    "SSE": ".SS", "SZSE": ".SZ",
    "SIX": ".SW",
    "TASE": ".TA",
}
EXCHANGE_COUNTRY_TO_YAHOO_SUFFIX = {
    ("EURONEXT", "France"): ".PA",
    ("EURONEXT", "Netherlands"): ".AS",
    ("EURONEXT", "Belgium"): ".BR",
    ("EURONEXT", "Portugal"): ".LS",
}


def yahoo_symbol_for(row):
    exch = row.get("exchange")
    # TradingView uses an underscore for share-class tickers (e.g. "INDU_A");
    # Yahoo Finance wants a hyphen there instead ("INDU-A") — verified against a
    # real delisted-looking miss that turned out to just be this naming mismatch.
    code = (row.get("name") or "").replace("_", "-")
    suffix = EXCHANGE_COUNTRY_TO_YAHOO_SUFFIX.get((exch, row.get("country")))
    if suffix is None:
        suffix = EXCHANGE_TO_YAHOO_SUFFIX.get(exch)
    if suffix is None:
        return None
    return code + suffix


def compute_5y_revenue_cagr(row):
    fy = row.get("total_revenue_fy_h")
    if not fy or len(fy) < 6 or not fy[5] or fy[5] <= 0 or not fy[0] or fy[0] <= 0:
        return None
    return ((fy[0] / fy[5]) ** (1.0 / 5.0) - 1.0) * 100.0


def process_global(universe_key, uni, gcfg, settings):
    slug = gcfg["slug"]
    sdir = os.path.join(DATA_DIR, universe_key, slug)
    active_path = os.path.join(sdir, "active.json")
    dropped_path = os.path.join(sdir, "dropped.json")
    runs_path = os.path.join(sdir, "runs.json")

    active = load_json(active_path, {"stocks": []})
    dropped = load_json(dropped_path, {"stocks": []})
    runs = load_json(runs_path, {"runs": []})
    if active.get("sample"):
        active = {"stocks": []}
    prior_by_code = {s["ticker"]: s for s in active["stocks"]}

    print("== %s / %s ==" % (uni["label"], gcfg["name"]))
    raw_rows = TV.fetch_universe(
        exclude_countries=gcfg.get("exclude_countries", []),
        min_market_cap=gcfg.get("min_market_cap", 10_000_000_000),
        min_eps_growth_pct=gcfg.get("min_eps_growth_pct", 25),
        max_debt_to_equity=gcfg.get("max_debt_to_equity", 0.1),
        limit=gcfg.get("scan_limit", 1000),
    )
    min_rev_cagr = gcfg.get("min_revenue_cagr_5y_pct", 25)
    rows = [r for r in raw_rows if (compute_5y_revenue_cagr(r) or -999) >= min_rev_cagr]
    print("  TradingView: %d matched base filters, %d clear the 5y revenue CAGR bar too" %
          (len(raw_rows), len(rows)))

    row_by_code = {r["name"]: r for r in rows}
    current_codes = set(row_by_code.keys())
    new_codes = [c for c in current_codes if c not in prior_by_code]
    dropped_codes = [c for c in prior_by_code if c not in current_codes]
    print("  new: %s" % (new_codes or "none"))
    print("  dropped: %s" % (dropped_codes or "none"))

    for code in dropped_codes:
        rec = prior_by_code.pop(code)
        rec["dropped_date"] = today()
        rec["frozen"] = True
        dropped["stocks"].insert(0, rec)

    # ---- technicals via Yahoo Finance -----------------------------------
    sym_by_code, unmapped = {}, []
    for code, row in row_by_code.items():
        sym = yahoo_symbol_for(row)
        if sym:
            sym_by_code[code] = sym
        else:
            unmapped.append((code, row.get("exchange")))
    if unmapped:
        print("  no Yahoo Finance mapping for %d stocks (exchange not covered yet): %s" %
              (len(unmapped), unmapped[:10]))

    bench_sym = settings.get("global_benchmark_index", "^GSPC")
    hist = T.download_history(list(sym_by_code.values()) + [bench_sym],
                              years=settings.get("history_years", 2))

    def df_for(code):
        sym = sym_by_code.get(code)
        return hist.get(sym) if sym else None

    bench_df = hist.get(bench_sym)
    universe_dfs = {c: df_for(c) for c in sym_by_code if df_for(c) is not None}
    if bench_df is None:
        raise RuntimeError("Benchmark data (%s) unavailable; aborting run." % bench_sym)
    regime = T.market_regime(bench_df, universe_dfs)
    print("  regime: %s (%d/6)" % (regime["label"], regime["score"]))

    # RS percentile: ranked within the tracked universe itself — no broad external
    # reference list exists for "global" the way India's Nifty 500 CSV does.
    rs_raw = {c: T._rs_raw(df["Close"]) for c, df in universe_dfs.items()}
    rs_pct = T.rs_percentiles(rs_raw)

    # Industry-group RS: same tracked-only fallback pipeline.py already uses when
    # no broad reference is configured — group by TradingView's own sector field.
    tracked_sector = {c: row_by_code[c].get("sector") for c in universe_dfs if row_by_code[c].get("sector")}
    sector_groups = {}
    for c, sec in tracked_sector.items():
        if rs_pct.get(c) is not None:
            sector_groups.setdefault(sec, []).append(c)
    ranked_sectors = sorted(
        (sec for sec, peers in sector_groups.items() if len(peers) >= 3),
        key=lambda sec: -(sum(rs_pct[p] for p in sector_groups[sec]) / len(sector_groups[sec])))
    quartile_of, rank_of = {}, {}
    for i, sec in enumerate(ranked_sectors):
        quartile_of[sec] = min(4, int(i / len(ranked_sectors) * 4) + 1)
        ordered = sorted(sector_groups[sec], key=lambda p: -rs_pct[p])
        rank_of[sec] = {p: (r, len(ordered)) for r, p in enumerate(ordered, start=1)}

    tech_by_code, fund_by_code, fetch_errors = {}, {}, {}
    for code, row in row_by_code.items():
        try:
            df = df_for(code)
            if df is None:
                if code not in sym_by_code:
                    raise RuntimeError(
                        "exchange %r has no Yahoo Finance suffix mapping yet" % row.get("exchange"))
                raise RuntimeError(
                    "mapped to %r but Yahoo Finance returned no data for it" % sym_by_code[code])
            prior_rec = prior_by_code.get(code)
            prior_scorecard = (prior_rec or {}).get("scorecard")
            prev_rs = None
            if prior_rec:
                prev_rs = ((prior_scorecard or {}).get("technicals") or {}).get("rs_percentile")
            tech = T.build_technical_payload(df, rs_percentile=rs_pct.get(code), rs_percentile_prev=prev_rs)
            sec = tracked_sector.get(code)
            if sec in quartile_of:
                rank, of = rank_of[sec].get(code, (None, None))
                if rank is not None:
                    tech["industry_group_rs_quartile"] = quartile_of[sec]
                    tech["industry_sector"] = sec
                    tech["group_leadership_rank"] = rank
                    tech["group_leadership_of"] = of
            tech_by_code[code] = tech
            fund_by_code[code] = build_fundamental_payload(row)
        except Exception as exc:
            fetch_errors[code] = exc

    # ---- evaluate ---------------------------------------------------------
    llm_provider = gcfg.get("llm_provider", "anthropic")
    llm_model = gcfg.get("llm_model") or LLM.DEFAULT_MODEL.get(llm_provider)
    llm_budget = gcfg.get("llm_max_new_stocks_per_run", 100)
    existing_recheck_budget = gcfg.get("llm_max_existing_catalyst_checks_per_run", 20)
    recheck_days = settings.get("llm_catalyst_recheck_days", 30)
    out_stocks, errors = [], []
    for code, row in row_by_code.items():
        prior_rec = prior_by_code.get(code)
        is_new = prior_rec is None
        try:
            if code in fetch_errors:
                raise fetch_errors[code]
            tech, fund = tech_by_code[code], fund_by_code[code]
            prior_scorecard = (prior_rec or {}).get("scorecard")
            prior_llm_checks = (prior_scorecard or {}).get("llm_checks") or {}
            llm_out, fresh_check = None, False

            if is_new and LLM.llm_available(llm_provider) and llm_budget > 0:
                pre = SC.evaluate(code, row.get("description") or code, tech, fund, regime, settings,
                                  mode="FULL", prior=None)
                llm_out = LLM.synthesize_verdict(pre, tech, fund, PROMPT_PATH, model=llm_model, provider=llm_provider)
                llm_budget -= 1
                fresh_check = llm_out is not None
            elif not is_new and LLM.llm_available(llm_provider) and existing_recheck_budget > 0:
                last_checked = prior_llm_checks.get("checked_date")
                stale = (not last_checked) or (
                    (datetime.date.today() - datetime.date.fromisoformat(last_checked)).days >= recheck_days)
                if stale:
                    llm_out = LLM.check_catalyst_and_governance(
                        code, row.get("description") or code, PROMPT_PATH, model=llm_model, provider=llm_provider)
                    existing_recheck_budget -= 1
                    fresh_check = llm_out is not None

            if llm_out is None and prior_llm_checks:
                llm_out = prior_llm_checks

            card = SC.evaluate(code, row.get("description") or code, tech, fund, regime, settings,
                                mode="FULL" if is_new else "MONTHLY",
                                prior=prior_scorecard, llm_verdict=llm_out)
            if card.get("llm_checks") is not None:
                card["llm_checks"]["checked_date"] = (
                    today() if fresh_check else prior_llm_checks.get("checked_date"))

            rec = {
                "ticker": code, "name": row.get("description") or code,
                "joined_date": prior_rec["joined_date"] if prior_rec else today(),
                "last_updated": today(),
                "scorecard": card,
            }
            out_stocks.append(rec)
            score_total = (card.get("scores") or {}).get("total")
            if score_total is not None:
                update_history(code, today(), score_total, card.get("action_bucket"),
                                (card.get("technicals") or {}).get("price"))
            print("  %-8s %s score=%s %s" % (
                code, card["status"], (card.get("scores") or {}).get("total", "-"), card["action_bucket"]))
        except Exception as exc:
            errors.append({"ticker": code, "error": str(exc)})
            print("  %-8s ERROR: %s" % (code, exc))
            if prior_rec:
                prior_rec["last_error"] = str(exc)
                out_stocks.append(prior_rec)

    out_stocks.sort(key=lambda r: -((r["scorecard"].get("scores") or {}).get("total") or 0))

    run_summary = {
        "run_date": today(), "regime": regime,
        "counts": {"active": len(out_stocks), "new": len(new_codes),
                   "dropped": len(dropped_codes), "errors": len(errors)},
        "new_tickers": new_codes, "dropped_tickers": dropped_codes,
        "actionable_now": [r["ticker"] for r in out_stocks
                           if r["scorecard"]["action_bucket"] == "ACTIONABLE_NOW"],
        "errors": errors,
        "llm": {"enabled": LLM.llm_available(llm_provider), "model": llm_model, "provider": llm_provider},
    }
    runs["runs"] = [run_summary] + runs["runs"][:11]

    save_json(active_path, {"generated_at": today(), "stocks": out_stocks})
    save_json(dropped_path, dropped)
    save_json(runs_path, runs)
    return {"universe": universe_key, "screen": slug, "label": gcfg["name"],
            "short": gcfg.get("short") or gcfg["name"][:2].upper(),
            "universe_label": uni["label"], "counts": run_summary["counts"],
            "regime": {"label": regime["label"], "score": regime["score"]}}


def main():
    prior_manifest = load_json(os.path.join(DATA_DIR, "manifest.json"), {})
    prior_screens = prior_manifest.get("screens", [])
    manifest = dict(prior_manifest) if prior_manifest else \
        {"generated_at": None, "sample": False, "screens": []}
    manifest["generated_at"] = datetime.datetime.utcnow().isoformat() + "Z"
    manifest["screens"] = []
    failures = 0
    try:
        with open(os.path.join(ROOT, "backend", "config.yaml")) as fh:
            cfg = yaml.safe_load(fh)
        settings = cfg.get("settings", {})

        for uni_key, uni in cfg.get("universes", {}).items():
            if not uni.get("enabled"):
                continue
            for gcfg in uni.get("global_screens", []):
                if not gcfg.get("enabled"):
                    continue
                try:
                    manifest["screens"].append(process_global(uni_key, uni, gcfg, settings))
                except Exception as exc:
                    failures += 1
                    print("GLOBAL SCREEN FAILED (%s): %s" % (gcfg["name"], exc))
                    traceback.print_exc()
                    manifest["screens"].append(
                        {"universe": uni_key, "screen": gcfg["slug"], "label": gcfg["name"],
                         "universe_label": uni["label"], "error": str(exc)})
    except Exception as exc:
        failures += 1
        print("PIPELINE FAILED: %s" % exc)
        traceback.print_exc()
        manifest["fatal_error"] = str(exc)

    existing_keys = {(s.get("universe"), s.get("screen")) for s in manifest["screens"]}
    for s in prior_screens:
        key = (s.get("universe"), s.get("screen"))
        if key not in existing_keys:
            manifest["screens"].append(s)
            existing_keys.add(key)

    save_json(os.path.join(DATA_DIR, "manifest.json"), manifest)
    print("done. manifest written.")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
