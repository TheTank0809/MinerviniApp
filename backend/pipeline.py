"""Weekly pipeline (run by GitHub Actions every Sunday, or locally):

1. Pull each enabled screen from screener.in.
2. Diff against tracked stocks: new joiners get a joined-date + MODE=FULL scorecard
   (with optional LLM verdict); existing stocks get a MODE=WEEKLY delta update.
3. Stocks that left the screen move to dropped.json with a dropped-date; their last
   scorecard is frozen and never updated again.
4. Write everything to docs/data/ where the GitHub Pages site reads it.

Usage:  SCREENER_SESSIONID=... python backend/pipeline.py
"""

import json
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
from screener_client import ScreenerClient, ScreenerError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "docs", "data")
PROMPT_PATH = os.path.join(ROOT, "PROMPT.md")


def load_json(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=1, default=str)


def today():
    return str(datetime.date.today())


def process_screen(client, universe_key, uni, screen, settings):
    if not screen.get("url"):
        raise ScreenerError(
            "Screen '%s' has no url set in backend/config.yaml. Open the screen on "
            "screener.in and paste its exact address-bar URL into config.yaml." % screen["name"])
    slug = screen["slug"]
    sdir = os.path.join(DATA_DIR, universe_key, slug)
    active_path = os.path.join(sdir, "active.json")
    dropped_path = os.path.join(sdir, "dropped.json")
    runs_path = os.path.join(sdir, "runs.json")

    active = load_json(active_path, {"stocks": []})
    dropped = load_json(dropped_path, {"stocks": []})
    runs = load_json(runs_path, {"runs": []})
    # discard any sample/demo data from the initial commit
    if active.get("sample"):
        active = {"stocks": []}
    if dropped.get("sample"):
        dropped = {"stocks": []}
    prior_by_code = {s["ticker"]: s for s in active["stocks"]}

    print("== %s / %s ==" % (uni["label"], screen["name"]))
    current = client.fetch_screen_stocks(screen["url"], screen_name=screen["name"])
    current_codes = {s["code"] for s in current}
    print("  screen returned %d stocks" % len(current))

    new_codes = [s for s in current if s["code"] not in prior_by_code]
    dropped_codes = [c for c in prior_by_code if c not in current_codes]
    print("  new: %s" % ([s["code"] for s in new_codes] or "none"))
    print("  dropped: %s" % (dropped_codes or "none"))

    # ---- move drop-outs (freeze their last scorecard, no further updates) ----
    for code in dropped_codes:
        rec = prior_by_code.pop(code)
        rec["dropped_date"] = today()
        rec["frozen"] = True
        dropped["stocks"].insert(0, rec)

    # ---- download OHLCV for the whole tracked universe + benchmark ----------
    suffixes = uni.get("yahoo_suffixes", [".NS", ".BO"])
    sym_map = {}
    for s in current:
        sym_map[s["code"]] = T.yahoo_symbol_candidates(s["code"], suffixes)
    primary = [v[0] for v in sym_map.values()]
    bench_sym = settings.get("benchmark_index", "^CRSLDX")
    hist = T.download_history(primary + [bench_sym, settings.get("benchmark_fallback", "^NSEI")],
                              years=settings.get("history_years", 2))
    # retry misses on fallback suffix
    misses = [c for c, cands in sym_map.items() if cands[0] not in hist and len(cands) > 1]
    if misses:
        retry = T.download_history([sym_map[c][1] for c in misses])
        hist.update(retry)

    def df_for(code):
        for cand in sym_map[code]:
            if cand in hist:
                return hist[cand]
        return None

    # ---- market regime (Stage 0, once per run) ------------------------------
    bench_df = hist.get(bench_sym)
    if bench_df is None or float(bench_df["Volume"].tail(25).sum()) == 0:
        fb = hist.get(settings.get("benchmark_fallback", "^NSEI"))
        bench_df = fb if fb is not None else bench_df
    universe_dfs = {c: df_for(c) for c in sym_map if df_for(c) is not None}
    if bench_df is None:
        raise RuntimeError("Benchmark data unavailable; aborting run.")
    regime = T.market_regime(bench_df, universe_dfs)
    print("  regime: %s (%d/6)" % (regime["label"], regime["score"]))

    # ---- RS percentiles across the tracked universe -------------------------
    rs_raw = {c: T._rs_raw(df["Close"]) for c, df in universe_dfs.items()}
    rs_pct = T.rs_percentiles(rs_raw)

    # ---- fetch fundamentals + technicals for every stock first --------------
    # (industry-group RS below needs every stock's sector + RS percentile known
    # before any single stock can be scored, so the fetch has to happen in its
    # own pass ahead of scoring rather than inline in the scoring loop)
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

    # ---- industry-group RS: a trade-off proxy ranked only against the stocks
    # this screen already tracks (currently ~a hundred), never the full market —
    # H1/H2 were previously always unverified for lack of a free full-market
    # classification. A sector needs >=3 tracked members to rank meaningfully;
    # smaller ones are left unverified rather than ranked off 1-2 stocks. --------
    sector_groups = {}
    for code, fund in fund_by_code.items():
        sec = fund.get("sector")
        if sec and rs_pct.get(code) is not None:
            sector_groups.setdefault(sec, []).append(code)
    ranked_sectors = sorted(
        (sec for sec, codes in sector_groups.items() if len(codes) >= 3),
        key=lambda sec: -(sum(rs_pct[c] for c in sector_groups[sec]) / len(sector_groups[sec])))
    for i, sec in enumerate(ranked_sectors):
        quartile = min(4, int(i / len(ranked_sectors) * 4) + 1)
        ordered = sorted(sector_groups[sec], key=lambda c: -rs_pct[c])
        for rank, peer_code in enumerate(ordered, start=1):
            tech_by_code[peer_code]["industry_group_rs_quartile"] = quartile
            tech_by_code[peer_code]["industry_sector"] = sec
            tech_by_code[peer_code]["group_leadership_rank"] = rank
            tech_by_code[peer_code]["group_leadership_of"] = len(ordered)

    # ---- evaluate every current stock ---------------------------------------
    llm_model = settings.get("llm_model", "claude-sonnet-5")
    llm_budget = settings.get("llm_max_new_stocks_per_run", 25)
    existing_recheck_budget = settings.get("llm_max_existing_catalyst_checks_per_run", 10)
    recheck_days = settings.get("llm_catalyst_recheck_days", 30)
    out_stocks, errors = [], []
    for s in current:
        code = s["code"]
        prior_rec = prior_by_code.get(code)
        is_new = prior_rec is None
        try:
            if code in fetch_errors:
                raise fetch_errors[code]
            tech = tech_by_code[code]
            fund = fund_by_code[code]
            prior_scorecard = (prior_rec or {}).get("scorecard")

            prior_llm_checks = (prior_scorecard or {}).get("llm_checks") or {}
            llm_out, fresh_check = None, False
            if is_new and LLM.llm_available() and llm_budget > 0:
                pre = SC.evaluate(code, name_by_code[code], tech, fund, regime, settings,
                                  mode="FULL", prior=None)
                llm_out = LLM.synthesize_verdict(pre, tech, fund, PROMPT_PATH, model=llm_model)
                llm_budget -= 1
                fresh_check = llm_out is not None
            elif not is_new and LLM.llm_available() and existing_recheck_budget > 0:
                last_checked = prior_llm_checks.get("checked_date")
                stale = (not last_checked) or (
                    (datetime.date.today() - datetime.date.fromisoformat(last_checked)).days >= recheck_days)
                if stale:
                    llm_out = LLM.check_catalyst_and_governance(
                        code, name_by_code[code], PROMPT_PATH, model=llm_model)
                    existing_recheck_budget -= 1
                    fresh_check = llm_out is not None

            if llm_out is None and prior_llm_checks:
                llm_out = prior_llm_checks  # carry forward last known H3/governance result

            card = SC.evaluate(
                code, name_by_code[code], tech, fund, regime, settings,
                mode="FULL" if is_new else "WEEKLY",
                prior=prior_scorecard,
                llm_verdict=llm_out)
            if card.get("llm_checks") is not None:
                card["llm_checks"]["checked_date"] = (
                    today() if fresh_check else prior_llm_checks.get("checked_date"))

            rec = {
                "ticker": code,
                "name": name_by_code[code],
                "joined_date": prior_rec["joined_date"] if prior_rec else today(),
                "last_updated": today(),
                "scorecard": card,
            }
            out_stocks.append(rec)
            print("  %-12s %s score=%s %s" % (
                code, card["status"],
                (card.get("scores") or {}).get("total", "-"), card["action_bucket"]))
        except Exception as exc:
            errors.append({"ticker": code, "error": str(exc)})
            print("  %-12s ERROR: %s" % (code, exc))
            traceback.print_exc(limit=1)
            if prior_rec:  # keep last good record rather than losing the stock
                prior_rec["last_error"] = str(exc)
                out_stocks.append(prior_rec)

    out_stocks.sort(key=lambda r: -((r["scorecard"].get("scores") or {}).get("total") or 0))

    # ---- run-level summary ---------------------------------------------------
    alerts = []
    for r in out_stocks:
        for a in (r["scorecard"].get("delta") or {}).get("alerts", []):
            alerts.append({"ticker": r["ticker"], "alert": a})
    prev_regime = (runs["runs"][0]["regime"]["label"] if runs["runs"] else None)
    if prev_regime and prev_regime != regime["label"]:
        alerts.append({"ticker": "*", "alert": "REGIME_CHANGE %s -> %s" % (prev_regime, regime["label"])})

    run_summary = {
        "run_date": today(), "mode": "WEEKLY",
        "regime": regime,
        "counts": {"active": len(out_stocks), "new": len(new_codes),
                   "dropped": len(dropped_codes), "errors": len(errors)},
        "new_tickers": [s["code"] for s in new_codes],
        "dropped_tickers": dropped_codes,
        "actionable_now": [r["ticker"] for r in out_stocks
                           if r["scorecard"]["action_bucket"] == "ACTIONABLE_NOW"],
        "alerts": alerts, "errors": errors,
        "llm": {"enabled": LLM.llm_available(), "model": settings.get("llm_model")},
    }
    runs["runs"] = [run_summary] + runs["runs"][:51]

    save_json(active_path, {"generated_at": today(), "stocks": out_stocks})
    save_json(dropped_path, dropped)
    save_json(runs_path, runs)
    return {"universe": universe_key, "screen": slug, "label": screen["name"],
            "short": screen.get("short") or screen["name"][:2].upper(),
            "universe_label": uni["label"], "counts": run_summary["counts"],
            "regime": {"label": regime["label"], "score": regime["score"]}}


def main():
    manifest = {"generated_at": datetime.datetime.utcnow().isoformat() + "Z",
                "sample": False, "screens": []}
    failures = 0
    try:
        with open(os.path.join(ROOT, "backend", "config.yaml")) as fh:
            cfg = yaml.safe_load(fh)
        settings = cfg.get("settings", {})
        client = ScreenerClient(delay=settings.get("request_delay_seconds", 1.2))

        for uni_key, uni in cfg.get("universes", {}).items():
            if not uni.get("enabled"):
                continue
            for screen in uni.get("screens", []):
                if not screen.get("enabled"):
                    continue
                try:
                    manifest["screens"].append(
                        process_screen(client, uni_key, uni, screen, settings))
                except Exception as exc:
                    # Any failure for one screen (auth, network, a bad URL, a bug in
                    # the pipeline itself, ...) must not lose the other screens' data
                    # or leave the site with no record that this run had a problem.
                    failures += 1
                    print("SCREEN FAILED (%s): %s" % (screen["name"], exc))
                    traceback.print_exc()
                    manifest["screens"].append(
                        {"universe": uni_key, "screen": screen["slug"], "label": screen["name"],
                         "universe_label": uni["label"], "error": str(exc)})
    except Exception as exc:
        # A failure before/outside the per-screen loop (bad config.yaml, etc.) — still
        # write a manifest so the site shows a failure banner instead of going stale
        # with no explanation.
        failures += 1
        print("PIPELINE FAILED: %s" % exc)
        traceback.print_exc()
        manifest["fatal_error"] = str(exc)

    save_json(os.path.join(DATA_DIR, "manifest.json"), manifest)
    print("done. manifest written.")
    if failures:
        sys.exit(1)  # surface any failure as a failed Actions run


if __name__ == "__main__":
    main()
