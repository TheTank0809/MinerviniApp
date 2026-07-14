/* Minervini Tracker front-end — reads static JSON written by the weekly pipeline.
   Multiple screens within a universe (e.g. Minervini Screener + Growth Screener) are
   merged into one list by ticker; each stock shows a tick badge per screen it's
   currently in, and the detail sheet can switch between each screen's own scorecard. */
(function () {
  "use strict";

  var state = { manifest: null, universeKey: null, universes: {}, screens: [],
                tab: "active", sort: "score", filter: null, data: { active: [], dropped: [] } };
  var $ = function (sel) { return document.querySelector(sel); };

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
        $("#regime-line").textContent = "no data yet — run the weekly scan";
      });
  }

  function renderErrorBanner(m) {
    var failed = (m.screens || []).filter(function (s) { return s.error; });
    var el = $("#error-banner");
    if (!failed.length) { el.hidden = true; return; }
    var isAuth = failed.some(function (s) { return /SCREENER_SESSIONID|401|403/i.test(s.error || ""); });
    var lines = failed.map(function (s) { return "• " + s.label + ": " + s.error; });
    el.innerHTML = "⚠ " + failed.length + " screen" + (failed.length > 1 ? "s" : "") +
      " failed on the last run" +
      (isAuth ? " — your screener.in session has likely expired. Grab a fresh sessionid cookie and update the SCREENER_SESSIONID secret on GitHub." : ".") +
      "<br><span style=\"opacity:.75\">" + lines.map(esc).join("<br>") + "</span>";
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
      e.isNew = activeSlugs.some(function (s) {
        var r = e.activeRecs[s];
        return r.joined_date && r.last_updated === r.joined_date;
      });
      if (activeSlugs.length) active.push(e); else if (Object.keys(e.droppedRecs).length) dropped.push(e);
    });
    return { active: active, dropped: dropped };
  }

  // ---------------------------------------------------------------- header

  function renderRegime(run) {
    var el = $("#regime-line");
    var llmEl = $("#llm-line");
    if (!run) {
      el.innerHTML = '<span class="cursor">▮</span> awaiting first scan';
      llmEl.hidden = true;
      return;
    }
    var r = run.regime || {};
    el.className = "regime " + (r.label === "CORRECTION" ? "correction" : r.label === "CAUTION" ? "caution" : "");
    el.innerHTML = '<span class="cursor">▮</span> MKT ' + esc(r.label || "?") +
      " " + (r.score != null ? r.score + "/6" : "");
    $("#runstamp").textContent = "scan " + fmtDate(run.run_date);

    var llm = run.llm;
    if (llm && llm.model) {
      llmEl.textContent = "verdicts: " + (llm.enabled ? llm.model : "rule-based (no LLM key set)");
      llmEl.hidden = false;
    } else {
      llmEl.hidden = true;
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
      b.onclick = function () { state.universeKey = uk; state.filter = null; renderChips(); loadUniverse(uk); };
      box.appendChild(b);
    });
  }

  function renderFilterChips() {
    var box = $("#screen-filter");
    box.innerHTML = "";
    if (state.screens.length < 2) { box.hidden = true; return; }
    box.hidden = false;
    var allBtn = document.createElement("button");
    allBtn.className = "chip" + (!state.filter ? " active" : "");
    allBtn.textContent = "All";
    allBtn.onclick = function () { state.filter = null; renderFilterChips(); renderList(); };
    box.appendChild(allBtn);
    state.screens.forEach(function (raw) {
      var s = screenMeta(raw.screen);
      var b = document.createElement("button");
      b.className = "chip" + (state.filter === s.screen ? " active" : "");
      b.textContent = s.short + " only";
      b.onclick = function () { state.filter = s.screen; renderFilterChips(); renderList(); };
      box.appendChild(b);
    });
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

  function gatebar(sc) {
    var tt = ((sc.gates || {}).trend_template) || {};
    var html = '<span class="gatebar" title="Trend Template c1–c8">';
    for (var i = 1; i <= 8; i++) {
      var v = tt["c" + i];
      html += "<i class=\"" + (v === true ? "on" : v === false ? "off" : "") + "\"></i>";
    }
    return html + "</span>";
  }

  function membershipPills(entry) {
    return state.screens.map(function (raw) {
      var s = screenMeta(raw.screen);
      var on = !!entry.activeRecs[s.screen];
      var droppedRec = entry.droppedRecs[s.screen];
      var title = s.label + (on ? " — active" : droppedRec ? " — left " + fmtDate(droppedRec.dropped_date) : " — not in this screen");
      return '<span class="mship' + (on ? " on" : "") + '" title="' + esc(title) + '">' + esc(s.short) + "</span>";
    }).join("");
  }

  function renderList() {
    var list = state.data[state.tab] || [];
    if (state.filter) {
      list = list.filter(function (e) { return e.activeRecs[state.filter] || e.droppedRecs[state.filter]; });
    }
    var box = $("#list");
    $("#thead").style.display = list.length ? "" : "none";
    $("#empty").hidden = !!list.length;
    $("#count").textContent = list.length + (state.tab === "active" ? " tracked" : " left");
    box.innerHTML = "";
    sorted(list).forEach(function (entry) {
      var rec = entry.primary;
      var sc = rec.scorecard || {};
      var t = sc.technicals || {};
      var scores = sc.scores || null;
      var tot = scores ? scores.total : null;
      var scoreCls = tot == null ? "low" : tot >= 80 ? "" : tot >= 60 ? "mid" : "low";
      var bucket = scores ? (sc.action_bucket || "") : "GATE_FAIL";
      var row = document.createElement("button");
      row.className = "row" + (state.tab === "dropped" ? " frozen" : "");
      var reason = rejectReason(sc);
      var statusText = reason || sc.quality_band || sc.status || "";
      var sub = state.tab === "dropped"
        ? "joined " + fmtDate(entry.joined_date) + " · left " + fmtDate(entry.dropped_date) + (reason ? " · " + reason : "")
        : "joined " + fmtDate(entry.joined_date) + (statusText ? " · " + statusText : "");
      var cells =
        '<span class="stockcell"><span class="ticker">' + esc(entry.ticker) + "</span>" +
        membershipPills(entry) +
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
      box.appendChild(row);
    });
  }

  // ---------------------------------------------------------------- detail sheet

  function itemRows(sec) {
    var keys = Object.keys(sec).filter(function (k) { return k !== "subtotal"; });
    return keys.map(function (k) {
      return "<b>" + (ITEM_LABEL[k] || k) + "</b> " + sec[k] + "/" + (ITEM_MAX[k] || "?");
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
    renderSheet(entry, slug || Object.keys(pool)[0]);
  }

  function renderSheet(entry, slug) {
    var rec = entry.activeRecs[slug] || entry.droppedRecs[slug];
    var sc = rec.scorecard || {};
    var t = sc.technicals || {};
    var scores = sc.scores;

    var html = '<div class="sheet-head"><div><h2>' + esc(entry.ticker) + "</h2>" +
      '<div class="sname">' + esc(entry.name || "") + "</div></div>" +
      '<button class="close" aria-label="Close">✕</button></div>';

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
    if (scores) html += '<span class="badge band">' + esc(sc.quality_band) + " · " + scores.total + "/100</span>";
    html += '<span class="badge">' + esc((sc.action_bucket || sc.status || "").replace(/_/g, " ")) + "</span>";
    if (rec.dropped_date) html += '<span class="badge frozen">left screen ' + fmtDate(rec.dropped_date) + " · frozen</span>";
    var reason = rejectReason(sc);
    if (reason) html += '<span class="badge flag">' + esc(reason) + "</span>";
    (sc.red_flags || []).forEach(function (f) { html += '<span class="badge flag">' + esc(f) + "</span>"; });
    if (sc.risk_level) html += '<span class="badge">risk ' + esc(sc.risk_level) + "</span>";
    html += "</div>";

    html += '<div class="kv">' +
      kv("Price", t.price != null ? "₹" + t.price : "—") +
      kv("RS pct", t.rs_percentile != null ? t.rs_percentile : "—") +
      kv("Off 52w high", t.pct_below_52w_high != null ? t.pct_below_52w_high + "%" : "—") +
      kv("Above 52w low", t.pct_above_52w_low != null ? "+" + t.pct_above_52w_low + "%" : "—") +
      kv("Joined", fmtDate(rec.joined_date)) +
      kv("As of", fmtDate(sc.as_of)) + "</div>";

    // gates
    var tt = ((sc.gates || {}).trend_template) || {};
    html += '<div class="sec-title">Gate 1 · Trend Template</div><div class="gategrid">';
    var GL = ["&gt;150/200d", "150&gt;200", "200d rising", "50&gt;150/200", "&gt;50d", "+30% low", "-25% high", "RS≥70"];
    for (var i = 1; i <= 8; i++) {
      var v = tt["c" + i];
      html += '<div class="gcell ' + (v === true ? "on" : v === false ? "off" : "") + '">c' + i +
        "<br>" + GL[i - 1] + "</div>";
    }
    html += "</div>";
    if (tt.near_miss_notes) html += '<p class="uv">' + esc(tt.near_miss_notes) + "</p>";

    if (scores) {
      html += '<div class="sec-title">Score · ' + scores.total + "/100</div><div class=\"scorebars\">";
      Object.keys(SECTION_MAX).forEach(function (k) {
        var sec = scores[k];
        var pct = Math.round(sec.subtotal / SECTION_MAX[k] * 100);
        html += '<div class="sbar"><span class="lbl">' + SECTION_LABEL[k] + "</span>" +
          '<span class="track"><span class="fill" style="width:' + pct + '%"></span></span>' +
          '<span class="val">' + sec.subtotal + "/" + SECTION_MAX[k] + "</span></div>" +
          '<div class="items">' + itemRows(sec) + "</div>";
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
    if (uv.length) html += '<p class="uv">Unverified (scored 0): ' + esc(uv.join(", ")) + "</p>";
    var vc = sc.valuation_context || {};
    if (vc.pe) html += '<p class="uv">Context only: P/E ' + vc.pe + " — never a criterion.</p>";

    var sheet = $("#sheet");
    var prevScroll = sheet.scrollTop;
    sheet.innerHTML = html;
    sheet.hidden = false;
    $("#backdrop").hidden = false;
    sheet.querySelector(".close").onclick = closeSheet;
    var toggleBtns = sheet.querySelectorAll(".stbtn");
    if (toggleBtns.length) {
      toggleBtns.forEach(function (btn) {
        btn.onclick = function () { renderSheet(entry, btn.dataset.slug); };
      });
      sheet.scrollTop = prevScroll;
    } else {
      sheet.querySelector(".close").focus();
    }
  }

  function kv(k, v) { return "<div><div class=\"k\">" + k + "</div><div class=\"v\">" + v + "</div></div>"; }

  function closeSheet() {
    $("#sheet").hidden = true;
    $("#backdrop").hidden = true;
    unlockBodyScroll();
  }

  // ---------------------------------------------------------------- wiring

  document.querySelectorAll(".tab").forEach(function (b) {
    b.onclick = function () {
      document.querySelectorAll(".tab").forEach(function (x) { x.classList.remove("active"); });
      b.classList.add("active");
      state.tab = b.dataset.tab;
      renderList();
    };
  });
  $("#sort").onchange = function () { state.sort = this.value; renderList(); };
  $("#backdrop").onclick = closeSheet;
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeSheet(); });

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
