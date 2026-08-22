/* Stock Tracker front-end — reads static JSON written by the weekly pipeline.
   Multiple screens within a universe (e.g. Minervini Screener + Growth Screener) are
   merged into one list by ticker; each stock shows a tick badge per screen it's
   currently in, and the detail sheet can switch between each screen's own scorecard. */
(function () {
  "use strict";

  var state = { manifest: null, universeKey: null, universes: {}, screens: [],
                tab: "active", sort: "score", filter: null, newPeriod: "all", searchQuery: "",
                shortlist: {}, bought: {}, activeSheet: null,
                data: { active: [], dropped: [] } };
  var $ = function (sel) { return document.querySelector(sel); };
  var lastFetchAt = 0;

  // Shortlist and Buy are independent personal tags, separate from the screen filters.
  // localStorage is the instant local cache (and offline fallback); Firebase Realtime
  // Database (firebase-sync.js) syncs them across devices when reachable. A stock can
  // carry either, both, or neither — they don't imply or exclude each other.
  var SHORTLIST_KEY = "mv_shortlist_v1";
  var BOUGHT_KEY = "mv_bought_v1";
  function loadSet(key) {
    try { return JSON.parse(localStorage.getItem(key) || "{}"); } catch (e) { return {}; }
  }
  function saveSet(key, obj) {
    try { localStorage.setItem(key, JSON.stringify(obj)); } catch (e) {}
  }
  function toggleMark(key, kind, obj, ticker) {
    var present = !obj[ticker];
    if (present) obj[ticker] = true; else delete obj[ticker];
    saveSet(key, obj);
    if (window.mvSync) window.mvSync.toggle(kind, ticker, present);
  }
  // Wired to window.mvSyncInit below so firebase-sync.js can call it once it's ready —
  // that module always loads after this script (deferred), so the reverse (this script
  // waiting on mvSync) would race; having the module call in gets the order right for free.
  var syncSeeded = { shortlist: false, bought: false };
  function wireSync(kind, storageKey) {
    window.mvSync.subscribe(kind, function (remote) {
      if (!syncSeeded[kind]) {
        syncSeeded[kind] = true;
        // First connect on a device that already had local-only marks: seed the (empty)
        // remote from them instead of wiping out what the user already picked here.
        if (!Object.keys(remote).length && Object.keys(state[kind]).length) {
          window.mvSync.replace(kind, state[kind]);
          return;
        }
      }
      state[kind] = remote;
      saveSet(storageKey, remote);
      renderList();
      if (state.activeSheet) renderSheet(state.activeSheet.entry, state.activeSheet.slug, false);
    });
  }
  window.mvSyncInit = function () {
    wireSync("shortlist", SHORTLIST_KEY);
    wireSync("bought", BOUGHT_KEY);
  };
  function markPills(entry) {
    var html = "";
    if (state.shortlist[entry.ticker]) html += '<span class="markpill sl" title="Shortlisted">SL</span>';
    if (state.bought[entry.ticker]) html += '<span class="markpill buy" title="Bought">BUY</span>';
    return html;
  }
  function markedEntriesFor(marks) {
    var byTicker = {};
    state.data.active.concat(state.data.dropped).forEach(function (e) {
      if (!byTicker[e.ticker]) byTicker[e.ticker] = e;
    });
    return Object.keys(marks).map(function (t) { return byTicker[t]; }).filter(Boolean);
  }

  var SECTION_MAX = { earnings: 25, revenue: 15, profitability: 10, balance_sheet: 5,
                      sponsorship: 5, rs_trend: 15, base_structure: 20, leadership: 5 };
  var SECTION_LABEL = { earnings: "A · Earnings", revenue: "B · Revenue",
                        profitability: "C · Profitability", balance_sheet: "D · Balance sheet",
                        sponsorship: "E · Sponsorship", rs_trend: "F · RS & trend",
                        base_structure: "G · Base structure", leadership: "H · Leadership" };
  var ITEM_MAX = { A1: 8, A2: 5, A3: 4, A4: 4, A5: 4, B1: 6, B2: 4, B3: 3, B4: 2,
                   C1: 3, C2: 3, C3: 2, C4: 2, D1: 2, D2: 1, D3: 1, D4: 1,
                   E1: 2, E2: 1, E3: 1, E4: 1, F1: 7, F2: 4, F3: 2, F4: 2,
                   G1: 3, G2: 4, G3: 4, G4: 4, G5: 3, G6: 2, H1: 2, H2: 2, H3: 1 };
  var ITEM_LABEL = {
    A1: "EPS", A2: "Accel", A3: "Annual", A4: "CAGR", A5: "Quality",
    B1: "Sales", B2: "Accel", B3: "CAGR", B4: "Backed",
    C1: "ROE", C2: "Margin", C3: "Cash", C4: "FCF",
    D1: "Debt", D2: "Coverage", D3: "Dilution", D4: "Pledge",
    E1: "Holding", E2: "Schemes", E3: "Marquee", E4: "Promoter",
    F1: "RS", F2: "Proximity", F3: "DMA50", F4: "Volume",
    G1: "Stage", G2: "Count", G3: "Depth", G4: "VCP", G5: "DryUp", G6: "Tight",
    H1: "Group", H2: "Rank", H3: "Catalyst",
  };
  // Plain-English descriptions shown on tap — see PROMPT.md for the exact rubric each
  // is scored against.
  var ITEM_DESC = {
    A1: "Latest quarter's EPS growth year-over-year. ≥100% = 8, 50–99% = 6, 25–49% = 4, 15–24% = 2, <15% = 0.",
    A2: "EPS growth rate accelerating across each of the last 3 quarters.",
    A3: "Latest full-year EPS growth vs the year before.",
    A4: "3-year compound annual EPS growth rate.",
    A5: "Positive EPS in all 8 quarters, and no quarter propped up by non-operating income (>30% of pre-tax profit).",
    B1: "Latest quarter's sales growth year-over-year.",
    B2: "Sales growth rate accelerating across each of the last 3 quarters.",
    B3: "3-year compound annual sales growth rate.",
    B4: "EPS growth actually driven by sales and margins, not a one-off or a lower tax rate.",
    C1: "Return on equity.",
    C2: "Net profit margin expanding year-over-year in the latest quarter.",
    C3: "3-year cumulative operating cash flow as a share of profit after tax — checks that earnings are real cash, not just accounting.",
    C4: "Free cash flow positive in at least 2 of the last 3 years — screener.in's own reported figure when it has one, otherwise our estimate (OCF minus approximate capex, since screener.in doesn't isolate capex as its own line).",
    D1: "Debt to equity ratio.",
    D2: "Operating profit as a multiple of interest expense.",
    D3: "Share count growth over 2 years — heavy dilution is a red flag.",
    D4: "Promoter shareholding pledged against loans — any pledge is a red flag.",
    E1: "Combined FII + DII institutional holding rising over recent quarters.",
    E2: "Number of mutual fund schemes holding the stock is increasing.",
    E3: "At least one well-regarded institutional holder.",
    E4: "Promoter holding stable or rising over the last 4 quarters.",
    F1: "Relative strength percentile — how this stock's price performance ranks against the rest of your tracked screens.",
    F2: "How close the price is to its 52-week high.",
    F3: "Price is above a rising 50-day moving average.",
    F4: "Volume on up days vs down days over the last 50 sessions — buying pressure vs selling.",
    G1: "Confirmed Stage 2 uptrend: trend template passed and price is well off its lows.",
    G2: "Which base (consolidation) since the Stage 2 uptrend began — earlier bases score higher, later ones flag late-stage risk.",
    G3: "How deep the current base is from its high.",
    G4: "Volatility Contraction Pattern — each pullback within the base shallower than the last, tightening into a pivot.",
    G5: "Volume drying up on the final contraction before a breakout, vs the 50-day average.",
    G6: "How tight the last 2–3 weekly closes are to each other — a classic pre-breakout signature.",
    H1: "Industry group relative strength.",
    H2: "Rank within its industry group by relative strength.",
    H3: "An identifiable new catalyst (product, capacity, order book, margin inflection) found by the LLM check, with a citation.",
  };

  var C_SHORT = { c1: "below 150/200 DMA", c2: "150 DMA under 200", c3: "200 DMA not rising",
                  c4: "50 DMA under 150/200", c5: "below 50 DMA", c6: "under +30% off low",
                  c7: "over 25% off high", c8: "RS below 70" };

  function rejectReason(sc) {
    var tt = (sc.gates || {}).trend_template;
    if (tt && tt.pass === false) {
      var fails = Object.keys(C_SHORT).filter(function (k) { return tt[k] === false; })
        .map(function (k) { return C_SHORT[k]; });
      var prefix = sc.scores ? "No trend (Gate 1): " : "Rejected: ";
      return prefix + (fails.join(" · ") || "trend template");
    }
    if (sc.status && sc.status.indexOf("FAIL_") === 0) {
      return "Rejected: " + sc.status.replace("FAIL_", "").toLowerCase() + " gate";
    }
    var tot = (sc.scores || {}).total;
    if (tot != null && tot < 60) return "Rejected: score " + tot + " below 60";
    return null;
  }

  function fmtDate(iso) {
    if (!iso) return "—";
    var d = new Date(iso + "T00:00:00");
    return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "2-digit" });
  }
  // Three calendar-week cohorts: current week, last week and the week before.
  // This keeps "New" useful across weekly Sunday scans instead of expiring after 14 days.
  var NEW_WINDOW_DAYS = 20;
  function daysSince(iso) {
    if (!iso) return null;
    var d = new Date(iso + "T00:00:00");
    return Math.floor((Date.now() - d.getTime()) / 86400000);
  }
  function dateOnly(iso) {
    if (!iso) return null;
    var parts = iso.slice(0, 10).split("-").map(Number);
    return new Date(parts[0], parts[1] - 1, parts[2]);
  }
  function startOfWeek(d) {
    var copy = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    var mondayOffset = (copy.getDay() + 6) % 7;
    copy.setDate(copy.getDate() - mondayOffset);
    return copy;
  }
  function newPeriod(entry) {
    var joined = dateOnly(entry.joined_date);
    if (!joined) return null;
    var today = new Date();
    today = new Date(today.getFullYear(), today.getMonth(), today.getDate());
    if (joined.getTime() === today.getTime()) return "today";
    var weeks = Math.floor((startOfWeek(today) - startOfWeek(joined)) / (7 * 86400000));
    if (weeks === 0) return "this-week";
    if (weeks === 1) return "last-week";
    if (weeks === 2) return "two-weeks";
    return null;
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function total(entry) {
    var sc = (entry.primary || {}).scorecard || {};
    return (sc.scores && sc.scores.total) || 0;
  }
  function screenMeta(slug) {
    var found = null;
    for (var i = 0; i < state.screens.length; i++) {
      if (state.screens[i].screen === slug) { found = state.screens[i]; break; }
    }
    var base = found || { screen: slug, label: slug };
    return { screen: base.screen, label: base.label, short: base.short || slug.slice(0, 2).toUpperCase() };
  }

  // ---------------------------------------------------------------- loading

  function loadManifest() {
    lastFetchAt = Date.now();
    return fetch("data/manifest.json", { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error("no manifest"); return r.json(); })
      .then(function (m) {
        state.manifest = m;
        $("#sample-banner").hidden = !m.sample;
        renderErrorBanner(m);
        var byUniverse = {};
        (m.screens || []).forEach(function (s) {
          if (!byUniverse[s.universe]) byUniverse[s.universe] = [];
          byUniverse[s.universe].push(s);
        });
        state.universes = byUniverse;
        var keys = Object.keys(byUniverse);
        state.universeKey = keys[0] || null;
        renderChips();
        return state.universeKey ? loadUniverse(state.universeKey) : renderList();
      })
      .catch(function () {
        $("#regime-line").textContent = "No data yet — run the weekly scan";
      });
  }

  // Kept under ~200 visible characters on purpose — the full per-screen error text and
  // the actionable fix both still exist, just moved into the title tooltip rather than
  // always being on screen.
  function renderErrorBanner(m) {
    var failed = (m.screens || []).filter(function (s) { return s.error; });
    var el = $("#error-banner");
    if (!failed.length && !m.fatal_error) { el.hidden = true; el.title = ""; return; }
    var isAuth = failed.some(function (s) { return /SCREENER_SESSIONID|401|403|404/i.test(s.error || ""); }) ||
      /SCREENER_SESSIONID|401|403|404/i.test(m.fatal_error || "");
    var headline = m.fatal_error
      ? "Last run crashed"
      : failed.length + " screen" + (failed.length > 1 ? "s" : "") + " failed";
    el.textContent = "⚠ " + headline + (isAuth ? " — session likely expired" : "");
    var lines = failed.map(function (s) { return s.label + ": " + s.error; });
    if (m.fatal_error) lines.unshift("pipeline: " + m.fatal_error);
    el.title = (isAuth ? "Your screener.in session has likely expired — grab a fresh sessionid " +
      "cookie and update the SCREENER_SESSIONID secret on GitHub. " : "") + lines.join(" · ");
    el.hidden = false;
  }

  function loadUniverse(universeKey) {
    var screens = (state.universes[universeKey] || []).filter(function (s) { return !s.error; });
    state.screens = screens;
    renderFilterChips();
    if (!screens.length) {
      state.data.active = []; state.data.dropped = [];
      renderList();
      return;
    }
    return Promise.all(screens.map(function (s) {
      var base = "data/" + s.universe + "/" + s.screen + "/";
      return Promise.all([
        fetch(base + "active.json", { cache: "no-store" }).then(function (r) { return r.ok ? r.json() : { stocks: [] }; }),
        fetch(base + "dropped.json", { cache: "no-store" }).then(function (r) { return r.ok ? r.json() : { stocks: [] }; }),
        fetch(base + "runs.json", { cache: "no-store" }).then(function (r) { return r.ok ? r.json() : { runs: [] }; })
      ]).then(function (res) {
        return { slug: s.screen, active: res[0].stocks || [], dropped: res[1].stocks || [], run: (res[2].runs || [])[0] };
      });
    })).then(function (results) {
      var merged = mergeScreens(results);
      state.data.active = merged.active;
      state.data.dropped = merged.dropped;
      renderRegime(results[0] && results[0].run);
      renderFilterChips();
      renderList();
    });
  }

  // ------------------------------------------------------------ cross-screen merge

  function pickPrimary(recMap) {
    var recs = Object.keys(recMap).map(function (k) { return recMap[k]; });
    recs.sort(function (a, b) {
      var ta = ((a.scorecard || {}).scores || {}).total; ta = ta == null ? -1 : ta;
      var tb = ((b.scorecard || {}).scores || {}).total; tb = tb == null ? -1 : tb;
      return tb - ta;
    });
    return recs[0];
  }
  function extremeDate(recMap, field, wantLatest) {
    var vals = Object.keys(recMap).map(function (k) { return recMap[k][field]; }).filter(Boolean).sort();
    if (!vals.length) return null;
    return wantLatest ? vals[vals.length - 1] : vals[0];
  }

  function mergeScreens(results) {
    var byTicker = {};
    results.forEach(function (res) {
      res.active.forEach(function (rec) {
        var e = byTicker[rec.ticker] || (byTicker[rec.ticker] =
          { ticker: rec.ticker, name: rec.name, activeRecs: {}, droppedRecs: {} });
        e.activeRecs[res.slug] = rec;
      });
      res.dropped.forEach(function (rec) {
        var e = byTicker[rec.ticker] || (byTicker[rec.ticker] =
          { ticker: rec.ticker, name: rec.name, activeRecs: {}, droppedRecs: {} });
        e.droppedRecs[res.slug] = rec;
      });
    });
    var active = [], dropped = [];
    Object.keys(byTicker).forEach(function (t) {
      var e = byTicker[t];
      var activeSlugs = Object.keys(e.activeRecs);
      var pool = activeSlugs.length ? e.activeRecs : e.droppedRecs;
      e.primary = pickPrimary(pool);
      e.joined_date = extremeDate(pool, "joined_date", false);
      e.dropped_date = extremeDate(e.droppedRecs, "dropped_date", true);
      var age = daysSince(e.joined_date);
      e.isNew = age !== null && age >= 0 && age <= NEW_WINDOW_DAYS;
      // A stock stays in "In screen" as long as it's active in at least one screen, and
      // separately appears in "Left the screen" as soon as it has dropped from at least
      // one — these are independent conditions now, so a stock dropped from Minervini
      // but still active via Growth shows in both (deliberately: it needs a mention in
      // Left as much as it needs live tracking in In-screen).
      if (activeSlugs.length) active.push(e);
      if (Object.keys(e.droppedRecs).length) dropped.push(e);
    });
    return { active: active, dropped: dropped };
  }

  // Classify a dropped entry into exactly one section: dropped from every currently
  // tracked screen ("all"), from exactly one specific screen, or (only possible with 3+
  // screens) from more than one but not all — a catch-all so nothing silently vanishes.
  function droppedSection(entry) {
    var droppedSlugs = Object.keys(entry.droppedRecs);
    var total = state.screens.length;
    if (total > 0 && droppedSlugs.length >= total) {
      return { key: "__all__", label: "Left all tracked screens", order: total };
    }
    if (droppedSlugs.length === 1) {
      var meta = screenMeta(droppedSlugs[0]);
      var order = state.screens.map(function (s) { return s.screen; }).indexOf(droppedSlugs[0]);
      return { key: droppedSlugs[0], label: "Left " + meta.label + " only", order: order < 0 ? 0 : order };
    }
    return { key: "__multi__", label: "Left multiple screens", order: total - 0.5 };
  }

  // ---------------------------------------------------------------- header

  function renderRegime(run) {
    var el = $("#regime-line");
    var llmEl = $("#llm-line");
    if (!run) {
      el.innerHTML = '<span class="cursor">▮</span> Awaiting first scan';
      llmEl.hidden = true;
      return;
    }
    var r = run.regime || {};
    el.className = "regime " + (r.label === "CORRECTION" ? "correction" : r.label === "CAUTION" ? "caution" : "");
    el.innerHTML = '<span class="cursor">▮</span> MKT ' + esc(r.label || "?") +
      " " + (r.score != null ? r.score + "/6" : "");
    $("#runstamp").textContent = "Scan " + fmtDate(run.run_date);

    var llm = run.llm;
    if (llm && llm.model) {
      llmEl.textContent = "Verdicts: " + (llm.enabled ? llm.model : "Rule-based (no LLM key set)");
      llmEl.hidden = false;
    } else {
      llmEl.hidden = true;
    }

    var rsEl = $("#rs-line");
    var rsu = run.rs_universe;
    if (rsu && rsu.source) {
      rsEl.textContent = rsu.succeeded + " scanned";
      if (rsu.used) {
        rsEl.className = "llmline";
        rsEl.title = "RS ranked against " + rsu.source + ": " + rsu.succeeded + "/" + rsu.attempted + " fetched";
      } else {
        rsEl.className = "llmline warn";
        rsEl.title = "Only " + rsu.succeeded + "/" + rsu.attempted + " of " + rsu.source +
          " fetched this run — fell back to tracked-only RS ranking";
      }
      rsEl.hidden = false;
    } else {
      rsEl.hidden = true;
    }
  }

  function renderChips() {
    var box = $("#screen-chips");
    box.innerHTML = "";
    Object.keys(state.universes).forEach(function (uk) {
      var screensHere = state.universes[uk];
      var label = (screensHere[0] && screensHere[0].universe_label) || uk;
      var hasError = screensHere.some(function (s) { return s.error; });
      var b = document.createElement("button");
      b.className = "chip" + (uk === state.universeKey ? " active" : "");
      b.textContent = label + (hasError ? " ⚠" : "");
      b.onclick = function () {
        state.universeKey = uk; state.filter = null; state.newPeriod = "all";
        renderChips(); loadUniverse(uk);
      };
      box.appendChild(b);
    });
  }

  function renderFilterChips() {
    var box = $("#screen-filter");
    box.innerHTML = "";
    // Shortlist is a separate, personal view — the screen filters don't apply to it.
    if (state.tab === "shortlist" || state.tab === "buy" || !state.screens.length) { box.hidden = true; return; }
    box.hidden = false;
    var allBtn = document.createElement("button");
    allBtn.className = "chip" + (!state.filter ? " active" : "");
    allBtn.textContent = "All";
    allBtn.onclick = function () {
      state.filter = null; state.newPeriod = "all"; renderFilterChips(); renderList();
    };
    box.appendChild(allBtn);
    if (state.screens.length >= 2) {
      state.screens.forEach(function (raw) {
        var s = screenMeta(raw.screen);
        var b = document.createElement("button");
        b.className = "chip" + (state.filter === s.screen ? " active" : "");
        b.textContent = s.short + " only";
        b.onclick = function () {
          state.filter = s.screen; state.newPeriod = "all"; renderFilterChips(); renderList();
        };
        box.appendChild(b);
      });
    }
    var newBtn = document.createElement("button");
    newBtn.className = "chip" + (state.filter === "new" ? " active" : "");
    var newCount = state.data.active.filter(function (e) { return e.isNew; }).length;
    newBtn.textContent = "New" + (newCount ? " " + newCount : "");
    newBtn.setAttribute("aria-expanded", state.filter === "new" ? "true" : "false");
    newBtn.onclick = function () {
      state.filter = "new"; state.newPeriod = "all"; renderFilterChips(); renderList();
    };
    box.appendChild(newBtn);
    renderNewFilter();
  }

  function renderNewFilter() {
    var wrap = $("#new-filter-wrap");
    var box = $("#new-filter");
    var visible = state.filter === "new" && state.tab === "active";
    wrap.hidden = !visible;
    if (!visible) { box.innerHTML = ""; return; }

    var newStocks = state.data.active.filter(function (e) { return e.isNew; });
    // Only date buckets that actually have a new stock in them are offered — an empty
    // "2 weeks ago (0)" option is dead weight, not a real filter choice.
    var periods = [
      { key: "today", label: "Today" },
      { key: "this-week", label: "Earlier this week" },
      { key: "last-week", label: "Last week" },
      { key: "two-weeks", label: "2 weeks ago" }
    ].map(function (opt) {
      return { key: opt.key, label: opt.label,
        count: newStocks.filter(function (e) { return newPeriod(e) === opt.key; }).length };
    }).filter(function (p) { return p.count > 0; });

    if (state.newPeriod !== "all" && !periods.some(function (p) { return p.key === state.newPeriod; })) {
      state.newPeriod = "all";
    }

    box.innerHTML = "";
    var select = document.createElement("select");
    select.setAttribute("aria-label", "Filter new stocks by date added");
    select.appendChild(new Option("All new (" + newStocks.length + ")", "all"));
    periods.forEach(function (p) {
      select.appendChild(new Option(p.label + " (" + p.count + ")", p.key));
    });
    select.value = state.newPeriod;
    select.onchange = function () { state.newPeriod = select.value; renderList(); };
    box.appendChild(select);
  }

  // ---------------------------------------------------------------- list

  function sorted(list) {
    var arr = list.slice();
    if (state.sort === "score") arr.sort(function (a, b) { return total(b) - total(a); });
    if (state.sort === "rs") arr.sort(function (a, b) {
      var ra = ((a.primary.scorecard || {}).technicals || {}).rs_percentile || -1;
      var rb = ((b.primary.scorecard || {}).technicals || {}).rs_percentile || -1;
      return rb - ra;
    });
    if (state.sort === "joined") arr.sort(function (a, b) {
      return (b.dropped_date || b.joined_date || "").localeCompare(a.dropped_date || a.joined_date || "");
    });
    if (state.sort === "ticker") arr.sort(function (a, b) { return a.ticker.localeCompare(b.ticker); });
    return arr;
  }

  // Each segment is color-only at a glance, so it also carries its own title text —
  // pass/fail must never depend on color alone (colorblind users, screen readers).
  function gatebar(sc) {
    var tt = ((sc.gates || {}).trend_template) || {};
    var html = '<span class="gatebar" title="Trend Template c1–c8">';
    for (var i = 1; i <= 8; i++) {
      var v = tt["c" + i];
      var state = v === true ? "on" : v === false ? "off" : "";
      var label = "c" + i + ": " + (v === true ? "pass" : v === false ? "fail" : "n/a");
      html += "<i class=\"" + state + "\" title=\"" + label + "\"></i>";
    }
    return html + "</span>";
  }

  function membershipPills(entry) {
    return state.screens.map(function (raw) {
      var s = screenMeta(raw.screen);
      var on = !!entry.activeRecs[s.screen];
      var droppedRec = entry.droppedRecs[s.screen];
      var title = s.label + (on ? " — active" : droppedRec ? " — left " + fmtDate(droppedRec.dropped_date) : " — not in this screen");
      var colorClass = "mship-" + s.short.toLowerCase().replace(/[^a-z0-9]/g, "");
      return '<span class="mship ' + colorClass + (on ? " on" : "") + '" title="' + esc(title) + '">' + esc(s.short) + "</span>";
    }).join("");
  }

  function renderList() {
    var list;
    if (state.tab === "shortlist") {
      list = markedEntriesFor(state.shortlist);
    } else if (state.tab === "buy") {
      list = markedEntriesFor(state.bought);
    } else {
      list = state.data[state.tab] || [];
      if (state.filter === "new") {
        list = list.filter(function (e) { return e.isNew; });
        if (state.newPeriod !== "all") {
          list = list.filter(function (e) { return newPeriod(e) === state.newPeriod; });
        }
      } else if (state.filter) {
        // Scoped to the tab being shown: a stock that dropped out of screen X but is
        // still active via screen Y must not count as "X only" on the In-screen tab —
        // it's no longer actually in X.
        list = list.filter(function (e) {
          return state.tab === "dropped" ? !!e.droppedRecs[state.filter] : !!e.activeRecs[state.filter];
        });
      }
    }
    if (state.searchQuery) {
      var q = state.searchQuery;
      list = list.filter(function (e) {
        return (e.ticker || "").toLowerCase().indexOf(q) !== -1 ||
          (e.name || "").toLowerCase().indexOf(q) !== -1;
      });
    }
    var box = $("#list");
    $("#thead").style.display = list.length ? "" : "none";
    $("#empty").hidden = !!list.length;
    $("#empty").textContent = state.tab === "shortlist"
      ? "Nothing shortlisted yet. Open a stock and tap Shortlist."
      : state.tab === "buy"
      ? "Nothing marked Buy yet. Open a stock and tap Buy."
      : "Nothing here yet. The first Sunday scan fills this in.";
    $("#count").textContent = list.length + (
      state.tab === "active" ? " tracked" :
      state.tab === "shortlist" || state.tab === "buy" ? " marked" : " left");
    box.innerHTML = "";

    if (state.tab !== "dropped") {
      // Shortlist and Buy are flat, independent lists — a stock can be in both, but each
      // tab only cares about its own tag, so there's no cross-tag grouping to do here.
      var isFrozenFn = state.tab === "active" ? function () { return false; }
        : function (entry) { return !Object.keys(entry.activeRecs).length; };
      sorted(list).forEach(function (entry) { box.appendChild(buildRow(entry, isFrozenFn(entry))); });
      return;
    }

    // Left the screen: grouped into sections by exactly which screen(s) each stock has
    // dropped from. A stock still active via another screen (not fully frozen) keeps
    // showing live data here — only entries dropped from every tracked screen are frozen.
    var groups = {};
    sorted(list).forEach(function (entry) {
      var sec = droppedSection(entry);
      if (!groups[sec.key]) groups[sec.key] = { label: sec.label, order: sec.order, entries: [] };
      groups[sec.key].entries.push(entry);
    });
    Object.keys(groups).map(function (k) { return groups[k]; })
      .sort(function (a, b) { return a.order - b.order; })
      .forEach(function (g) {
        var head = document.createElement("div");
        head.className = "section-head";
        head.textContent = g.label + " (" + g.entries.length + ")";
        box.appendChild(head);
        g.entries.forEach(function (entry) {
          var isFrozen = !Object.keys(entry.activeRecs).length;
          box.appendChild(buildRow(entry, isFrozen));
        });
      });
  }

  function buildRow(entry, isFrozen) {
    var rec = entry.primary;
    var sc = rec.scorecard || {};
    var t = sc.technicals || {};
    var scores = sc.scores || null;
    var tot = scores ? scores.total : null;
    var scoreCls = tot == null ? "low" : tot >= 80 ? "" : tot >= 60 ? "mid" : "low";
    var bucket = scores ? (sc.action_bucket || "") : "GATE_FAIL";
    var row = document.createElement("button");
    row.className = "row" + (isFrozen ? " frozen" : "");
    var reason = rejectReason(sc);
    var statusText = reason || sc.quality_band || sc.status || "";
    var sub = state.tab === "dropped"
      ? "Joined " + fmtDate(entry.joined_date) + " · Left " + fmtDate(entry.dropped_date) + (reason ? " · " + reason : "")
      : "Joined " + fmtDate(entry.joined_date) + (statusText ? " · " + statusText : "");
    var cells =
      '<span class="stockcell"><span class="ticker">' + esc(entry.ticker) + "</span>" +
      membershipPills(entry) + markPills(entry) +
      (entry.isNew ? '<span class="newpill">NEW</span>' : "") +
      '<div class="sname">' + esc(entry.name || "") + '</div>' +
      '<div class="sub' + (reason && state.tab !== "dropped" ? " reject" : "") + '" title="' + esc(sub) + '">' + esc(sub) + "</div></span>" +
      '<span class="score ' + scoreCls + '">' + (tot == null ? "—" : tot) + '<span class="of">/100</span></span>' +
      gatebar(sc);
    var subs = "";
    ["earnings", "revenue", "profitability", "balance_sheet", "sponsorship", "rs_trend", "base_structure", "leadership"]
      .forEach(function (k) {
        var v = scores ? scores[k].subtotal : null;
        subs += '<span class="cellnum wide' + (v ? "" : " dim") + '">' + (v == null ? "·" : v) + "</span>";
      });
    cells += '<span class="cellnum wide' + (t.rs_percentile == null ? " dim" : "") + '">' +
             (t.rs_percentile == null ? "·" : t.rs_percentile) + "</span>" + subs +
             '<span class="bucket wide ' + esc(bucket) + '">' + esc(bucket.replace(/_/g, " ")) + "</span>" +
             '<span class="datecell wide">' + fmtDate(entry.joined_date) + "</span>";
    row.innerHTML = cells;
    row.onclick = function () { openSheet(entry); };
    return row;
  }

  // ---------------------------------------------------------------- detail sheet

  // H1/H2 are a watchlist-relative proxy (see backend/scorecard.py _score_H) rather
  // than a true full-market figure — flagged distinctly so it reads as "approximate,"
  // not as either a fully verified score or a silent zero.
  var PROXY_FIELD_TO_KEY = { industry_group_rs: "H1", group_leadership_rank: "H2" };
  function proxyTitle(t) {
    var bits = ["Approximate — ranked only within your tracked screens, not the full market"];
    if (t.industry_sector) bits.push(t.industry_sector);
    if (t.group_leadership_rank && t.group_leadership_of) {
      bits.push("rank " + t.group_leadership_rank + " of " + t.group_leadership_of + " tracked peers");
    }
    return bits.join(" · ");
  }
  // Every item is a real <button> (not a styled link) so tapping it works identically
  // on desktop and mobile — title/hover tooltips don't fire reliably on touch. Buttons
  // already inherit plain text styling from the global `button` reset in app.css, so
  // this adds a description without looking like a hyperlink.
  function itemRows(sec, proxyKeys, proxyTitleText, extraDesc) {
    var keys = Object.keys(sec).filter(function (k) { return k !== "subtotal"; });
    return keys.map(function (k) {
      var label = ITEM_LABEL[k] || k;
      var text = "<b>" + esc(label) + "</b> " + sec[k] + "/" + (ITEM_MAX[k] || "?");
      var isProxy = proxyKeys && proxyKeys[k];
      var desc = ITEM_DESC[k] || "";
      if (isProxy && proxyTitleText) desc = (desc ? desc + " " : "") + proxyTitleText;
      if (extraDesc && extraDesc[k]) desc = (desc ? desc + " " : "") + extraDesc[k];
      var cls = "item-btn" + (isProxy ? " proxy-item" : "");
      return '<button type="button" class="' + cls + '" data-item-key="' + esc(k) +
        '" data-item-desc="' + esc(desc) + '">' + text + "</button>";
    }).join(" · ");
  }

  // iOS Safari/PWA doesn't reliably honor `overflow:hidden` on body to stop background
  // scroll behind a fixed modal — a touch-drag inside the sheet can instead scroll the
  // page underneath it, making sheet content look "missing". Locking with position:fixed
  // and restoring scrollY on close is the standard cross-browser workaround.
  var savedScrollY = 0;
  function lockBodyScroll() {
    savedScrollY = window.scrollY || window.pageYOffset || 0;
    document.body.style.position = "fixed";
    document.body.style.top = "-" + savedScrollY + "px";
    document.body.style.width = "100%";
  }
  function unlockBodyScroll() {
    document.body.style.position = "";
    document.body.style.top = "";
    document.body.style.width = "";
    window.scrollTo(0, savedScrollY);
  }

  function openSheet(entry) {
    lockBodyScroll();
    var slug = null;
    var pool = Object.keys(entry.activeRecs).length ? entry.activeRecs : entry.droppedRecs;
    for (var k in pool) { if (pool[k] === entry.primary) { slug = k; break; } }
    renderSheet(entry, slug || Object.keys(pool)[0], true);
  }

  function renderSheet(entry, slug, isFreshOpen) {
    state.activeSheet = { entry: entry, slug: slug };
    var rec = entry.activeRecs[slug] || entry.droppedRecs[slug];
    var sc = rec.scorecard || {};
    var t = sc.technicals || {};
    var scores = sc.scores;
    var isShortlisted = !!state.shortlist[entry.ticker];
    var isBought = !!state.bought[entry.ticker];

    var html = '<div class="sheet-head"><div><h2>' + esc(entry.ticker) + "</h2>" +
      '<div class="sname">' + esc(entry.name || "") + "</div>" +
      (rec.joined_date ? '<div class="sjoined">Joined ' + esc(fmtDate(rec.joined_date)) + "</div>" : "") +
      "</div>" +
      '<button class="close" aria-label="Close">✕</button></div>';

    // Independent toggles — a stock can be Shortlisted, Bought, both, or neither. Tap an
    // active button again to remove that tag.
    html += '<div class="marktoggle">' +
      '<button class="stbtn' + (isShortlisted ? " marked-sl" : "") +
        '" data-mark="shortlist" aria-pressed="' + isShortlisted + '">Shortlist</button>' +
      '<button class="stbtn' + (isBought ? " active" : "") +
        '" data-mark="buy" aria-pressed="' + isBought + '">Buy</button>' +
      "</div>";

    var allSlugs = Object.keys(entry.activeRecs).concat(
      Object.keys(entry.droppedRecs).filter(function (s) { return !entry.activeRecs[s]; }));
    if (allSlugs.length > 1) {
      html += '<div class="screentoggle">' + allSlugs.map(function (s2) {
        var meta = screenMeta(s2);
        var isDropped = !entry.activeRecs[s2];
        return '<button class="stbtn' + (s2 === slug ? " active" : "") + (isDropped ? " dropped" : "") +
          '" data-slug="' + esc(s2) + '">' + esc(meta.short) + (isDropped ? " ✕" : "") + "</button>";
      }).join("") + "</div>";
    }

    html += '<div class="badges">';
    if (scores) html += '<button type="button" class="badge band hist-open" data-ticker="' + esc(entry.ticker) +
      '" title="Tap to see score history">' + esc(sc.quality_band) + " · " + scores.total + "/100</button>";
    html += '<span class="badge">' + esc((sc.action_bucket || sc.status || "").replace(/_/g, " ")) + "</span>";
    if (rec.dropped_date) html += '<span class="badge frozen">Left screen ' + fmtDate(rec.dropped_date) + " · frozen</span>";
    var reason = rejectReason(sc);
    if (reason) html += '<span class="badge flag">' + esc(reason) + "</span>";
    (sc.red_flags || []).forEach(function (f) { html += '<span class="badge flag">' + esc(f) + "</span>"; });
    if (sc.risk_level) html += '<span class="badge">risk ' + esc(sc.risk_level) + "</span>";
    html += "</div>";

    html += '<div class="kv">' +
      kv("Price", t.price != null ? "₹" + t.price : "—") +
      kv("RS pct", t.rs_percentile != null ? t.rs_percentile : "—",
        "Relative Strength percentile — price performance ranked against other stocks. Not the same as RSI.") +
      kv("RSI", t.rsi != null ? t.rsi : "—",
        "Wilder's 14-day momentum oscillator (overbought/oversold on this stock's own price) — context only, never scored, and not the same as RS pct.") +
      kv("ROCE", t.roce != null ? t.roce + "%" : "—",
        "Return on Capital Employed — how efficiently the company turns capital into profit. Context only; only ROE feeds the score.") +
      kv("Off 52w high", t.pct_below_52w_high != null ? t.pct_below_52w_high + "%" : "—") +
      kv("Above 52w low", t.pct_above_52w_low != null ? "+" + t.pct_above_52w_low + "%" : "—") + "</div>";

    // gates — cells are buttons so tapping any one shows its definition below the grid
    // (a shared slot, not per-cell, so the grid never reflows when opened/closed).
    var tt = ((sc.gates || {}).trend_template) || {};
    html += '<div class="sec-title">Gate 1 · Trend Template</div><div class="gategrid">';
    var GL_WORD = ["Uptrend", "Aligned", "Rising", "Leading", "Holding", "Rebounded", "Nearing", "Leader"];
    var GL_DEF = ["Price is above both the 150-day and 200-day moving average.",
                  "150-day moving average is above the 200-day moving average.",
                  "200-day moving average has been trending up for at least 1 month.",
                  "50-day moving average is above both the 150-day and 200-day moving average.",
                  "Price is above the 50-day moving average.",
                  "Price is at least 30% above its 52-week low.",
                  "Price is within 25% of its 52-week high.",
                  "Relative strength percentile is 70 or higher."];
    for (var i = 1; i <= 8; i++) {
      var v = tt["c" + i];
      html += '<button type="button" class="gcell ' + (v === true ? "on" : v === false ? "off" : "") +
        '" data-gate-key="c' + i + '" data-gate-word="' + esc(GL_WORD[i - 1]) +
        '" data-gate-desc="' + esc(GL_DEF[i - 1]) + '">c' + i +
        "<br>" + GL_WORD[i - 1] + "</button>";
    }
    html += "</div><div class=\"gate-desc\" hidden></div>";
    if (tt.near_miss_notes) html += '<p class="uv">' + esc(tt.near_miss_notes) + "</p>";

    if (scores) {
      var proxyKeys = {};
      ((sc.data_quality || {}).proxy_fields || []).forEach(function (f) {
        var k = PROXY_FIELD_TO_KEY[f];
        if (k) proxyKeys[k] = true;
      });
      var proxyTitleText = proxyTitle(t);
      // The C4 score is just a positive-years count — without this, the actual FCF
      // figure it's based on was never visible anywhere, only its derived 0/2.
      var extraDesc = {};
      if (t.fcf_latest_cr != null) {
        extraDesc.C4 = "Latest: ₹" + t.fcf_latest_cr + " Cr (" +
          (t.fcf_source === "reported" ? "screener.in's own figure" : "our estimate") + ").";
      }
      html += '<div class="sec-title">Score · ' + scores.total + "/100</div><div class=\"scorebars\">";
      Object.keys(SECTION_MAX).forEach(function (k) {
        var sec = scores[k];
        var pct = Math.round(sec.subtotal / SECTION_MAX[k] * 100);
        html += '<div class="sbar"><span class="lbl">' + SECTION_LABEL[k] + "</span>" +
          '<span class="track"><span class="fill" style="width:' + pct + '%"></span></span>' +
          '<span class="val">' + sec.subtotal + "/" + SECTION_MAX[k] + "</span></div>" +
          '<div class="items">' + itemRows(sec, proxyKeys, proxyTitleText, extraDesc) + "</div>" +
          '<div class="item-desc" hidden></div>';
      });
      html += "</div>";
    }

    var b = sc.base || {};
    if (b.pivot) {
      html += '<div class="sec-title">Base</div><div class="plan">' +
        "pivot ₹" + b.pivot + " · " + (b.pct_from_pivot != null ? b.pct_from_pivot + "% from pivot" : "") +
        (b.depth_pct != null ? " · depth " + b.depth_pct + "%" : "") +
        (b.weeks != null ? " · " + b.weeks + " wks" : "") +
        (b.base_count != null ? " · base #" + b.base_count : "") + "</div>";
    }
    var tp = sc.trade_plan;
    if (tp) {
      html += '<div class="sec-title">Trade plan</div><div class="plan">' +
        "buy ₹" + tp.buy_range[0] + " – ₹" + tp.buy_range[1] +
        " · stop ₹" + tp.stop + " (" + tp.stop_pct + "%)" +
        (tp.position_size_pct_equity ? " · size " + tp.position_size_pct_equity + "% eq @ " + tp.risk_pct + "% risk" : "") +
        "<br>" + esc(tp.notes || "") + "</div>";
    }

    var v = sc.verdict || {};
    html += '<div class="sec-title">Verdict</div><div class="verdict">';
    if (v.summary) html += "<p>" + esc(v.summary) + "</p>";
    if (v.strengths && v.strengths.length) html += "<p><b>Strengths</b></p><ul>" +
      v.strengths.map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") + "</ul>";
    if (v.weaknesses && v.weaknesses.length) html += "<p><b>Weaknesses</b></p><ul>" +
      v.weaknesses.map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") + "</ul>";
    if (v.catalysts && v.catalysts.length) html += "<p><b>Catalysts</b></p><ul>" +
      v.catalysts.map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") + "</ul>";
    if (v.biggest_risk) html += "<p><b>Biggest risk:</b> " + esc(v.biggest_risk) + "</p>";
    if (v.conviction_0_10 != null) html += "<p><b>Conviction:</b> " + v.conviction_0_10 + "/10</p>";
    html += "</div>";

    var d = sc.delta || {};
    if ((d.alerts && d.alerts.length) || d.bucket_change) {
      html += '<div class="sec-title">This week</div><div class="note">' +
        (d.bucket_change ? esc(d.bucket_change) + "<br>" : "") +
        (d.score_change ? "score " + (d.score_change > 0 ? "+" : "") + d.score_change + "<br>" : "") +
        esc((d.alerts || []).join(" · ")) +
        (d.gate_flips && d.gate_flips.length ? "<br>" + esc(d.gate_flips.join(" · ")) : "") + "</div>";
    }

    var uv = ((sc.data_quality || {}).unverified_fields) || [];
    var uvReasons = ((sc.data_quality || {}).unverified_reasons) || {};
    if (uv.length) {
      var uvText = uv.map(function (f) {
        return uvReasons[f] ? f + " (" + uvReasons[f] + ")" : f;
      }).join(", ");
      html += '<p class="uv">Unverified (scored 0): ' + esc(uvText) + "</p>";
    }
    var pf = ((sc.data_quality || {}).proxy_fields) || [];
    if (pf.length) html += '<p class="uv proxy-note">Approximate (' + esc(pf.join(", ")) +
      "): ranked only within your tracked screens, not the full market.</p>";
    var vc = sc.valuation_context || {};
    if (vc.pe) html += '<p class="uv">Context only: P/E ' + vc.pe + " — never a criterion.</p>";

    var sheet = $("#sheet");
    var backdrop = $("#backdrop");
    var prevScroll = sheet.scrollTop;
    sheet.innerHTML = html;
    if (isFreshOpen) {
      // A pending close (from a fast close-then-reopen tap) could otherwise fire its
      // hide-after-animation timeout after this reopen and yank the sheet shut again.
      if (sheetCloseTimer) { clearTimeout(sheetCloseTimer); sheetCloseTimer = null; }
      // Two-step reveal: unhide first (so height/layout resolve), then add .show on the
      // next frame so the transform/opacity transition actually has a starting state to
      // animate from — toggling both at once would skip straight to the end state.
      sheet.hidden = false;
      backdrop.hidden = false;
      sheet.classList.remove("show");
      backdrop.classList.remove("show");
      void sheet.offsetHeight;
      requestAnimationFrame(function () {
        sheet.classList.add("show");
        backdrop.classList.add("show");
      });
    }
    sheet.querySelector(".close").onclick = closeSheet;
    // Scoped to .screentoggle specifically — .marktoggle also uses the .stbtn look, and
    // its buttons have no data-slug, so a bare ".stbtn" query here would misfire on them.
    sheet.querySelectorAll(".screentoggle .stbtn").forEach(function (btn) {
      btn.onclick = function () { renderSheet(entry, btn.dataset.slug, false); };
    });
    sheet.querySelectorAll(".marktoggle [data-mark]").forEach(function (btn) {
      btn.onclick = function () {
        if (btn.dataset.mark === "shortlist") toggleMark(SHORTLIST_KEY, "shortlist", state.shortlist, entry.ticker);
        else toggleMark(BOUGHT_KEY, "bought", state.bought, entry.ticker);
        renderSheet(entry, slug, false);
        renderList();
      };
    });
    // Tap a gate cell or score item to show its definition — tap the same one again
    // (or tap elsewhere on the page) to close it.
    sheet.querySelectorAll(".gategrid .gcell").forEach(function (btn) {
      btn.onclick = function () {
        var box = sheet.querySelector(".gate-desc");
        var key = btn.dataset.gateKey;
        var open = !box.hidden && box.dataset.openKey === key;
        box.hidden = open;
        box.dataset.openKey = open ? "" : key;
        if (!open) box.innerHTML = "<b>" + esc(key) + " " + esc(btn.dataset.gateWord) + ":</b> " + esc(btn.dataset.gateDesc);
      };
    });
    sheet.querySelectorAll(".items .item-btn").forEach(function (btn) {
      btn.onclick = function () {
        var box = btn.parentElement.nextElementSibling;
        var key = btn.dataset.itemKey;
        var open = !box.hidden && box.dataset.openKey === key;
        box.hidden = open;
        box.dataset.openKey = open ? "" : key;
        if (!open) box.innerHTML = "<b>" + esc(ITEM_LABEL[key] || key) + ":</b> " + esc(btn.dataset.itemDesc);
      };
    });
    var histOpenBtn = sheet.querySelector(".hist-open");
    if (histOpenBtn) {
      histOpenBtn.onclick = function () { openHistoryPopup(entry.ticker); };
    }
    if (isFreshOpen) {
      sheet.querySelector(".close").focus();
    } else {
      sheet.scrollTop = prevScroll;
    }
  }

  function kv(k, v, title) {
    return '<div' + (title ? ' title="' + esc(title) + '"' : "") + '><div class="k">' + k +
      '</div><div class="v">' + v + "</div></div>";
  }

  // Score history — lazy-fetched per ticker (one small JSON file, only on tap) so
  // opening the sheet never costs an extra request unless the chart is actually opened.
  var HIST_W = 300, HIST_H = 108, HIST_PAD_L = 8, HIST_PAD_R = 8, HIST_PAD_T = 10, HIST_PAD_B = 20;
  var HIST_PLOT_W = HIST_W - HIST_PAD_L - HIST_PAD_R, HIST_PLOT_H = HIST_H - HIST_PAD_T - HIST_PAD_B;
  var histCache = {};

  function histX(i, n) { return HIST_PAD_L + (n === 1 ? 0 : (i / (n - 1)) * HIST_PLOT_W); }
  function histY(score) {
    var s = Math.max(0, Math.min(100, score));
    return HIST_PAD_T + (1 - s / 100) * HIST_PLOT_H;
  }

  function renderHistoryChart(points) {
    if (!points || points.length < 2) {
      return '<p class="uv">Not enough history yet — check back after next week’s scan.</p>';
    }
    var n = points.length, first = points[0], last = points[n - 1];
    var delta = last.score - first.score;
    var deltaCls = delta > 0 ? "up" : delta < 0 ? "down" : "";
    var deltaText = (delta > 0 ? "+" : "") + delta + " over " + (n - 1) + (n - 1 === 1 ? " week" : " weeks");

    var linePts = points.map(function (p, i) { return histX(i, n) + "," + histY(p.score); }).join(" ");
    var areaPts = linePts + " " + histX(n - 1, n) + "," + (HIST_PAD_T + HIST_PLOT_H) +
      " " + histX(0, n) + "," + (HIST_PAD_T + HIST_PLOT_H);
    var grid = [25, 50, 75].map(function (g) {
      return '<line x1="' + HIST_PAD_L + '" y1="' + histY(g) + '" x2="' + (HIST_W - HIST_PAD_R) +
        '" y2="' + histY(g) + '" class="hist-grid" />';
    }).join("");

    var svg = '<svg viewBox="0 0 ' + HIST_W + ' ' + HIST_H + '" class="hist-svg" ' +
      'preserveAspectRatio="none" role="img" aria-label="Score history, ' + n + ' data points">' +
      grid +
      '<polygon points="' + areaPts + '" class="hist-area"></polygon>' +
      '<polyline points="' + linePts + '" class="hist-line"></polyline>' +
      '<circle cx="' + histX(0, n) + '" cy="' + histY(first.score) + '" r="3" class="hist-dot hist-dot-first"></circle>' +
      '<circle cx="' + histX(n - 1, n) + '" cy="' + histY(last.score) + '" r="3.5" class="hist-dot hist-dot-last"></circle>' +
      "</svg>";

    return '<div class="hist-summary"><span class="hist-score">' + last.score + '/100</span>' +
      '<span class="hist-delta ' + deltaCls + '">' + esc(deltaText) + "</span></div>" +
      svg +
      '<div class="hist-axis"><span>' + esc(fmtDate(first.date)) + '</span><span>' + esc(fmtDate(last.date)) + "</span></div>" +
      '<div class="hist-tip" hidden></div>';
  }

  function wireHistoryChart(box, points) {
    var svg = box.querySelector(".hist-svg");
    var tip = box.querySelector(".hist-tip");
    if (!svg || !tip || !points || points.length < 2) return;
    var n = points.length;
    svg.addEventListener("click", function (e) {
      var rect = svg.getBoundingClientRect();
      var relX = (e.clientX - rect.left) / rect.width * HIST_W;
      var idx = Math.round(((relX - HIST_PAD_L) / HIST_PLOT_W) * (n - 1));
      idx = Math.max(0, Math.min(n - 1, idx));
      var p = points[idx];
      tip.hidden = false;
      tip.textContent = fmtDate(p.date) + " · score " + p.score +
        (p.bucket ? " · " + p.bucket.replace(/_/g, " ") : "");
    });
  }

  function loadHistoryChart(ticker, box) {
    if (histCache[ticker]) {
      box.innerHTML = renderHistoryChart(histCache[ticker]);
      wireHistoryChart(box, histCache[ticker]);
      return;
    }
    box.innerHTML = '<p class="uv">Loading…</p>';
    fetch("data/history/" + encodeURIComponent(ticker) + ".json", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : { points: [] }; })
      .then(function (data) {
        var points = data.points || [];
        histCache[ticker] = points;
        box.innerHTML = renderHistoryChart(points);
        wireHistoryChart(box, points);
      })
      .catch(function () {
        box.innerHTML = '<p class="uv">Couldn’t load history right now.</p>';
      });
  }

  // Score history opens as its own small popup on top of the detail sheet (rather than
  // an inline section) — it's a side-lookup, not part of reading the scorecard, so it
  // shouldn't add scroll length to the sheet every time.
  var histPopupCloseTimer = null;
  function openHistoryPopup(ticker) {
    var popup = $("#hist-popup");
    var backdrop = $("#hist-backdrop");
    if (histPopupCloseTimer) { clearTimeout(histPopupCloseTimer); histPopupCloseTimer = null; }
    popup.innerHTML = '<div class="sheet-head"><h2>' + esc(ticker) + " · Score history</h2>" +
      '<button type="button" class="close" aria-label="Close">✕</button></div>' +
      '<div class="hist-chart"></div>';
    popup.querySelector(".close").onclick = closeHistoryPopup;
    popup.hidden = false;
    backdrop.hidden = false;
    popup.classList.remove("show");
    backdrop.classList.remove("show");
    void popup.offsetHeight;
    requestAnimationFrame(function () {
      popup.classList.add("show");
      backdrop.classList.add("show");
    });
    loadHistoryChart(ticker, popup.querySelector(".hist-chart"));
  }
  function closeHistoryPopup() {
    var popup = $("#hist-popup");
    var backdrop = $("#hist-backdrop");
    popup.classList.remove("show");
    backdrop.classList.remove("show");
    if (histPopupCloseTimer) clearTimeout(histPopupCloseTimer);
    var finish = function () { histPopupCloseTimer = null; popup.hidden = true; backdrop.hidden = true; };
    var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) finish(); else histPopupCloseTimer = setTimeout(finish, 200);
  }

  var sheetCloseTimer = null;
  function closeSheet() {
    var sheet = $("#sheet");
    var backdrop = $("#backdrop");
    state.activeSheet = null;
    sheet.classList.remove("show");
    backdrop.classList.remove("show");
    unlockBodyScroll();
    if (sheetCloseTimer) clearTimeout(sheetCloseTimer);
    var finish = function () { sheetCloseTimer = null; sheet.hidden = true; backdrop.hidden = true; };
    var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) finish(); else sheetCloseTimer = setTimeout(finish, 220);
  }

  // ---------------------------------------------------------------- wiring

  document.querySelectorAll(".tab").forEach(function (b) {
    b.onclick = function () {
      document.querySelectorAll(".tab").forEach(function (x) { x.classList.remove("active"); });
      b.classList.add("active");
      state.tab = b.dataset.tab;
      if (state.tab !== "active" && state.filter === "new") {
        state.filter = null;
        state.newPeriod = "all";
      }
      renderFilterChips();
      renderNewFilter();
      renderList();
    };
  });
  $("#sort").onchange = function () { state.sort = this.value; renderList(); };

  // Search — collapsed to an icon by default; tap to expand, type to filter the
  // current view by ticker or name. Collapses again on close, click-outside, or Escape.
  function openSearch() {
    var input = $("#search-input");
    var btn = $("#search-btn");
    $("#search").classList.add("open");
    input.hidden = false;
    btn.setAttribute("aria-expanded", "true");
    void input.offsetWidth;
    requestAnimationFrame(function () { input.classList.add("show"); });
    input.focus();
  }
  function closeSearch() {
    var input = $("#search-input");
    var btn = $("#search-btn");
    input.classList.remove("show");
    $("#search").classList.remove("open");
    btn.setAttribute("aria-expanded", "false");
    if (state.searchQuery) { state.searchQuery = ""; input.value = ""; renderList(); }
    setTimeout(function () { input.hidden = true; }, 180);
  }
  $("#search-btn").onclick = function () {
    if ($("#search-input").hidden) openSearch(); else closeSearch();
  };
  $("#search-input").addEventListener("input", function () {
    state.searchQuery = this.value.trim().toLowerCase();
    renderList();
  });
  $("#search-input").addEventListener("keydown", function (e) {
    if (e.key === "Escape") { e.stopPropagation(); closeSearch(); $("#search-btn").focus(); }
  });
  document.addEventListener("click", function (e) {
    if (!$("#search-input").hidden && !$("#search").contains(e.target)) closeSearch();
  });

  $("#backdrop").onclick = closeSheet;
  $("#hist-backdrop").onclick = closeHistoryPopup;
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    var popup = $("#hist-popup");
    if (popup && !popup.hidden) { closeHistoryPopup(); return; }
    closeSheet();
  });

  // Installed PWAs are often just resumed from a suspended/frozen state when reopened
  // (no real page load), so a fetch made hours or days ago can sit on screen indefinitely
  // looking current. Re-fetch whenever the app comes back to the foreground, throttled so
  // quick app-switches don't refetch needlessly.
  function refreshIfStale() {
    if (document.body.classList.contains("locked")) return;
    if (Date.now() - lastFetchAt < 60000) return;
    loadManifest();
  }
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") refreshIfStale();
  });
  window.addEventListener("pageshow", function (e) { if (e.persisted) refreshIfStale(); });
  window.addEventListener("focus", refreshIfStale);

  // ---------------------------------------------------------------- passphrase gate
  // Basic deterrent only. The SHA-256 of the passphrase is stored, never the plaintext.
  // To change it: open the console and run  hashPassphrase("your new phrase")  then
  // paste the result into PASS_HASH below and push. (Not real security — see README.)
  var PASS_HASH = "10f2cc0be9fa2cf6c64c59749e20cbd0f0e1fdd67cac6934e56380c69a24c54d";
  var UNLOCK_KEY = "mv_unlocked_v1";

  function sha256Hex(str) {
    var buf = new TextEncoder().encode(str);
    return crypto.subtle.digest("SHA-256", buf).then(function (d) {
      return Array.prototype.map.call(new Uint8Array(d), function (b) {
        return ("0" + b.toString(16)).slice(-2);
      }).join("");
    });
  }
  window.hashPassphrase = function (p) { return sha256Hex(p); };

  function start() {
    state.shortlist = loadSet(SHORTLIST_KEY);
    state.bought = loadSet(BOUGHT_KEY);
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("sw.js").catch(function () {});
    }
    loadManifest();
  }

  function unlock() {
    document.body.classList.remove("locked");
    $("#gate").hidden = true;
    start();
  }

  if (!PASS_HASH || localStorage.getItem(UNLOCK_KEY) === PASS_HASH) {
    unlock();
  } else {
    document.body.classList.add("locked");
    $("#gate").hidden = false;
    var input = $("#gate-pass");
    setTimeout(function () { input.focus(); }, 50);
    $("#gate-form").addEventListener("submit", function (e) {
      e.preventDefault();
      sha256Hex(input.value).then(function (h) {
        if (h === PASS_HASH) {
          try { localStorage.setItem(UNLOCK_KEY, h); } catch (err) {}
          unlock();
        } else {
          $("#gate-err").hidden = false;
          input.value = "";
          input.focus();
        }
      });
    });
  }
})();
