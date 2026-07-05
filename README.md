# MinerviniApp

Personal tracker for my screener.in **Minervini Screener** screen. Every Sunday a GitHub
Action pulls the screen, scores every stock with the [Minervini Scorecard Engine v3](PROMPT.md),
records who joined and who fell out, and publishes a mobile-friendly website
(installable as an app) on GitHub Pages. Everything runs on free tiers — no domain, no server.

## How it works

```
screener.in (your saved screen)          Yahoo Finance (OHLCV)
        │                                        │
        ▼                                        ▼
GitHub Action, every Sunday 9:30 AM IST  (backend/pipeline.py)
  • diff screen vs tracked stocks
  • NEW stocks  -> joined_date + MODE=FULL scorecard (optional LLM verdict)
  • EXISTING    -> MODE=WEEKLY delta (score changes, gate flips, alerts)
  • DROPPED     -> moved to dropped.json with dropped_date, scorecard frozen
        │
        ▼
docs/data/*.json  ──►  GitHub Pages site (PWA) ──► your phone
```

Scoring is deterministic Python implementing PROMPT.md exactly: Stage 0 market regime,
Gate 1 Trend Template (hard gate), Gate 2 investability, the 100-point A–H rubric,
red flags, classification, and trade plans. Per Prime Directive 2, any field that can't
be verified from screener.in / Yahoo scores 0 and is listed under "unverified".

## One-time setup (~5 minutes)

1. **Create the GitHub repo** (must be public — GitHub Pages is free only for public repos):
   go to <https://github.com/new>, name it `MinerviniApp`, no README, then:

   ```bash
   cd MinerviniApp
   git remote add origin https://github.com/<your-username>/MinerviniApp.git
   git push -u origin main
   ```

2. **Add your screener.in session secret**: log in to screener.in in Chrome →
   DevTools (⌥⌘I) → Application → Cookies → `https://www.screener.in` → copy the value
   of `sessionid`. Then on GitHub: repo → Settings → Secrets and variables → Actions →
   **New repository secret** → name `SCREENER_SESSIONID`, paste the value.
   > The session lasts a long time, but if a run fails with an auth error, paste a fresh one.

3. **Enable GitHub Pages**: repo → Settings → Pages → Source: **GitHub Actions**.

4. **First run**: repo → Actions → *Weekly Minervini scan* → **Run workflow**.
   After it finishes, your site is at `https://<your-username>.github.io/MinerviniApp/`.

5. **Install on your phone**: open that URL in Chrome (Android) or Safari (iOS) →
   Share → **Add to Home Screen**. It opens full-screen like an app and keeps the
   last data readable offline.

### Optional: richer verdicts with Claude

Add an `ANTHROPIC_API_KEY` repository secret and new stocks get an LLM-written verdict
(summary, strengths, weaknesses, catalyst check for H3) using PROMPT.md as the system
prompt. Without the key everything still works — verdicts are rule-generated.
This is the only non-free option (a few paise per new stock; capped by
`llm_max_new_stocks_per_run` in the config).

## Adding the "Growth filter" screen later

Open the screen on screener.in and copy the exact address-bar URL. Edit
[backend/config.yaml](backend/config.yaml), paste it into `url:` under `Growth filter`,
and set `enabled: true`. The next run picks it up and the website grows a new chip
automatically. The `us` universe
block is a placeholder for the planned US/global section — the data layout and UI
already support multiple universes; only a US data fetcher needs to be added.

## Repo layout

| Path | What |
|---|---|
| `PROMPT.md` | Minervini Scorecard Engine v3 — the spec everything implements |
| `backend/pipeline.py` | Weekly orchestrator (diff, score, freeze drop-outs) |
| `backend/scorecard.py` | Gates + 100-point rubric + buckets + trade plans + deltas |
| `backend/technicals.py` | DMAs, RS percentiles, base/VCP detection, market regime |
| `backend/screener_client.py` / `fundamentals.py` | screener.in fetch + parse |
| `backend/llm.py` | Optional Claude verdict layer |
| `docs/` | The website / PWA (GitHub Pages serves this folder) |
| `docs/data/` | All state: active.json, dropped.json, runs.json per screen |
| `.github/workflows/weekly.yml` | Sunday cron + manual trigger |

## Running locally

```bash
pip install -r backend/requirements.txt
SCREENER_SESSIONID=<value> python backend/pipeline.py
python3 -m http.server 8000 --directory docs   # then open http://localhost:8000
```

## Honest limitations (free-data reality)

- **RS percentile** is ranked within the tracked screen universe, not all of Nifty 500 —
  fine in practice since your screen pre-filters for strength.
- Industry-group RS, MF scheme counts, marquee-holder checks, FCF, and governance-event
  scans aren't available from free sources → those items score 0 and are listed as
  unverified (per the prompt's own rule, never estimated).
- Base/VCP detection is a heuristic on daily bars; treat G-section scores as a guide
  and eyeball the chart before buying.
- The site currently shows FII+DII holdings as the institutional sponsorship proxy
  (screener.in doesn't split out MF holdings).
- The site is public (repo is public). It contains only stock lists and scores — your
  screener session id stays in GitHub Secrets and is never written to the repo.
