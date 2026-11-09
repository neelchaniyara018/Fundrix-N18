# Fundrix N18 — a mutual-fund screener (independent rebuild)

An original implementation of the workflow found in Tickertape's *Mutual Fund Screener*: filter a universe of funds
with 50+ metrics, sort and page the results, save and share screens, build custom filters, export, and drill into a fund.
No code, data, text or branding was copied from Tickertape; this is a clean-room rebuild from observable behaviour.

> **Data honesty.** Demo mode uses a **generated** dataset (fictional fund houses, synthetic NAVs). Real mode (`pipeline/`) uses AMFI NAVs. Either way every return, risk and
> ratio is computed from the NAV series with real formulas; in demo mode the numbers describe
> nothing in the real market. See docs/DEPLOY.md to switch to real data. Not investment advice.

## 1. Reverse-engineering notes (what was analysed and how)

Hopper/Ghidra are binary disassemblers and have nothing to analyse in a hosted web app (there is no native binary), so the
equivalent was used: the rendered DOM of `/screener/mutual-fund`, public help/blog pages, and observable behaviour.

| Area | Finding |
|---|---|
| **Workflow** | Greeting + title → filter panel ("N filters applied", *Reset*, per-filter *Remove*) → results table "Showing 1–20 of 1789", sortable columns, "last updated" stamp. Saved screens ("All Screens / New Screen / Edit Filters"), a note field ("Top 5 tax-saving ELSS funds"), shareable screen URLs, CSV export with no row limit. |
| **Default screen** | Universe = all funds; filters Plan (Growth), AUM, CAGR 3Y, Expense Ratio; sorted by AUM descending. Columns = Name, Sub Category, Plan + one column per applied filter. |
| **Filters** | 50+ in five groups — Scheme Info, Returns, Risk, Portfolio Composition, Ratios — plus user-defined *custom filters* built from data items (e.g. VaR from standard deviation). Multi-select (category, plan, benchmark) and range sliders (AUM, CAGR, expense, exit load, lock-in, min lumpsum…). Universe = all funds or watchlist. |
| **Fund page** | Scheme info (AUM, exit load, lock-in, min SIP/lumpsum), returns, key ratios (Sharpe, alpha/beta, tracking error, volatility, YTM), manager, tax, peer comparison (1Y, 3Y CAGR, expense ratio). |
| **Stack signals** | Server-rendered React/Next-style front-end over a JSON API (no inspectable native code). |

## 2. Architecture (final)

* **Static site on Cloudflare Pages** – `frontend/` is plain HTML/CSS/JS with no build step. All filtering, sorting, custom formulas, fund pages, the SIP calculator and CSV export run **in the browser** (`frontend/engine.js`) on JSON files.
* **Daily data build on GitHub Actions** – `pipeline/` downloads AMFI's NAV file, keeps a weekly NAV history, computes returns / risk / ratios (`backend/data.py::compute_metrics`) and writes `frontend/data/*.json`.
* **Supabase** – login (email OTP, phone + email + password, forgot password) via an Edge Function, plus per-user saved screens and watchlists protected by Row Level Security. Logged-out users keep screens in the browser, and they are moved into the account on login.
* **`backend/` (FastAPI)** is no longer deployed. It remains as the reference implementation: `tests/test_parity.py` requires the browser engine to give identical answers.
* See **`docs/DEPLOY.md`** for the step-by-step deployment.

## 3. Feature matrix

| Feature | Status |
|---|---|
| 51 filters in 5 groups; range sliders + typed min/max; multi-select with search; add/remove/reset | ✅ |
| Default screen mirroring the reference; "N filters applied"; Reset all | ✅ |
| Custom filters (formula over any numeric metric, validate with live range, then slider/sort/column/export like any filter) | ✅ |
| Sorting (nulls always last), pagination (20/50/100), "Showing x–y of N", name/fund-house search | ✅ |
| Column picker (filters auto-add their column) | ✅ |
| Saved screens with name + note, update/save-as-new, delete, share link, starter presets | ✅ |
| Universe: all funds / watchlist; watchlist stars | ✅ |
| Export **all** matching rows to CSV (with spreadsheet-formula-injection guard) | ✅ |
| Fund page: NAV chart (1M–MAX, hover), returns vs category, risk/ratio cards with peer percentile, portfolio mix, holdings, sectors, scheme details, tax note, peers | ✅ |
| SIP / lumpsum returns calculator (XIRR) on real NAV history | ✅ |
| Compare up to 4 funds: rebased growth chart + best-value highlighting | ✅ |
| Light/dark theme, responsive layout, keyboard-accessible controls | ✅ |
| Accounts: sign up with phone + email + password, 6-digit email OTP, forgot password, log in with email **or** phone (see `supabase/README.md`) | ✅ built, tested against a local Supabase mock; not yet run against a live project |
| **Compulsory sign-in:** 30 s free look, then an undismissable centered pop-up; filters, saved screens, compare and watchlist locked for guests (`frontend/gate.js`, see docs/DEPLOY.md) | ✅ front-end gate (static data files remain public) |
| Real-data loader (mfapi.in) | ⚠️ implemented, parsing unit-tested offline; live fetch not exercised in the build sandbox |

Not built (not observable without an account / out of scope): user accounts & auth, live data feeds, portfolio import.
Screens and watchlists are **anonymous** (keyed by a random id in `localStorage`); anyone holding a screen id can *read* it (that is the share link), only its creator can edit/delete. There is no real authentication.

## 4. Run it locally
```bash
pip install -r requirements.txt
python -m pipeline.build --mode demo --out frontend/data       # demo dataset (instant); or --mode real, see docs/DEPLOY.md
python -m http.server 8000 -d frontend                          # open http://localhost:8000
```
Accounts need Supabase settings in `frontend/config.js` (see `supabase/README.md`); without them the site runs fully, minus login.

### Tests
```bash
pytest tests/test_api.py tests/test_ingest.py tests/test_pipeline.py tests/test_parity.py tests/test_deploy_files.py   # fast, no browser
node --test tests/gateway.test.mjs                        # account gateway unit tests
pip install playwright && playwright install chromium
pytest                                                    # + browser end-to-end suite (~45 s)
```

### Real data (older, mfapi.in-based loader for the dev API; the production pipeline is `pipeline/`)
```bash
python -m backend.ingest_real --out data/funds.db --limit 300 --match "flexi cap|mid cap|elss"
python -m backend.ingest_real --out data/funds.db --extras extras.csv   # add AUM / expense ratio / exit load you compiled
```
Real NAV history gives real returns and risk metrics. AUM, expense ratio, exit load, holdings and riskometer are *not* in NAV data — supply them via `extras.csv`
(`scheme_code,aum,expense_ratio,min_sip,min_lumpsum,exit_load,lock_in,benchmark,riskometer`) or they stay blank. The benchmark is a peer average, not an index TRI.

## 5. API (all under `/api`, header `X-Client-Id` identifies the anonymous user)

| Method & path | Purpose |
|---|---|
| `GET /meta` | metric registry (labels, groups, bounds, options), category tree, as-of date |
| `POST /screen` | `{universe, ids, filters[{key,min,max,values}], custom[{name,expr}], search, sort_key, sort_dir, page, page_size, columns}` → rows |
| `POST /export` | same body → CSV of **all** matching rows |
| `POST /custom/validate` | check a formula; returns count/min/max/median |
| `GET /funds/{id}`, `/funds/{id}/nav`, `/funds/{id}/calculator?mode=sip\|lumpsum&amount&years` | fund page data |
| `GET /compare?ids=1,2,3` | 2–4 funds, rebased series |
| `GET/PUT/DELETE /watchlist[/{id}]` | watchlist |
| `GET/POST /screens`, `GET/PUT/DELETE /screens/{id}` | saved screens |

## 6. Layout
```
pipeline/ amfi.py (parser) · state.py (history) · build.py (static data) · requirements.txt
docs/     DEPLOY.md
.github/workflows/deploy.yml
backend/  main.py (reference API, not deployed) · data.py (analytics + demo universe) · metrics.py (registry) · expr.py (safe formulas) · ingest_real.py · schema.sql
frontend/ index.html · style.css · app.js · engine.js · data.js · auth.js · gate.js · config.js · _headers
supabase/ schema.sql · functions/gateway (logic.js, handler.js, index.ts) · README.md
tests/    test_api.py · test_ingest.py · test_e2e.py · test_auth_e2e.py · gateway.test.mjs · mock_supabase.mjs
```
To add a metric: add a row in `metrics.py`, compute it in `data.py` — filters, columns, sorting, export, compare and the custom-filter language pick it up automatically.
