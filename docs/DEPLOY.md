# Deploying Fundrix N18 for free (GitHub + Cloudflare Pages + Supabase)

```
 AMFI (NAV files) --daily--> GitHub Actions (Python) --> static JSON  --> Cloudflare Pages  <-- visitors' browsers
                                   |                                                          |
                                   +--> `data` branch (saved NAV history)       Supabase: logins, saved screens, watchlists
```
Visitors only download static files from Cloudflare. Supabase is touched only by logged-in users (login, saved screens, watchlist).

## 0. What you need (all free)
GitHub account · Cloudflare account · your existing Supabase project (accounts already set up per `supabase/README.md`).

## 1. Supabase: add the new tables (2 minutes)
SQL Editor -> paste the **whole** `supabase/schema.sql` -> Run. It is safe to re-run; it adds `screens`, `watchlist` and the share function.

## 2. Put the code on GitHub
Create a **public** repository (public repos get free Actions minutes) and push this folder to the `main` branch.
Never commit secrets: only the public Supabase URL and `anon` key ever go in the frontend.

## 3. Cloudflare: token + account id
1. Cloudflare dashboard -> right sidebar shows your **Account ID** (copy it).
2. My Profile -> API Tokens -> Create Token -> *Custom token* -> permission **Account / Cloudflare Pages / Edit** -> create, copy the token.

## 4. GitHub: secrets and variables (repo -> Settings -> Secrets and variables -> Actions)
| Type | Name | Value |
|---|---|---|
| Secret | `CLOUDFLARE_API_TOKEN` | token from step 3 |
| Secret | `CLOUDFLARE_ACCOUNT_ID` | account id from step 3 |
| Variable | `SUPABASE_URL` | e.g. `https://abcd1234.supabase.co` |
| Variable | `SUPABASE_ANON_KEY` | the *anon public* key |
| Variable | `TURNSTILE_SITE_KEY` | optional (free CAPTCHA site key) |
| Variable | `CF_PAGES_PROJECT` | optional; default `fundrix-n18` (becomes `fundrix-n18.pages.dev`) |

## 5. First deploy (demo data, works immediately)
Push to `main` (or Actions -> *Update data and deploy* -> Run workflow with backfill `0`). With no real data yet, it publishes the **demo** dataset
so you can check the site, logins and saved screens at `https://<project>.pages.dev`.

## 6. Load real data (one time) 
Actions -> *Update data and deploy* -> **Run workflow** -> `backfill_weeks` = `530`.
This downloads today's AMFI NAV file plus ~10 years of weekly history (about 530 small requests, roughly 10-40 minutes) and publishes real data.
From then on the workflow runs by itself every morning at 07:00 IST with `backfill_weeks` = 0 (only today's file is needed).

**If step 6 fails** with a download error, AMFI may be refusing GitHub's servers, or its format changed. The workflow stops without publishing anything, so the site keeps its previous data. Fallback: run the same command on your own computer (a normal home connection usually works):
```bash
pip install numpy
python -m pipeline.build --mode real --state state --out frontend/data --backfill-weeks 530
# then publish state so GitHub can continue daily:
cd state && git init -b data && git add state.json.gz && git commit -m state && git remote add origin <your repo url> && git push -f origin data
```
and re-run the workflow with backfill `0`. (The `data` branch must contain `state.json.gz` at its root.)

## 7. Supabase settings that depend on your site address
* Auth -> URL configuration: **Site URL** = `https://<project>.pages.dev`.
* Edge Function secret `ALLOWED_ORIGIN` = the same origin (`supabase secrets set ALLOWED_ORIGIN=https://<project>.pages.dev`), then redeploy the gateway.
* Optional Turnstile: add `TURNSTILE_SECRET` as an Edge Function secret and `TURNSTILE_SITE_KEY` as the GitHub variable.

## 8. Optional: AUM, expense ratio, exit load, riskometer
NAV files do not contain them. Put what you have in `data/extras.csv` (see `data/extras.example.csv`; keyed by AMFI scheme code), commit, and the next run merges it.
Until then those columns are simply blank and the default screen hides the AUM / expense filters.

## 9. Custom domain (optional, free)
Cloudflare Pages project -> Custom domains. A domain name itself is not free; `*.pages.dev` is.

## Keeping it alive
* GitHub disables scheduled workflows after ~60 days with no repository activity: open the Actions tab now and then, or push any small commit.
* Supabase free projects pause after a week of *no activity*; real users logging in keep it awake. If it pauses, restore it from the dashboard.
* Watch the Actions tab: a red run means nothing new was published (the site keeps serving yesterday's data).

## Limits and honesty
* NAVs change once per business day, so "live" = refreshed every morning, like the reference product.
* Cloudflare Pages free limits (files per deployment, file size) should be fine here (~300 files, a few MB) but check current limits in Cloudflare's docs.
* The AMFI download URLs and file layout were written from documentation and could not be tested from the build environment; the first real run is the real test.
* Returns come from weekly NAV samples interpolated to the exact date, so they can differ slightly (typically well under a percentage point for calm funds, more for very volatile ones) from figures computed on daily NAVs.
* This is information, not investment advice, and you are responsible for the legal and privacy side of running a public site (see the notice in `frontend/auth.js`).
