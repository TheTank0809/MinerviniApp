"""Monthly Nifty-index pipeline (run by GitHub Actions at the end of each month, or
locally): the same Minervini Scorecard Engine v3 as pipeline.py, but for an index
CONSTITUENT LIST (currently Nifty 100) instead of a screener.in saved screen — every
stock in the index is tracked, not just the ones that pass a growth/momentum filter.

Fundamentals still come from screener.in (it covers every NSE/BSE-listed equity, not
just screen-filtered ones — Nifty 100 names are the largest, most liquid stocks in the
market, so coverage should be at least as good as the existing two screens). The stock
list itself comes from a static NSE constituents CSV (backend/data/universe_nifty100.csv)
instead of scraping a screen URL. Any single stock's fundamentals fetch failing is
caught and reported per-stock (data_quality / error banner), same as everywhere else in
this pipeline — it never aborts the whole run.

Bootstrap (first-ever) run: every stock is a bulk load, not a "new joiner" — joined_date
is left unset and nothing is tagged New, since being a Nifty 100 constituent on day one
isn't the same event as a stock newly qualifying for a screen. It also means the whole
~100-stock universe wants an LLM verdict in that one run, not spread over months — so
this pipeline's LLM budget defaults are sized for that (see config.yaml) rather than
reusing the weekly pipeline's slower-drip budget. Subsequent monthly runs behave
normally: a ticker entering the index for the first time (NSE's periodic rebalance)
gets a real joined_date and the New tag like any other screen.

LLM provider is configurable per index (config.yaml `llm_provider: "anthropic"` or
"deepseek") so you can run a cheap first pass on one provider and switch to another for
the ongoing recheck cadence — see llm.py.

Usage:  SCREENER_SESSIONID=... DEEPSEEK_API_KEY=... python backend/pipeline_nifty.py
        (or ANTHROPIC_API_KEY=..., depending on config.yaml's llm_provider)
"""

import csv
import os
import sys
import datetime
import traceback

import yaml

sys.path.insert(0, os.path.dirname(__file__))
import technicals as T
import scorecard as SC
import llm as LLM
from fundamentals import build_fundamental_payload
from screener_client import ScreenerClient
from pipeline import (  # reuse rather than reimplement — see pipeline.py
    load_json, save_json, today, update_history, load_rs_universe_symbols,
    load_rs_universe_industries, DATA_DIR,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPT_PATH = os.path.join(ROOT, "PROMPT.md")


def load_index_constituents(csv_path):
    """Same CSV shape as universe_india.csv (Company Name, Industry, Symbol, ...) —
    NSE publishes one of these per index at https://archives.nseindia.com/content/
    indices/ind_nifty<N>list.csv. Refresh manually every so often the same way
    universe_india.csv is (see pipeline.py's _load_rs_universe_csv docstring):
      curl -A "Mozilla/5.0" https://archives.nseindia.com/content/indices/ind_nifty100list.csv \\
        -o backend/data/universe_nifty100.csv
    """
    path = os.path.join(ROOT, csv_path)
    out = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            sym = (row.get("Symbol") or "").strip()
            if not sym:
                continue
            out.append({"code": sym, "name": (row.get("Company Name") or "").strip(),
                        "industry": (row.get("Industry") or "").strip()})
    return out


def process_index(client, universe_key, uni, index_cfg, settings):
    slug = index_cfg["slug"]
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
    # First-ever run for this index: every constituent is a bulk load, not a "new
    # joiner" — see module docstring. Detected the same way process_screen() detects
    # a per-stock new joiner (prior_by_code empty), just applied universe-wide.
    is_bootstrap = not prior_by_code

    print("== %s / %s ==" % (uni["label"], index_cfg["name"]))
    current = load_index_constituents(index_cfg["constituents_csv"])
    current_codes = {s["code"] for s in current}
    print("  index has %d constituents%s" % (len(current), " (bootstrap load)" if is_bootstrap else ""))

    new_codes = [] if is_bootstrap else [s for s in current if s["code"] not in prior_by_code]
    dropped_codes = [] if is_bootstrap else [c for c in prior_by_code if c not in current_codes]
    if not is_bootstrap:
        print("  new: %s" % ([s["code"] for s in new_codes] or "none"))
        print("  dropped: %s" % (dropped_codes or "none"))

    for code in dropped_codes:
        rec = prior_by_code.pop(code)
        rec["dropped_date"] = today()
        rec["frozen"] = True
        dropped["stocks"].insert(0, rec)

    # ---- download OHLCV for the whole index + benchmark ---------------------
    suffixes = uni.get("yahoo_suffixes", [".NS", ".BO"])
    sym_map = {s["code"]: T.yahoo_symbol_candidates(s["code"], suffixes) for s in current}
    primary = [v[0] for v in sym_map.values()]
    bench_sym = settings.get("benchmark_index", "^CRSLDX")
    hist = T.download_history(primary + [bench_sym, settings.get("benchmark_fallback", "^NSEI")],
                              years=settings.get("history_years", 2))
    misses = [c for c, cands in sym_map.items() if cands[0] not in hist and len(cands) > 1]
    if misses:
        retry = T.download_history([sym_map[c][1] for c in misses])
        hist.update(retry)

    def df_for(code):
        for cand in sym_map[code]:
            if cand in hist:
                return hist[cand]
        return None

    # ---- market regime (Stage 0) — its own breadth read against this index, not
    # the weekly pipeline's tracked list, since they're different universes ------
    bench_df = hist.get(bench_sym)
    if bench_df is None or float(bench_df["Volume"].tail(25).sum()) == 0:
        fb = hist.get(settings.get("benchmark_fallback", "^NSEI"))
        bench_df = fb if fb is not None else bench_df
    universe_dfs = {c: df_for(c) for c in sym_map if df_for(c) is not None}
    if bench_df is None:
        raise RuntimeError("Benchmark data unavailable; aborting run.")
    regime = T.market_regime(bench_df, universe_dfs)
    print("  regime: %s (%d/6)" % (regime["label"], regime["score"]))

    # ---- RS percentiles, ranked against the same broad reference as India-S -----
    rs_raw = {c: T._rs_raw(df["Close"]) for c, df in universe_dfs.items()}
    rs_universe_stats = {"source": None, "attempted": 0, "succeeded": 0, "used": False}
    broad_symbols = load_rs_universe_symbols(uni)
    if broad_symbols:
        min_needed = settings.get("rs_universe_min_symbols", 100)
        broad_hist, attempted, succeeded = T.download_broad_universe_history(
            broad_symbols, years=settings.get("rs_universe_lookback_years", 1))
        rs_universe_stats = {"source": uni.get("rs_universe_label", "broad universe"),
                              "attempted": attempted, "succeeded": succeeded, "used": False}
        if succeeded >= min_needed:
            combined_raw = {c: T._rs_raw(df["Close"]) for c, df in broad_hist.items()}
            combined_raw.update(rs_raw)
            rs_raw = combined_raw
            rs_universe_stats["used"] = True
            print("  RS universe: %d/%d %s stocks — ranking against the broad universe" %
                  (succeeded, attempted, rs_universe_stats["source"]))
        else:
            print("  RS universe: only %d/%d %s stocks fetched (need >=%d) — "
                  "falling back to tracked-only ranking" %
                  (succeeded, attempted, rs_universe_stats["source"], min_needed))
    rs_pct = T.rs_percentiles(rs_raw)

    # ---- fetch fundamentals + technicals for every constituent -------------
    name_by_code = {s["code"]: s["name"] for s in current}
    tech_by_code, fund_by_code, fetch_errors = {}, {}, {}
    for s in current:
        code = s["code"]
        try:
            df = df_for(code)
            if df is None:
                raise RuntimeError("no OHLCV data on Yahoo Finance")
            prior_rec = prior_by_code.get(code)
            prior_scorecard = (prior_rec or {}).get("scorecard")
            prev_rs = None
            if prior_rec:
                prev_rs = ((prior_scorecard or {}).get("technicals") or {}).get("rs_percentile")
            tech_by_code[code] = T.build_technical_payload(
                df, rs_percentile=rs_pct.get(code), rs_percentile_prev=prev_rs)
            fund_by_code[code] = build_fundamental_payload(client.fetch_company(code))
        except Exception as exc:
            fetch_errors[code] = exc

    # ---- industry-group RS — same approach as process_screen(), but the index CSV
    # already carries each constituent's own Industry column directly, so no
    # fundamentals-sector fallback or symbol cross-reference is needed for the
    # tracked side. -------------------------------------------------------------
    industry_by_symbol = load_rs_universe_industries(uni) if rs_universe_stats["used"] else {}
    tracked_industry = {s["code"]: s["industry"] for s in current if s.get("industry")}
    needed_industries = set(tracked_industry.values())
    use_broad_groups = bool(industry_by_symbol and needed_industries)

    sector_groups = {}
    if use_broad_groups:
        for sym, industry in industry_by_symbol.items():
            if industry in needed_industries and rs_pct.get(sym) is not None:
                sector_groups.setdefault(industry, []).append(sym)
    else:
        for code, industry in tracked_industry.items():
            if rs_pct.get(code) is not None:
                sector_groups.setdefault(industry, []).append(code)

    ranked_sectors = sorted(
        (sec for sec, peers in sector_groups.items() if len(peers) >= 3),
        key=lambda sec: -(sum(rs_pct[p] for p in sector_groups[sec]) / len(sector_groups[sec])))
    quartile_of, rank_of = {}, {}
    for i, sec in enumerate(ranked_sectors):
        quartile_of[sec] = min(4, int(i / len(ranked_sectors) * 4) + 1)
        ordered = sorted(sector_groups[sec], key=lambda p: -rs_pct[p])
        rank_of[sec] = {p: (r, len(ordered)) for r, p in enumerate(ordered, start=1)}

    for code, industry in tracked_industry.items():
        if industry not in quartile_of or code not in tech_by_code:
            continue
        peer_key = sym_map.get(code, [None])[0] if use_broad_groups else code
        rank, of = rank_of[industry].get(peer_key, (None, None))
        if rank is None:
            continue
        tech_by_code[code]["industry_group_rs_quartile"] = quartile_of[industry]
        tech_by_code[code]["industry_sector"] = industry
        tech_by_code[code]["group_leadership_rank"] = rank
        tech_by_code[code]["group_leadership_of"] = of

    # ---- evaluate every constituent -------------------------------------------
    llm_provider = index_cfg.get("llm_provider", "anthropic")
    llm_model = index_cfg.get("llm_model") or LLM.DEFAULT_MODEL.get(llm_provider)
    llm_budget = index_cfg.get("llm_max_new_stocks_per_run", 100)
    existing_recheck_budget = index_cfg.get("llm_max_existing_catalyst_checks_per_run", 20)
    recheck_days = settings.get("llm_catalyst_recheck_days", 30)
    out_stocks, errors = [], []
    for s in current:
        code = s["code"]
        prior_rec = prior_by_code.get(code)
        is_new = (not is_bootstrap) and prior_rec is None
        try:
            if code in fetch_errors:
                raise fetch_errors[code]
            tech = tech_by_code[code]
            fund = fund_by_code[code]
            prior_scorecard = (prior_rec or {}).get("scorecard")
            prior_llm_checks = (prior_scorecard or {}).get("llm_checks") or {}
            llm_out, fresh_check = None, False

            wants_verdict = is_new or is_bootstrap
            if wants_verdict and LLM.llm_available(llm_provider) and llm_budget > 0:
                pre = SC.evaluate(code, name_by_code[code], tech, fund, regime, settings,
                                  mode="FULL", prior=None)
                llm_out = LLM.synthesize_verdict(pre, tech, fund, PROMPT_PATH,
                                                  model=llm_model, provider=llm_provider)
                llm_budget -= 1
                fresh_check = llm_out is not None
            elif not wants_verdict and LLM.llm_available(llm_provider) and existing_recheck_budget > 0:
                last_checked = prior_llm_checks.get("checked_date")
                stale = (not last_checked) or (
                    (datetime.date.today() - datetime.date.fromisoformat(last_checked)).days >= recheck_days)
                if stale:
                    llm_out = LLM.check_catalyst_and_governance(
                        code, name_by_code[code], PROMPT_PATH, model=llm_model, provider=llm_provider)
                    existing_recheck_budget -= 1
                    fresh_check = llm_out is not None

            if llm_out is None and prior_llm_checks:
                llm_out = prior_llm_checks  # carry forward last known H3/governance result

            card = SC.evaluate(
                code, name_by_code[code], tech, fund, regime, settings,
                mode="FULL" if wants_verdict else "MONTHLY",
                prior=prior_scorecard,
                llm_verdict=llm_out)
            if card.get("llm_checks") is not None:
                card["llm_checks"]["checked_date"] = (
                    today() if fresh_check else prior_llm_checks.get("checked_date"))

            rec = {
                "ticker": code,
                "name": name_by_code[code],
                # Bootstrap load: no joined_date — a constituent list doesn't have a
                # "joined" moment the way a screener pick does. A genuine new entrant
                # in a later monthly run (NSE rebalance) gets a real one below.
                "joined_date": None if is_bootstrap else (prior_rec["joined_date"] if prior_rec else today()),
                "last_updated": today(),
                "scorecard": card,
            }
            out_stocks.append(rec)
            score_total = (card.get("scores") or {}).get("total")
            if score_total is not None:
                update_history(code, today(), score_total, card.get("action_bucket"),
                                (card.get("technicals") or {}).get("price"))
            print("  %-12s %s score=%s %s" % (
                code, card["status"], (card.get("scores") or {}).get("total", "-"), card["action_bucket"]))
        except Exception as exc:
            errors.append({"ticker": code, "error": str(exc)})
            print("  %-12s ERROR: %s" % (code, exc))
            traceback.print_exc(limit=1)
            if prior_rec:
                prior_rec["last_error"] = str(exc)
                out_stocks.append(prior_rec)

    out_stocks.sort(key=lambda r: -((r["scorecard"].get("scores") or {}).get("total") or 0))

    run_summary = {
        "run_date": today(), "mode": "BOOTSTRAP" if is_bootstrap else "MONTHLY",
        "regime": regime,
        "counts": {"active": len(out_stocks), "new": len(new_codes),
                   "dropped": len(dropped_codes), "errors": len(errors)},
        "new_tickers": [s["code"] for s in new_codes],
        "dropped_tickers": dropped_codes,
        "actionable_now": [r["ticker"] for r in out_stocks
                           if r["scorecard"]["action_bucket"] == "ACTIONABLE_NOW"],
        "errors": errors,
        "llm": {"enabled": LLM.llm_available(llm_provider), "model": llm_model, "provider": llm_provider},
        "rs_universe": rs_universe_stats,
    }
    runs["runs"] = [run_summary] + runs["runs"][:11]  # 12 months of history is plenty

    save_json(active_path, {"generated_at": today(), "stocks": out_stocks})
    save_json(dropped_path, dropped)
    save_json(runs_path, runs)
    return {"universe": universe_key, "screen": slug, "label": index_cfg["name"],
            "short": index_cfg.get("short") or index_cfg["name"][:2].upper(),
            "universe_label": uni["label"], "counts": run_summary["counts"],
            "regime": {"label": regime["label"], "score": regime["score"]}}


def main():
    # Preserve the weekly pipeline's manifest entries — see pipeline.py's main() for
    # the matching half of this merge.
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
        client = ScreenerClient(delay=settings.get("request_delay_seconds", 1.2))

        for uni_key, uni in cfg.get("universes", {}).items():
            if not uni.get("enabled"):
                continue
            for index_cfg in uni.get("indices", []):
                if not index_cfg.get("enabled"):
                    continue
                try:
                    manifest["screens"].append(process_index(client, uni_key, uni, index_cfg, settings))
                except Exception as exc:
                    failures += 1
                    print("INDEX FAILED (%s): %s" % (index_cfg["name"], exc))
                    traceback.print_exc()
                    manifest["screens"].append(
                        {"universe": uni_key, "screen": index_cfg["slug"], "label": index_cfg["name"],
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
